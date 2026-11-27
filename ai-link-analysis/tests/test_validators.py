# Unit and async pytest tests for app.utils.validators
# covering URL normalization, scheme/port/length format checks,
# and SSRF protection.

# Tests blocked local, loopback, private, link-local,
# metadata, IPv6/NAT64, and .local targets,
# including fake DNS resolution via monkeypatch.

# Verifies mixed public/private DNS records
# and blocked domains raise BlockedURLError,
# unresolvable hosts raise HostResolutionError, and valid public URLs pass.

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


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "ftp://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
        "http://user:pw@example.com",
        "https://example.com:22",
        "https://exa mple.com",
        "a" * 2100,
    ],
)
def test_invalid_formats(bad):
    with pytest.raises(InvalidURLError):
        parse_and_check_format(normalize_url(bad))


# ---- SSRF: no DNS needed ----
@pytest.mark.parametrize(
    "url",
    [
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
    ],
)
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
