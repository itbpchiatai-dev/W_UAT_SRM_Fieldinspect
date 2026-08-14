"""SSRF guard — validate an outbound URL before fetching it server-side.

Closes the SSRF gap (AGENTS.md §3 rule 9 + docs/security.md §3.6). URL-
fetching features are the single most common SSRF source in AI-generated
code: an attacker supplies a URL that points at an internal service or the
cloud metadata endpoint (169.254.169.254) to steal credentials.

`assert_safe_url(url)` rejects anything that is not a plain http(s) URL whose
host resolves ENTIRELY to public, routable IP addresses. Use it on every URL
that originates from request input BEFORE handing it to httpx/requests::

    from app.core.safe_url import assert_safe_url

    safe = assert_safe_url(payload.image_url)        # raises UnsafeURLError
    async with httpx.AsyncClient() as client:
        resp = await client.get(safe)

Optional allowlist — when you only ever fetch a known set of hosts, pass it
and everything else is rejected even if public::

    assert_safe_url(url, allowed_hosts={"api.partner.com"})

Caveats (documented, not hidden):
  * TOCTOU / DNS rebinding: we resolve at validation time; a hostile resolver
    could return a public IP now and a private one when httpx connects. For
    high-assurance paths, resolve once here and connect to the validated IP
    (pin the Host header), or keep an allowlist. For the common internal-tool
    case, host+IP validation closes the practical attack surface.
  * We block by IP class, not by port — combine with an allowlist if you must
    restrict ports.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF safety checks."""


def _is_public_ip(ip_str: str) -> bool:
    """True only for globally routable addresses.

    Blocks loopback, private (RFC 1918), link-local (incl. the
    169.254.169.254 cloud-metadata address), multicast, reserved and
    unspecified ranges — for both IPv4 and IPv6, plus IPv4-mapped IPv6.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_public_ip(str(ip.ipv4_mapped))
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError("cannot resolve host: " + host) from exc
    return [info[4][0] for info in infos]


def assert_safe_url(url, *, allowed_hosts=None, allowed_schemes=None):
    """Validate `url` for safe server-side fetching; return it unchanged.

    Raises UnsafeURLError on any failure. Never performs the request itself.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL must be a non-empty string")

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    schemes = allowed_schemes or _ALLOWED_SCHEMES
    if scheme not in schemes:
        raise UnsafeURLError("scheme not allowed: " + (scheme or "(none)"))

    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")

    if allowed_hosts is not None:
        if host.lower() not in {h.lower() for h in allowed_hosts}:
            raise UnsafeURLError("host not in allowlist: " + host)

    # Literal IP host → validate directly; hostname → resolve every A/AAAA.
    try:
        ipaddress.ip_address(host)
        candidate_ips = [host]
    except ValueError:
        candidate_ips = _resolve_ips(host)

    if not candidate_ips:
        raise UnsafeURLError("host did not resolve: " + host)

    for ip_str in candidate_ips:
        if not _is_public_ip(ip_str):
            raise UnsafeURLError(
                "host resolves to a non-public address: "
                + host + " -> " + ip_str
            )
    return url


def is_safe_url(url, *, allowed_hosts=None, allowed_schemes=None) -> bool:
    """Boolean convenience wrapper around assert_safe_url."""
    try:
        assert_safe_url(
            url, allowed_hosts=allowed_hosts, allowed_schemes=allowed_schemes
        )
        return True
    except UnsafeURLError:
        return False
