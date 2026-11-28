# Async API tests for POST /link-analysis covering response structure,
# TTL caching across normalized URLs, deduplicating concurrent requests,
# and not caching failures.

# Verifies request validation errors, exception-to-HTTP status/code mapping,
# safe error messages without internal leaks, and analysis timeouts.

# Checks rate limiting (counting only uncached work and setting Retry-After)
# alongside error formatting for unknown routes, wrong methods, and /health.

import asyncio

import httpx
import pytest
from app import main
from app.config import Settings
from app.main import app
from app.schemas import AnalysisMeta, LinkAnalysisResponse, LLMAnalysisResult
from app.services.llm_service import (
    LLMAuthError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.services.website_extractor import (
    InsufficientContentError,
    SiteAccessRestrictedError,
    SiteTimeoutError,
    SiteUnreachableError,
)
from app.utils.validators import BlockedURLError, HostResolutionError

from tests.test_analysis_service import sample_payload


def make_response() -> LinkAnalysisResponse:
    result = LLMAnalysisResult.model_validate(sample_payload())
    return LinkAnalysisResponse(
        website="https://example.com/",
        brand=result.brand,
        analysis=result.analysis,
        meta=AnalysisMeta(
            provider="gemini",
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            processing_ms=5,
        ),
    )


def use_settings(monkeypatch, **overrides):
    monkeypatch.setattr(main, "get_settings", lambda: Settings(**overrides))


@pytest.fixture(autouse=True)
def fresh_state():
    app.state.services = None
    yield
    app.state.services = None


@pytest.fixture
def fake_analyze(monkeypatch):
    calls: list[str] = []

    async def fake(url: str):
        calls.append(url)
        return make_response()

    monkeypatch.setattr(main, "analyze_url", fake)
    return calls


def api() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def post(client: httpx.AsyncClient, url: str):
    return await client.post("/link-analysis", json={"url": url})


# ---- success + cache ----
async def test_success_shape(fake_analyze):
    async with api() as c:
        r = await post(c, "https://example.com")
    body = r.json()
    assert r.status_code == 200 and body["success"] is True
    assert set(body["analysis"]) == {
        "competitor_analysis",
        "target_search_queries",
        "seo_improvements",
        "top_services",
        "target_audience",
    }
    assert body["meta"]["cached"] is False
    assert "x-request-id" in r.headers


async def test_cache_hit_skips_analysis(fake_analyze):
    async with api() as c:
        first = await post(c, "https://example.com")
        second = await post(c, "https://EXAMPLE.com/")
    assert len(fake_analyze) == 1
    assert (
        first.json()["meta"]["cached"] is False
        and second.json()["meta"]["cached"] is True
    )


async def test_identical_concurrent_requests_share_one_analysis(monkeypatch):
    calls: list[str] = []

    async def slow(url):
        calls.append(url)
        await asyncio.sleep(0.05)
        return make_response()

    monkeypatch.setattr(main, "analyze_url", slow)
    async with api() as c:
        r1, r2 = await asyncio.gather(
            post(c, "https://example.com"), post(c, "https://example.com")
        )
    assert r1.status_code == r2.status_code == 200
    assert len(calls) == 1


async def test_failures_are_not_cached(monkeypatch):
    calls: list[str] = []

    async def boom(url):
        calls.append(url)
        raise SiteUnreachableError("down")

    monkeypatch.setattr(main, "analyze_url", boom)
    async with api() as c:
        await post(c, "https://example.com")
        await post(c, "https://example.com")
    assert len(calls) == 2


# ---- request validation ----
@pytest.mark.parametrize(
    "bad", ["", "ftp://x.com", "http://user:pw@x.com", "https://x.com:22", "not a url"]
)
async def test_invalid_urls_return_400(bad, fake_analyze):
    async with api() as c:
        r = await post(c, bad)
    assert r.status_code == 400
    assert r.json()["success"] is False and r.json()["error"]["code"] == "INVALID_URL"
    assert fake_analyze == []


async def test_missing_field_and_bad_json(fake_analyze):
    async with api() as c:
        missing = await c.post("/link-analysis", json={})
        bad_json = await c.post(
            "/link-analysis",
            content="{bad",
            headers={"content-type": "application/json"},
        )
    assert (
        missing.status_code == 400
        and missing.json()["error"]["message"] == "A 'url' field is required."
    )
    assert (
        bad_json.status_code == 400
        and bad_json.json()["error"]["code"] == "INVALID_REQUEST"
    )


# ---- error mapping ----
@pytest.mark.parametrize(
    "exc,status,code",
    [
        (BlockedURLError("x"), 400, "BLOCKED_URL"),
        (HostResolutionError("no such host"), 400, "HOST_NOT_RESOLVED"),
        (SiteUnreachableError("x"), 502, "SITE_UNREACHABLE"),
        (SiteTimeoutError("x"), 504, "SITE_TIMEOUT"),
        (SiteAccessRestrictedError("x"), 422, "SITE_ACCESS_RESTRICTED"),
        (InsufficientContentError("x"), 422, "INSUFFICIENT_CONTENT"),
        (LLMTimeoutError("x"), 504, "LLM_TIMEOUT"),
        (LLMRateLimitError("x"), 503, "LLM_RATE_LIMITED"),
        (LLMInvalidResponseError("x"), 502, "LLM_INVALID_RESPONSE"),
        (LLMAuthError("secret-key-detail"), 500, "LLM_NOT_CONFIGURED"),
        (RuntimeError("boom"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_error_mapping(monkeypatch, exc, status, code):
    async def fail(url):
        raise exc

    monkeypatch.setattr(main, "analyze_url", fail)
    async with api() as c:
        r = await post(c, "https://example.com")
    assert r.status_code == status
    assert r.json() == {
        "success": False,
        "error": {"code": code, "message": r.json()["error"]["message"]},
    }
    assert "secret-key-detail" not in r.text and "boom" not in r.text


async def test_analysis_timeout(monkeypatch):
    use_settings(monkeypatch, analysis_timeout_seconds=0.05)

    async def slow(url):
        await asyncio.sleep(1)

    monkeypatch.setattr(main, "analyze_url", slow)
    async with api() as c:
        r = await post(c, "https://example.com")
    assert r.status_code == 504 and r.json()["error"]["code"] == "ANALYSIS_TIMEOUT"


# ---- rate limit ----
async def test_rate_limit_counts_only_uncached_work(monkeypatch, fake_analyze):
    use_settings(monkeypatch, rate_limit_per_minute=2)
    async with api() as c:
        assert (await post(c, "https://a.example.com")).status_code == 200
        assert (await post(c, "https://b.example.com")).status_code == 200
        blocked = await post(c, "https://c.example.com")
        cached = await post(c, "https://a.example.com")  # cache hit: not limited
    assert (
        blocked.status_code == 429 and blocked.json()["error"]["code"] == "RATE_LIMITED"
    )
    assert int(blocked.headers["retry-after"]) >= 1
    assert cached.status_code == 200


# ---- misc routes ----
async def test_unknown_route_and_method_use_error_shape():
    async with api() as c:
        nf = await c.get("/nope")
        na = await c.get("/link-analysis")
        ok = await c.get("/health")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "NOT_FOUND"
    assert na.status_code == 405 and na.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert ok.json()["status"] == "ok"
