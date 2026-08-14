"""Round-4 HIGH-2 regression — production must reject in-memory rate-
limit storage. Also verifies the proxy-aware client IP helper rejects
spoofed X-Forwarded-For when there is no trusted proxy configured."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Round 8-16D.1 — see the note in test_jwt_secret_validation.py: this is a
# RuntimeError subclass now so pydantic cannot echo the raw settings dict.
from app.core.config import RateLimitStorageConfigError, Settings
from app.core.rate_limit import _client_ip


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "DB_PASSWORD": "test-only",
        "JWT_SECRET_KEY": "deadbeef0123456789abcdeffedcba9876543210abcdef0123456789cafebabe",
        "API_CORS_ORIGINS": "https://app.example.com",
        "APP_ENV": "production",
        # Round 8-16D — this dict is a MINIMALLY VALID production env, and
        # production now also refuses to boot without a parseable trusted-proxy
        # list (Settings._production_runtime_preflight). Added so these tests
        # keep isolating the storage-URI behaviour they are actually about;
        # the preflight itself is covered by
        # tests/security/test_production_runtime_validation.py.
        "TRUSTED_PROXY_IPS": "10.1.2.0/24",
    }
    base.update(overrides)
    return base


def test_production_memory_storage_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _env(RATE_LIMIT_STORAGE_URI="memory://").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RateLimitStorageConfigError, match="in-memory"):
        Settings(_env_file=None)


def test_production_redis_storage_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _env(RATE_LIMIT_STORAGE_URI="redis://localhost:6379/0").items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.RATE_LIMIT_STORAGE_URI.startswith("redis://")


def test_dev_memory_storage_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(APP_ENV="dev", RATE_LIMIT_STORAGE_URI="memory://")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    Settings(_env_file=None)  # no raise


def _fake_request(client_host: str, *, xff: str | None = None) -> object:
    req = MagicMock()
    req.client.host = client_host
    req.headers.get = lambda key, default="": (
        xff if xff is not None and key.lower() == "x-forwarded-for" else default
    )
    # slowapi's get_remote_address also looks at scope; mimic enough.
    req.scope = {"client": (client_host, 0)}
    return req


def test_client_ip_ignores_xff_when_no_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _TRUSTED_PROXY_NETS is resolved at module import; tests/security
    # conftest leaves TRUSTED_PROXY_IPS empty by default so it should
    # already be empty.
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = []  # ensure empty for this test
    req = _fake_request("203.0.113.5", xff="198.51.100.99")
    assert _client_ip(req) == "203.0.113.5"


def test_client_ip_honours_xff_from_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ipaddress
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = [ipaddress.ip_network("10.0.0.0/8")]
    req = _fake_request("10.0.0.5", xff="198.51.100.99, 10.0.0.5")
    assert _client_ip(req) == "198.51.100.99"


def test_client_ip_rejects_xff_from_untrusted_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ipaddress
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = [ipaddress.ip_network("10.0.0.0/8")]
    # Peer is NOT in 10/8 — XFF must be ignored.
    req = _fake_request("203.0.113.5", xff="198.51.100.99")
    assert _client_ip(req) == "203.0.113.5"


def test_client_ip_rejects_malformed_xff(monkeypatch: pytest.MonkeyPatch) -> None:
    import ipaddress
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = [ipaddress.ip_network("10.0.0.0/8")]
    req = _fake_request("10.0.0.5", xff="not-an-ip, something")
    assert _client_ip(req) == "10.0.0.5"


# ── Round-5 HIGH-1 — rightmost-walk regression tests ────────────────

def test_client_ip_rejects_leftmost_when_attacker_prepends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original Round-4 implementation read XFF leftmost. nginx
    appends, so an attacker at 198.51.100.99 sending
    `X-Forwarded-For: 1.2.3.4` makes nginx forward
    `X-Forwarded-For: 1.2.3.4, 198.51.100.99`. The Round-5 fix walks
    rightward and skips trusted proxies — the real client is
    198.51.100.99 (the leftmost non-trusted hop), NOT the spoofed 1.2.3.4.
    """
    import ipaddress
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = [ipaddress.ip_network("10.0.0.0/8")]
    req = _fake_request("10.0.0.5", xff="1.2.3.4, 198.51.100.99")
    assert _client_ip(req) == "198.51.100.99", \
        "Round-5 HIGH-1 — leftmost XFF entry is attacker-spoofable; must walk rightward"


def test_client_ip_walks_past_multiple_trusted_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chained-proxy case: client → proxy1 (10.x) → proxy2 (10.x) →
    backend. XFF reads `<client>, 10.0.0.1, 10.0.0.2`. The rightmost
    non-trusted hop is the client."""
    import ipaddress
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = [ipaddress.ip_network("10.0.0.0/8")]
    req = _fake_request("10.0.0.2", xff="203.0.113.7, 10.0.0.1, 10.0.0.2")
    assert _client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_when_every_hop_is_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All hops inside our infra — nothing claims to be a client. Use
    the leftmost (deepest visibility) rather than reporting nothing."""
    import ipaddress
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = [ipaddress.ip_network("10.0.0.0/8")]
    req = _fake_request("10.0.0.2", xff="10.0.0.1, 10.0.0.2")
    assert _client_ip(req) == "10.0.0.1"


def test_client_ip_malformed_hop_collapses_to_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single malformed hop anywhere in the chain → don\'t trust the
    chain at all. Safer than guessing which entry was real."""
    import ipaddress
    import app.core.rate_limit as rl
    rl._TRUSTED_PROXY_NETS = [ipaddress.ip_network("10.0.0.0/8")]
    req = _fake_request("10.0.0.2", xff="not-an-ip, 198.51.100.99, 10.0.0.2")
    assert _client_ip(req) == "10.0.0.2"
