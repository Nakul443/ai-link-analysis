# Validates URL format, length, scheme, credentials,
# and ports and rejects disallowed hostnames
# and internal/private IP addresses to prevent SSRF.

# Asynchronously resolves hostnames via DNS
# and checks every resulting IPv4/IPv6 address
# (including NAT64, 6to4, and IPv4-mapped ranges)
# to ensure only public endpoints are reached.

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
    ".local",
    ".localhost",
    ".internal",
    ".localdomain",
    ".lan",
    ".home",
    ".corp",
)

# Ranges Python may treat as "global" but that can embed or reach private IPv4s.
EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64
    ipaddress.ip_network("2002::/16"),  # 6to4
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
    return [str(info[4][0]).split("%")[0] for info in infos]


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
            raise BlockedURLError(
                "Access to private or internal IP addresses is not allowed."
            )
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
    assert parsed.hostname is not None
    await assert_public_host(parsed.hostname)
    return url
