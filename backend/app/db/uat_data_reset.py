"""Safe UAT Data Reset + deterministic Supplier/Plot seed (round 8-25A,
corrected round 8-25A.1 — read this module's docstring before touching
anything, the ground rules changed twice already).

    python -m app.db.uat_data_reset                       # dry-run (default)
    python -m app.db.uat_data_reset --make-backup-only     # pg_dump only, no wipe
    python -m app.db.uat_data_reset --rehearse-restore PATH_TO_DUMP
    python -m app.db.uat_data_reset --apply --confirm-phrase "RESET SRM_FIELDINSPECT LOCAL UAT DATA"

Decisions locked in by the round 8-25A.1 approval gate (do not change
without a fresh round + fresh user approval):

  - Master Data type crop AND variety: DELETED (variety first, then crop —
    variety.parent references a crop value, deleted in dependency order even
    though there is no DB-level FK). Every OTHER master_data type is left
    untouched (checked as a pre-commit invariant, not just asserted).
  - After reset: suppliers=10, supplier:owner local users=10, plots=100,
    plot_cycles=0, records=0, master_data:crop=0, master_data:variety=0.
    Public Inspect is NOT usable until a future round imports Master Data
    and starts a planting cycle (round 8-25C).
  - activity_logs / system_logs / ai_call_logs: PRESERVED, never touched.
  - revoked_tokens: PARTIALLY touched — rows belonging to a DELETED
    candidate supplier:owner user are removed (their user_id FK is
    ON DELETE CASCADE, and this script also deletes them explicitly per the
    "never rely on cascade" rule); rows belonging to every other user are
    untouched. Never report this table as "not touched at all".
  - Existing inspection-photo files on disk: PRESERVED this round.
    Quarantine (move, never delete) is designed and unit-tested here but
    NOT executed against the real media root in this round — see
    `_quarantine_media_files`/`_restore_media_files` and the module-level
    "media" section below. No hard-delete of quarantined files exists
    anywhere in this file, by design.
  - Plot access phone: ONE synthetic phone number PER SUPPLIER, reused
    across all 10 of that supplier's plots (not one per plot — round
    8-25A.1 decision, supports a future multi-plot-per-phone flow).
  - Plot access password (PIN): NEVER derived from a supplier/plot index.
    Read from the environment variable UAT_PLOT_ACCESS_PASSWORD, validated
    via the SAME shared validator the real admin PUT uses
    (`app.auth.plot_access_password.validate_plot_access_password`), and
    used as-is for every one of the 100 credentials. A consequence worth
    stating plainly: since every credential is hashed from the same PIN,
    every plot_access_credentials.password_lookup_digest row this script
    writes is IDENTICAL (same pepper, same input) — acceptable for
    synthetic UAT data, called out explicitly in the PART I report.
  - Deletion scope for existing local users (round 8-25A.1 PART A fix,
    replacing 8-25A's broader "all 4 supplier:owner users" rule): a user is
    a deletion candidate ONLY if ALL of — auth_provider == "local", holds
    role 'supplier:owner', holds NO role whose name starts with "internal:",
    and is not the reserved system placeholder account. See
    `_is_deletion_candidate` (pure, unit-tested) — an Azure AD supplier
    owner, an internal+supplier dual-role account, and the system account
    are all EXCLUDED even though the first two hold 'supplier:owner'.
  - Protected, never touched: alembic_version, permissions, roles,
    role_permissions, menu_items, app_settings, field_definitions,
    inspection protocols, every user holding any `internal:*` role (in
    particular internal:super_admin), the `system` FK-placeholder user.
    Checked as pre-commit invariants (counts must be bit-for-bit unchanged),
    not just asserted in a comment.

Safety model:
  - Refuses to run unless DB_HOST is localhost/127.0.0.1 (same convention as
    app.db.seed_reset_farmlog_full) AND APP_ENV != "production".
  - Defaults to --dry-run: read-only queries + a full printed plan, zero
    writes, no backup taken.
  - --apply requires --confirm-phrase to match _CONFIRM_PHRASE EXACTLY,
    checked before anything else runs.
  - --apply: pg_dump (custom format, -Fc — restorable with pg_restore, round
    8-25A.1 PART G fix; 8-25A's plain-SQL dump had no tested restore
    contract) BEFORE opening any transaction, THEN an automatic restore
    rehearsal of that exact backup against a disposable
    `uat_restore_rehearsal_*` database (never DB_NAME) — if either the
    backup or the rehearsal fails, --apply aborts before the wipe/seed
    transaction ever opens. This is stronger than "we tested restore once
    in a previous round": every real --apply proves ITS OWN backup restores
    before risking anything.
  - The wipe + seed + pre-commit invariant check run inside ONE transaction,
    ONE commit at the very end. A failed invariant check RAISES (never
    silently continues), which — since app.db.session.get_db_session only
    rolls back on exception — rolls the whole transaction back with zero
    rows committed. A post-commit re-verification runs afterward as a
    second, independent safety net; if THAT fails, the process exits
    non-zero and never prints a "done"/"READY" line (the data is already
    committed by that point, so this is a loud alarm, not a gate).
  - Rerun-safe by construction: the wipe unconditionally precedes the seed
    inside that same transaction.
  - Both secrets (UAT_SUPPLIER_TEMP_PASSWORD, UAT_PLOT_ACCESS_PASSWORD) are
    validated against their respective shared policy BEFORE any DB write —
    a failure raises ResetAbortedError with a STATIC policy message and
    touches nothing. Neither value is ever printed, logged, or written to a
    file by this script.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import get_public_plot_rls_context
from app.auth.password import PasswordPolicyError, hash_password, validate_password
from app.auth.plot_access_password import (
    PlotAccessPasswordPolicyError,
    build_plot_access_password_lookup_digest,
    hash_plot_access_password,
    validate_plot_access_password,
)
from app.core.config import get_settings
from app.db.models.master_data import MasterData
from app.db.models.plot import Plot
from app.db.models.plot_access_credential import PlotAccessCredential
from app.db.models.plot_access_phone import ACCESS_TYPE_PRIMARY, PlotAccessPhone
from app.db.models.plot_assignment import PlotAssignment
from app.db.models.plot_cycle import PlotCycle
from app.db.models.record import Record
from app.db.models.revoked_token import RevokedToken
from app.db.models.role import Role
from app.db.models.supplier import Supplier
from app.db.models.user import User
from app.db.models.user_permission_override import UserPermissionOverride
from app.db.session import close_db, get_db_session, init_db
from app.services.plot_qr_key import generate_qr_key

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_CONFIRM_PHRASE = "RESET SRM_FIELDINSPECT LOCAL UAT DATA"
_ENV_USER_PASSWORD_VAR = "UAT_SUPPLIER_TEMP_PASSWORD"
_ENV_PLOT_PIN_VAR = "UAT_PLOT_ACCESS_PASSWORD"

_SUPPLIER_COUNT = 10
_PLOTS_PER_SUPPLIER = 10
_SUPPLIER_ROLE_NAME = "supplier:owner"

# Matches app.db.seed_reset_farmlog_full._SYSTEM_EMAILS — the one non-human
# FK-placeholder account. Kept as an independent constant (this script must
# stand alone) rather than importing from that one-off seed module.
_SYSTEM_EMAILS = frozenset({"external-field-helper@system.local"})

_DB_CONTAINER_NAME = "srm-fieldinspect-db"
_DEFAULT_BACKUP_DIR = Path(__file__).resolve().parents[2] / "scratchpad" / "uat_reset_backups"
_REHEARSAL_DB_PREFIX = "uat_restore_rehearsal_"

# Deterministic synthetic geography — never a real address/coordinate tied to
# an identifiable person. Independent copy of the same shape as
# app.db.seed_reset_farmlog_full._LOCATIONS — this script must stand alone.
_LOCATIONS: list[tuple[str, str, str, str, str]] = [
    ("เชียงใหม่", "แม่ริม", "บ้านสันโป่ง (UAT)", "18.9100", "98.9400"),
    ("เชียงราย", "เมือง", "บ้านป่าอ้อ (UAT)", "19.9100", "99.8300"),
    ("นครราชสีมา", "ปากช่อง", "บ้านหนองสาหร่าย (UAT)", "14.7000", "101.4160"),
    ("ขอนแก่น", "เมือง", "บ้านโนนม่วง (UAT)", "16.4320", "102.8360"),
    ("กาญจนบุรี", "ท่าม่วง", "บ้านหนองตากยา (UAT)", "13.9970", "99.6100"),
    ("ราชบุรี", "ปากท่อ", "บ้านห้วยยางโทน (UAT)", "13.3830", "99.6700"),
    ("จันทบุรี", "ท่าใหม่", "บ้านเขาบายศรี (UAT)", "12.6100", "102.1000"),
    ("เพชรบูรณ์", "หล่มสัก", "บ้านน้ำก้อ (UAT)", "16.7700", "101.2400"),
    ("สุพรรณบุรี", "อู่ทอง", "บ้านดอนคา (UAT)", "14.3700", "99.8900"),
    ("ประจวบคีรีขันธ์", "หัวหิน", "บ้านหนองพลับ (UAT)", "12.5700", "99.7900"),
]

_BUSINESS_TABLES = (
    ("suppliers", Supplier),
    ("plots", Plot),
    ("plot_cycles", PlotCycle),
    ("records", Record),
    ("plot_assignments", PlotAssignment),
    ("plot_access_phones", PlotAccessPhone),
    ("plot_access_credentials", PlotAccessCredential),
)


class ResetAbortedError(RuntimeError):
    """Raised for any pre-flight or invariant failure. Always caught at the
    top level and turned into a clean, non-zero-exit message — never a raw
    traceback that might repeat a secret-bearing local variable in its
    frame."""


class InvariantViolationError(ResetAbortedError):
    """A pre-commit invariant failed. Raised INSIDE the shared
    get_db_session() block, which only rolls back on exception — so raising
    this is what makes the rollback happen. Never caught anywhere except the
    top-level `if __name__` handler."""


# --------------------------------------------------------------------------
# Pure helpers (no DB, no filesystem) — unit-tested directly.
# --------------------------------------------------------------------------


def _synthetic_email(index: int) -> str:
    # .invalid is IANA-reserved (RFC 2606) — guaranteed to never route real
    # mail.
    return f"supplier{index:02d}.uat@example.invalid"


def _synthetic_supplier_phone(supplier_index: int) -> str:
    """ONE phone per supplier (round 8-25A.1 decision 1) — the SAME value is
    reused across all 10 of that supplier's plots. Matches PlotAccessPhone's
    CHECK constraint ^0[689][0-9]{8}$. This is a non-secret locator, not a
    credential, so deterministic index-derivation is fine here — the
    "never derive from index" rule (decision 6) applies to the PIN only.
    Collision-free for 1<=supplier_index<=10.
    """
    return f"08{supplier_index:08d}"


def _mask_email(email: str) -> str:
    """`ab***yz@example.invalid` — enough to eyeball "yes that's the right
    account family", never enough to reconstruct the real value. Used only
    in printed dry-run/preview output, never in the DB."""
    local, sep, domain = email.partition("@")
    if not local:
        return email
    if len(local) <= 2:
        masked = local[0] + "*"
    else:
        masked = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked}{sep}{domain}"


def _is_deletion_candidate(*, email: str, auth_provider: str, role_names: set[str]) -> bool:
    """PART A (round 8-25A.1) — the exact, narrow scope the user approved,
    replacing round 8-25A's broader "delete all 4 existing supplier:owner
    users" rule. A user is a candidate iff ALL of:
      - auth_provider == 'local' (an Azure AD account has no password_hash
        and was never a synthetic UAT throwaway to begin with)
      - holds the 'supplier:owner' role
      - holds NO role whose name starts with 'internal:' (a dual-role
        account — e.g. internal:auditor + supplier:owner — is treated as
        internal FIRST and excluded; this also covers
        internal:super_admin without needing a separate check)
      - is not the reserved system placeholder account

    Pure and side-effect-free so it can be unit-tested against every
    combination without a database.
    """
    if email in _SYSTEM_EMAILS:
        return False
    if auth_provider != "local":
        return False
    if _SUPPLIER_ROLE_NAME not in role_names:
        return False
    if any(name.startswith("internal:") for name in role_names):
        return False
    return True


# --------------------------------------------------------------------------
# Media quarantine (PART D) — reversible move, NEVER a delete. Designed and
# unit-tested this round; NOT invoked against the real media root this round
# (no --apply happens in round 8-25A.1 at all). A future round wires this
# into the apply path with a rollback-on-DB-failure call to
# `_restore_media_files`, and a SEPARATE, later round (8-25C, per the user's
# own decision 4) may add a hard-delete of an aged quarantine directory —
# that function does not exist anywhere in this file.
# --------------------------------------------------------------------------


def _scan_media(root: Path) -> tuple[int, int]:
    """Read-only. Returns (file_count, total_bytes). Never touches anything."""
    if not root.exists():
        return 0, 0
    count = 0
    total_bytes = 0
    for p in root.rglob("*"):
        if p.is_file():
            count += 1
            total_bytes += p.stat().st_size
    return count, total_bytes


def _quarantine_media_files(root: Path, quarantine_root: Path) -> list[tuple[Path, Path]]:
    """Move every top-level file under `root` into a fresh timestamped
    subdirectory of `quarantine_root`, preserving filenames. Reversible by
    construction — `_restore_media_files` replays the exact same pairs in
    reverse. Contains NO unlink/rmtree call anywhere: only `Path.rename`.

    Containment guard: refuses to run at all unless `root` resolves to an
    existing directory, and skips (does not abort the batch for) any entry
    whose resolved parent is not `root` itself — defends against a symlink
    escaping the media root, even though LocalPhotoStorage never nests or
    symlinks in practice.
    """
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise ResetAbortedError(
            f"Refusing to quarantine media: {resolved_root} is not a directory."
        )
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest_dir = quarantine_root / f"quarantine_{stamp}"
    moved: list[tuple[Path, Path]] = []
    for p in sorted(resolved_root.iterdir()):
        if not p.is_file():
            continue
        resolved_file = p.resolve()
        if resolved_file.parent != resolved_root:
            continue  # symlink/escape guard — skip, never abort the batch
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / resolved_file.name
        resolved_file.rename(dest)
        moved.append((resolved_file, dest))
    return moved


def _restore_media_files(moved: list[tuple[Path, Path]]) -> None:
    """Reverse `_quarantine_media_files` exactly — moves every file back to
    its original path, in reverse order. Best-effort per file (an entry
    already restored, or whose quarantined copy is missing, is silently
    skipped) so a partial failure never blocks restoring the rest. Called
    from the (future) apply path's except-block if the DB transaction fails
    after files were already quarantined."""
    for original, quarantined in reversed(moved):
        if not quarantined.exists():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        quarantined.rename(original)


def _assert_media_root_matches_config(root: Path) -> None:
    """Defense in depth: refuses to quarantine anything unless `root` is
    EXACTLY the configured INSPECTION_PHOTOS_DIR — never a caller-supplied
    path that merely looks similar."""
    configured = Path(get_settings().INSPECTION_PHOTOS_DIR).resolve()
    if root.resolve() != configured:
        raise ResetAbortedError(
            "Refusing to quarantine: target directory does not match the "
            "configured INSPECTION_PHOTOS_DIR."
        )


# --------------------------------------------------------------------------
# CLI / environment / secret gates
# --------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="Actually write to the database. Default is dry-run.")
    parser.add_argument("--confirm-phrase", default="",
                         help=f'Required with --apply. Must equal exactly: "{_CONFIRM_PHRASE}"')
    parser.add_argument("--backup-dir", default=str(_DEFAULT_BACKUP_DIR),
                         help="Where the pre-apply pg_dump backup is written.")
    parser.add_argument("--make-backup-only", action="store_true",
                         help="Standalone: take a pg_dump (-Fc) of the CURRENT database and "
                              "exit. Read-only against the app DB. No wipe/seed happens.")
    parser.add_argument("--rehearse-restore", metavar="DUMP_PATH", default=None,
                         help="Standalone: restore DUMP_PATH into a disposable "
                              f"{_REHEARSAL_DB_PREFIX}* database, verify row counts, drop it. "
                              "Never touches the application database. No wipe/seed happens.")
    return parser.parse_args()


def _assert_confirm_phrase(args: argparse.Namespace) -> None:
    if not args.apply:
        return
    if args.confirm_phrase != _CONFIRM_PHRASE:
        raise ResetAbortedError(
            "Refusing to --apply: --confirm-phrase does not match exactly. "
            f'Required phrase: "{_CONFIRM_PHRASE}"'
        )


def _assert_target_environment() -> None:
    settings = get_settings()
    print("=== target environment ===")
    print(f"APP_ENV={settings.APP_ENV}  DB_HOST={settings.DB_HOST}  "
          f"DB_PORT={settings.DB_PORT}  DB_NAME={settings.DB_NAME}")
    if settings.is_production:
        raise ResetAbortedError("Refusing to run: APP_ENV=production.")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise ResetAbortedError(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1 "
            "— this script only runs against a confirmed local dev/UAT database, "
            "never a centralized host."
        )


def _validate_shared_user_password(apply: bool) -> str | None:
    """UAT_SUPPLIER_TEMP_PASSWORD — user-login policy (>=12 chars, >=2
    character classes, <=72 UTF-8 bytes), validated against all 10 planned
    email local-parts as context terms, before any DB write."""
    raw = os.environ.get(_ENV_USER_PASSWORD_VAR, "")
    if not raw:
        if apply:
            raise ResetAbortedError(
                f"Refusing to --apply: environment variable {_ENV_USER_PASSWORD_VAR} "
                "is not set. Set it to a temporary password that meets the policy "
                "and re-run. The value is never logged."
            )
        print(f"[DRY RUN] {_ENV_USER_PASSWORD_VAR} is not set yet — required at --apply time.")
        return None
    context_terms = [_synthetic_email(i).split("@", 1)[0] for i in range(1, _SUPPLIER_COUNT + 1)]
    try:
        validate_password(raw, context_terms=context_terms)
    except PasswordPolicyError as exc:
        raise ResetAbortedError(
            f"Refusing to proceed: {_ENV_USER_PASSWORD_VAR} fails the password policy: {exc} "
            "STOPPING without any database change."
        ) from exc
    print(f"[{'APPLY' if apply else 'DRY RUN'}] {_ENV_USER_PASSWORD_VAR}: present, passes "
          "policy (value not shown).")
    return raw


def _validate_shared_plot_pin(apply: bool) -> str | None:
    """UAT_PLOT_ACCESS_PASSWORD — the plot inspection PIN, validated via the
    SAME shared validator the real admin PUT uses. Never derived from a
    supplier/plot index (round 8-25A.1 decision 6)."""
    raw = os.environ.get(_ENV_PLOT_PIN_VAR, "")
    if not raw:
        if apply:
            raise ResetAbortedError(
                f"Refusing to --apply: environment variable {_ENV_PLOT_PIN_VAR} "
                "is not set. Set it to a value that passes the plot-access policy "
                "and re-run. The value is never logged."
            )
        print(f"[DRY RUN] {_ENV_PLOT_PIN_VAR} is not set yet — required at --apply time.")
        return None
    try:
        normalized = validate_plot_access_password(raw)
    except PlotAccessPasswordPolicyError as exc:
        raise ResetAbortedError(
            f"Refusing to proceed: {_ENV_PLOT_PIN_VAR} fails the plot-access policy: {exc} "
            "STOPPING without any database change."
        ) from exc
    print(f"[{'APPLY' if apply else 'DRY RUN'}] {_ENV_PLOT_PIN_VAR}: present, passes policy "
          "(value not shown).")
    return normalized


# --------------------------------------------------------------------------
# DB reads
# --------------------------------------------------------------------------


async def _candidate_supplier_owner_users(s: AsyncSession) -> list[User]:
    """PART A — every user holding 'supplier:owner', narrowed through the
    pure `_is_deletion_candidate` predicate. User.roles is lazy="selectin",
    so `u.roles` below is already loaded, no extra query per user."""
    stmt = select(User).where(User.roles.any(Role.name == _SUPPLIER_ROLE_NAME))
    rows = (await s.execute(stmt)).unique().scalars().all()
    return [
        u for u in rows
        if _is_deletion_candidate(
            email=u.email, auth_provider=u.auth_provider,
            role_names={r.name for r in u.roles},
        )
    ]


async def _snapshot(label: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with get_db_session() as s:
        # RLS (migrations 0016/0035/0039/0046) FORCEs row-level security on
        # plots/records/plot_cycles/plot_access_phones/plot_access_credentials
        # for EVERY role, including the table owner — an unset app.scope GUC
        # silently returns ZERO rows from these tables (matches nothing, not
        # an error). Must be set once per session/transaction.
        await get_public_plot_rls_context(db=s)
        for name, model in _BUSINESS_TABLES:
            counts[name] = (await s.execute(select(func.count()).select_from(model))).scalar_one()
        for kind in ("crop", "variety"):
            counts[f"master_data:{kind}"] = (await s.execute(
                select(func.count()).select_from(MasterData).where(MasterData.type == kind)
            )).scalar_one()
        counts["master_data:other"] = (await s.execute(
            select(func.count()).select_from(MasterData).where(MasterData.type.notin_(["crop", "variety"]))
        )).scalar_one()
        counts["supplier_owner_users_total"] = (await s.execute(
            select(func.count()).select_from(User).join(User.roles)
            .where(Role.name == _SUPPLIER_ROLE_NAME)
        )).scalar_one()
        candidates = await _candidate_supplier_owner_users(s)
        counts["supplier_owner_users_candidates"] = len(candidates)
        candidate_ids = [c.id for c in candidates]
        if candidate_ids:
            counts["revoked_tokens:candidate_users"] = (await s.execute(
                select(func.count()).select_from(RevokedToken)
                .where(RevokedToken.user_id.in_(candidate_ids))
            )).scalar_one()
        else:
            counts["revoked_tokens:candidate_users"] = 0
        total_revoked = (await s.execute(
            select(func.count()).select_from(RevokedToken)
        )).scalar_one()
        counts["revoked_tokens:other_users"] = total_revoked - counts["revoked_tokens:candidate_users"]
        # Structural/protected tables — printed for evidence and checked as
        # pre-commit invariants, never modified.
        for name, sql in (
            ("permissions", "SELECT count(*) FROM permissions"),
            ("roles", "SELECT count(*) FROM roles"),
            ("role_permissions", "SELECT count(*) FROM role_permissions"),
            ("menu_items", "SELECT count(*) FROM menu_items"),
            ("app_settings", "SELECT count(*) FROM app_settings"),
            ("field_definitions", "SELECT count(*) FROM field_definitions"),
            ("activity_logs", "SELECT count(*) FROM activity_logs"),
            ("system_logs", "SELECT count(*) FROM system_logs"),
        ):
            counts[f"protected:{name}"] = (await s.execute(text(sql))).scalar_one()
        counts["protected:internal_users"] = (await s.execute(text(
            "SELECT count(DISTINCT u.id) FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id WHERE r.name LIKE 'internal:%'"
        ))).scalar_one()
    print(f"=== snapshot: {label} ===")
    for k, v in counts.items():
        print(f"  {k}={v}")
    return counts


def _report_candidates(candidates: list[User]) -> None:
    print(f"=== deletion candidates: {len(candidates)} local supplier:owner user(s) ===")
    for u in candidates:
        print(f"  {_mask_email(u.email)}  auth_provider={u.auth_provider}  "
              f"roles={sorted(r.name for r in u.roles)}")


# --------------------------------------------------------------------------
# Wipe (PART B/C/E) — takes an already-open, shared session/transaction.
# --------------------------------------------------------------------------


async def _wipe_master_data(s: AsyncSession, dry_run: bool) -> dict[str, int]:
    """Deletes crop AND variety, variety FIRST (variety.parent references a
    crop value — no DB-level FK, but the same dependency direction). Every
    OTHER master_data type is never touched by this function."""
    variety_count = (await s.execute(
        select(func.count()).select_from(MasterData).where(MasterData.type == "variety")
    )).scalar_one()
    crop_count = (await s.execute(
        select(func.count()).select_from(MasterData).where(MasterData.type == "crop")
    )).scalar_one()
    planned = {"master_data:variety": variety_count, "master_data:crop": crop_count}
    if dry_run:
        return planned
    await s.execute(delete(MasterData).where(MasterData.type == "variety"))
    await s.execute(delete(MasterData).where(MasterData.type == "crop"))
    return planned


async def _wipe_business_data(
    s: AsyncSession, dry_run: bool, candidate_users: list[User],
) -> dict[str, int]:
    """FK-safe explicit delete order (most-dependent first). Never relies on
    an implicit ON DELETE CASCADE — every table is deleted explicitly."""
    planned: dict[str, int] = {}
    for name, model in _BUSINESS_TABLES:
        planned[name] = (await s.execute(select(func.count()).select_from(model))).scalar_one()

    candidate_ids = [u.id for u in candidate_users]
    planned["candidate_supplier_owner_users"] = len(candidate_ids)
    if candidate_ids:
        candidate_revoked = (await s.execute(
            select(func.count()).select_from(RevokedToken).where(RevokedToken.user_id.in_(candidate_ids))
        )).scalar_one()
    else:
        candidate_revoked = 0
    total_revoked = (await s.execute(select(func.count()).select_from(RevokedToken))).scalar_one()
    planned["revoked_tokens:candidate_users"] = candidate_revoked
    planned["revoked_tokens:other_users"] = total_revoked - candidate_revoked

    if dry_run:
        return planned

    # 1. plot_access_credentials, plot_access_phones (FK -> plots CASCADE,
    #    deleted explicitly anyway per the "never rely on cascade" rule).
    await s.execute(delete(PlotAccessCredential))
    await s.execute(delete(PlotAccessPhone))
    # 2. plot_assignments (FK -> plots, users CASCADE).
    await s.execute(delete(PlotAssignment))
    # 3. records (FK -> plot_cycles/plots/suppliers RESTRICT).
    await s.execute(delete(Record))
    # 4. plot_cycles (FK -> plots RESTRICT — must go before plots).
    await s.execute(delete(PlotCycle))
    # 5. plots (FK -> suppliers CASCADE, deleted explicitly anyway).
    await s.execute(delete(Plot))
    # 6. suppliers.
    await s.execute(delete(Supplier))
    # 7. ONLY the narrow PART A candidate set — user_permission_overrides
    #    and user_roles (both CASCADE on user_id) deleted explicitly first,
    #    then revoked_tokens (also CASCADE, deleted explicitly for the same
    #    reason), then the user row itself.
    if candidate_ids:
        await s.execute(delete(UserPermissionOverride).where(
            UserPermissionOverride.user_id.in_(candidate_ids)
        ))
        await s.execute(delete(RevokedToken).where(RevokedToken.user_id.in_(candidate_ids)))
        await s.execute(
            text("DELETE FROM user_roles WHERE user_id = ANY(:ids)"),
            {"ids": candidate_ids},
        )
        await s.execute(delete(User).where(User.id.in_(candidate_ids)))
    # NOTE: activity_logs, system_logs, ai_call_logs, and every OTHER
    # master_data type are NEVER touched — no delete() call for any of them
    # exists anywhere in this module, by design.
    return planned


async def _seed(
    s: AsyncSession, dry_run: bool, user_password: str | None, plot_pin: str | None,
) -> dict[str, int]:
    stats = {
        "suppliers": 0, "supplier_users": 0, "plots": 0,
        "plot_cycles": 0, "records": 0,
        "plot_access_phones": 0, "distinct_phone_numbers": 0,
        "plot_access_credentials": 0,
    }

    if dry_run:
        stats["suppliers"] = _SUPPLIER_COUNT
        stats["supplier_users"] = _SUPPLIER_COUNT
        stats["plots"] = _SUPPLIER_COUNT * _PLOTS_PER_SUPPLIER
        stats["plot_access_phones"] = _SUPPLIER_COUNT * _PLOTS_PER_SUPPLIER
        stats["distinct_phone_numbers"] = _SUPPLIER_COUNT
        stats["plot_access_credentials"] = _SUPPLIER_COUNT * _PLOTS_PER_SUPPLIER
        return stats

    assert user_password is not None  # guaranteed by _validate_shared_user_password when apply=True
    assert plot_pin is not None       # guaranteed by _validate_shared_plot_pin when apply=True

    owner_role = (await s.execute(
        select(Role).where(Role.name == _SUPPLIER_ROLE_NAME)
    )).scalar_one_or_none()
    if owner_role is None:
        raise ResetAbortedError(
            f"Refusing to --apply: role '{_SUPPLIER_ROLE_NAME}' does not exist "
            "in the roles table (run app.seed first)."
        )

    suppliers: list[Supplier] = []
    for i in range(1, _SUPPLIER_COUNT + 1):
        code = f"SUP{i:03d}"
        supplier = Supplier(
            code=code,
            name=f"UAT ซัพพลายเออร์ {i:02d} จำกัด",
            tax_id=f"099{i:010d}"[:13],
            contact_name=f"ผู้ประสานงาน UAT {i:02d}",
            contact_email=f"contact{i:02d}.uat@example.invalid",
            contact_phone=_synthetic_supplier_phone(i),
            address=f"เลขที่ {i} หมู่ {(i % 12) + 1} ต.ทดสอบยูเอที อ.เมือง (ข้อมูลสังเคราะห์)",
            is_active=True,
        )
        s.add(supplier)
        suppliers.append(supplier)
    await s.flush()

    for i, supplier in enumerate(suppliers, start=1):
        email = _synthetic_email(i)
        user = User(
            email=email,
            full_name=f"ผู้ดูแล UAT Supplier {i:02d}",
            auth_provider="local",
            password_hash=hash_password(
                user_password, context_terms=[email.split("@", 1)[0]]
            ),
            is_active=True,
            is_approved=True,
            email_verified=True,
            supplier_id=supplier.id,
            is_supplier_admin=False,  # role membership is the real gate (see scope.py)
        )
        user.roles = [owner_role]
        s.add(user)
    await s.flush()

    # Plots — NO crop/variety/lot_no/planting_date, NO plant_count/expected
    # yield (round 8-25A.1 PART B: those four "current_*" fields and the
    # yield-planning trio are all denormalised MIRRORS of the plot's ACTIVE
    # PlotCycle — see app/db/models/plot.py's own docstring — and this round
    # deliberately creates NO PlotCycle at all, so every one of those columns
    # stays NULL. The reusable get-or-create cycle helper (app.db.seed_helpers)
    # is not imported or called anywhere in this module.
    plots: list[Plot] = []
    gidx = 0
    for supplier in suppliers:
        for n in range(1, _PLOTS_PER_SUPPLIER + 1):
            province, district, village, lat, lng = _LOCATIONS[gidx % len(_LOCATIONS)]
            plot_code = f"{supplier.code}-P{n:03d}"
            plot = Plot(
                supplier_id=supplier.id,
                plot_code=plot_code,
                name=f"แปลง UAT {plot_code}",
                village=village, district=district, province=province,
                latitude=Decimal(lat), longitude=Decimal(lng),
                rai=Decimal(str(2 + (gidx % 15))) + Decimal("0.50"),
                is_active=True,
                qr_key=generate_qr_key(),
            )
            s.add(plot)
            plots.append(plot)
            gidx += 1
    await s.flush()

    # Access phone: ONE per supplier, shared across its 10 plots (round
    # 8-25A.1 decision 1). Access credential: one per plot (UNIQUE(plot_id)),
    # every one hashed from the SAME env-sourced PIN (never index-derived).
    distinct_phones: set[str] = set()
    gidx = 0
    for si, supplier in enumerate(suppliers, start=1):
        phone = _synthetic_supplier_phone(si)
        distinct_phones.add(phone)
        for _n in range(1, _PLOTS_PER_SUPPLIER + 1):
            plot = plots[gidx]
            s.add(PlotAccessPhone(
                plot_id=plot.id, phone_normalized=phone,
                access_type=ACCESS_TYPE_PRIMARY, is_active=True,
            ))
            s.add(PlotAccessCredential(
                plot_id=plot.id,
                password_hash=hash_plot_access_password(plot_pin),
                password_lookup_digest=build_plot_access_password_lookup_digest(plot_pin),
                credential_version=1,
                is_active=True,
                updated_by_id=None,
            ))
            gidx += 1

    # NO commit here — the caller (_wipe_and_seed) commits ONCE after the
    # pre-commit invariant check passes.
    stats["suppliers"] = len(suppliers)
    stats["supplier_users"] = len(suppliers)
    stats["plots"] = len(plots)
    stats["plot_access_phones"] = len(plots)
    stats["distinct_phone_numbers"] = len(distinct_phones)
    stats["plot_access_credentials"] = len(plots)
    return stats


# --------------------------------------------------------------------------
# PART F — pre-commit invariants (shared session, raises to force rollback)
# and post-commit re-verification (separate session, reports, never raises).
# --------------------------------------------------------------------------


async def _collect_invariant_problems(s: AsyncSession, before_protected: dict[str, int]) -> list[str]:
    problems: list[str] = []

    async def count(model) -> int:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()

    supplier_count = await count(Supplier)
    if supplier_count != _SUPPLIER_COUNT:
        problems.append(f"suppliers == {_SUPPLIER_COUNT}: got {supplier_count}")

    owner_count = (await s.execute(
        select(func.count()).select_from(User).join(User.roles)
        .where(Role.name == _SUPPLIER_ROLE_NAME)
    )).scalar_one()
    if owner_count != _SUPPLIER_COUNT:
        problems.append(f"supplier:owner local users == {_SUPPLIER_COUNT}: got {owner_count}")

    expected_plots = _SUPPLIER_COUNT * _PLOTS_PER_SUPPLIER
    plot_count = await count(Plot)
    if plot_count != expected_plots:
        problems.append(f"plots == {expected_plots}: got {plot_count}")

    uneven = (await s.execute(text(
        "SELECT supplier_id, count(*) FROM plots GROUP BY supplier_id HAVING count(*) <> :n"
    ), {"n": _PLOTS_PER_SUPPLIER})).all()
    if uneven:
        problems.append(
            f"every supplier must have exactly {_PLOTS_PER_SUPPLIER} plots: "
            f"{len(uneven)} supplier(s) off"
        )

    cycle_count = await count(PlotCycle)
    if cycle_count != 0:
        problems.append(f"plot_cycles == 0: got {cycle_count}")

    record_count = await count(Record)
    if record_count != 0:
        problems.append(f"records == 0: got {record_count}")

    for kind in ("crop", "variety"):
        n = (await s.execute(
            select(func.count()).select_from(MasterData).where(MasterData.type == kind)
        )).scalar_one()
        if n != 0:
            problems.append(f"master_data:{kind} == 0: got {n}")

    other_master = (await s.execute(
        select(func.count()).select_from(MasterData).where(MasterData.type.notin_(["crop", "variety"]))
    )).scalar_one()
    if other_master != before_protected["master_data:other"]:
        problems.append(
            f"master_data (other types) unchanged: before="
            f"{before_protected['master_data:other']} after={other_master}"
        )

    phone_count = await count(PlotAccessPhone)
    if phone_count != expected_plots:
        problems.append(f"plot_access_phones == {expected_plots}: got {phone_count}")
    distinct_phones = (await s.execute(
        select(func.count(func.distinct(PlotAccessPhone.phone_normalized)))
    )).scalar_one()
    if distinct_phones != _SUPPLIER_COUNT:
        problems.append(
            f"distinct plot_access_phones.phone_normalized == {_SUPPLIER_COUNT}: "
            f"got {distinct_phones}"
        )

    credential_count = await count(PlotAccessCredential)
    if credential_count != expected_plots:
        problems.append(f"plot_access_credentials == {expected_plots}: got {credential_count}")

    orphan_plots = (await s.execute(text(
        "SELECT count(*) FROM plots p LEFT JOIN suppliers sup ON sup.id = p.supplier_id "
        "WHERE sup.id IS NULL"
    ))).scalar_one()
    if orphan_plots != 0:
        problems.append(f"orphan plots (no supplier) == 0: got {orphan_plots}")

    for key, sql in (
        ("protected:roles", "SELECT count(*) FROM roles"),
        ("protected:permissions", "SELECT count(*) FROM permissions"),
        ("protected:role_permissions", "SELECT count(*) FROM role_permissions"),
    ):
        n = (await s.execute(text(sql))).scalar_one()
        if n != before_protected[key]:
            problems.append(f"{key} unchanged: before={before_protected[key]} after={n}")

    internal_users = (await s.execute(text(
        "SELECT count(DISTINCT u.id) FROM users u "
        "JOIN user_roles ur ON ur.user_id = u.id "
        "JOIN roles r ON r.id = ur.role_id WHERE r.name LIKE 'internal:%'"
    ))).scalar_one()
    if internal_users != before_protected["protected:internal_users"]:
        problems.append(
            f"internal:* users unchanged: before="
            f"{before_protected['protected:internal_users']} after={internal_users}"
        )

    return problems


async def _verify_invariants_or_raise(s: AsyncSession, before_protected: dict[str, int]) -> None:
    problems = await _collect_invariant_problems(s, before_protected)
    if problems:
        raise InvariantViolationError(
            "Pre-commit invariant check FAILED — rolling back, zero rows committed:\n  - "
            + "\n  - ".join(problems)
        )
    print("=== pre-commit invariants: ALL PASS ===")


async def _post_commit_verify(before_protected: dict[str, int]) -> bool:
    """Read-only, runs in its OWN session AFTER commit. Reports PASS/FAIL;
    never raises. Returns True only if every check passed — the caller must
    exit non-zero (and must NOT print a done/READY line) when this is False,
    even though the data is already committed at this point."""
    print("=== post-commit verification ===")
    async with get_db_session() as s:
        await get_public_plot_rls_context(db=s)
        problems = await _collect_invariant_problems(s, before_protected)
    if problems:
        for p in problems:
            print(f"  FAIL: {p}")
        return False
    print("  PASS: all invariants hold post-commit")
    return True


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


async def _wipe_and_seed(
    dry_run: bool, before_protected: dict[str, int],
    user_password: str | None, plot_pin: str | None,
) -> tuple[list[User], dict[str, int], dict[str, int], dict[str, int]]:
    """The ONE transaction for the whole destructive operation. wipe + seed
    + (apply-only) pre-commit invariant check all run against the SAME
    session, with exactly ONE commit at the end — a crash, an unhandled
    exception, or a failed invariant check at ANY point leaves the database
    completely unchanged (get_db_session rolls back on exception, and this
    function never calls commit() before the invariant check has passed)."""
    async with get_db_session() as s:
        await get_public_plot_rls_context(db=s)
        candidates = await _candidate_supplier_owner_users(s)
        master_plan = await _wipe_master_data(s, dry_run)
        wipe_plan = await _wipe_business_data(s, dry_run, candidates)
        seed_plan = await _seed(s, dry_run, user_password, plot_pin)
        if not dry_run:
            await _verify_invariants_or_raise(s, before_protected)
            await s.commit()
    return candidates, master_plan, wipe_plan, seed_plan


def _run_backup(backup_dir: Path) -> Path:
    """pg_dump in CUSTOM format (-Fc) — round 8-25A.1 PART G fix. Custom
    format is what pg_restore needs; 8-25A's plain-SQL dump had no tested
    restore contract at all. Read-only against the app DB."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    settings = get_settings()
    backup_path = backup_dir / f"uat_reset_backup_{stamp}.dump"
    print(f"=== backup: pg_dump -Fc -> {backup_path} ===")
    try:
        with open(backup_path, "wb") as fh:
            result = subprocess.run(
                ["docker", "exec", _DB_CONTAINER_NAME, "pg_dump",
                 "-U", settings.DB_USER, "-d", settings.DB_NAME, "-Fc"],
                stdout=fh, stderr=subprocess.PIPE, timeout=180,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResetAbortedError(f"Refusing to --apply: backup command failed to run: {exc}") from exc
    if result.returncode != 0:
        raise ResetAbortedError(
            "Refusing to --apply: pg_dump exited non-zero "
            f"(code {result.returncode}): {result.stderr.decode('utf-8', 'replace')[:500]}"
        )
    size = backup_path.stat().st_size
    if size < 1024:
        raise ResetAbortedError(
            f"Refusing to --apply: backup file is suspiciously small ({size} bytes) "
            "— treating this as a failed backup."
        )
    print(f"backup OK: {size:,} bytes (custom format, pg_restore-compatible)")
    return backup_path


def _run_psql_maintenance(args: list[str]) -> None:
    """docker exec ... psql against the `postgres` maintenance database —
    the only database CREATE DATABASE/DROP DATABASE can run from."""
    settings = get_settings()
    result = subprocess.run(
        ["docker", "exec", _DB_CONTAINER_NAME, "psql", "-U", settings.DB_USER, "-d", "postgres", *args],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise ResetAbortedError(
            f"psql maintenance command failed: {result.stderr.decode('utf-8', 'replace')[:500]}"
        )


def _run_psql_scalar(database: str, sql: str) -> str:
    settings = get_settings()
    result = subprocess.run(
        ["docker", "exec", _DB_CONTAINER_NAME, "psql", "-U", settings.DB_USER,
         "-d", database, "-t", "-A", "-c", sql],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise ResetAbortedError(
            f"psql query failed against {database}: {result.stderr.decode('utf-8', 'replace')[:500]}"
        )
    return result.stdout.decode("utf-8").strip()


def _restore_rehearsal(backup_path: Path) -> dict[str, str]:
    """Proves `backup_path` is actually restorable, against a DISPOSABLE
    database — this function is the only place in this module that ever
    creates or drops a database, and every create/drop is guarded by the
    _REHEARSAL_DB_PREFIX check immediately before the call. NEVER touches
    DB_NAME (the application database) at any point — it is only ever read
    by pg_dump, in a caller that ran before this function was invoked.
    """
    settings = get_settings()
    if not backup_path.is_file():
        raise ResetAbortedError(f"Refusing rehearsal: {backup_path} does not exist.")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    throwaway_db = f"{_REHEARSAL_DB_PREFIX}{stamp}"
    if not throwaway_db.startswith(_REHEARSAL_DB_PREFIX):
        raise ResetAbortedError("Refusing rehearsal: throwaway DB name failed its own prefix check.")
    if throwaway_db == settings.DB_NAME:
        raise ResetAbortedError("Refusing rehearsal: throwaway DB name collides with the app DB name.")

    print(f"=== restore rehearsal: creating {throwaway_db} ===")
    _run_psql_maintenance(["-c", f'CREATE DATABASE "{throwaway_db}"'])
    try:
        print(f"=== restore rehearsal: pg_restore {backup_path.name} -> {throwaway_db} ===")
        with open(backup_path, "rb") as fh:
            result = subprocess.run(
                ["docker", "exec", "-i", _DB_CONTAINER_NAME, "pg_restore",
                 "-U", settings.DB_USER, "-d", throwaway_db, "--no-owner", "--no-privileges"],
                stdin=fh, stderr=subprocess.PIPE, timeout=300,
            )
        if result.returncode != 0:
            raise ResetAbortedError(
                f"Restore rehearsal FAILED — pg_restore exited {result.returncode}: "
                f"{result.stderr.decode('utf-8', 'replace')[:800]}"
            )
        counts: dict[str, str] = {}
        for table in ("suppliers", "users", "plots", "master_data", "roles", "permissions"):
            counts[table] = _run_psql_scalar(throwaway_db, f"SELECT count(*) FROM {table}")
        print(f"=== restore rehearsal: row counts in {throwaway_db} ===")
        for k, v in counts.items():
            print(f"  {k}={v}")
        return counts
    finally:
        print(f"=== restore rehearsal: dropping {throwaway_db} ===")
        if throwaway_db.startswith(_REHEARSAL_DB_PREFIX) and throwaway_db != settings.DB_NAME:
            _run_psql_maintenance(["-c", f'DROP DATABASE IF EXISTS "{throwaway_db}"'])
        else:
            print(f"REFUSING to drop {throwaway_db} — failed the prefix/name safety re-check.")


async def main() -> None:
    args = _parse_args()

    if args.rehearse_restore:
        _assert_target_environment()
        _restore_rehearsal(Path(args.rehearse_restore))
        return

    if args.make_backup_only:
        _assert_target_environment()
        backup_path = _run_backup(Path(args.backup_dir))
        print(f"backup written: {backup_path}")
        return

    _assert_confirm_phrase(args)
    _assert_target_environment()
    user_password = _validate_shared_user_password(apply=args.apply)
    plot_pin = _validate_shared_plot_pin(apply=args.apply)

    settings = get_settings()
    media_root = Path(settings.INSPECTION_PHOTOS_DIR)
    media_file_count, media_total_bytes = _scan_media(media_root)
    print(f"=== media scan (read-only): {media_root} ===")
    print(f"  files={media_file_count}  bytes={media_total_bytes:,} "
          f"({media_total_bytes / (1024 * 1024):.1f} MiB)")
    print("  quarantine NOT executed this run — media files are left exactly as-is.")

    await init_db()
    try:
        before = await _snapshot("before")
        candidates_preview = await _candidate_supplier_owner_users_standalone()
        _report_candidates(candidates_preview)

        backup_path: Path | None = None
        if args.apply:
            backup_path = _run_backup(Path(args.backup_dir))
            _restore_rehearsal(backup_path)  # abort BEFORE any wipe if this fails

        candidates, master_plan, wipe_plan, seed_plan = await _wipe_and_seed(
            dry_run=not args.apply, before_protected=before,
            user_password=user_password, plot_pin=plot_pin,
        )

        after = await _snapshot("after" if args.apply else "after (dry-run — unchanged)")

        print("=== plan / result ===")
        print(f"mode: {'APPLY' if args.apply else 'DRY RUN (no changes made)'}")
        if backup_path is not None:
            print(f"backup: {backup_path}")
        print(f"master_data would delete / deleted: {master_plan}")
        print(f"business data would delete / deleted: {wipe_plan}")
        print(f"would create / created: {seed_plan}")
        print("=== before -> after deltas ===")
        for key in before:
            b, a = before[key], after.get(key, "?")
            marker = "" if b == a else "  <-- changed"
            print(f"  {key}: {b} -> {a}{marker}")

        if args.apply:
            ok = await _post_commit_verify(before)
            if not ok:
                print("=== APPLY VERIFICATION FAILED — NOT READY ===", file=sys.stderr)
                sys.exit(1)
            print("=== APPLY COMPLETE — READY ===")
        else:
            print("=== DRY RUN COMPLETE (no changes made) ===")
    finally:
        await close_db()


async def _candidate_supplier_owner_users_standalone() -> list[User]:
    """Same predicate as `_candidate_supplier_owner_users`, in its own
    session — used only for the preview print in main(), independent of the
    later wipe/seed transaction (which recomputes it again for correctness
    against pre-wipe state, at the moment right before deleting)."""
    async with get_db_session() as s:
        await get_public_plot_rls_context(db=s)
        return await _candidate_supplier_owner_users(s)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ResetAbortedError as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        sys.exit(1)
