"""Rate limiting — slowapi singleton + FastAPI bootstrap.

We register ONE Limiter for the whole app and let routers reach it via
`from app.core.rate_limit import limiter`. Per-route limits are applied
with `@limiter.limit("N/minute")` on the route function (slowapi
requires the function to take `request: Request` as a named arg — every
sensitive route in this scaffold already does).

Production reality (Round-4 HIGH-2):
  * Storage: settings.RATE_LIMIT_STORAGE_URI selects the backend.
    `memory://` is per-worker — useless with --workers > 1. The
    Settings._production_needs_shared_rate_limit_storage validator
    blocks APP_ENV=production + memory:// at boot. For production set
    `redis://host:6379/0` and slowapi (via the `limits` package) will
    share counters across workers AND replicas.
  * Client IP: when fronted by a reverse proxy (nginx in the scaffold\'s
    docker-compose prod topology), request.client.host is the PROXY ip,
    not the real client — every request shares one bucket. We read
    X-Forwarded-For instead, but ONLY when the immediate hop is in the
    operator-configured TRUSTED_PROXY_IPS CIDR list. Untrusted hops are
    NOT allowed to set the header (otherwise any internet attacker
    spoofs it to dodge the limit).

Why a separate module:
  - Auth router (login/SSO) and the public approval router both need the
    same limiter instance — a module-level singleton avoids passing it
    through dependency injection.
  - Tests can monkey-patch `limiter.enabled = False` for fast unit runs.

Why `config_filename` is set to a sentinel path:
  - slowapi auto-reads `.env` if it finds one next to the CWD. On
    Windows the default Python text-mode decoder is cp1252, and the
    project\'s `.env` contains Thai comments → UnicodeDecodeError at
    import time. We pass an unused sentinel filename so slowapi calls
    `starlette.config.Config(<missing-file>)`, which just emits one
    UserWarning at boot and returns an empty config. The wider Settings
    class still loads `.env` through pydantic-settings (UTF-8 explicit).
"""
from __future__ import annotations

import ipaddress
import warnings

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.config import get_settings


def _parse_proxy_networks() -> list[ipaddress._BaseNetwork]:
    """Parse settings.trusted_proxy_networks into ipaddress network objects.

    Resolved once per process (Limiter is built once at import time).
    Invalid entries are silently dropped after a logged warning — we
    don\'t want a typo in one entry to break the whole limiter.
    """
    out: list[ipaddress._BaseNetwork] = []
    for raw in get_settings().trusted_proxy_networks:
        try:
            out.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            warnings.warn(
                f"TRUSTED_PROXY_IPS: dropping invalid CIDR {raw!r}",
                stacklevel=2,
            )
    return out


_TRUSTED_PROXY_NETS = _parse_proxy_networks()


def _client_ip(request: Request) -> str:
    """Resolve the rate-limit key.

    Round-5 HIGH-1 fix — walk RIGHTWARD, not leftmost. nginx\'s standard
    `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` APPENDS
    the immediate peer\'s IP rather than replacing the header. So when a
    malicious client sends `X-Forwarded-For: 1.2.3.4`, nginx forwards
    `X-Forwarded-For: 1.2.3.4, <real-client-ip>`. The LEFTMOST is the
    attacker-controlled value; the actual client is the leftmost entry
    that is NOT itself a trusted proxy (RFC 7239 §5.2).

    Algorithm:
      1. If no TRUSTED_PROXY_IPS configured → ignore the header entirely
         (the immediate hop IS the client, or we don\'t have a basis to
         trust any chain).
      2. If the immediate peer is not in the trusted set → same: ignore
         the header (header could be attacker-injected; we have no proxy
         to vouch for it).
      3. If the immediate peer IS trusted → walk XFF entries from right
         to left, skipping IPs that are themselves in the trusted set,
         until we find one that ISN\'t. That\'s the real client.
      4. If every entry is a trusted proxy → use the leftmost (everyone
         in the chain is on our infra, so the leftmost is the entry
         closest to the original client we have visibility into).
      5. If any entry is malformed → fall back to the direct hop. Don\'t
         trust a chain we can\'t parse.
    """
    remote = get_remote_address(request)
    if not _TRUSTED_PROXY_NETS:
        return remote
    try:
        peer_ip = ipaddress.ip_address(remote)
    except ValueError:
        return remote
    if not any(peer_ip in net for net in _TRUSTED_PROXY_NETS):
        return remote
    xff = request.headers.get("x-forwarded-for", "").strip()
    if not xff:
        return remote
    hops_raw = [h.strip() for h in xff.split(",") if h.strip()]
    if not hops_raw:
        return remote
    # Parse + validate every hop. A single malformed entry collapses the
    # whole chain to the direct hop — safer than guessing.
    hops: list[ipaddress._BaseAddress] = []
    for raw in hops_raw:
        try:
            hops.append(ipaddress.ip_address(raw))
        except ValueError:
            return remote
    # Walk rightward (real-client-first per RFC 7239). The first non-
    # trusted hop is the client. If every hop is trusted, fall through
    # to the leftmost (the deepest visibility we have).
    for candidate in reversed(hops):
        if not any(candidate in net for net in _TRUSTED_PROXY_NETS):
            return str(candidate)
    return str(hops[0])


# Public alias: the record-create endpoints store the submitting client's
# IP (records.submitted_ip) and must resolve it with exactly the same
# trusted-proxy rules as the rate limiter — one algorithm, one truth.
get_client_ip = _client_ip


# Module-level singleton — import directly from routers.
with warnings.catch_warnings():
    # Silence the one-time UserWarning about the missing sentinel file.
    warnings.simplefilter("ignore", UserWarning)
    limiter = Limiter(
        key_func=_client_ip,
        default_limits=[],
        storage_uri=get_settings().RATE_LIMIT_STORAGE_URI,
        config_filename=".slowapi-no-env",
    )


def bootstrap_rate_limiting(app: FastAPI) -> None:
    """Wire the limiter into a FastAPI app. Call once from create_app().

    Side effects:
      * `app.state.limiter` — slowapi reads this when a @limiter.limit
        decorator fires, so it MUST be set on the same app instance.
      * Adds the SlowAPI middleware that actually applies the limits.
      * Registers the 429 exception handler so the SPA gets a clean
        `{ "error": "Rate limit exceeded" }` body instead of HTML.
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
