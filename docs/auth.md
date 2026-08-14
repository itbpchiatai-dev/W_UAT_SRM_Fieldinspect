# docs/auth.md

> Authentication reference สำหรับ dual provider:
> **Internal users** → Azure AD SSO via MSAL
> **External users** → Local auth (admin invite + email verification + bcrypt + optional 2FA)
>
> ⚠️ Hard rule: ห้าม mix providers ใน user เดียวกัน

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│  CLIENT (Frontend)                                       │
│                                                          │
│  ┌────────────────────┐    ┌─────────────────────────┐  │
│  │ Internal Login     │    │ External Login          │  │
│  │ (MSAL Redirect)    │    │ (email + password)      │  │
│  └────────┬───────────┘    └──────────┬──────────────┘  │
│           │                            │                 │
│           ▼                            ▼                 │
│      Azure AD                    POST /auth/login       │
│           │                            │                 │
│           └──── ID/Access Token ───────┘                 │
└─────────────────────┬────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────┐
│  BACKEND (FastAPI)                                       │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Auth Middleware                                  │   │
│  │  1. Read Authorization: Bearer <token>            │   │
│  │  2. Decode JWT                                    │   │
│  │  3. Verify by `auth_provider` claim:              │   │
│  │     - "azure_ad" → verify with MSAL/JWKS          │   │
│  │     - "local"    → verify with JWT_SECRET_KEY     │   │
│  │  4. Load user from DB                             │   │
│  │  5. Check is_active, roles                        │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

**Single Source of Truth:** `users` table — มี column `auth_provider` ที่บอกว่า user นี้ใช้ provider ไหน

**AUTH_SCOPE จาก `project.config`** กำหนดว่า project รองรับ provider ไหน — backend code ใช้ค่านี้ผ่าน env var เพื่อ enable/disable endpoint ที่เกี่ยวข้อง

---

## 2. User Model (Recap)

```python
class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    # "azure_ad" | "local"
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # NULL for azure_ad users; bcrypt hash for local users
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, nullable=False)
    business_unit_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
```

### 2.1 Role Naming Convention

Roles ต้องมี prefix:
- `internal:*` — สำหรับ Azure AD users (employees)
- `external:*` — สำหรับ Local users (vendors/customers)

**Seeded default roles (8 ตัว — 4 internal + 2 external, system roles ห้ามลบ):**

| Internal (Azure AD) | External (Local) |
|---|---|
| `internal:super_admin` (ทุก permission) | `external:admin` (จัดการ user ภายในองค์กรของตัวเอง) |
| `internal:admin` (สร้าง/แก้ user, ดู settings) | `external:user` (ดู Dashboard เท่านั้น) |
| `internal:super_user` (ดู menu config + dashboard) | |
| `internal:user` (ดู Dashboard เท่านั้น) | |

ตัวอย่าง custom roles ที่ super admin สร้างเองได้ผ่าน `/settings/roles`:
- `internal:product_manager`, `internal:bu_manager`
- `external:vendor`, `external:partner_admin`

**Hard rule:** AI ห้าม assign `internal:*` role ให้ local user หรือ `external:*` role ให้ azure_ad user

> Seed file: `backend/app/seed.py` — running `python -m app.seed` (idempotent) จะ upsert role + permission catalog + menu tree
>
> Per-user permission overrides (เพิ่ม/เพิกถอนเฉพาะคน) อยู่ใน L1 baseline แล้ว — ใช้ table `user_permission_overrides` (ดู `docs/admin-config.md` Pattern B)

---

## 3. Internal Users (Azure AD SSO via MSAL)

> **JIT user creation:** ครั้งแรกที่ Azure AD user login (callback ใน `backend/app/api/v1/auth.py`) ระบบจะ auto-create row ใน `users` table (`auth_provider="azure_ad"`, `email_verified=True`) — แต่ **ยังไม่ assign role ใดๆ** super admin ต้อง grant role ผ่าน `/settings/users` ก่อน user เข้า protected route ได้
>
> ข้อยกเว้น: ถ้า email ตรงกับ `AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL` (และ seed รันแล้ว) user row + `internal:super_admin` role ถูกสร้างไว้ล่วงหน้า login ครั้งแรก = ได้สิทธิ์ทันที

### 3.1 Backend Setup

**`app/integrations/azure_ad.py`:**

```python
from functools import lru_cache
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JWTError

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _get_jwks_uri() -> str:
    settings = get_settings()
    return (
        f"https://login.microsoftonline.com/{settings.AZURE_AD_TENANT_ID}"
        f"/discovery/v2.0/keys"
    )


_jwks_cache: dict[str, Any] | None = None


async def _get_jwks() -> dict[str, Any]:
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(_get_jwks_uri())
            response.raise_for_status()
            _jwks_cache = response.json()
    return _jwks_cache


async def verify_azure_ad_token(token: str) -> dict[str, Any]:
    """Verify Azure AD ID token and return decoded claims."""
    settings = get_settings()
    jwks = await _get_jwks()
    unverified_header = jwt.get_unverified_header(token)

    rsa_key: dict[str, Any] = {}
    for key in jwks["keys"]:
        if key["kid"] == unverified_header["kid"]:
            rsa_key = {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n": key["n"],
                "e": key["e"],
            }
            break
    if not rsa_key:
        raise JWTError("Unable to find appropriate signing key")

    return jwt.decode(
        token,
        rsa_key,
        algorithms=["RS256"],
        audience=settings.AZURE_AD_CLIENT_ID,
        issuer=f"https://login.microsoftonline.com/{settings.AZURE_AD_TENANT_ID}/v2.0",
    )
```

### 3.2 Login Endpoint (Token Exchange)

Pattern: frontend ใช้ MSAL.js redirect → ได้ Azure AD token → ส่งให้ backend → backend สร้าง app session token

**`app/api/v1/auth.py`:**

```python
from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep
from app.core.security import create_access_token
from app.integrations.azure_ad import verify_azure_ad_token
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    AzureAdLoginRequest,
    LoginResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/azure-ad/login", response_model=LoginResponse)
async def azure_ad_login(payload: AzureAdLoginRequest, db: DbDep) -> LoginResponse:
    """Exchange Azure AD ID token for app access token."""
    try:
        claims = await verify_azure_ad_token(payload.id_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Azure AD token",
        ) from exc

    email = claims.get("email") or claims.get("preferred_username")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain email",
        )

    repo = UserRepository(db)
    user = await repo.get_by_email(email.lower())

    if user is None:
        user = await repo.create({
            "email": email.lower(),
            "full_name": claims.get("name", email),
            "auth_provider": "azure_ad",
            "password_hash": None,
            "roles": _map_groups_to_roles(claims.get("groups", [])),
            "is_active": True,
            "email_verified": True,
        })
        await db.commit()
    elif user.auth_provider != "azure_ad":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered with different auth provider",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    access_token = create_access_token(
        subject=str(user.id), extra_claims={"auth_provider": "azure_ad"}
    )
    return LoginResponse(access_token=access_token, token_type="bearer")


def _map_groups_to_roles(group_ids: list[str]) -> list[str]:
    """Map Azure AD group IDs to app roles.

    Configure mapping in project's environment-specific config.
    Default: empty roles (must be assigned by admin).
    """
    GROUP_TO_ROLE: dict[str, str] = {
        # "00000000-0000-0000-0000-000000000001": "internal:admin",
    }
    return [GROUP_TO_ROLE[g] for g in group_ids if g in GROUP_TO_ROLE]
```

### 3.3 Frontend MSAL Setup

**`src/lib/auth.ts`:**

```typescript
import { PublicClientApplication, type Configuration } from '@azure/msal-browser';

const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_AZURE_AD_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_AZURE_AD_TENANT_ID}`,
    redirectUri: import.meta.env.VITE_AZURE_AD_REDIRECT_URI,
    postLogoutRedirectUri: '/login',
  },
  cache: {
    cacheLocation: 'localStorage',
    storeAuthStateInCookie: false,
  },
};

export const msalInstance = new PublicClientApplication(msalConfig);
await msalInstance.initialize();

export const loginRequest = {
  scopes: ['openid', 'profile', 'email', 'User.Read'],
};
```

**`src/features/auth/AzureAdLoginButton.tsx`:**

```tsx
import { useMsal } from '@azure/msal-react';
import { useTranslation } from 'react-i18next';
import { apiClient } from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import { loginRequest } from '@/lib/auth';

export function AzureAdLoginButton() {
  const { t } = useTranslation();
  const { instance } = useMsal();
  const setAuth = useAuthStore((s) => s.setAuth);

  const handleLogin = async () => {
    const result = await instance.loginPopup(loginRequest);
    const { data } = await apiClient.post('/auth/azure-ad/login', {
      idToken: result.idToken,
    });
    setAuth(data.accessToken, data.user);
  };

  return <button onClick={handleLogin}>{t('auth.loginWithMicrosoft')}</button>;
}
```

---

## 4. External Users (Local Auth)

### 4.1 Password Hashing

**`app/core/security.py`:**

```python
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return _pwd_context.verify(plain_password, password_hash)


def create_access_token(
    *, subject: str, extra_claims: dict[str, Any] | None = None
) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    claims: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(UTC),
        "type": "access",
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(*, subject: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    claims = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(UTC),
        "type": "refresh",
    }
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_jwt(token: str, secret: str, algorithm: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=[algorithm])
```

### 4.2 Admin Invite Flow

```python
@router.post(
    "/admin/invite-external",
    dependencies=[Depends(require_role("internal:admin"))],
)
async def invite_external_user(
    payload: InviteRequest,
    db: DbDep,
    background_tasks: BackgroundTasks,
) -> dict:
    repo = UserRepository(db)
    existing = await repo.get_by_email(payload.email.lower())
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = await repo.create({
        "email": payload.email.lower(),
        "full_name": payload.full_name,
        "auth_provider": "local",
        "password_hash": None,
        "roles": payload.roles,  # must all start with "external:"
        "is_active": False,
        "email_verified": False,
    })

    invite_token = create_invite_token(user.id)
    await db.commit()

    background_tasks.add_task(send_invite_email, user.email, invite_token)

    return {"message": "Invitation sent", "user_id": str(user.id)}


def create_invite_token(user_id: str) -> str:
    """Single-use token, expires in 48 hours."""
    settings = get_settings()
    claims = {
        "sub": str(user_id),
        "type": "invite",
        "exp": datetime.now(UTC) + timedelta(hours=48),
    }
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
```

### 4.3 Password Set + Email Verification

```python
@router.post("/accept-invite")
async def accept_invite(payload: AcceptInviteRequest, db: DbDep) -> LoginResponse:
    settings = get_settings()
    try:
        claims = jwt.decode(
            payload.invite_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if claims.get("type") != "invite":
            raise ValueError("Wrong token type")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired invite") from exc

    repo = UserRepository(db)
    user = await repo.get_by_id(claims["sub"])
    if user is None or user.auth_provider != "local":
        raise HTTPException(status_code=400, detail="User not found")
    if user.password_hash is not None:
        raise HTTPException(status_code=400, detail="Invite already used")

    # hash_password() enforces the strength policy (raises PasswordPolicyError);
    # pass the email local-part as a context term so it can't appear in the pw.
    try:
        user.password_hash = hash_password(
            payload.password, context_terms=[user.email.split("@", 1)[0]]
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user.email_verified = True
    user.is_active = True
    await db.commit()

    access_token = create_access_token(
        subject=str(user.id), extra_claims={"auth_provider": "local"}
    )
    return LoginResponse(access_token=access_token, token_type="bearer")


# Strength policy ("Easy + blocklist") lives in ONE place — the scaffolded
# `app/auth/password.py::validate_password` — and is invoked by hash_password().
# Mirror copy for the setup wizard: scripts/password_policy.py. Do NOT inline a
# third ad-hoc check here. Tuned for low friction on external (vendor/customer)
# users — length + blocklist carry the security (NIST SP 800-63B). It enforces:
#   • length ≥ 12
#   • ≥ 2 of 4 character classes (upper / lower / digit / symbol) → blocks
#     all-numeric AND all-letter, but a 12-char passphrase still passes easily
#   • rejects all-same-char, repeated patterns (123456123456), keyboard/number
#     sequences (123456789012), a common-password blocklist, and any context
#     term (email local-part) appearing inside the password
# Raises PasswordPolicyError → callers translate to HTTP 400 (see accept_invite).
```

### 4.4 Local Login

```python
@router.post("/local/login", response_model=LoginResponse)
async def local_login(payload: LocalLoginRequest, db: DbDep) -> LoginResponse:
    repo = UserRepository(db)
    user = await repo.get_by_email(payload.email.lower())

    # Always run hashing to prevent timing attacks (even if user is None)
    if user is None or user.auth_provider != "local" or user.password_hash is None:
        verify_password(payload.password, "$2b$12$" + "x" * 53)  # dummy hash
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Email not verified")

    # If 2FA enabled, require code
    if user.totp_secret is not None:
        if not payload.totp_code or not verify_totp(user.totp_secret, payload.totp_code):
            raise HTTPException(status_code=401, detail="Invalid 2FA code")

    access_token = create_access_token(
        subject=str(user.id), extra_claims={"auth_provider": "local"}
    )
    refresh_token = create_refresh_token(subject=str(user.id))
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
    )
```

### 4.5 Password Reset

```python
@router.post("/local/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: DbDep,
    background_tasks: BackgroundTasks,
) -> dict:
    repo = UserRepository(db)
    user = await repo.get_by_email(payload.email.lower())

    # Always return same response (don't leak if email exists)
    if user is not None and user.auth_provider == "local":
        reset_token = create_reset_token(str(user.id))
        background_tasks.add_task(send_password_reset_email, user.email, reset_token)

    return {"message": "If the email exists, a reset link has been sent"}


@router.post("/local/reset-password")
async def reset_password(payload: ResetPasswordRequest, db: DbDep) -> dict:
    settings = get_settings()
    try:
        claims = jwt.decode(
            payload.reset_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if claims.get("type") != "password_reset":
            raise ValueError()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired token") from exc

    repo = UserRepository(db)
    user = await repo.get_by_id(claims["sub"])
    if user is None or user.auth_provider != "local":
        raise HTTPException(status_code=400, detail="User not found")

    try:
        user.password_hash = hash_password(
            payload.new_password, context_terms=[user.email.split("@", 1)[0]]
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()

    return {"message": "Password updated"}
```

---

## 5. Optional 2FA (TOTP)

```python
import pyotp


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_provisioning_uri(secret: str, email: str, issuer: str) -> str:
    """issuer = project display name (from project.config PROJECT_DISPLAY_NAME)"""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)
```

Endpoint:

```python
@router.post("/local/2fa/setup")
async def setup_2fa(user: CurrentUser, db: DbDep, settings: SettingsDep) -> dict:
    if user.auth_provider != "local":
        raise HTTPException(status_code=400, detail="2FA only supported for local accounts")
    secret = generate_totp_secret()
    user.totp_secret = secret
    await db.commit()
    return {
        "secret": secret,
        "provisioning_uri": get_totp_provisioning_uri(
            secret, user.email, settings.APP_NAME
        ),
    }
```

---

## 6. Verifying Tokens (Middleware)

```python
async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbDep,
    settings: SettingsDep,
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing token")

    try:
        payload = decode_jwt(
            credentials.credentials, settings.JWT_SECRET_KEY, settings.JWT_ALGORITHM
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    user_id = payload.get("sub")
    auth_provider = payload.get("auth_provider")
    if not user_id or auth_provider not in {"azure_ad", "local"}:
        raise HTTPException(status_code=401, detail="Invalid token claims")

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    if user.auth_provider != auth_provider:
        raise HTTPException(status_code=401, detail="Auth provider mismatch")

    return user
```

---

## 7. Logout

### 7.1 Backend

```python
@router.post("/logout")
async def logout(user: CurrentUser, db: DbDep) -> dict:
    return {"message": "Logged out"}
```

### 7.2 Frontend

```tsx
import { useMsal } from '@azure/msal-react';
import { apiClient } from '@/api/client';
import { useAuthStore } from '@/stores/authStore';

export function useLogout() {
  const { instance } = useMsal();
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const user = useAuthStore((s) => s.user);

  return async () => {
    try {
      await apiClient.post('/auth/logout');
    } finally {
      clearAuth();
      if (user?.authProvider === 'azure_ad') {
        await instance.logoutRedirect();
      } else {
        window.location.href = '/login';
      }
    }
  };
}
```

---

## 8. Token Lifetime Defaults

| Token | Lifetime | Notes |
|---|---|---|
| Azure AD ID Token | 1 hour | Managed by Azure AD |
| App Access Token | 60 minutes | Configurable |
| Refresh Token (local) | 7 days | Configurable |
| Invite Token | 48 hours | Single-use |
| Password Reset Token | 1 hour | Single-use |
| TOTP Code | 30 seconds | Standard TOTP |

---

## 9. RBAC (Role-Based Access Control)

### 9.1 Defining Permissions

```python
@router.delete(
    "/products/{id}",
    dependencies=[Depends(require_role("internal:admin", "internal:product_manager"))],
)
async def delete_product(
    product_id: str, db: DbDep, user: CurrentUser
) -> None:
    # implement: delete product with permission check
    ...
```

### 9.2 Combining Filters (BU + Role)

```python
async def get_product(self, product_id: str, *, user: User) -> Product:
    product = await self.repo.get_by_id(product_id)
    if product is None:
        raise NotFoundError()
    if user.auth_provider == "local" and not product.is_public:
        raise PermissionError()
    if (
        user.auth_provider == "azure_ad"
        and product.business_unit_id not in user.business_unit_ids
    ):
        raise PermissionError()
    return product
```

---

## 10. Hard Rules (Non-Negotiables)

1. **ห้าม** internal user ใช้ local auth
2. **ห้าม** external user เข้า Azure AD
3. **ห้าม** mix role prefix
4. **ห้าม** auto-provision external users — admin invite only
5. **ห้าม** disable email verification สำหรับ local users
6. **ห้าม** log passwords, tokens, TOTP secrets
7. **ห้าม** return user.password_hash ใน API response
8. **ห้าม** ใช้ algorithm `none` ใน JWT
9. **ห้าม** ใช้ session cookies ที่ไม่มี `Secure` + `HttpOnly` + `SameSite=Lax` ใน production

---

## 11. Bootstrap Super Admin

ครั้งแรกที่ scaffold project ใหม่ — wizard ([`scripts/setup.py`](../scripts/setup.py)) ถาม:

1. **Bootstrap super admin email** — required ถ้า `AUTH_SCOPE != external_only`
2. **Auth type:** `1) SSO` (Azure AD) หรือ `2) Local` (email + password)
3. **Initial password** (เฉพาะ local) — masked input ผ่าน `getpass`, บังคับ policy
   เดียวกับ runtime (≥ 12 ตัว + ≥ 2 ใน 4 กลุ่มอักขระ + บล็อก common/sequence/
   ตัวอักษรล้วน-เลขล้วน + ห้ามมี email local-part) ผ่าน `scripts/password_policy.py`

Wizard เขียนค่าลง `backend/.env`:

```bash
AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL=admin@chiataigroup.com
AUTH_BOOTSTRAP_SUPER_ADMIN_AUTH_TYPE=local       # หรือ "sso"
AUTH_BOOTSTRAP_INITIAL_PASSWORD=bootstrap-temp-12chars   # เฉพาะ local — one-shot
```

Email ถูก normalize เป็น lowercase ตั้งแต่ wizard และ seed เพื่อให้ `users.email`
มีรูปแบบเดียวกันเสมอ และ local login เช็คแบบไม่สนตัวพิมพ์เล็ก/ใหญ่ของ email
(password ยัง case-sensitive ตามปกติ). Migration `0010_normalize_user_emails`
จะ lower-case email ที่มีอยู่แล้วตอน upgrade; ถ้าเจอ duplicate ที่ต่างกันแค่
ตัวพิมพ์เล็ก/ใหญ่ migration จะหยุดเพื่อให้ admin merge account เองอย่างตั้งใจ

แล้ว seed ([`backend/app/seed.py`](../backend/app/seed.py)) อ่านค่าทั้ง 3 ตัวตอน `python -m app.seed`:

- สร้าง row ใน `users` table (`auth_provider=local|azure_ad`, `is_active=true`, `email_verified=true`)
- ถ้า local → bcrypt hash password เก็บใน `password_hash`
- assign role `internal:super_admin`
- **Idempotent:** ถ้ามี user email นี้แล้ว — ไม่แตะ (รัน seed ซ้ำได้ปลอดภัย)

> ⚠️ **Security:** หลัง seed รันสำเร็จ — **ลบ `AUTH_BOOTSTRAP_INITIAL_PASSWORD` ออกจาก `.env` ทันที** (super admin ควรเปลี่ยน password ผ่าน UI หลัง login ครั้งแรก) `print_summary` ใน wizard เตือนข้อนี้

---

## 12. Runtime Provider Toggle (App Settings)

[`app_settings`](../docs/admin-config.md#pattern-c-app-settings-required-l1) table เก็บ 4 keys สำหรับ provider toggle:

| Key | Type | Default (seed) | Effect |
|---|---|---|---|
| `auth.local.enabled` | bool | `true` ถ้า `AUTH_SCOPE != internal_only` | Login form (email+password) บนหน้า `/login` |
| `auth.sso.enabled` | bool | `true` ถ้า `AUTH_SCOPE != external_only` | ปุ่ม "Sign in with Microsoft" บนหน้า `/login` |
| `auth.local.signup_enabled` | bool | `false` | เปิด public signup endpoint (default = admin-invite only) |
| `auth.signup_default_role` | string | `external:user` | Role ที่ assign ให้ user ที่ signup เอง |

**Endpoint:**

```
GET  /api/v1/admin/settings              # list ทุก setting (require admin_settings.read)
PUT  /api/v1/admin/settings/{key}         # update (require admin_settings.update)
GET  /api/v1/admin/settings/public        # auth-free subset — login page อ่านก่อน user known
```

`/admin/settings/public` คืน camelCase keys สำหรับ frontend:

```json
{ "authLocalEnabled": true, "authSsoEnabled": true }
```

### 12.1 AUTH_SCOPE Ceiling (Compile-Time)

`AUTH_SCOPE` ใน `project.config` / `.env` เป็น **ceiling** — runtime toggle เกินไม่ได้:

| `AUTH_SCOPE` | `auth.local.enabled` | `auth.sso.enabled` |
|---|---|---|
| `internal_only` | locked = `false` (PUT ปฏิเสธ 400) | toggle ได้อิสระ |
| `external_only` | toggle ได้อิสระ | locked = `false` (PUT ปฏิเสธ 400) |
| `both` (default) | toggle ได้อิสระ | toggle ได้อิสระ |

Backend logic: `backend/app/api/v1/admin_settings.py::_scope_locked_value()` reject write ที่ขัด ceiling พร้อม `400 BAD_REQUEST` (`"Setting ... is locked by AUTH_SCOPE env"`)

Frontend (`frontend/src/pages/settings/AuthSettings.tsx`) อ่าน `import.meta.env.VITE_AUTH_SCOPE` แล้ว disable toggle ที่ ceiling ปิดไว้ — UX ดีกว่าให้ PUT แล้วเด้ง 400

### 12.2 ใครเปลี่ยน toggle ได้

Default: เฉพาะ role ที่มี permission `admin_settings.update` — ตามค่าเริ่มต้น = `internal:super_admin` เท่านั้น

ถ้าต้อง delegate ให้ role อื่น (เช่น `internal:admin`) — super admin grant permission ที่ `/settings/roles`

---

## 13. Quick Reference: เมื่อ AI ได้รับ task

| Task | Steps |
|---|---|
| เพิ่ม endpoint ที่ต้อง auth | Add `CurrentUser` to dependency injection |
| เพิ่ม endpoint ที่ต้อง specific role | Add `dependencies=[Depends(require_role("..."))]` |
| Onboarding internal user | ไม่ต้องทำอะไร — auto-provision เมื่อ login Azure AD ครั้งแรก |
| Onboarding external user | สร้าง admin endpoint หรือ UI ที่ invite + send email |
| เปลี่ยน auth logic | **High-risk operation** — ขอ confirm จาก user ก่อน |
| Add 2FA | Only for `auth_provider == "local"` users |
