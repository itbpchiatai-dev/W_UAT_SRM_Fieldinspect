"""Microsoft Graph email sender.

Auth: client_credentials grant against the Azure AD tenant, requesting
the `https://graph.microsoft.com/.default` scope. Needs an Azure AD app
registration with the `Mail.Send` Application permission consented by
IT — see docs/patterns/email.md for the one-time setup.

Token caching: 50 minutes (Graph tokens last 60). Cheap to refresh, but
sending 30 mails in a minute shouldn\'t hit the OAuth endpoint 30 times.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

_token_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}


async def _get_access_token() -> str | None:
    """Fetch + cache an access token. Returns None if config is incomplete."""
    settings = get_settings()
    if not (
        settings.M365_TENANT_ID
        and settings.M365_CLIENT_ID
        and settings.M365_CLIENT_SECRET
    ):
        return None

    now = time.time()
    cached = _token_cache.get("value")
    if cached and _token_cache.get("expires_at", 0.0) > now + 60:
        return cached

    url = _TOKEN_URL.format(tenant=settings.M365_TENANT_ID)
    data = {
        "client_id": settings.M365_CLIENT_ID,
        "client_secret": settings.M365_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, data=data)
    if resp.status_code != 200:
        log.warning("M365 token fetch failed: %s %s", resp.status_code, resp.text[:200])
        return None
    body = resp.json()
    token = body.get("access_token")
    expires_in = body.get("expires_in", 3000)
    if not token:
        return None
    _token_cache["value"] = token
    _token_cache["expires_at"] = now + min(expires_in, 3000)
    return token


async def send_html_email(
    to: list[str],
    subject: str,
    html_body: str,
) -> bool:
    """Send an HTML email via Graph. Returns True on success.

    Never raises — caller is a fire-and-forget notification path.
    """
    if not to:
        return False
    settings = get_settings()
    if not settings.M365_SENDER_EMAIL:
        log.warning("M365_SENDER_EMAIL not set; skipping email send")
        return False

    token = await _get_access_token()
    if not token:
        return False

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
        },
        "saveToSentItems": "true",
    }
    url = _GRAPH_SEND_URL.format(sender=settings.M365_SENDER_EMAIL)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("Graph sendMail HTTP error: %s", exc)
        return False
    # Graph returns 202 Accepted on queue-for-send. Anything else is an error.
    if resp.status_code != 202:
        log.warning(
            "Graph sendMail rejected: %s body=%s",
            resp.status_code, resp.text[:300],
        )
        return False
    return True
