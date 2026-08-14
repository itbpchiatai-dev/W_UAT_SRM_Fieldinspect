"""Plot/supplier inspection-code gate — RETIRED at runtime (round 8-3G).

HISTORICAL-MIGRATION-COMPATIBILITY ONLY as of round 8-3G: no live endpoint
calls anything in this module anymore — both verification endpoints
(plots.py's verify_plot_inspection_code and public_plots.py's
verify_inspection_code_public) and suppliers.inspection_code itself
(migration 0040) are retired; the public inspection flow is phone-access-
only. This file is kept solely because migrations 0023 and 0027 import
DEFAULT_INSPECTION_CODE / hash_inspection_code from it — a fresh `alembic
upgrade head` on an empty database must still be able to replay that
history. Do not add a new runtime caller; do not delete this file.

(Everything below describes the retired feature as it worked before round
8-3G, for context on why these specific helpers exist.)

As of migration 0027 the code lived PLAINTEXT on the supplier
(suppliers.inspection_code), shared by all of that supplier's plots, so an
admin could read it back to hand to field workers. Verification was
therefore a plain constant-time string compare (verify_inspection_code_plain).

The bcrypt hash_inspection_code / verify_inspection_code helpers below are
retained only for the 0027 downgrade path (which re-creates the old
per-plot hashed column) and any legacy callers. bcrypt is reused directly
(already a project dependency) rather than app.auth.password.hash_password,
whose validate_password() strength policy (12+ chars, mixed classes) would
reject a short PIN.
"""
from __future__ import annotations

import hmac

import bcrypt

DEFAULT_INSPECTION_CODE = "1111"


def verify_inspection_code_plain(code: str, expected: str) -> bool:
    """Constant-time compare of a submitted code against the stored plaintext
    supplier code. False on any empty side (an unset code never verifies)."""
    if not code or not expected:
        return False
    return hmac.compare_digest(code, expected)


def hash_inspection_code(code: str) -> str:
    """Legacy — bcrypt hash. Retained for the 0027 downgrade migration."""
    return bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_inspection_code(code: str, code_hash: str) -> bool:
    """Legacy — verify against a bcrypt hash. Retained for legacy callers."""
    if not code_hash:
        return False
    try:
        return bcrypt.checkpw(code.encode("utf-8"), code_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash on disk — treat as verification failure, not a crash.
        return False
