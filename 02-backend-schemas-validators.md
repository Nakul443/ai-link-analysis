# B2: Schemas + URL Validators + SSRF Guard

**Goal:** Define all Pydantic models (request, response, error) and a URL validator that blocks private/internal addresses. No HTTP fetching or LLM yet.

**Rules for the agent**
- Only touch the files listed below.
- Pydantic v2 syntax only (`field_validator`, `model_config`).
- Do not weaken the SSRF checks. If a test fails, fix the code, not the test.

**Files**
- `app/utils/validators.py` (replace placeholder)
- `app/schemas.py` (replace placeholder)
- `tests/test_validators.py` (new)
- `pytest.ini` (new)

---

## 1. `app/utils/validators.py`

```python
"""URL validation and SSRF protection."""
import asyncio
import ipaddress
import socket
from urllib.parse import ParseResult, urlparse

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
MAX_URL_LENGTH = 2048

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}
BLOCKED_SUFFIXES = (
    ".local", ".localhost", ".internal", ".localdomain", ".lan", ".home", ".corp",
)

# Ranges Python may treat as "global" but that can embed or reach private IPv4s.
EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64
    ipaddress.ip_network("2002::/16"),     # 6to4
)


class InvalidURLError(ValueError):
    """URL is malformed or violates format rules."""


class BlockedURLError(ValueError):
    """URL points to a private, internal or otherwise disallowed address."""


class HostResolutionError(ValueError):
    """Hostname could not be resolved."""


def normalize_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        raise InvalidURLError("URL is empty.")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidURLError("URL is too long.")
    if any(ord(c) < 32 or c.isspace() for c in url):
        raise InvalidURLError("URL contains whitespace or control characters.")
    if "://" not in url:
        url = "https://" + url
    return url


def parse_and_check_format(url: str) -> ParseResult:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidURLError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise InvalidURLError("URL has no hostname.")
    if parsed.username or parsed.password:
        raise InvalidURLError("URLs with embedded credentials are not allowed.")
    try:
        port = parsed.port
    except ValueError:
        raise InvalidURLError("URL has an invalid port.")
    if port not in ALLOWED_PORTS:
        raise InvalidURLError("Only ports 80 and 443 are allowed.")
    return parsed


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_multicast or not ip.is_global:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        return not any(ip in net for net in EXTRA_BLOCKED_NETWORKS)
    return True


async def _resolve(host: str) -> list[str]:
    """Resolve host to IP strings. Separate function so tests can patch it."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HostResolutionError(f"Could not resolve host: {host}") from exc
    return [info[4][0].split("%")[0] for info in infos]


async def assert_public_host(host: str) -> None:
    h = host.lower().rstrip(".")
    if h in BLOCKED_HOSTNAMES or h.endswith(BLOCKED_SUFFIXES):
        raise BlockedURLError("Access to internal hostnames is not allowed.")

    try:
        literal = ipaddress.ip_address(h)
    except ValueError:
        literal = None

    if literal is not None:
        if not is_public_ip(literal):
            raise BlockedURLError("Access to private or internal IP addresses is not allowed.")
        return

    ips = await _resolve(h)
    if not ips:
        raise HostResolutionError(f"Could not resolve host: {h}")
    # Check EVERY resolved address; one private record is enough to block.
    for ip_str in ips:
        if not is_public_ip(ipaddress.ip_address(ip_str)):
            raise BlockedURLError("URL resolves to a private or internal address.")


async def validate_public_url(raw: str) -> str:
    """Full check: normalize, format, DNS + IP. Returns the normalized URL.

    B3 must call this again for EVERY redirect hop.
    """
    url = normalize_url(raw)
    parsed = parse_and_check_format(url)
    await assert_public_host(parsed.hostname)
    return url
```

---

## 2. `app/schemas.py`

```python
"""Pydantic request/response models."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import InvalidURLError, normalize_url, parse_and_check_format


# ---------- Request ----------
class LinkAnalysisRequest(BaseModel):
    url: str = Field(..., examples=["https://example.com"])

    @field_validator("url")
    @classmethod
    def check_url_format(cls, v: str) -> str:
        # Format only (sync). DNS/SSRF check happens later via validate_public_url.
        try:
            url = normalize_url(v)
            parse_and_check_format(url)
        except InvalidURLError as exc:
            raise ValueError(str(exc)) from exc
        return url


# ---------- Shared ----------
Source = Literal["website", "inferred"]


class Platform(str, Enum):
    GOOGLE = "Google"
    FACEBOOK = "Facebook"
    INSTAGRAM = "Instagram"
    LINKEDIN = "LinkedIn"
    X = "X"


class Brand(BaseModel):
    name: str
    category: str
    summary: str


# ---------- Section 1: Competitor vs brand ----------
class ComparisonProfile(BaseModel):
    brand_name: str
    main_service: str
    target_market: str
    key_strength: str
    opportunity: str


class Competitor(ComparisonProfile):
    website: str | None = None
    strengths: list[str] = []
    weaknesses: list[str] = []
    # Competitors are never verified here, so they are labelled as AI suggestions.
    source: Literal["ai_suggested", "website"] = "ai_suggested"


class CompetitorAnalysis(BaseModel):
    brand_overview: str
    business_category: str
    main_products_services: list[str]
    your_brand: ComparisonProfile
    competitors: list[Competitor]  # aim for 3+ when reliable info exists
    competitive_advantages: list[str]
    positioning_opportunities: list[str]


# ---------- Section 2: Search queries ----------
class SearchQuery(BaseModel):
    query: str
    platforms: list[Platform] = Field(..., min_length=1)
    intent: str
    business_relevance: str
    target_audience: str
    # No search volume / ranking fields on purpose: never invent them.


# ---------- Section 3: SEO ----------
class SupportingContent(BaseModel):
    type: Literal[
        "service_landing_page",
        "industry_page",
        "faq",
        "blog_topic",
        "location_page",
        "supporting_article",
    ]
    title: str
    rationale: str


class SeoImprovements(BaseModel):
    primary_keyword: str
    secondary_keywords: list[str]
    long_tail_keywords: list[str]
    business_keywords: list[str]
    recommended_title: str          # target <= 60 chars (enforced in prompt, B5)
    title_structure: str            # e.g. "Primary Keyword | Benefit | Brand"
    meta_description: str           # target <= 160 chars (enforced in prompt, B5)
    supporting_content: list[SupportingContent]


# ---------- Section 4: Services ----------
class Service(BaseModel):
    name: str
    description: str
    target_industry: str
    target_customer: str
    main_benefit: str
    source: Source  # "website" = found on page, "inferred" = AI recommendation


# ---------- Section 5: Target audience ----------
class AudienceSegment(BaseModel):
    audience: str
    business_type: str
    requirement: str


class TargetAudience(BaseModel):
    primary_audience: list[str]
    secondary_audience: list[str]
    industries: list[str]
    business_types: list[str]
    customer_profiles: list[str]
    geographic_focus: str | None = None
    customer_needs: list[str]
    customer_interests: list[str]
    recommended_segments: list[AudienceSegment]


# ---------- Analysis wrapper ----------
class Analysis(BaseModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery] = Field(..., min_length=5, max_length=5)
    seo_improvements: SeoImprovements
    top_services: list[Service] = Field(..., min_length=1, max_length=5)
    target_audience: TargetAudience


class LLMAnalysisResult(BaseModel):
    """Exactly what the LLM must return (used for validation in B4/B5)."""
    brand: Brand
    analysis: Analysis


# ---------- Response ----------
class AnalysisMeta(BaseModel):
    model_used: str
    cached: bool = False
    processing_ms: int


class LinkAnalysisResponse(BaseModel):
    success: Literal[True] = True
    website: str
    brand: Brand
    analysis: Analysis
    meta: AnalysisMeta | None = None


class ErrorDetail(BaseModel):
    code: str       # e.g. INVALID_URL, BLOCKED_URL, SITE_UNREACHABLE, LLM_ERROR
    message: str


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail
```

---

## 3. `pytest.ini`

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

---

## 4. `tests/test_validators.py`

```python
import pytest

from app.utils import validators
from app.utils.validators import (
    BlockedURLError,
    HostResolutionError,
    InvalidURLError,
    normalize_url,
    parse_and_check_format,
    validate_public_url,
)


# ---- format ----
def test_normalize_adds_https():
    assert normalize_url("example.com") == "https://example.com"


@pytest.mark.parametrize("bad", ["", "   ", "ftp://example.com", "file:///etc/passwd",
                                 "javascript:alert(1)", "https://", "http://user:pw@example.com",
                                 "https://example.com:22", "https://exa mple.com", "a" * 2100])
def test_invalid_formats(bad):
    with pytest.raises(InvalidURLError):
        parse_and_check_format(normalize_url(bad))


# ---- SSRF: no DNS needed ----
@pytest.mark.parametrize("url", [
    "http://localhost",
    "http://127.0.0.1",
    "http://127.1",
    "http://0.0.0.0",
    "http://10.0.0.5",
    "http://172.16.0.1",
    "http://192.168.1.1",
    "http://169.254.169.254/latest/meta-data",
    "http://100.64.0.1",
    "http://[::1]",
    "http://[fe80::1]",
    "http://[::ffff:127.0.0.1]",
    "http://[64:ff9b::7f00:1]",
    "http://metadata.google.internal",
    "http://printer.local",
])
async def test_blocked_hosts(url, monkeypatch):
    # Short-forms like 127.1 / decimal IPs go through DNS resolution; fake what a resolver returns.
    async def fake_resolve(host):
        return {"127.1": ["127.0.0.1"]}.get(host, [host])
    monkeypatch.setattr(validators, "_resolve", fake_resolve)
    with pytest.raises(BlockedURLError):
        await validate_public_url(url)


# ---- SSRF: DNS-based ----
async def test_domain_resolving_to_private_is_blocked(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34", "10.0.0.8"]  # one bad record is enough
    monkeypatch.setattr(validators, "_resolve", fake_resolve)
    with pytest.raises(BlockedURLError):
        await validate_public_url("https://evil.example.com")


async def test_public_domain_allowed(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34"]
    monkeypatch.setattr(validators, "_resolve", fake_resolve)
    assert await validate_public_url("example.com") == "https://example.com"


async def test_unresolvable_host(monkeypatch):
    async def fake_resolve(host):
        raise HostResolutionError("nope")
    monkeypatch.setattr(validators, "_resolve", fake_resolve)
    with pytest.raises(HostResolutionError):
        await validate_public_url("https://does-not-exist.invalid")
```

---

## 5. Verify

```bash
pytest -q
python -c "from app.schemas import LinkAnalysisRequest as R; print(R(url='example.com').url)"
python -c "from app.schemas import LinkAnalysisRequest as R; R(url='ftp://x.com')"   # must raise ValidationError
```

Checklist:
- [ ] `pytest -q` passes, all tests green
- [ ] `LinkAnalysisRequest(url="example.com").url` prints `https://example.com`
- [ ] `ftp://x.com` raises a validation error
- [ ] `uvicorn app.main:app --port 4000` still starts

**Notes for later steps**
- B3 must call `validate_public_url()` on the initial URL **and every redirect hop** (follow redirects manually, max 3).
- B6 maps errors to `ErrorResponse`: `InvalidURLError`/`BlockedURLError` and request-validation errors → 400, `HostResolutionError` → 400/502, unreachable site → 502, timeout → 504. Override FastAPI's default 422 handler so invalid URLs also return the `ErrorResponse` shape.
- Known residual risk: DNS rebinding between check and fetch. Document it in the README; optional hardening in B3 is connecting to the validated IP directly.

**Done when all four boxes are checked. Next: B3 (website extractor).**