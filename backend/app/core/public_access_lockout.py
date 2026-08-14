"""Failure lockout for the public phone+password lookup (round 8-9C).

Two counters, both checked BEFORE any digest, query or bcrypt round:

  1. per (client IP, phone fingerprint) — stops one attacker grinding one
     phone's 4-digit-minimum keyspace.
  2. per phone fingerprint alone — stops the same grind spread across a
     botnet, which tier 1 cannot see.

Why a second limiter at all, on top of the route's @limiter.limit: slowapi's
route limit counts EVERY request from an IP, so it throttles honest traffic
just as hard as an attack and resets on its own window regardless of outcome.
This one counts only FAILED authentications and buckets by phone, which is the
thing actually under attack. The route limit stays as the outer, cheaper gate.

Storage: the SAME RATE_LIMIT_STORAGE_URI the app already validates
(memory:// in dev; a shared redis:// is required in production by
Settings._production_needs_shared_rate_limit_storage). No new dependency —
`limits` is already installed as slowapi's own engine, and only its PUBLIC API
is used (storage_from_string / FixedWindowRateLimiter / RateLimitItem*), never
slowapi's private attributes, so a slowapi upgrade can't silently break this.

Keys never contain a phone, a password, a plot code, or a supplier code —
only the HMAC fingerprint (app.auth.plot_access_password.
build_phone_lockout_fingerprint). Nothing here is ever logged.
"""
from __future__ import annotations

from limits import RateLimitItemPerHour, RateLimitItemPerMinute
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter

from app.core.config import get_settings

# Tier 1 — one IP grinding one phone. 10 failures per 15 minutes.
# (limits models windows in fixed units; 15 minutes is expressed as a
# per-minute item with a 15-minute multiple.)
PER_IP_PHONE_LIMIT = RateLimitItemPerMinute(10, 15)
# Tier 2 — the same phone attacked from anywhere. 50 failures per hour.
PER_PHONE_LIMIT = RateLimitItemPerHour(50)

_KEY_NAMESPACE = "plot-access-lockout"

_storage = None
_limiter: FixedWindowRateLimiter | None = None


def _rate_limiter() -> FixedWindowRateLimiter:
    """Lazily built so importing this module never opens a connection, and so
    tests can reset it. One instance per process."""
    global _storage, _limiter
    if _limiter is None:
        _storage = storage_from_string(get_settings().RATE_LIMIT_STORAGE_URI)
        _limiter = FixedWindowRateLimiter(_storage)
    return _limiter


def reset_for_tests() -> None:
    """Drop the cached storage/limiter so a test can point at a fresh backend.
    Test-support only — never called from production code."""
    global _storage, _limiter
    _storage = None
    _limiter = None


def _keys(client_ip: str, phone_fingerprint: str) -> tuple[str, str]:
    return (
        f"{_KEY_NAMESPACE}:ip:{client_ip}:{phone_fingerprint}",
        f"{_KEY_NAMESPACE}:phone:{phone_fingerprint}",
    )


def is_locked_out(client_ip: str, phone_fingerprint: str) -> bool:
    """True when EITHER counter is already exhausted.

    Uses `test` (peek) rather than `hit`, so merely asking does not consume
    budget — the caller increments only on an actual failed authentication.

    Fail-OPEN on a storage error, deliberately: the credential itself is still
    bcrypt-verified on every attempt, and the route-level slowapi limit is a
    separate, independently-enforced gate. Failing closed here would turn a
    Redis blip into a total outage of the public inspection flow for every
    honest field user — a worse outcome than briefly losing the extra counter.
    Documented in the round 8-9C brief as the chosen trade-off.
    """
    try:
        limiter = _rate_limiter()
        ip_key, phone_key = _keys(client_ip, phone_fingerprint)
        return not (
            limiter.test(PER_IP_PHONE_LIMIT, ip_key)
            and limiter.test(PER_PHONE_LIMIT, phone_key)
        )
    except Exception:      # noqa: BLE001 — any storage failure, never logged
        return False


def register_failure(client_ip: str, phone_fingerprint: str) -> None:
    """Count one FAILED authentication against both tiers. Never called on
    success. Swallows storage errors for the same fail-open reason."""
    try:
        limiter = _rate_limiter()
        ip_key, phone_key = _keys(client_ip, phone_fingerprint)
        limiter.hit(PER_IP_PHONE_LIMIT, ip_key)
        limiter.hit(PER_PHONE_LIMIT, phone_key)
    except Exception:      # noqa: BLE001
        return


def clear_failures(client_ip: str, phone_fingerprint: str) -> None:
    """Reset BOTH tiers after a successful authentication.

    Clearing tier 2 (global-per-phone) on success is deliberate: the person
    who just proved they know the password is the legitimate holder, and
    leaving a shared counter armed would let an attacker lock the real user
    out by burning it — a denial-of-service against the honest party. An
    attacker cannot reach this path without the password, which is the thing
    the counter exists to protect.
    """
    try:
        limiter = _rate_limiter()
        ip_key, phone_key = _keys(client_ip, phone_fingerprint)
        limiter.clear(PER_IP_PHONE_LIMIT, ip_key)
        limiter.clear(PER_PHONE_LIMIT, phone_key)
    except Exception:      # noqa: BLE001
        return


__all__ = [
    "PER_IP_PHONE_LIMIT",
    "PER_PHONE_LIMIT",
    "is_locked_out",
    "register_failure",
    "clear_failures",
    "reset_for_tests",
]
