# Tests `TTLCache` for capacity eviction, TTL expiration with a fake clock,
# and disabling behavior when TTL is zero.

# Checks `cache_key` URL normalization across casing, trailing slashes,
# fragments, and query parameters.

# Verifies `RateLimiter` sliding-window enforcement per key,
# window reset over time, and disabling when the limit is zero.

from app.utils.cache import TTLCache, cache_key
from app.utils.rate_limit import RateLimiter


def test_cache_expiry_and_eviction():
    now = [0.0]
    c = TTLCache(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # evicts "a"
    assert c.get("a") is None and c.get("b") == 2 and c.get("c") == 3
    now[0] = 11
    assert c.get("b") is None


def test_cache_disabled_when_ttl_zero():
    c = TTLCache(ttl_seconds=0, max_entries=10)
    c.set("a", 1)
    assert c.get("a") is None


def test_cache_key_normalization():
    assert cache_key("https://Example.com/") == cache_key("https://example.com")
    assert cache_key("https://example.com/a#frag") == "https://example.com/a"
    assert cache_key("https://example.com/a?b=1") == "https://example.com/a?b=1"


def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: now[0])
    assert rl.check("ip") == 0 and rl.check("ip") == 0
    assert rl.check("ip") > 0
    assert rl.check("other") == 0
    now[0] = 61
    assert rl.check("ip") == 0


def test_rate_limiter_disabled():
    assert RateLimiter(0).check("x") == 0
