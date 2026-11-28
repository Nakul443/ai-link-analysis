# Provides async LLM client adapters for OpenAI, Gemini,
# and Anthropic with typed errors, exponential-backoff retries on
# transient failures, and optional fallback to a secondary model.

# Enforces structured JSON output and Pydantic schema validation,
# attempting a single repair prompt if parsing or validation fails
# (without repairing truncated outputs).

# Exposes generate_json to return validated data alongside token counts,
# attempt counts, total latency, and fallback status.

"""LLM client wrapper: provider adapters, retries, JSON validation, fallback."""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

RETRY_BACKOFF_BASE = 2.0  # seconds; tests set this to 0


# ---------- Errors (each has a code for B6 to map) ----------
class LLMError(Exception):
    code = "LLM_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LLMConfigError(LLMError):
    code = "LLM_NOT_CONFIGURED"


class LLMAuthError(LLMError):
    code = "LLM_AUTH_ERROR"


class LLMTimeoutError(LLMError):
    code = "LLM_TIMEOUT"


class LLMRateLimitError(LLMError):
    code = "LLM_RATE_LIMITED"


class LLMUnavailableError(LLMError):
    code = "LLM_UNAVAILABLE"


class LLMInvalidResponseError(LLMError):
    code = "LLM_INVALID_RESPONSE"


class LLMTruncatedError(LLMInvalidResponseError):
    code = "LLM_TRUNCATED"


RETRYABLE = (LLMTimeoutError, LLMUnavailableError)  # 429 -> go to fallback, don't retry


# ---------- Data ----------
@dataclass(frozen=True)
class _Cfg:
    provider: str
    model: str
    api_key: str


@dataclass
class _Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMOutput(Generic[T]):
    data: T
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int  # total, including retries/fallback
    attempts: int  # HTTP calls made
    used_fallback: bool


# ---------- HTTP ----------
async def _post(
    client: httpx.AsyncClient, url: str, headers: dict, payload: dict
) -> dict:
    try:
        resp = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise LLMTimeoutError("LLM request timed out.") from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailableError("Could not reach the LLM provider.") from exc

    code = resp.status_code
    if code in (401, 403):
        raise LLMAuthError("LLM provider rejected the credentials.")
    if code == 429:
        raise LLMRateLimitError("LLM provider rate limit reached.")
    if code >= 500 or code == 529:
        raise LLMUnavailableError(f"LLM provider error (HTTP {code}).")
    if code >= 400:
        raise LLMError(f"LLM request rejected (HTTP {code}): {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise LLMInvalidResponseError("LLM provider returned a non-JSON body.") from exc


# ---------- Provider adapters: return (text, input_tokens, output_tokens) ----------
async def _call_openai(client, cfg: _Cfg, system: str, user: str, s: Settings):
    data = await _post(
        client,
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {cfg.api_key}"},
        {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": s.llm_max_output_tokens,
        },
    )
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
        finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvalidResponseError("Unexpected response shape from OpenAI.") from exc
    if finish == "length":
        raise LLMTruncatedError("LLM output was cut off (max tokens reached).")
    usage = data.get("usage") or {}
    return text, usage.get("prompt_tokens"), usage.get("completion_tokens")


async def _call_gemini(client, cfg: _Cfg, system: str, user: str, s: Settings):
    model = cfg.model.removeprefix("models/")
    data = await _post(
        client,
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": cfg.api_key},
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": s.llm_temperature,
                "maxOutputTokens": s.llm_max_output_tokens,
            },
        },
    )
    candidates = data.get("candidates") or []
    if not candidates:
        raise LLMInvalidResponseError(
            "Gemini returned no candidates (prompt may have been blocked)."
        )
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if cand.get("finishReason") == "MAX_TOKENS":
        raise LLMTruncatedError("LLM output was cut off (max tokens reached).")
    usage = data.get("usageMetadata") or {}
    out_tokens = (usage.get("candidatesTokenCount") or 0) + (
        usage.get("thoughtsTokenCount") or 0
    )
    return text, usage.get("promptTokenCount"), out_tokens


async def _call_anthropic(client, cfg: _Cfg, system: str, user: str, s: Settings):
    data = await _post(
        client,
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"},
        {
            "model": cfg.model,
            "max_tokens": s.llm_max_output_tokens,
            "temperature": s.llm_temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
    )
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    if data.get("stop_reason") == "max_tokens":
        raise LLMTruncatedError("LLM output was cut off (max tokens reached).")
    usage = data.get("usage") or {}
    return text, usage.get("input_tokens"), usage.get("output_tokens")


ADAPTERS = {
    "openai": _call_openai,
    "gemini": _call_gemini,
    "anthropic": _call_anthropic,
}


# ---------- Config ----------
def _config(provider: str, model: str, api_key: str) -> _Cfg:
    provider = (provider or "").lower().strip()
    if provider == "google":
        provider = "gemini"
    if provider not in ADAPTERS:
        raise LLMConfigError(f"Unsupported LLM provider: '{provider}'.")
    if not model or not api_key:
        raise LLMConfigError("LLM_MODEL and LLM_API_KEY must be set.")
    return _Cfg(provider, model, api_key)


def _fallback_config(s: Settings, primary: _Cfg) -> _Cfg | None:
    if not s.llm_fallback_model:
        return None
    try:
        cfg = _config(
            s.llm_fallback_provider or s.llm_provider,
            s.llm_fallback_model,
            s.llm_fallback_api_key or s.llm_api_key,
        )
    except LLMConfigError as exc:
        logger.warning("Fallback LLM ignored: %s", exc.message)
        return None
    return None if cfg == primary else cfg


# ---------- JSON handling ----------
def extract_json(text: str) -> dict:
    """Parse model output into a dict. Tolerates code fences and stray prose."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE).strip()
    candidates = [t]
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        candidates.append(t[start : end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise LLMInvalidResponseError("Model did not return a valid JSON object.")


def _short(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:400]


def _with_json_instruction(
    system: str, response_model: type[BaseModel], include_schema: bool
) -> str:
    suffix = "\n\nReturn ONLY one valid JSON object. No markdown, no code fences, no commentary."
    if include_schema:
        schema = json.dumps(response_model.model_json_schema(), separators=(",", ":"))
        suffix += f" It must conform to this JSON Schema:\n{schema}"
    return system + suffix


# ---------- Core ----------
async def _call_with_retries(
    client, cfg: _Cfg, system: str, user: str, usage: _Usage
) -> str:
    s = get_settings()
    adapter = ADAPTERS[cfg.provider]
    last: LLMError | None = None
    for attempt in range(s.llm_max_retries + 1):
        started = time.perf_counter()
        usage.calls += 1
        try:
            text, tin, tout = await adapter(client, cfg, system, user, s)
        except RETRYABLE as exc:
            last = exc
            logger.warning(
                "LLM call failed (%s) provider=%s model=%s attempt=%d",
                exc.code,
                cfg.provider,
                cfg.model,
                attempt + 1,
            )
            if attempt < s.llm_max_retries:
                await asyncio.sleep(RETRY_BACKOFF_BASE * (2**attempt))
            continue
        usage.input_tokens += tin or 0
        usage.output_tokens += tout or 0
        logger.info(
            "LLM ok provider=%s model=%s in=%s out=%s ms=%d",
            cfg.provider,
            cfg.model,
            tin,
            tout,
            int((time.perf_counter() - started) * 1000),
        )
        return text
    assert last is not None
    raise last


async def _run_config(
    client, cfg: _Cfg, system: str, user: str, response_model: type[T], usage: _Usage
) -> T:
    """One provider/model: call, validate, and repair once if the output is invalid."""
    prompt = user
    for is_repair in (False, True):
        text = await _call_with_retries(
            client, cfg, system, prompt, usage
        )  # truncation raises here, no repair
        try:
            return response_model.model_validate(extract_json(text))
        except (LLMInvalidResponseError, ValidationError) as exc:
            if is_repair:
                raise LLMInvalidResponseError(
                    f"Model output failed validation: {_short(exc)}"
                ) from exc
            logger.warning("Invalid LLM output, attempting repair: %s", _short(exc))
            prompt = (
                f"{user}\n\nYour previous reply was invalid ({_short(exc)}). "
                "Return the complete corrected JSON object only."
            )
    raise AssertionError("unreachable")


async def generate_json(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    *,
    include_schema: bool = True,
    client: httpx.AsyncClient | None = None,
) -> LLMOutput[T]:
    """Call the primary model (then the fallback, if configured) and return validated data.

    include_schema=False: skip appending the JSON Schema (use when your prompt already
    contains a compact output template, to save tokens).
    """
    s = get_settings()
    primary = _config(
        s.llm_provider, s.llm_model, s.llm_api_key
    )  # raises LLMConfigError
    configs = [primary]
    fallback = _fallback_config(s, primary)
    if fallback:
        configs.append(fallback)

    system = _with_json_instruction(system_prompt, response_model, include_schema)
    usage = _Usage()
    errors: list[LLMError] = []
    started = time.perf_counter()

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(s.llm_timeout_seconds))
    try:
        for idx, cfg in enumerate(configs):
            try:
                data = await _run_config(
                    client, cfg, system, user_prompt, response_model, usage
                )
            except LLMError as exc:
                logger.warning(
                    "LLM failed provider=%s model=%s code=%s",
                    cfg.provider,
                    cfg.model,
                    exc.code,
                )
                errors.append(exc)
                continue
            return LLMOutput(
                data=data,
                provider=cfg.provider,
                model=cfg.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempts=usage.calls,
                used_fallback=idx > 0,
            )
    finally:
        if own_client:
            await client.aclose()
    raise errors[0]
