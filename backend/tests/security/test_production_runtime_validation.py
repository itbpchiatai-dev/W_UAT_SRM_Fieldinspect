"""Round 8-16D — APP_ENV=production runtime preflight + proxy-trust regressions.

Covers the fail-fast gates added in `app/core/config.py`
(`_production_runtime_preflight`) and the spoofing properties of
`app/core/rate_limit._client_ip` that those gates exist to protect.

No real secrets: every Settings is built from a placeholder env dict with
`_env_file=None`, so the developer's `.env` is never read (same pattern as
tests/security/test_rate_limit_production_storage.py). The JWT value below
is fixed hex chosen only to satisfy the length/entropy validator — it is not
a credential for anything.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import ProductionConfigError, Settings

# Satisfies _jwt_secret_strong_enough (>=32 chars, >=12 distinct, not a
# known placeholder word). Test-only constant, never a real key.
_FAKE_JWT = "deadbeef0123456789abcdeffedcba9876543210abcdef0123456789cafebabe"


def _prod_env(**overrides: str) -> dict[str, str]:
    """A MINIMALLY VALID production env; each test breaks one thing."""
    base = {
        "DB_PASSWORD": "placeholder-not-a-real-secret",
        "JWT_SECRET_KEY": _FAKE_JWT,
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "API_CORS_ORIGINS": "https://farmlog.example.co.th",
        "RATE_LIMIT_STORAGE_URI": "redis://redis.internal:6379/0",
        "TRUSTED_PROXY_IPS": "10.1.2.0/24,10.1.3.5/32",
    }
    base.update(overrides)
    return base


def _settings(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


# --- the baseline must actually pass, or every negative test below is vacuous

def test_valid_production_config_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, _prod_env())
    assert s.APP_ENV == "production"
    assert s.trusted_proxy_networks == ["10.1.2.0/24", "10.1.3.5/32"]


def test_dev_is_unaffected_by_the_production_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev defaults that would fail every production check must still
    load cleanly — this gate is production-only by design."""
    s = _settings(monkeypatch, _prod_env(
        APP_ENV="dev",
        TRUSTED_PROXY_IPS="",
        API_CORS_ORIGINS="http://localhost:5173",
        RATE_LIMIT_STORAGE_URI="memory://",
        APP_DEBUG="true",
    ))
    assert s.APP_ENV == "dev"


# --- TRUSTED_PROXY_IPS ------------------------------------------------------

def test_production_rejects_empty_trusted_proxy_ips(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProductionConfigError, match="TRUSTED_PROXY_IPS is empty"):
        _settings(monkeypatch, _prod_env(TRUSTED_PROXY_IPS=""))


def test_production_rejects_unsubstituted_template_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The .env.prod.example placeholders are non-empty STRINGS but parse to
    zero usable networks — rate_limit._parse_proxy_networks would drop them
    and silently restore the single-bucket failure. Copying the template
    without substituting must not boot."""
    with pytest.raises(ProductionConfigError, match="not a valid IP/CIDR"):
        _settings(monkeypatch, _prod_env(
            TRUSTED_PROXY_IPS="<nginx-container-cidr>,<it-proxy-ip>/32",
        ))


def test_production_rejects_partially_invalid_proxy_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One good entry does not excuse a typo in another — the bad one would
    be dropped silently, narrowing the trusted set without telling anyone."""
    with pytest.raises(ProductionConfigError, match="not a valid IP/CIDR"):
        _settings(monkeypatch, _prod_env(TRUSTED_PROXY_IPS="10.1.2.0/24,not-an-ip"))


def test_production_accepts_bare_ip_as_trusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, _prod_env(TRUSTED_PROXY_IPS="10.1.3.5"))
    assert s.trusted_proxy_networks == ["10.1.3.5"]


# --- CORS / debug -----------------------------------------------------------

@pytest.mark.parametrize("origin", [
    "http://localhost:5173",
    "https://app.example.com,http://localhost:3000",
    "http://127.0.0.1:8080",
])
def test_production_rejects_localhost_cors_origin(
    monkeypatch: pytest.MonkeyPatch, origin: str,
) -> None:
    with pytest.raises(ProductionConfigError, match="localhost origin"):
        _settings(monkeypatch, _prod_env(API_CORS_ORIGINS=origin))


def test_production_rejects_debug_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProductionConfigError, match="APP_DEBUG"):
        _settings(monkeypatch, _prod_env(APP_DEBUG="true"))


# --- error hygiene ----------------------------------------------------------

def test_preflight_failure_reports_every_problem_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator fixing a production deploy should not have to rediscover
    the next problem on each restart."""
    with pytest.raises(ProductionConfigError) as exc:
        _settings(monkeypatch, _prod_env(
            TRUSTED_PROXY_IPS="", API_CORS_ORIGINS="http://localhost:5173", APP_DEBUG="true",
        ))
    message = str(exc.value)
    assert "TRUSTED_PROXY_IPS" in message
    assert "localhost origin" in message
    assert "APP_DEBUG" in message


def test_preflight_failure_never_echoes_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ProductionConfigError is a RuntimeError, NOT a ValueError, precisely so
    pydantic does not wrap it into a ValidationError that echoes the raw
    settings input (which carries every env secret) into logs and tickets."""
    sentinel = "SENTINEL_SECRET_VALUE_MUST_NOT_APPEAR_9f3a2b"
    with pytest.raises(ProductionConfigError) as exc:
        _settings(monkeypatch, _prod_env(DB_PASSWORD=sentinel, TRUSTED_PROXY_IPS=""))
    rendered = str(exc.value)
    assert sentinel not in rendered
    assert _FAKE_JWT not in rendered


# --- the property the gate protects: XFF spoofing ---------------------------

def _request(peer: str, xff: str | None = None) -> MagicMock:
    request = MagicMock()
    request.client.host = peer
    request.headers = {"x-forwarded-for": xff} if xff is not None else {}
    return request


def _client_ip_with(monkeypatch: pytest.MonkeyPatch, trusted: str, request: MagicMock) -> str:
    """Rebuild rate_limit's module-level trusted set under a patched env.

    _TRUSTED_PROXY_NETS is resolved once at import, so a test that only sets
    the env var would silently exercise the ALREADY-imported (empty) set and
    pass for the wrong reason.
    """
    monkeypatch.setenv("TRUSTED_PROXY_IPS", trusted)
    from app.core import rate_limit
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(rate_limit, "_TRUSTED_PROXY_NETS", rate_limit._parse_proxy_networks())
    try:
        return rate_limit._client_ip(request)
    finally:
        get_settings.cache_clear()


def test_spoofed_xff_from_untrusted_peer_cannot_change_the_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attacker hitting the app directly sets X-Forwarded-For themselves.
    Because their own address is not a trusted proxy, the header must be
    ignored entirely — otherwise every request could claim a fresh bucket and
    the limit would be trivially bypassed."""
    ip = _client_ip_with(
        monkeypatch, "10.1.2.0/24", _request(peer="203.0.113.9", xff="1.2.3.4"),
    )
    assert ip == "203.0.113.9"


def test_attacker_prepended_xff_does_not_win_over_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nginx APPENDS ($proxy_add_x_forwarded_for), so a client-supplied value
    ends up LEFTMOST. Taking the leftmost entry would hand the attacker a
    per-request bucket; the resolver must walk rightward instead."""
    ip = _client_ip_with(
        monkeypatch, "10.1.2.0/24",
        _request(peer="10.1.2.7", xff="1.2.3.4, 203.0.113.9"),
    )
    assert ip == "203.0.113.9"


def test_full_production_chain_resolves_the_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented topology: client -> IT proxy -> nginx -> backend.
    Backend's peer is the nginx container and XFF is
    "<real-client>, <it-proxy>". BOTH infra hops must be trusted for the
    real client to surface — this is the case .env.prod.example warns about.
    """
    ip = _client_ip_with(
        monkeypatch, "10.1.2.0/24,10.1.3.5/32",
        _request(peer="10.1.2.7", xff="203.0.113.9, 10.1.3.5"),
    )
    assert ip == "203.0.113.9"


def test_omitting_an_infra_hop_collapses_everyone_into_one_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression lock for the subtle misconfiguration: trusting ONLY the
    nginx subnet and forgetting the IT proxy makes the resolver stop at the
    proxy and report IT as the client — so every user shares one bucket while
    the limiter still appears to work. Asserting the broken behaviour here
    documents exactly why .env.prod.example insists on listing every hop."""
    ip = _client_ip_with(
        monkeypatch, "10.1.2.0/24",  # nginx only; IT proxy 10.1.3.5 omitted
        _request(peer="10.1.2.7", xff="203.0.113.9, 10.1.3.5"),
    )
    assert ip == "10.1.3.5"
    assert ip != "203.0.113.9"
