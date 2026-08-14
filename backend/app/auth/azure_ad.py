"""Azure AD OAuth + token verification via MSAL.

Two surfaces:
- `build_authorize_url()` for the SSO redirect endpoint.
- `verify_id_token()` for the callback — pulls JWKS once per process and
  caches it (Azure rotates rarely; cache invalidation is a process
  restart problem, not a feature).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
from jose import jwt

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _jwks() -> dict[str, Any]:
    settings = get_settings()
    tenant = settings.AZURE_AD_TENANT_ID or "common"
    url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def build_authorize_url(*, state: str, scopes: list[str] | None = None) -> str:
    settings = get_settings()
    from urllib.parse import urlencode

    params = {
        "client_id": settings.AZURE_AD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.AZURE_AD_REDIRECT_URI,
        "response_mode": "query",
        "scope": " ".join(scopes or ["openid", "profile", "email"]),
        "state": state,
    }
    tenant = settings.AZURE_AD_TENANT_ID or "common"
    return (
        f"https://login.microsoftonline.com/{tenant}"
        f"/oauth2/v2.0/authorize?{urlencode(params)}"
    )


async def exchange_code_for_token(code: str) -> dict[str, Any]:
    settings = get_settings()
    tenant = settings.AZURE_AD_TENANT_ID or "common"
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": settings.AZURE_AD_CLIENT_ID,
        "client_secret": settings.AZURE_AD_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.AZURE_AD_REDIRECT_URI,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, data=data)
    resp.raise_for_status()
    return resp.json()


# Algorithm allowlist for Azure id_token verification. Hardcoded —
# Azure always signs with RS256 and we MUST NOT trust the JWT header\'s
# `alg` field (a classic algorithm-confusion attack: an attacker
# substitutes "none" or "HS256" in the header to bypass signature check).
_AZURE_ID_TOKEN_ALGS = ["RS256"]


def verify_id_token(id_token: str) -> dict[str, Any]:
    """Verify the id_token signature + return claims.

    Hardens against algorithm-confusion: we ignore the header `alg` and
    constrain the verifier to RS256 (Azure\'s only published signing
    algorithm at time of writing). Also validates issuer against the
    configured Azure AD tenant — a token from a different tenant or a
    different identity provider gets rejected even if Azure JWKS happens
    to know the kid.

    Raises jose.JWTError on bad signature / expired / wrong audience /
    wrong issuer.
    """
    settings = get_settings()
    keys = _jwks().get("keys", [])
    unverified = jwt.get_unverified_header(id_token)
    kid = unverified.get("kid")
    key = next((k for k in keys if k.get("kid") == kid), None)
    if key is None:
        # Cache miss — JWKS may have rotated. Bust + retry once.
        _jwks.cache_clear()
        keys = _jwks().get("keys", [])
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:
            raise jwt.JWTError("Signing key not found in Azure JWKS")
    # Expected issuer pattern: https://login.microsoftonline.com/{tenant}/v2.0
    # Use literal tenant when configured; fall back to "common" only for
    # multi-tenant apps that intentionally accept any tenant.
    tenant = settings.AZURE_AD_TENANT_ID or "common"
    expected_issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
    return jwt.decode(
        id_token,
        key,
        algorithms=_AZURE_ID_TOKEN_ALGS,
        audience=settings.AZURE_AD_CLIENT_ID,
        issuer=expected_issuer,
        options={"verify_at_hash": False},
    )
