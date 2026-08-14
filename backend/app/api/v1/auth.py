"""Auth endpoints — local login, SSO redirect/callback, refresh, logout.

Refresh tokens land in an httpOnly cookie so the SPA never touches them
in JS (XSS mitigation). Access tokens are returned in the response body
for in-memory storage by the SPA.

Note: this module deliberately does NOT use `from __future__ import
annotations`. slowapi\'s @limiter.limit wraps the route with
functools.wraps, which sets the wrapper\'s __globals__ to slowapi\'s
module. With string annotations (PEP 563), FastAPI\'s forward-ref
resolver would look up `LoginRequest` / `TokenResponse` in slowapi\'s
namespace and fail at app-boot with PydanticUndefinedAnnotation.
Concrete annotations sidestep that — types are resolved at definition
time, before any decoration runs.
"""
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.azure_ad import (
    build_authorize_url,
    exchange_code_for_token,
    verify_id_token,
)
from app.auth.jwt_service import (
    decode_token,
    encode_access_token,
    encode_refresh_token,
)
from app.auth.password import verify_password
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.models.app_setting import AppSetting
from app.db.models.revoked_token import RevokedToken
from app.db.models.role import Role
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import CamelBaseModel, LoginRequest, TokenResponse
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"


async def _is_jti_revoked(db: AsyncSession, jti: str) -> bool:
    """Check the refresh-token blocklist (Deep-Audit HIGH-3)."""
    if not jti:
        # No jti = legacy token from before the blocklist landed. Reject —
        # all tokens minted by encode_refresh_token() carry jti now, so a
        # missing jti is either tampering or pre-upgrade. Safer to force
        # re-login than to wave through.
        return True
    result = await db.execute(
        select(RevokedToken.id).where(RevokedToken.jti == jti).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _revoke_jti(
    db: AsyncSession, *, jti: str, user_id: UUID | None,
    expires_at_ts: int, reason: str,
) -> None:
    """Mark a refresh-token jti as revoked (idempotent on the unique index).

    `expires_at_ts` is the JWT `exp` claim (unix seconds) so the cleanup
    job can drop the row after the underlying token would have expired
    anyway. Idempotent: a second call with the same jti is a no-op
    courtesy of the unique constraint + ON CONFLICT DO NOTHING.
    """
    if not jti:
        return
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    expires_at = datetime.fromtimestamp(expires_at_ts, tz=timezone.utc)
    stmt = (
        pg_insert(RevokedToken)
        .values(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
            reason=reason,
        )
        .on_conflict_do_nothing(index_elements=["jti"])
    )
    await db.execute(stmt)


async def _get_setting_bool(db: AsyncSession, key: str, default: bool) -> bool:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        return default
    return bool(row.value)


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/api/v1/auth",
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")  # HIGH-1: brute-force defence
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    if not await _get_setting_bool(db, "auth.local.enabled", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local authentication is disabled by administrator",
        )

    normalized_email = payload.email.strip().lower()
    stmt = (
        select(User)
        .where(func.lower(User.email) == normalized_email)
        .options(selectinload(User.roles))
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    audit = ActivityLogger(db)
    if user is None or user.auth_provider != "local" or not user.is_active:
        await audit.log(
            action="auth.login_failed",
            action_type="login_failed",
            risk_level="medium",
            request=request,
            metadata={"reason": "user_not_found_or_inactive"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid credentials")

    if not verify_password(payload.password, user.password_hash or ""):
        await audit.log(
            action="auth.login_failed",
            action_type="login_failed",
            user=user,
            risk_level="medium",
            request=request,
            metadata={"reason": "bad_password"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid credentials")

    # Approval gate — credentials are valid but the account hasn't been
    # approved by an admin yet. Distinct error so the SPA can show a
    # friendly "awaiting approval" message instead of "wrong password".
    if not user.is_approved:
        await audit.log(
            action="auth.login_failed",
            action_type="login_failed",
            user=user,
            risk_level="low",
            request=request,
            metadata={"reason": "not_approved"},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="account_not_approved")

    access = encode_access_token(subject=str(user.id), auth_provider=user.auth_provider)
    refresh = encode_refresh_token(subject=str(user.id), auth_provider=user.auth_provider)
    _set_refresh_cookie(response, refresh)

    await audit.log(
        action="auth.login",
        action_type="login",
        user=user,
        request=request,
        risk_level="low",
    )

    settings = get_settings()
    return TokenResponse(
        access_token=access,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    # Revoke the current refresh jti server-side before clearing the
    # cookie (Deep-Audit HIGH-3). A copy of the cookie that an attacker
    # already exfiltrated will now fail at /refresh even though the JWT
    # itself is still inside its natural exp window.
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie:
        try:
            claims = decode_token(cookie)
        except Exception:
            claims = {}
        jti = claims.get("jti")
        exp = claims.get("exp")
        sub = claims.get("sub")
        if jti and isinstance(exp, int):
            try:
                uid = UUID(sub) if sub else None
            except (TypeError, ValueError):
                uid = None
            await _revoke_jti(
                db, jti=jti, user_id=uid, expires_at_ts=int(exp),
                reason="logout",
            )
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/v1/auth")
    audit = ActivityLogger(db)
    await audit.log(action="auth.logout", action_type="logout", request=request)
    return {"status": "ok"}


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")  # HIGH-1/HIGH-3: defence-in-depth on rotation
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="No refresh token")
    try:
        claims = decode_token(cookie)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid refresh token") from exc
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Not a refresh token")

    # Server-side revocation check (Deep-Audit HIGH-3). Rejects tokens
    # that were already invalidated by /logout, by a previous rotation,
    # or by an admin-side password change.
    old_jti = claims.get("jti") or ""
    if await _is_jti_revoked(db, old_jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Refresh token revoked")

    user_id = claims["sub"]
    auth_provider = claims.get("auth_provider", "local")

    # is_active gate (Deep-Audit HIGH-3 / MEDIUM-11). A deactivated user
    # must not be able to keep refreshing — otherwise the existing
    # cookie outlives the admin\'s "Deactivate" click by up to 7 days.
    try:
        uid = UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Bad subject claim") from exc
    refreshed_user = (await db.execute(
        select(User).where(User.id == uid)
    )).scalar_one_or_none()
    if refreshed_user is None or not refreshed_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="User not found or inactive")

    # Revoke the old jti BEFORE minting the new one — even if the mint
    # blows up, the stolen old token stays unusable.
    old_exp = claims.get("exp")
    if old_jti and isinstance(old_exp, int):
        await _revoke_jti(
            db, jti=old_jti, user_id=uid, expires_at_ts=int(old_exp),
            reason="rotation",
        )

    access = encode_access_token(subject=user_id, auth_provider=auth_provider)
    # Rotate refresh token — defence in depth against token replay.
    new_refresh = encode_refresh_token(subject=user_id, auth_provider=auth_provider)
    _set_refresh_cookie(response, new_refresh)
    # Audit the refresh so a stolen-token replay storm shows up in the
    # Activity Logs page (action_type="login" so it falls in the Login
    # tab) instead of being silent.
    await ActivityLogger(db).log(
        action="auth.refresh",
        action_type="login",
        user=refreshed_user,
        request=request,
        risk_level="low",
    )
    settings = get_settings()
    return TokenResponse(
        access_token=access,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


_SSO_STATE_COOKIE = "ct_sso_state"
_SSO_STATE_TTL_SECONDS = 600  # 10 minutes — Azure round-trip + slow user.


@router.get("/sso/redirect")
async def sso_redirect(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Hand the SPA an Azure authorize URL + bind the CSRF state to an
    httponly cookie that the matching /sso/callback POST must echo back.

    The cookie is samesite=lax so it survives the OAuth round-trip
    (Azure → redirect_uri → SPA → POST callback). It carries no PII —
    just a 32-byte URL-safe nonce we look up on callback.
    """
    if not await _get_setting_bool(db, "auth.sso.enabled", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="SSO is disabled by administrator")
    state = secrets.token_urlsafe(32)
    settings = get_settings()
    response.set_cookie(
        key=_SSO_STATE_COOKIE,
        value=state,
        max_age=_SSO_STATE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/api/v1/auth",
    )
    # Frontend Login expects `{ url }` — keep this shape stable; do NOT
    # leak the raw state in the JSON body (it lives in the cookie).
    return {"url": build_authorize_url(state=state)}


class SsoCallbackPayload(CamelBaseModel):
    code: str
    state: str


@router.post("/sso/callback", response_model=TokenResponse)
@limiter.limit("3/minute")  # HIGH-1: SSO callback enumeration defence
async def sso_callback(
    payload: SsoCallbackPayload,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Validate the OAuth state then exchange the code.

    Three failure modes, all 400 — the SPA renders a generic \"sign-in
    failed\" message; we don\'t leak which guard tripped to keep the
    error surface uniform against probing.
    """
    if not await _get_setting_bool(db, "auth.sso.enabled", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="SSO is disabled by administrator")

    cookie_state = request.cookies.get(_SSO_STATE_COOKIE)
    if not cookie_state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="sso_state_missing")
    # Constant-time compare to defeat any timing-based oracle.
    if not secrets.compare_digest(cookie_state, payload.state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="sso_state_mismatch")
    # Single-use — clear the cookie immediately so a replay of the same
    # callback (e.g. user hits Back in their browser) fails.
    response.delete_cookie(
        key=_SSO_STATE_COOKIE,
        path="/api/v1/auth",
    )

    token_response = await exchange_code_for_token(payload.code)
    id_token = token_response.get("id_token")
    if not id_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Azure response missing id_token")
    claims: dict[str, Any] = verify_id_token(id_token)
    email = (claims.get("preferred_username") or claims.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="id_token has no email claim")

    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()
    if user is None:
        # First-touch JIT user creation for known-good Azure identities.
        # New JIT users honor auth.auto_approve_new_users — when off, the
        # account is created but gated; admin must approve before login.
        auto_approve = await _get_setting_bool(db, "auth.auto_approve_new_users", False)
        user = User(
            email=email,
            full_name=claims.get("name", email),
            auth_provider="azure_ad",
            is_active=True,
            email_verified=True,
            is_approved=auto_approve,
        )
        db.add(user)
        await db.flush()

    if user.auth_provider != "azure_ad":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account is not an Azure AD identity")

    if not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="account_not_approved")

    access = encode_access_token(subject=str(user.id), auth_provider=user.auth_provider)
    refresh_tok = encode_refresh_token(subject=str(user.id), auth_provider=user.auth_provider)
    _set_refresh_cookie(response, refresh_tok)

    audit = ActivityLogger(db)
    await audit.log(action="auth.login", action_type="login", user=user, request=request)

    settings = get_settings()
    return TokenResponse(
        access_token=access,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/password-reset", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def password_reset_stub() -> dict[str, str]:
    """Password reset email flow — deferred (needs SMTP wired)."""
    return {"status": "not_implemented"}


@router.post("/invite", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def invite_stub() -> dict[str, str]:
    """External-user invite email — deferred (needs SMTP wired)."""
    return {"status": "not_implemented"}
