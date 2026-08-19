"""User CRUD — internal:admin+ for write, internal/external admins for read."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import CurrentUser, require_permission
from app.auth.password import PasswordPolicyError, hash_password
from app.auth.permissions import PermissionKey
from app.db.models.permission import Permission
from app.db.models.role import Role
from app.db.models.user import User
from app.db.models.user_permission_override import UserPermissionOverride
from app.db.session import get_db
from app.schemas.auth import (
    AdminPasswordResetRequest,
    AdminPasswordResetResult,
    BulkApproveRequest,
    OverrideRequest,
    UserCreate,
    UserOverrideRead,
    UserRead,
    UserSummary,
    UserUpdate,
)
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["users"])

SUPER_ADMIN_ROLE = "internal:super_admin"


def _require_role_assign(caller: User, target_role_names: list[str]) -> None:
    """Gate role assignments — kept separate from users.update so admins
    can edit profile fields without being able to silently elevate anyone
    (incl. themselves) by passing role_names through the same payload.

    Two checks:
      1. Caller must hold `roles.assign` — separate from `users.update`.
      2. Caller cannot grant `internal:super_admin` unless they ARE one.
         Otherwise an admin with roles.assign could bootstrap any account
         (or their own) to super-admin.
    """
    caller_perms: set[str] = getattr(caller, "_effective_permissions", set())
    if "roles.assign" not in caller_perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing permission: roles.assign",
        )
    if SUPER_ADMIN_ROLE in set(target_role_names):
        caller_role_names = {r.name for r in caller.roles}
        if SUPER_ADMIN_ROLE not in caller_role_names:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only super_admin can grant the super_admin role",
            )


@router.get("", response_model=list[UserSummary], dependencies=[
    Depends(require_permission(PermissionKey.USERS_READ))
])
async def list_users(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
) -> list[UserSummary]:
    stmt = (
        select(User)
        .options(selectinload(User.roles))
        .order_by(User.created_at.desc())
    )
    # Free-text search — the SPA's Users page sends ?q=. Match on email OR
    # full_name (case-insensitive). `|` builds an OR clause without needing
    # an or_() import. Filter BEFORE limit/offset so pagination is correct.
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(User.email.ilike(pattern) | User.full_name.ilike(pattern))
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return [UserSummary.model_validate(u) for u in result.scalars().all()]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.USERS_CREATE))])
async def create_user(
    payload: UserCreate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    if payload.auth_provider not in ("local", "azure_ad"):
        raise HTTPException(status_code=400, detail="Invalid auth_provider")
    normalized_email = payload.email.strip().lower()
    existing = await db.execute(select(User).where(func.lower(User.email) == normalized_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email already exists")

    # Admin-created users are approved by default — the admin\'s act of
    # creating them IS the approval. Pre-creating gated accounts (e.g.
    # for bulk imports that admins need to review individually) is
    # supported via payload.require_approval=True, which also triggers
    # the admin notification queue (so the reviewing admin gets pinged
    # via email per app_settings.notifications.email.enabled) AND
    # generates a one-time approval token used by the link-driven
    # approve/reject flow on /approve/<token>.
    is_approved = not payload.require_approval
    approval_token_raw: str | None = None
    approval_token_hash: str | None = None
    approval_token_expires_at: datetime | None = None
    if payload.require_approval:
        import hashlib, secrets as _secrets
        approval_token_raw = _secrets.token_urlsafe(32)
        approval_token_hash = hashlib.sha256(approval_token_raw.encode("utf-8")).hexdigest()
        # 7-day TTL — see app_settings.notifications.approval_link_ttl_days
        # (resolved server-side later; we use the env default here so the
        # token can be created from a single sync code path).
        approval_token_expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    # Validate supplier exists when supplier_id provided
    if payload.supplier_id is not None:
        from sqlalchemy import select as _select
        from app.db.models.supplier import Supplier as _Supplier
        sup = (await db.execute(_select(_Supplier).where(_Supplier.id == payload.supplier_id))).scalar_one_or_none()
        if sup is None:
            raise HTTPException(status_code=400, detail="Supplier not found")

    new_user = User(
        email=normalized_email,
        full_name=payload.full_name,
        auth_provider=payload.auth_provider,
        is_active=True,
        is_approved=is_approved,
        approval_token_hash=approval_token_hash,
        approval_token_expires_at=approval_token_expires_at,
        business_unit_ids=payload.business_unit_ids,
        supplier_id=payload.supplier_id,
        is_supplier_admin=payload.is_supplier_admin,
    )
    if payload.auth_provider == "local":
        if not payload.password:
            raise HTTPException(status_code=400, detail="Password required for local auth")
        # Enforce the strength policy here so a weak password is a clean 400,
        # not an uncaught PasswordPolicyError → 500. email local-part is a
        # context term (trivially guessable for this account).
        try:
            new_user.password_hash = hash_password(
                payload.password, context_terms=[normalized_email.split("@", 1)[0]]
            )
        except PasswordPolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.role_names:
        _require_role_assign(user, payload.role_names)
        roles_result = await db.execute(
            select(Role).where(Role.name.in_(payload.role_names))
        )
        new_user.roles = list(roles_result.scalars().all())

    db.add(new_user)
    await db.flush()

    audit = ActivityLogger(db)
    await audit.log(
        action="user.created", action_type="create", resource_type="user",
        resource_id=str(new_user.id), user=user, request=request, risk_level="medium",
    )
    # Pending-approval path → ping admins so they don\'t miss the queue.
    # Fire-and-forget; the dispatcher swallows transport failures.
    # Pass the RAW token (not the hash) — the email needs to put it in URLs.
    if not is_approved:
        from app.services.notifications import notify_admin_new_signup
        await notify_admin_new_signup(
            db, user_email=new_user.email, user_name=new_user.full_name,
            approval_token=approval_token_raw,
        )
    return await _load_user(db, new_user.id)


async def _load_user(db: AsyncSession, user_id: UUID) -> UserRead:
    stmt = (
        select(User).where(User.id == user_id)
        .options(selectinload(User.roles))
    )
    result = await db.execute(stmt)
    found = result.scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="User not found")
    read = UserRead.model_validate(found)
    # permission_overrides is lazy="selectin" (already loaded); flatten it to
    # {permissionKey, granted} so the SPA can render grant/revoke state.
    read.overrides = [
        UserOverrideRead(permission_key=o.permission.key, granted=o.granted)
        for o in found.permission_overrides
    ]
    return read


@router.get("/{user_id}", response_model=UserRead, dependencies=[
    Depends(require_permission(PermissionKey.USERS_READ))
])
async def get_user(user_id: UUID, db: AsyncSession = Depends(get_db)) -> UserRead:
    return await _load_user(db, user_id)


@router.patch("/{user_id}", response_model=UserRead, dependencies=[
    Depends(require_permission(PermissionKey.USERS_UPDATE))
])
async def patch_user(
    user_id: UUID,
    payload: UserUpdate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Self-update guard (Deep-Audit HIGH-2). Even a holder of users.update +
    # users.approve must not be able to flip privileged fields on their own
    # account. Self profile-edit (full_name only) still flows through /me
    # if a project wires it; this endpoint stays admin-only.
    is_self = target.id == user.id
    caller_perms: set[str] = getattr(user, "_effective_permissions", set())

    if payload.is_approved is not None:
        # Approval transitions need a SEPARATE perm so users.update on its
        # own can\'t bypass the approval queue. Closes Deep-Audit HIGH-2.
        if "users.approve" not in caller_perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing permission: users.approve",
            )
        if is_self:
            # No self-approval, ever — even if you somehow hold users.approve.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot change approval state on your own account",
            )

    if payload.is_active is not None and is_self:
        # No self-deactivation through the edit endpoint — defends against
        # an admin accidentally locking themselves out AND blocks a
        # compromised-token attacker from disabling the legit owner.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change active state on your own account",
        )

    if payload.role_names is not None and is_self:
        # No self role change — would let an admin elevate themselves.
        # _require_role_assign still runs for non-self callers below.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change roles on your own account",
        )

    if payload.full_name is not None:
        target.full_name = payload.full_name
    if payload.is_active is not None:
        target.is_active = payload.is_active
    if payload.is_approved is not None:
        target.is_approved = payload.is_approved
    if payload.business_unit_ids is not None:
        target.business_unit_ids = payload.business_unit_ids
    if payload.role_names is not None:
        _require_role_assign(user, payload.role_names)
        roles_result = await db.execute(
            select(Role).where(Role.name.in_(payload.role_names))
        )
        target.roles = list(roles_result.scalars().all())
    # FarmLog supplier linkage
    if "supplier_id" in payload.model_fields_set:
        if payload.supplier_id is not None:
            from app.db.models.supplier import Supplier as _Supplier
            sup = (await db.execute(select(_Supplier).where(_Supplier.id == payload.supplier_id))).scalar_one_or_none()
            if sup is None:
                raise HTTPException(status_code=400, detail="Supplier not found")
        target.supplier_id = payload.supplier_id
    if payload.is_supplier_admin is not None:
        target.is_supplier_admin = payload.is_supplier_admin

    audit = ActivityLogger(db)
    await audit.log(
        action="user.updated", action_type="update", resource_type="user",
        resource_id=str(target.id), user=user, request=request, risk_level="low",
    )
    return await _load_user(db, target.id)


@router.post("/bulk-approve", dependencies=[
    Depends(require_permission(PermissionKey.USERS_APPROVE))
])
async def bulk_approve(
    payload: BulkApproveRequest,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Flip is_approved=true on every user_id in payload — idempotent.
    Already-approved rows are no-ops. Single audit row per call for the
    bulk batch; per-row trail lives in updated_at.

    Side effect: a notification email goes to every user that flipped
    from pending → approved (toggleable via
    app_settings.notifications.email.enabled). We collect the rows
    BEFORE commit then dispatch after, so a transient mail-send hiccup
    can\'t roll back the approval audit row.
    """
    if not payload.user_ids:
        return {"updated": 0}
    # Self-approval guard (Deep-Audit HIGH-2): silently drop the caller\'s
    # own id from the batch so an admin can\'t bootstrap themselves by
    # POSTing {user_ids: [self.id]}. Same defence pattern as patch_user.
    target_ids = [uid for uid in payload.user_ids if uid != user.id]
    if not target_ids:
        return {"updated": 0}
    result = await db.execute(select(User).where(User.id.in_(target_ids)))
    targets = list(result.scalars().all())
    just_approved: list[tuple[str, str | None]] = []
    changed = 0
    for t in targets:
        if not t.is_approved:
            t.is_approved = True
            changed += 1
            just_approved.append((t.email, t.full_name))
    audit = ActivityLogger(db)
    await audit.log(
        action="user.bulk_approved", action_type="update", resource_type="user",
        user=user, request=request, risk_level="medium",
        metadata={"count": changed, "requested": len(payload.user_ids)},
    )
    # Fire-and-forget approval notifications. dispatcher swallows errors.
    from app.services.notifications import notify_user_approval_granted
    for email, name in just_approved:
        await notify_user_approval_granted(db, user_email=email, user_name=name)
    return {"updated": changed}


@router.post("/{user_id}/deactivate", dependencies=[
    Depends(require_permission(PermissionKey.USERS_DEACTIVATE))
])
async def deactivate_user(
    user_id: UUID,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    # Self-deactivation guard (Deep-Audit HIGH-2): an admin must not lock
    # themselves out through this endpoint — covers the same surface as
    # patch_user.is_active above.
    if user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot deactivate your own account",
        )
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    target.is_active = False
    audit = ActivityLogger(db)
    await audit.log(
        action="user.deactivated", action_type="update", resource_type="user",
        resource_id=str(target.id), user=user, request=request, risk_level="medium",
    )
    return {"status": "ok"}


# --- Admin password reset (round 8-23A) ---------------------------------
#
# Every message below is fixed Thai text. None of them contains the
# submitted password, its length, or which policy rule it broke — the
# response body and the audit row are both attacker-visible surfaces.
_MSG_RESET_SELF = "ไม่สามารถตั้งรหัสผ่านใหม่ให้บัญชีของตนเองผ่านหน้าจัดการผู้ใช้ได้"
_MSG_RESET_AZURE = (
    "บัญชีนี้เข้าสู่ระบบผ่าน Microsoft (Azure AD) "
    "กรุณาจัดการรหัสผ่านผ่านระบบของ Microsoft"
)
_MSG_RESET_NOT_LOCAL = "บัญชีนี้ไม่ได้ใช้รหัสผ่านของระบบ จึงตั้งรหัสผ่านใหม่ไม่ได้"
_MSG_RESET_BAD_INPUT = "รูปแบบรหัสผ่านไม่ถูกต้อง"
_MSG_RESET_POLICY = (
    "รหัสผ่านไม่ผ่านเกณฑ์ความปลอดภัย — ต้องยาวอย่างน้อย 12 ตัวอักษร "
    "และไม่เกิน 72 ไบต์เมื่อเข้ารหัสแบบ UTF-8 (ตัวอักษรที่ไม่ใช่ ASCII เช่นภาษาไทย "
    "นับมากกว่า 1 ไบต์ต่อตัว), "
    "ผสมอย่างน้อย 2 ประเภท (พิมพ์ใหญ่ / พิมพ์เล็ก / ตัวเลข / สัญลักษณ์), "
    "ไม่ใช่รหัสที่คาดเดาง่ายหรือเรียงตามแป้นพิมพ์ "
    "และต้องไม่มีอีเมลของผู้ใช้อยู่ในรหัสผ่าน"
)
# Round 8-23A.1 — this is ONLY a coarse request-size/DoS guard (an
# unbounded string is needless work to even count/encode before it is
# ever hashed). It is deliberately generous and MUST NOT be read as the
# real bcrypt limit: bcrypt's actual boundary is 72 BYTES of UTF-8, not
# 200 characters, and up to 3 bytes/char for non-ASCII text (Thai, etc.)
# means a string well under this cap can still exceed 72 bytes. The
# authoritative byte-length check is app.auth.password.validate_password's
# MAX_PASSWORD_BYTES gate, called (via hash_password) below — this
# constant only rejects a pathologically long input before that call.
_RESET_PASSWORD_MAX_LEN = 200


@router.post("/{user_id}/reset-password", response_model=AdminPasswordResetResult,
             dependencies=[
                 Depends(require_permission(PermissionKey.USERS_RESET_PASSWORD))
             ])
async def reset_user_password(
    user_id: UUID,
    payload: AdminPasswordResetRequest,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> AdminPasswordResetResult:
    """Admin sets a new password on a LOCAL account (round 8-23A).

    Gated by users.reset_password — a SEPARATE permission from
    users.update, because setting someone's password is account takeover.
    Seeded to internal:super_admin only.

    Touches exactly two columns — password_hash and auth_version — under a
    row lock, in the single transaction get_db commits. Email, roles,
    supplier, approval, and active state are never read for writing here.

    Bumping auth_version is what makes the old sessions dead: every
    access/refresh token carries the generation it was minted at, and both
    get_current_user and /auth/refresh reject a mismatch fail-closed. This
    is NOT the self-service email flow — /api/v1/auth/password-reset stays
    a separate (still stubbed) endpoint and is not repurposed here.
    """
    # Self-reset guard first: cheap, and it leaks nothing (the caller
    # necessarily already knows their own id). An admin changing their own
    # password must go through the self-service flow, so that a stolen
    # admin session cannot quietly lock the real owner out of their account
    # while keeping itself alive. Same family as the patch_user /
    # deactivate_user / add_override self-guards.
    if user_id == user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_MSG_RESET_SELF)

    # Lock the row for the whole read-modify-write. Two concurrent resets
    # therefore serialise here, and the second one computes its increment
    # from the first one's committed value — no lost update. Same shape as
    # plot_access_credential_repository's credential_version bump.
    target = (await db.execute(
        select(User).where(User.id == user_id).with_for_update()
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target.auth_provider != "local":
        raise HTTPException(
            status_code=400,
            detail=_MSG_RESET_AZURE if target.auth_provider == "azure_ad"
            else _MSG_RESET_NOT_LOCAL,
        )

    # Hand-written input checks — see AdminPasswordResetRequest's docstring
    # for why this cannot be a pydantic constraint. Both branches answer
    # with the SAME fixed message and never include the value.
    new_password = payload.new_password
    if not isinstance(new_password, str) or not new_password:
        raise HTTPException(status_code=400, detail=_MSG_RESET_BAD_INPUT)
    if len(new_password) > _RESET_PASSWORD_MAX_LEN:
        raise HTTPException(status_code=400, detail=_MSG_RESET_BAD_INPUT)

    # 400 matches create_user's existing PasswordPolicyError convention.
    # The message is a fixed Thai summary of the policy — deliberately NOT
    # str(exc), which would reveal which specific rule the candidate broke.
    try:
        new_hash = hash_password(
            new_password, context_terms=[target.email.split("@", 1)[0]]
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=_MSG_RESET_POLICY) from exc

    target.password_hash = new_hash
    target.auth_version = (target.auth_version or 0) + 1
    await db.flush()

    # Security event, high risk. The ONLY identifiers recorded are the two
    # user ids (actor via `user=`, target via resource_id) — never the
    # password, its hash, its length, or the request body.
    await ActivityLogger(db).log(
        action="user.password_reset",
        action_type="update",
        resource_type="user",
        resource_id=str(target.id),
        user=user,
        request=request,
        is_security_event=True,
        risk_level="high",
    )

    return AdminPasswordResetResult(
        user_id=target.id,
        auth_version=target.auth_version,
    )


@router.post("/{user_id}/overrides", response_model=UserRead, dependencies=[
    Depends(require_permission(PermissionKey.PERMISSIONS_GRANT_OVERRIDE))
])
async def add_override(
    user_id: UUID,
    payload: OverrideRequest,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Round-4 HIGH-4: self-elevation guard. The HIGH-2 round-3 fix gated
    # patch_user / bulk_approve / deactivate_user against self-id, but
    # add_override (the sibling endpoint) had no such check. A holder of
    # permissions.grant_override could otherwise grant themselves
    # users.approve, admin_settings.update, or any other action perm —
    # completely bypassing the HIGH-2 self-update guard.
    if target_user.id == user.id:
        # Audit the denied attempt BEFORE raising so the row survives the
        # request rollback. ActivityLogger commits when is_security_event
        # is True via the same path _audit_permission_denied uses.
        await ActivityLogger(db).log(
            action="user.override_self_blocked", action_type="role_change",
            resource_type="user", resource_id=str(target_user.id),
            user=user, request=request,
            is_security_event=True, risk_level="high",
            metadata={
                "attempted_permission_key": payload.permission_key,
                "attempted_granted": payload.granted,
                "reason": "self_elevation_blocked",
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot grant permission overrides on your own account",
        )

    # Round-5 HIGH-2: privilege-management gate (expanded from Round-4).
    # The original Round-4 fix blocked only permissions.* keys, but a
    # holder of permissions.grant_override could still grant a
    # confederate any of:
    #   - users.approve     → bypasses HIGH-2 approval gate
    #   - users.delete      → consolidate power by deleting other admins
    #   - users.deactivate  → lock out other admins
    #   - admin_settings.update → redirect approval emails to attacker
    #   - roles.create/update/delete/assign → craft super-admin-equivalent
    #     role and assign it (assign is partly mitigated by
    #     _require_role_assign\'s super_admin guard, but defence in depth)
    #   - menus.delete      → destructive metadata change
    # The deny-list below is the complete set of overrides that require
    # super_admin. Anything not in this list (typical *.read keys, plus
    # any host-app feature perms) can still be granted by a non-super-
    # admin holder of permissions.grant_override.
    _PRIVILEGE_MANAGEMENT_KEYS = {
        "permissions.grant_override",
        "permissions.revoke_override",
        "admin_settings.update",
        "admin_settings.read",  # reading admin settings can leak SMTP/M365 creds
        "users.approve",
        "users.delete",
        "users.deactivate",
        "users.create",
        # Round 8-23A — setting another account's password IS account
        # takeover, so it can never be handed out via a per-user override
        # by a non-super-admin holder of permissions.grant_override.
        "users.reset_password",
        "roles.create", "roles.update", "roles.delete", "roles.assign",
        "menus.delete",
        # Database Connections module (opt-in) — stored external-DB credentials
        # + arbitrary SQL execution. super_admin only; never per-user grantable.
        "db_connections.read", "db_connections.manage", "db_connections.query",
    }

    if (
        payload.permission_key in _PRIVILEGE_MANAGEMENT_KEYS
        or payload.permission_key.startswith("permissions.")
    ):
        caller_role_names = {r.name for r in user.roles}
        if SUPER_ADMIN_ROLE not in caller_role_names:
            await ActivityLogger(db).log(
                action="user.override_priv_escalation_blocked",
                action_type="role_change",
                resource_type="user", resource_id=str(target_user.id),
                user=user, request=request,
                is_security_event=True, risk_level="high",
                metadata={
                    "attempted_permission_key": payload.permission_key,
                    "attempted_granted": payload.granted,
                    "reason": "non_super_admin_priv_management",
                },
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Only super_admin can grant permission overrides for "
                    "privilege-management keys"
                ),
            )

    perm = (
        await db.execute(select(Permission).where(Permission.key == payload.permission_key))
    ).scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=404, detail="Permission key unknown")

    # Upsert: one override row per (user, permission). Repeated grant/revoke
    # toggles the same row instead of stacking duplicates. Any historical
    # duplicates (from before this guard) are collapsed onto the first row.
    existing = (
        await db.execute(
            select(UserPermissionOverride).where(
                UserPermissionOverride.user_id == target_user.id,
                UserPermissionOverride.permission_id == perm.id,
            )
        )
    ).scalars().all()
    if existing:
        existing[0].granted = payload.granted
        existing[0].granted_by_user_id = user.id
        existing[0].reason = payload.reason
        for extra in existing[1:]:
            await db.delete(extra)
    else:
        db.add(UserPermissionOverride(
            user_id=target_user.id,
            permission_id=perm.id,
            granted=payload.granted,
            granted_by_user_id=user.id,
            reason=payload.reason,
        ))

    audit = ActivityLogger(db)
    await audit.log(
        action="user.override_added", action_type="role_change", resource_type="user",
        resource_id=str(target_user.id), user=user, request=request,
        is_security_event=True, risk_level="high",
        metadata={"permission_key": payload.permission_key, "granted": payload.granted},
    )
    return await _load_user(db, target_user.id)
