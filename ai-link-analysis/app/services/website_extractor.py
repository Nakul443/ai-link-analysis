# Asynchronously fetches web pages with manual redirect following,
# per-hop SSRF checks, content-type/size/timeout enforcement,
# and typed extraction errors.
# Safely downloads web pages one hop at a time,
# checking security rules, size limits, timeouts,
# and redirects while raising clear errors when a site fails.

# Parses HTML with BeautifulSoup/lxml to extract metadata,
# JSON-LD, navigation links, headings,
# and cleaned body text while stripping scripts,
# hidden/cookie elements, and non-HTML assets.
# Reads the HTML to pull out titles, descriptions, links, headings,
# snd main text while removing scripts, cookie banners, hidden boxes,
# and file downloads.

# Coordinates extract_website to fetch the homepage and priority subpages
# (such as services, about, and pricing) and format a
# character-budgeted summary via ExtractedContent.to_prompt_text.
# Grabs the homepage plus key subpages
# (like about, services, or pricing) and
# packs the cleaned text within a set size limit for the AI.

"""Fetch and clean website content."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.utils.validators import validate_public_url

logger = logging.getLogger(__name__)

REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_PAGE_CHARS = 30_000

NOISE_TAGS = [
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "template",
    "input",
    "select",
    "textarea",
    "button",
]
NOISE_HINTS = ("cookie", "consent", "gdpr")
NOISE_CONTAINERS = {"div", "section", "aside", "dialog"}

# Priority order: earlier keywords are picked first.
EXTRA_PAGE_KEYWORDS = [
    "service",
    "solution",
    "product",
    "offering",
    "what-we-do",
    "about",
    "company",
    "industr",
    "pricing",
    "portfolio",
]
SKIP_EXT = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".zip",
    ".mp4",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
)


# ---------- Errors (each has a code for B6 to map) ----------
class ExtractionError(Exception):
    code = "EXTRACTION_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SiteUnreachableError(ExtractionError):
    code = "SITE_UNREACHABLE"


class SiteAccessRestrictedError(ExtractionError):
    code = "SITE_ACCESS_RESTRICTED"


class SiteTimeoutError(ExtractionError):
    code = "SITE_TIMEOUT"


class UnsupportedContentError(ExtractionError):
    code = "UNSUPPORTED_CONTENT"


class TooManyRedirectsError(ExtractionError):
    code = "TOO_MANY_REDIRECTS"


class InsufficientContentError(ExtractionError):
    code = "INSUFFICIENT_CONTENT"


# ---------- Data ----------
@dataclass
class PageContent:
    url: str
    title: str = ""
    meta_description: str = ""
    og_site_name: str = ""
    og_title: str = ""
    og_description: str = ""
    keywords: str = ""
    canonical: str = ""
    lang: str = ""
    structured_data: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (url, text)
    text: str = ""


@dataclass
class ExtractedContent:
    input_url: str
    final_url: str
    domain: str
    home: PageContent
    extra_pages: list[PageContent] = field(default_factory=list)

    def to_prompt_text(self, max_chars: int) -> str:
        """Compact, budgeted text that goes to the LLM."""
        h = self.home
        parts = [f"URL: {self.final_url}"]
        if h.title:
            parts.append(f"TITLE: {h.title}")
        if h.meta_description:
            parts.append(f"META DESCRIPTION: {h.meta_description}")
        if h.og_site_name:
            parts.append(f"SITE NAME: {h.og_site_name}")
        if h.og_title and h.og_title != h.title:
            parts.append(f"OG TITLE: {h.og_title}")
        if h.og_description and h.og_description != h.meta_description:
            parts.append(f"OG DESCRIPTION: {h.og_description}")
        if h.keywords:
            parts.append(f"META KEYWORDS: {h.keywords}")
        if h.lang:
            parts.append(f"LANGUAGE: {h.lang}")
        if h.structured_data:
            parts.append(
                "STRUCTURED DATA:\n" + "\n".join(f"- {s}" for s in h.structured_data)
            )
        nav = [t for _, t in h.links if t][:25]
        if nav:
            parts.append("NAVIGATION: " + " | ".join(nav))
        if h.headings:
            parts.append("HEADINGS:\n" + "\n".join(x[:120] for x in h.headings[:30]))
        header = "\n".join(parts)

        remaining = max(max_chars - len(header) - 200, 1000)
        extra_budget = int(remaining * 0.35) if self.extra_pages else 0
        sections = [
            header,
            "HOMEPAGE TEXT:\n" + _truncate(h.text, remaining - extra_budget),
        ]
        per_page = extra_budget // max(len(self.extra_pages), 1)
        for p in self.extra_pages:
            block = f"PAGE: {p.url}\nTITLE: {p.title}\n" + _truncate(p.text, per_page)
            sections.append(block)
        return _truncate("\n\n".join(sections), max_chars)


# ---------- Helpers ----------
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    cut = text[:n]
    idx = max(cut.rfind("\n"), cut.rfind(". "))
    if idx > n * 0.6:
        cut = cut[:idx]
    return cut.rstrip() + " …[truncated]"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _attr_str(val: object) -> str:
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    return str(val) if val is not None else ""


def _meta(soup: BeautifulSoup, **attrs: object) -> str:
    tag = soup.find("meta", attrs=attrs)  # type: ignore[arg-type]
    return _clean(_attr_str(tag.get("content", ""))) if tag else ""


def _extract_json_ld(soup: BeautifulSoup) -> list[str]:
    out: list[str] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(nodes, list):
            nodes = [nodes]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name, desc = node.get("name"), node.get("description")
            if name or desc:
                out.append(
                    _clean(f"{node.get('@type', '')}: {name or ''} - {desc or ''}")[
                        :300
                    ]
                )
    return out[:5]


def _collect_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    base_host = _host(base_url)
    anchors = soup.select(
        "nav a[href], header a[href], footer a[href]"
    ) or soup.find_all("a", href=True)
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for a in anchors:
        href = _attr_str(a.get("href", "")).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        abs_url = urljoin(base_url, href).split("#")[0]
        if (
            urlparse(abs_url).scheme not in ("http", "https")
            or _host(abs_url) != base_host
        ):
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        links.append((abs_url, _clean(a.get_text())[:80]))
        if len(links) >= 40:
            break
    return links


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if getattr(tag, "decomposed", False) or tag.attrs is None:
            continue
        if tag.get("aria-hidden") == "true" or tag.has_attr("hidden"):
            tag.decompose()
            continue
        if tag.name in NOISE_CONTAINERS:
            ident = f"{tag.get('id') or ''} {' '.join(tag.get('class') or [])}".lower()
            if any(h in ident for h in NOISE_HINTS):
                tag.decompose()


def _extract_headings(soup: BeautifulSoup) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = _clean(h.get_text())
        if not (3 <= len(text) <= 200) or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(f"{h.name.upper()}: {text}")
        if len(out) >= 40:
            break
    return out


def _extract_text(soup: BeautifulSoup) -> str:
    container = soup.find("main") or soup.find("article") or soup.find(role="main")
    if container is None or len(container.get_text(strip=True)) < 300:
        container = soup.body or soup
    seen: set[str] = set()
    lines: list[str] = []
    for raw_line in container.get_text("\n", strip=True).splitlines():
        line = _clean(raw_line)
        if len(line) < 3 or line.lower() in seen:
            continue
        seen.add(line.lower())
        lines.append(line)
    return "\n".join(lines)[:MAX_PAGE_CHARS]


def _parse_page(url: str, raw: bytes) -> PageContent:
    soup = BeautifulSoup(raw, "lxml")

    # Read metadata, JSON-LD and links BEFORE stripping noise.
    html_tag = soup.find("html")
    canonical_tag = soup.find("link", rel="canonical")
    page = PageContent(
        url=url,
        title=_clean(soup.title.get_text()) if soup.title else "",
        meta_description=_meta(soup, name="description"),
        og_site_name=_meta(soup, property="og:site_name"),
        og_title=_meta(soup, property="og:title"),
        og_description=_meta(soup, property="og:description"),
        keywords=_meta(soup, name="keywords"),
        canonical=(_attr_str(canonical_tag.get("href", "")) if canonical_tag else ""),
        lang=(_attr_str(html_tag.get("lang", "")) if html_tag else ""),
        structured_data=_extract_json_ld(soup),
        links=_collect_links(soup, url),
    )

    _strip_noise(soup)
    for nav in soup.find_all("nav"):  # links already captured above
        nav.decompose()
    page.headings = _extract_headings(soup)
    page.text = _extract_text(soup)
    return page


# ---------- Fetching ----------
async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[str, bytes]:
    """Fetch one page. Redirects are followed manually; each hop is SSRF-checked."""
    settings = get_settings()
    current = url
    for _ in range(settings.fetch_max_redirects + 1):
        current = await validate_public_url(current)
        try:
            async with client.stream("GET", current) as resp:
                if resp.status_code in REDIRECT_CODES:
                    location = resp.headers.get("location")
                    if not location:
                        raise SiteUnreachableError(
                            "Website sent a redirect without a target."
                        )
                    current = urljoin(current, location)
                    continue
                if resp.status_code in (401, 403, 429):
                    raise SiteAccessRestrictedError(
                        f"Website blocked automated access (HTTP {resp.status_code})."
                    )
                if resp.status_code >= 400:
                    raise SiteUnreachableError(
                        f"Website returned HTTP {resp.status_code}."
                    )
                ctype = resp.headers.get("content-type", "").lower()
                if ctype and not any(
                    t in ctype for t in ("text/html", "application/xhtml")
                ):
                    raise UnsupportedContentError(
                        f"Unsupported content type: {ctype.split(';')[0]}"
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= settings.fetch_max_bytes:
                        break
                return current, b"".join(chunks)[: settings.fetch_max_bytes]
        except httpx.TimeoutException as exc:
            raise SiteTimeoutError("Website took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise SiteUnreachableError("Could not connect to the website.") from exc
    raise TooManyRedirectsError("Website redirected too many times.")


async def _fetch_and_parse(client: httpx.AsyncClient, url: str) -> PageContent:
    final_url, raw = await _fetch(client, url)
    return _parse_page(final_url, raw)


def _pick_extra_urls(home: PageContent, limit: int) -> list[str]:
    if limit <= 0:
        return []
    seen = {home.url.rstrip("/")}
    picked: list[str] = []
    for kw in EXTRA_PAGE_KEYWORDS:
        for link_url, text in home.links:
            u = link_url.rstrip("/")
            if u in seen or u.lower().endswith(SKIP_EXT):
                continue
            if kw in urlparse(u).path.lower() or kw in text.lower():
                picked.append(link_url)
                seen.add(u)
                if len(picked) >= limit:
                    return picked
    return picked


def _build_client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        follow_redirects=False,  # manual, so every hop is validated
        timeout=httpx.Timeout(s.fetch_timeout_seconds),
        headers={
            "User-Agent": s.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


# ---------- Public API ----------
async def extract_website(
    url: str,
    client: httpx.AsyncClient | None = None,
    extra_pages_max: int | None = None,
) -> ExtractedContent:
    settings = get_settings()
    limit = settings.extra_pages_max if extra_pages_max is None else extra_pages_max
    own_client = client is None
    client = client or _build_client()
    try:
        final_url, raw = await _fetch(client, url)
        home = _parse_page(final_url, raw)

        extra_urls = _pick_extra_urls(home, limit)
        results = await asyncio.gather(
            *(_fetch_and_parse(client, u) for u in extra_urls), return_exceptions=True
        )
    finally:
        if own_client:
            await client.aclose()

    extras: list[PageContent] = []
    for u, r in zip(extra_urls, results):
        if isinstance(r, PageContent):
            extras.append(r)
        else:
            logger.info("Skipping extra page %s: %s", u, r)

    if len(home.text) + len(home.title) + len(home.meta_description) < 100:
        raise InsufficientContentError(
            "Not enough readable content found. The site may be JavaScript-rendered or empty."
        )

    return ExtractedContent(
        input_url=url,
        final_url=final_url,
        domain=_host(final_url),
        home=home,
        extra_pages=extras,
    )
