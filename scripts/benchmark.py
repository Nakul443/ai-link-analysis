"""Benchmark candidate LLMs on the real analysis task.

Measures latency, tokens, cost, JSON validity and automatic quality checks.

Usage:
    python -m scripts.benchmark URL [URL ...] [--runs 1] [--delay 7] [--schema] [--tag NAME]
                                [--models provider:model,provider:model]

Keys (models without a key are skipped):
    GEMINI_API_KEY (falls back to LLM_API_KEY when LLM_PROVIDER=gemini),
    OPENAI_API_KEY, ANTHROPIC_API_KEY
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from app.config import get_settings
from app.schemas import LLMAnalysisResult, Platform
from app.services import llm_service
from app.services.analysis_service import build_prompts
from app.services.competitor_service import clean_competitors
from app.services.website_extractor import extract_website

DEFAULT_MODELS = [
    ("gemini", "gemini-3.7-flash"),
    ("gemini", "gemini-3.8-flash"),
    ("openai", "gpt-5.6-luna"),
    ("anthropic", "claude-haiku-5-5"),
]

# USD per 1M tokens (input, output). Paid-tier list prices checked 2026-11-27.
PRICES = {
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
    "gpt-5.6-luna": (0.20, 1.20),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts <= 100K tokens
}
PRICES_2027 = {"gemini-3.7-flash": (1.50, 7.50), "gemini-3.8-flash": (1.50, 7.50)}

OUT_DIR = Path("docs")
KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


# ---------- setup ----------
def api_key_for(provider: str) -> str:
    key = os.getenv(KEY_ENV[provider], "")
    if not key and provider == "gemini":
        s = get_settings()
        if s.llm_provider.lower() in ("gemini", "google"):
            key = s.llm_api_key
    return key


def pick_models(arg: str | None) -> list[tuple[str, str, str]]:
    pairs = (
        DEFAULT_MODELS
        if not arg
        else [tuple(x.strip().split(":", 1)) for x in arg.split(",")]
    )
    picked = []
    for provider, model in pairs:
        key = api_key_for(provider)
        if key:
            picked.append((provider, model, key))
        else:
            print(f"skip {provider}:{model} (no API key set)")
    return picked


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def save_sample(data: LLMAnalysisResult, url: str, model: str) -> None:
    d = OUT_DIR / "samples"
    d.mkdir(parents=True, exist_ok=True)
    host = urlparse(url).hostname or "site"
    (d / f"{slug(host)}__{slug(model)}.json").write_text(
        data.model_dump_json(indent=2), encoding="utf-8"
    )


# ---------- automatic quality checks ----------
def words_in(text: str, name: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 4]
    return bool(words) and all(w in text for w in words)


def quality(data: LLMAnalysisResult, page_text: str, domain: str) -> dict:
    a = data.analysis
    seo = a.seo_improvements
    covered = {p for q in a.target_search_queries for p in q.platforms}
    comps = clean_competitors(
        a.competitor_analysis.competitors, data.brand.name, domain
    )
    site_services = [sv for sv in a.top_services if sv.source == "website"]
    grounded = (
        sum(words_in(page_text, sv.name) for sv in site_services) / len(site_services)
        if site_services
        else None
    )
    return {
        "title_ok": len(seo.recommended_title) <= 60,
        "meta_ok": len(seo.meta_description) <= 160,
        "platform_coverage": len(covered) / len(Platform),
        "competitors_ok": len(comps) >= 3,
        "services_grounded": grounded,
    }


# ---------- one call ----------
async def run_once(
    client, provider, model, key, url, system, user, page_text, domain
) -> dict:
    cfg = llm_service._Cfg(provider, model, key)
    usage = llm_service._Usage()
    row = {"url": url, "provider": provider, "model": model, "ok": False, "error": None}
    waited = 0.0
    started = time.perf_counter()
    for attempt in (1, 2):
        try:
            data = await llm_service._run_config(
                client, cfg, system, user, LLMAnalysisResult, usage
            )
        except llm_service.LLMRateLimitError:
            if attempt == 1:
                print("429, waiting 65s then retrying once...", end=" ", flush=True)
                await asyncio.sleep(65)
                waited += 65
                continue
            row["error"] = "LLM_RATE_LIMITED"
        except llm_service.LLMError as exc:
            row["error"] = exc.code
        else:
            row.update(ok=True, **quality(data, page_text, domain))
            save_sample(data, url, model)
        break
    row.update(
        latency_s=round(time.perf_counter() - started - waited, 2),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        calls=usage.calls,
        single_call=usage.calls == 1,
    )
    return row


# ---------- reporting ----------
def mean(values) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.fmean(vals) if vals else None


def rate(values) -> float | None:
    vals = list(values)
    return sum(1 for v in vals if v) / len(vals) if vals else None


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for model in dict.fromkeys(r["model"] for r in rows):
        mr = [r for r in rows if r["model"] == model]
        ok = [r for r in mr if r["ok"]]
        in_t, out_t = (
            mean(r["input_tokens"] for r in ok),
            mean(r["output_tokens"] for r in ok),
        )
        price, price27 = PRICES.get(model), PRICES_2027.get(model, PRICES.get(model))

        def cost(p):
            return (in_t * p[0] + out_t * p[1]) / 1e6 if ok and p else None

        lat = [r["latency_s"] for r in ok]
        out.append(
            {
                "model": model,
                "runs": len(mr),
                "success_rate": rate(r["ok"] for r in mr),
                "single_call_rate": rate(r["single_call"] for r in ok),
                "latency_median_s": statistics.median(lat) if lat else None,
                "latency_mean_s": mean(lat),
                "avg_input_tokens": in_t,
                "avg_output_tokens": out_t,
                "cost_per_analysis_usd": cost(price),
                "cost_per_analysis_2027_usd": cost(price27),
                "title_ok_rate": rate(r["title_ok"] for r in ok),
                "meta_ok_rate": rate(r["meta_ok"] for r in ok),
                "platform_coverage": mean(r["platform_coverage"] for r in ok),
                "competitors_ok_rate": rate(r["competitors_ok"] for r in ok),
                "services_grounded": mean(r["services_grounded"] for r in ok),
            }
        )
    return out


def fmt(v, kind="num") -> str:
    if v is None:
        return "n/a"
    if kind == "pct":
        return f"{v * 100:.0f}%"
    if kind == "usd":
        return f"${v:.4f}"
    return f"{v:.1f}" if isinstance(v, float) else str(v)


def render_markdown(summary: list[dict], schema: bool) -> str:
    head = (
        "| Model | Runs | Success | Single call | Median s | Mean s | Avg in tok | Avg out tok | "
        "$/analysis | $/analysis (2027 price) | Title <=60 | Meta <=160 | 5/5 platforms | >=3 competitors | "
        "Services grounded |"
    )
    lines = [
        f"Benchmark date: {time.strftime('%Y-%m-%d')} | full JSON Schema in prompt: {schema}",
        "",
        head,
        "|" + "---|" * 15,
    ]
    for s in summary:
        lines.append(
            "| "
            + " | ".join(
                [
                    s["model"],
                    str(s["runs"]),
                    fmt(s["success_rate"], "pct"),
                    fmt(s["single_call_rate"], "pct"),
                    fmt(s["latency_median_s"]),
                    fmt(s["latency_mean_s"]),
                    fmt(
                        round(s["avg_input_tokens"]) if s["avg_input_tokens"] else None
                    ),
                    fmt(
                        round(s["avg_output_tokens"])
                        if s["avg_output_tokens"]
                        else None
                    ),
                    fmt(s["cost_per_analysis_usd"], "usd"),
                    fmt(s["cost_per_analysis_2027_usd"], "usd"),
                    fmt(s["title_ok_rate"], "pct"),
                    fmt(s["meta_ok_rate"], "pct"),
                    fmt(s["platform_coverage"], "pct"),
                    fmt(s["competitors_ok_rate"], "pct"),
                    fmt(s["services_grounded"], "pct"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_outputs(rows: list[dict], schema: bool, tag: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    suffix = f"_{slug(tag)}" if tag else ""
    summary = summarize(rows)
    (OUT_DIR / f"benchmark_results{suffix}.json").write_text(
        json.dumps(
            {
                "date": time.strftime("%Y-%m-%d"),
                "schema_in_prompt": schema,
                "rows": rows,
                "summary": summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    md = render_markdown(summary, schema)
    (OUT_DIR / f"benchmark_results{suffix}.md").write_text(md, encoding="utf-8")
    print("\n" + md)


# ---------- main ----------
async def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark LLMs on the link-analysis task")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument(
        "--delay",
        type=float,
        default=7.0,
        help="seconds between calls (free tier is ~10 RPM)",
    )
    ap.add_argument(
        "--schema",
        action="store_true",
        help="append the full JSON Schema to the prompt",
    )
    ap.add_argument("--tag", default="", help="suffix for output files")
    ap.add_argument("--models", help="provider:model,provider:model")
    args = ap.parse_args()

    s = get_settings()
    models = pick_models(args.models)
    if not models:
        raise SystemExit(
            "No API keys found. Set GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY."
        )

    rows: list[dict] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(s.llm_timeout_seconds)
    ) as client:
        for url in args.urls:
            print(f"\n== {url}")
            try:
                extracted = await extract_website(url)
            except Exception as exc:  # noqa: BLE001 - report and continue with the next site
                print(f"  extraction failed: {exc}")
                continue
            system, user = build_prompts(extracted, s.max_content_chars)
            system = llm_service._with_json_instruction(
                system, LLMAnalysisResult, args.schema
            )
            page_text = " ".join(
                [extracted.home.text, *[p.text for p in extracted.extra_pages]]
            ).lower()
            for provider, model, key in models:
                for run in range(args.runs):
                    print(f"  {model} run {run + 1}...", end=" ", flush=True)
                    row = await run_once(
                        client,
                        provider,
                        model,
                        key,
                        url,
                        system,
                        user,
                        page_text,
                        extracted.domain,
                    )
                    rows.append(row)
                    print(
                        "ok" if row["ok"] else f"FAILED ({row['error']})",
                        f"{row['latency_s']}s",
                    )
                    await asyncio.sleep(args.delay)

    if not rows:
        raise SystemExit("No results collected.")
    write_outputs(rows, args.schema, args.tag)


asyncio.run(main())
