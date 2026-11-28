# Normalizes competitor brand names and domains,
# sanitizes website URLs to scheme://host (or None),
# and rejects raw IP addresses or invalid URLs.

# Filters out the target brand itself (by normalized name or domain)
# and deduplicates entries by name and host up to a configurable limit.

# Forces source="ai

"""Competitor cleanup. Competitors are AI suggestions: never verified, always labelled."""

import ipaddress
import re
from urllib.parse import urlparse

from app.schemas import Competitor


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _host(url: str | None) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.") if url else ""


def _clean_site(website: str | None) -> str | None:
    """Return a safe origin like https://example.com, or None if it looks wrong."""
    if not website or not website.strip():
        return None
    raw = website.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # raw IPs are never a competitor brand site
    except ValueError:
        pass
    return f"{parsed.scheme}://{host}"


def clean_competitors(
    competitors: list[Competitor],
    brand_name: str,
    brand_domain: str,
    limit: int = 5,
) -> list[Competitor]:
    """Drop the brand itself and duplicates, sanitize websites, force the AI label."""
    own_name = _norm(brand_name)
    own_host = brand_domain.lower().removeprefix("www.")
    seen: set[str] = set()
    cleaned: list[Competitor] = []

    for c in competitors:
        name_key = _norm(c.brand_name)
        site = _clean_site(c.website)
        site_host = _host(site)
        if not name_key:
            continue
        if name_key == own_name or (site_host and site_host == own_host):
            continue
        if name_key in seen or (site_host and site_host in seen):
            continue
        seen.add(name_key)
        if site_host:
            seen.add(site_host)
        cleaned.append(c.model_copy(update={"website": site, "source": "ai_suggested"}))
        if len(cleaned) >= limit:
            break
    return cleaned
