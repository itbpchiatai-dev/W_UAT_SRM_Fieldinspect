"""Regression test for the empty-string app.user_id RLS bug (round 10).

plots_scope/records_scope (migration 0016)'s 'assigned' branch does
`user_id = current_setting('app.user_id', true)::uuid` inside a subquery
that doesn't correlate against the outer table. Postgres's planner hoists
that into an InitPlan that runs once, unconditionally — regardless of
which CASE branch (scope='all'/'supplier'/'assigned') actually applies at
runtime. So app.user_id must always be a syntactically valid UUID string,
never "", even for the public (unauthenticated) flows that have no real
user — confirmed live against Postgres during round 10's DB verification
(same query, same GUCs: "" crashes with "invalid input syntax for type
uuid", the sentinel doesn't).

Round 8-1 (migration 0037) additionally hardened records_scope/plots_scope
themselves with a `NULLIF(current_setting('app.user_id', true), '')::uuid`
guard, so the DB policy no longer crashes on an empty/missing GUC either
(same fix migration 0035 already applied to plot_cycles_scope) — see
tests/unit/test_rls_uuid_guard_migration.py. This app-layer sentinel is kept
as defense-in-depth on top of that DB-level fix, not superseded by it: the
tests below still verify `_NO_USER_ID` is always a syntactically valid UUID,
never "".

No DB fixture exists in this repo, so this verifies the actual SQL params
`_set_rls_config` sends via a mocked db.execute — matching the established
source/behavior-inspection pattern in
tests/security/test_public_plot_verify_wiring.py and
tests/security/test_public_record_create_wiring.py.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.api.deps import scope as scope_module


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    return db


def _captured_params(db: MagicMock) -> dict:
    args, _kwargs = db.execute.call_args
    return args[1]


async def test_get_public_plot_rls_context_never_sets_empty_user_id() -> None:
    db = _mock_db()
    await scope_module.get_public_plot_rls_context(db=db)

    params = _captured_params(db)
    assert params["uid"] != ""
    # "" would raise ValueError here, exactly like Postgres's ::uuid cast —
    # this is what actually broke every public endpoint call before the fix.
    uuid.UUID(params["uid"])
    assert params["scope"] == "all"


async def test_set_public_record_rls_context_never_sets_empty_user_id() -> None:
    db = _mock_db()
    supplier_id = uuid4()
    await scope_module.set_public_record_rls_context(db, supplier_id)

    params = _captured_params(db)
    assert params["uid"] != ""
    uuid.UUID(params["uid"])
    assert params["scope"] == "supplier"
    assert params["sid"] == str(supplier_id)


def test_no_user_sentinel_constant_is_a_valid_uuid_not_blank() -> None:
    assert scope_module._NO_USER_ID != ""
    uuid.UUID(scope_module._NO_USER_ID)
