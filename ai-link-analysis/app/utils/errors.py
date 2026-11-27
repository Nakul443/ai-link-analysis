# Defines RateLimitExceeded, InternalError, KNOWN_ERRORS, and ErrorInfo
# to map validation, URL, extraction, LLM, and rate-limit exceptions
# to HTTP status codes and error codes.

# Translates domain exceptions into safe user-facing messages
# and optional headers (such as Retry-After)
# while keeping internal error details out of responses.

# Provides map_validation_error to convert FastAPI RequestValidationError
# payloads into consistent 400 error responses.

"""Maps exceptions to HTTP status + stable error codes."""

from dataclasses import dataclass, field

from fastapi.exceptions import RequestValidationError

from app.services.llm_service import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.services.website_extractor import (
    ExtractionError,
    InsufficientContentError,
    SiteAccessRestrictedError,
    SiteTimeoutError,
    SiteUnreachableError,
    TooManyRedirectsError,
    UnsupportedContentError,
)
from app.utils.validators import BlockedURLError, HostResolutionError, InvalidURLError


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Rate limit exceeded; retry in {retry_after}s")
        self.retry_after = retry_after


class InternalError(Exception):
    """Wraps any unexpected error so it is handled inside the CORS middleware."""


# Exceptions the route lets through untouched (they have handlers).
KNOWN_ERRORS = (
    InvalidURLError,
    BlockedURLError,
    HostResolutionError,
    ExtractionError,
    LLMError,
    RateLimitExceeded,
    TimeoutError,
)


@dataclass(frozen=True)
class ErrorInfo:
    status: int
    code: str
    message: str
    headers: dict[str, str] = field(default_factory=dict)


EXTRACTION_STATUS: dict[type, int] = {
    SiteUnreachableError: 502,
    TooManyRedirectsError: 502,
    SiteTimeoutError: 504,
    SiteAccessRestrictedError: 422,
    UnsupportedContentError: 422,
    InsufficientContentError: 422,
}


def map_exception(exc: BaseException) -> ErrorInfo:
    if isinstance(exc, InvalidURLError):
        return ErrorInfo(400, "INVALID_URL", str(exc))
    if isinstance(exc, BlockedURLError):
        return ErrorInfo(
            400,
            "BLOCKED_URL",
            "This URL points to a private or internal address and cannot be analyzed.",
        )
    if isinstance(exc, HostResolutionError):
        return ErrorInfo(400, "HOST_NOT_RESOLVED", str(exc))
    if isinstance(exc, ExtractionError):
        return ErrorInfo(EXTRACTION_STATUS.get(type(exc), 502), exc.code, exc.message)

    # LLM errors: generic messages only, details stay in the logs.
    if isinstance(exc, (LLMAuthError, LLMConfigError)):
        return ErrorInfo(
            500, "LLM_NOT_CONFIGURED", "The AI service is not configured correctly."
        )
    if isinstance(exc, LLMTimeoutError):
        return ErrorInfo(
            504, exc.code, "The AI model took too long to respond. Please try again."
        )
    if isinstance(exc, LLMRateLimitError):
        return ErrorInfo(
            503,
            exc.code,
            "The AI service is busy. Please retry shortly.",
            {"Retry-After": "30"},
        )
    if isinstance(exc, LLMUnavailableError):
        return ErrorInfo(
            503,
            exc.code,
            "The AI service is temporarily unavailable. Please try again.",
        )
    if isinstance(exc, LLMError):
        return ErrorInfo(
            502,
            exc.code,
            "The AI model returned an unusable response. Please try again.",
        )

    if isinstance(exc, RateLimitExceeded):
        return ErrorInfo(
            429,
            "RATE_LIMITED",
            f"Too many requests. Retry in {exc.retry_after}s.",
            {"Retry-After": str(exc.retry_after)},
        )
    if isinstance(exc, TimeoutError):
        return ErrorInfo(
            504, "ANALYSIS_TIMEOUT", "The analysis took too long. Please try again."
        )
    return ErrorInfo(500, "INTERNAL_ERROR", "Unexpected server error.")


def map_validation_error(exc: RequestValidationError) -> ErrorInfo:
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = tuple(first.get("loc", ()))
    msg = str(first.get("msg", "Invalid request."))
    if first.get("type") == "missing":
        msg = "A 'url' field is required."
    elif first.get("type") == "json_invalid":
        msg = "Request body must be valid JSON."
    msg = msg.removeprefix("Value error, ")
    code = "INVALID_URL" if "url" in loc else "INVALID_REQUEST"
    return ErrorInfo(400, code, msg)
