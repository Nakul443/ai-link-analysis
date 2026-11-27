"""FastAPI app: routes, middleware, error handlers."""

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.schemas import (
    ErrorDetail,
    ErrorResponse,
    LinkAnalysisRequest,
    LinkAnalysisResponse,
)
from app.services.analysis_service import analyze_url
from app.utils.cache import TTLCache, cache_key
from app.utils.errors import (
    KNOWN_ERRORS,
    ErrorInfo,
    InternalError,
    RateLimitExceeded,
    map_exception,
    map_validation_error,
)
from app.utils.rate_limit import RateLimiter

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")

app = FastAPI(
    title="AI Link Analysis API",
    description=(
        "Analyzes a website URL with an LLM and returns competitor, search query, SEO, "
        "services and target audience insights as structured JSON.\n\n"
        "Competitors are AI-generated suggestions (not live-verified). "
        "No search-volume data is included."
    ),
    version="1.0.0",
)


# ---------- State (created lazily so tests can reset it) ----------
@dataclass
class AppState:
    cache: TTLCache
    limiter: RateLimiter
    semaphore: asyncio.Semaphore
    inflight: dict[str, asyncio.Task]


def get_state(application: FastAPI) -> AppState:
    state = getattr(application.state, "services", None)
    if state is None:
        s = get_settings()
        state = AppState(
            cache=TTLCache(s.cache_ttl_seconds, s.cache_max_entries),
            limiter=RateLimiter(s.rate_limit_per_minute),
            semaphore=asyncio.Semaphore(max(s.max_concurrent_analyses, 1)),
            inflight={},
        )
        application.state.services = state
    return state


# ---------- Middleware ----------
@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %d (%dms) id=%s",
        request.method,
        request.url.path,
        response.status_code,
        int((time.perf_counter() - started) * 1000),
        request_id,
    )
    return response


app.add_middleware(  # added last = outermost, so preflight and errors get CORS headers
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


# ---------- Error handlers ----------
def _json_error(info: ErrorInfo) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=info.code, message=info.message))
    return JSONResponse(
        status_code=info.status, content=body.model_dump(), headers=info.headers
    )


async def known_error_handler(request: Request, exc: Exception) -> JSONResponse:
    info = map_exception(exc)
    log = logger.error if info.status >= 500 else logger.warning
    log("request failed: %d %s (%s)", info.status, info.code, exc)
    return _json_error(info)


for _cls in (*KNOWN_ERRORS, InternalError):
    app.add_exception_handler(_cls, known_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    info = map_validation_error(exc)
    logger.warning("invalid request: %s (%s)", info.code, info.message)
    return _json_error(info)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
        exc.status_code, "HTTP_ERROR"
    )
    return _json_error(
        ErrorInfo(exc.status_code, code, str(exc.detail), dict(exc.headers or {}))
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return _json_error(ErrorInfo(500, "INTERNAL_ERROR", "Unexpected server error."))


# ---------- Helpers ----------
def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[
                -1
            ].strip()  # entry added by our own proxy, not client-supplied
    return request.client.host if request.client else "unknown"


def _as_cached(resp: LinkAnalysisResponse, started: float) -> LinkAnalysisResponse:
    out = resp.model_copy(deep=True)
    if out.meta:
        out.meta.cached = True
        out.meta.processing_ms = int((time.perf_counter() - started) * 1000)
    return out


async def _analyze(
    state: AppState, s: Settings, url: str, key: str
) -> LinkAnalysisResponse:
    async with state.semaphore:
        result = await asyncio.wait_for(
            analyze_url(url), timeout=s.analysis_timeout_seconds
        )
    state.cache.set(key, result)  # only successes are cached
    return result


def _finish(state: AppState, key: str, task: asyncio.Task) -> None:
    if state.inflight.get(key) is task:
        del state.inflight[key]
    if not task.cancelled():
        task.exception()  # mark retrieved, avoids "never retrieved" warnings


# ---------- Routes ----------
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid or blocked URL"},
    422: {
        "model": ErrorResponse,
        "description": "Website could not be analyzed (restricted, unsupported or empty)",
    },
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    500: {"model": ErrorResponse, "description": "Server or configuration error"},
    502: {
        "model": ErrorResponse,
        "description": "Website unreachable or AI response unusable",
    },
    503: {"model": ErrorResponse, "description": "AI service busy or unavailable"},
    504: {"model": ErrorResponse, "description": "Timeout"},
}


@app.post(
    "/link-analysis",
    response_model=LinkAnalysisResponse,
    responses=ERROR_RESPONSES,
    tags=["analysis"],
    summary="Analyze a website URL",
)
async def link_analysis(
    body: LinkAnalysisRequest, request: Request
) -> LinkAnalysisResponse:
    s = get_settings()
    state = get_state(request.app)
    started = time.perf_counter()
    key = cache_key(body.url)
    ip = client_ip(request, s.trust_proxy)

    cached = state.cache.get(key)
    if cached is not None:
        logger.info("analysis cache hit url=%s ip=%s", body.url, ip)
        return _as_cached(cached, started)

    task = state.inflight.get(key)
    joined = task is not None
    if task is None:
        retry_after = state.limiter.check(ip)  # only uncached, non-joined work counts
        if retry_after:
            raise RateLimitExceeded(math.ceil(retry_after))
        logger.info("analysis start url=%s ip=%s", body.url, ip)
        task = asyncio.create_task(_analyze(state, s, body.url, key))
        state.inflight[key] = task
        task.add_done_callback(lambda t, k=key: _finish(state, k, t))

    try:
        result = await asyncio.shield(
            task
        )  # a client disconnect doesn't cancel the shared work
    except KNOWN_ERRORS:
        raise
    except Exception as exc:
        logger.exception("unexpected error analyzing %s", body.url)
        raise InternalError() from exc
    return _as_cached(result, started) if joined else result


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": "AI Link Analysis API",
        "endpoint": "POST /link-analysis",
        "docs": "/docs",
        "health": "/health",
    }
