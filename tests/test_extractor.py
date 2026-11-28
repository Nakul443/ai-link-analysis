# Async pytest tests for extract_website using mocked HTTP responses and
# fake public DNS resolution to test HTML parsing and text cleanup.

# Checks extra subpage fetching, skipping failed subpages,
# prompt text length limits, and blocking redirects to internal IP addresses.

# Verifies proper error handling for redirect loops, forbidden pages,
# non-HTML files, timeouts, and pages with too little content.

import httpx
import pytest
from app.services.website_extractor import (
    InsufficientContentError,
    SiteAccessRestrictedError,
    SiteTimeoutError,
    TooManyRedirectsError,
    UnsupportedContentError,
    extract_website,
)
from app.utils import validators
from app.utils.validators import BlockedURLError

HOME_HTML = """
<html lang="en"><head>
<title>Acme Plumbing | Emergency Plumbers in Austin</title>
<meta name="description" content="24/7 emergency plumbing in Austin.">
<meta property="og:site_name" content="Acme Plumbing">
<script>alert('x')</script><style>.a{}</style></head>
<body>
<nav><a href="/services">Services</a><a href="/about">About us</a><a href="https://other.com/x">Ext</a></nav>
<main>
<h1>Emergency plumbers in Austin</h1>
<p>We fix leaks, burst pipes and water heaters across Austin, Texas. Call us any time of day.</p>
<p>We fix leaks, burst pipes and water heaters across Austin, Texas. Call us any time of day.</p>
<h2>Our services</h2>
<p>Drain cleaning, water heater repair, leak detection and repiping for homes and small businesses.</p>
</main></body></html>
"""

SERVICES_HTML = """
<html><head><title>Services | Acme Plumbing</title></head>
<body><main><h1>Plumbing services</h1>
<p>Drain cleaning, leak detection, water heater installation, repiping and bathroom remodels for Austin homeowners.</p>
</main></body></html>
"""


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(validators, "_resolve", fake_resolve)


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )


def html_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, headers={"content-type": "text/html; charset=utf-8"}, content=body
    )


async def test_parses_basic_page():
    async with make_client(lambda req: html_response(HOME_HTML)) as client:
        result = await extract_website(
            "https://example.com", client=client, extra_pages_max=0
        )
    h = result.home
    assert h.title.startswith("Acme Plumbing")
    assert h.meta_description == "24/7 emergency plumbing in Austin."
    assert h.og_site_name == "Acme Plumbing"
    assert h.lang == "en"
    assert any("Emergency plumbers" in x for x in h.headings)
    assert "alert(" not in h.text
    assert h.text.count("We fix leaks") == 1  # deduped
    assert all(
        u.startswith("https://example.com") for u, _ in h.links
    )  # external link dropped
    assert result.domain == "example.com"


async def test_extra_pages_fetched_and_failures_skipped():
    def handler(req):
        if req.url.path == "/services":
            return html_response(SERVICES_HTML)
        if req.url.path == "/about":
            return html_response("nope", 404)
        return html_response(HOME_HTML)

    async with make_client(handler) as client:
        result = await extract_website(
            "https://example.com", client=client, extra_pages_max=2
        )
    assert len(result.extra_pages) == 1
    assert "Services" in result.extra_pages[0].title


async def test_redirect_to_internal_address_is_blocked():
    def handler(req):
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )

    async with make_client(handler) as client:
        with pytest.raises(BlockedURLError):
            await extract_website("https://example.com", client=client)


async def test_too_many_redirects():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    async with make_client(handler) as client:
        with pytest.raises(TooManyRedirectsError):
            await extract_website("https://example.com", client=client)


async def test_forbidden_site():
    async with make_client(lambda req: html_response("no", 403)) as client:
        with pytest.raises(SiteAccessRestrictedError):
            await extract_website("https://example.com", client=client)


async def test_non_html_content():
    def handler(req):
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF"
        )

    async with make_client(handler) as client:
        with pytest.raises(UnsupportedContentError):
            await extract_website("https://example.com", client=client)


async def test_timeout():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    async with make_client(handler) as client:
        with pytest.raises(SiteTimeoutError):
            await extract_website("https://example.com", client=client)


async def test_empty_page_is_insufficient():
    async with make_client(
        lambda req: html_response("<html><body><p>Hi</p></body></html>")
    ) as client:
        with pytest.raises(InsufficientContentError):
            await extract_website(
                "https://example.com", client=client, extra_pages_max=0
            )


async def test_prompt_text_respects_limit():
    async with make_client(lambda req: html_response(HOME_HTML)) as client:
        result = await extract_website(
            "https://example.com", client=client, extra_pages_max=0
        )
    text = result.to_prompt_text(400)
    assert len(text) <= 400 + len(" …[truncated]")
    assert "URL: https://example.com" in text
