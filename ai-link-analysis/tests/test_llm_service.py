# Async pytest tests for llm_service covering extract_json parsing/error cases
# and provider request/response adapters for OpenAI, Gemini, and Anthropic.

# Verifies retries on rate limits, single-retry repair on invalid JSON
# or schema violations, token usage aggregation, and fallback model execution.

# Checks error handling for repeated invalid outputs, all-model failures,
# non-repaired truncated responses, timeouts, and missing API keys.

import json

import httpx
import pytest
from app.config import Settings
from app.services import llm_service
from app.services.llm_service import (
    LLMConfigError,
    LLMInvalidResponseError,
    LLMTimeoutError,
    LLMTruncatedError,
    LLMUnavailableError,
    extract_json,
    generate_json,
)
from pydantic import BaseModel


class Toy(BaseModel):
    name: str
    items: list[str]


TOY = '{"name":"acme","items":["a","b"]}'


def use_settings(monkeypatch, **overrides):
    base = {
        "llm_provider": "openai",
        "llm_api_key": "k",
        "llm_model": "primary",
        "llm_fallback_model": "",
        "llm_max_retries": 1,
    }
    base.update(overrides)
    monkeypatch.setattr(llm_service, "get_settings", lambda: Settings(**base))


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(llm_service, "RETRY_BACKOFF_BASE", 0)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def openai_ok(content, finish="stop", tin=10, tout=5):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": tin, "completion_tokens": tout},
        },
    )


# ---- extract_json ----
@pytest.mark.parametrize(
    "text", [TOY, f"```json\n{TOY}\n```", f"Here you go:\n{TOY}\nHope it helps"]
)
def test_extract_json_ok(text):
    assert extract_json(text)["name"] == "acme"


@pytest.mark.parametrize("text", ["", "not json", "[1,2,3]"])
def test_extract_json_bad(text):
    with pytest.raises(LLMInvalidResponseError):
        extract_json(text)


# ---- providers ----
async def test_openai_happy_path(monkeypatch):
    use_settings(monkeypatch)
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return openai_ok(TOY)

    async with client_for(handler) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.data.name == "acme"
    assert (out.provider, out.model, out.input_tokens, out.output_tokens) == (
        "openai",
        "primary",
        10,
        5,
    )
    assert out.attempts == 1 and out.used_fallback is False
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in seen["body"]["messages"][0]["content"]


async def test_fenced_json_is_accepted(monkeypatch):
    use_settings(monkeypatch)
    async with client_for(lambda r: openai_ok(f"```json\n{TOY}\n```")) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.data.items == ["a", "b"]


async def test_gemini_adapter(monkeypatch):
    use_settings(monkeypatch, llm_provider="gemini")
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["key"] = req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "thinking...", "thought": True},
                                {"text": TOY},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 7,
                    "candidatesTokenCount": 3,
                    "thoughtsTokenCount": 2,
                },
            },
        )

    async with client_for(handler) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.provider == "gemini" and out.data.name == "acme"
    assert (out.input_tokens, out.output_tokens) == (7, 5)
    assert seen["path"].endswith("/models/primary:generateContent")
    assert seen["key"] == "k"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


async def test_anthropic_adapter(monkeypatch):
    use_settings(monkeypatch, llm_provider="anthropic")
    seen = {}

    def handler(req):
        seen["key"] = req.headers["x-api-key"]
        seen["ver"] = req.headers["anthropic-version"]
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": TOY}],
                "usage": {"input_tokens": 4, "output_tokens": 6},
                "stop_reason": "end_turn",
            },
        )

    async with client_for(handler) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.provider == "anthropic" and (out.input_tokens, out.output_tokens) == (
        4,
        6,
    )
    assert seen["key"] == "k" and seen["ver"]


# ---- retries / repair / fallback ----
async def test_retries_on_rate_limit(monkeypatch):
    use_settings(monkeypatch, llm_max_retries=1)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] == 1 else openai_ok(TOY)

    async with client_for(handler) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.attempts == 2


async def test_repairs_invalid_json_once(monkeypatch):
    use_settings(monkeypatch)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return openai_ok("not json at all") if calls["n"] == 1 else openai_ok(TOY)

    async with client_for(handler) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.attempts == 2
    assert (out.input_tokens, out.output_tokens) == (
        20,
        10,
    )  # usage summed across calls


async def test_repairs_schema_violation(monkeypatch):
    use_settings(monkeypatch)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return (
            openai_ok('{"name":"x"}') if calls["n"] == 1 else openai_ok(TOY)
        )  # missing "items"

    async with client_for(handler) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.data.items == ["a", "b"]


async def test_second_invalid_output_raises(monkeypatch):
    use_settings(monkeypatch)
    async with client_for(lambda r: openai_ok("nope")) as c:
        with pytest.raises(LLMInvalidResponseError):
            await generate_json("sys", "user", Toy, client=c)


async def test_fallback_model_used(monkeypatch):
    use_settings(monkeypatch, llm_max_retries=0, llm_fallback_model="fb")

    def handler(req):
        model = json.loads(req.content)["model"]
        return httpx.Response(500) if model == "primary" else openai_ok(TOY)

    async with client_for(handler) as c:
        out = await generate_json("sys", "user", Toy, client=c)
    assert out.used_fallback is True and out.model == "fb" and out.attempts == 2


async def test_all_models_fail(monkeypatch):
    use_settings(monkeypatch, llm_max_retries=0)
    async with client_for(lambda r: httpx.Response(500)) as c:
        with pytest.raises(LLMUnavailableError):
            await generate_json("sys", "user", Toy, client=c)


async def test_truncated_output_is_not_repaired(monkeypatch):
    use_settings(monkeypatch)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return openai_ok('{"name":"a', finish="length")

    async with client_for(handler) as c:
        with pytest.raises(LLMTruncatedError):
            await generate_json("sys", "user", Toy, client=c)
    assert calls["n"] == 1


async def test_timeout(monkeypatch):
    use_settings(monkeypatch, llm_max_retries=0)

    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    async with client_for(handler) as c:
        with pytest.raises(LLMTimeoutError):
            await generate_json("sys", "user", Toy, client=c)


async def test_missing_api_key(monkeypatch):
    use_settings(monkeypatch, llm_api_key="")
    async with client_for(lambda r: openai_ok(TOY)) as c:
        with pytest.raises(LLMConfigError):
            await generate_json("sys", "user", Toy, client=c)
