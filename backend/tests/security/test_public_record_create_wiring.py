"""Public record creation (round 8) — wiring + no-leak checks.

Confirms:
- POST /api/v1/public/records requires no login.
- It IS rate-limited (@limiter.limit).
- It's the ONLY route in this router — no public read/list/update/delete
  for records anywhere (this file, or otherwise, since this is the only
  public_records router module in the app).
- The response schema never carries recorded_by_id/email or the raw
  inspection_session_token/claims.
- Nothing in the route logs the token or any code.
- get_current_user (round 6/7's own lock, re-checked here since this
  round is what actually consumes the token) still only accepts
  type == "access".
- set_public_record_rls_context is a plain function (not a Depends()
  dependency) that reuses _set_rls_config rather than a new RLS policy.

No DB fixture exists in this repo, so — matching the established pattern
(tests/security/test_plot_lookup_wiring.py) — this is a source/route-table
inspection rather than a live HTTP request.
"""
from __future__ import annotations

import inspect

from app.api.v1 import public_records as public_records_module
from app.api.v1.installed_routers import ROUTERS
from app.schemas.record import PublicRecordCreateResult


def _route():
    return next(
        r for r in public_records_module.router.routes if r.path == "/records"
    )


def test_public_records_router_mounted_under_api_v1_public() -> None:
    prefixes = {prefix for router, prefix in ROUTERS if router is public_records_module.router}
    assert prefixes == {"/api/v1/public"}


def test_create_record_public_does_not_require_login() -> None:
    route = _route()
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert not any("require_permission" in q or "require_any_permission" in q for q in qualnames)
    assert not any("get_current_user" in q for q in qualnames)

    src = inspect.getsource(public_records_module)
    assert "CurrentUser" not in src
    assert "require_permission" not in src


def test_create_record_public_is_rate_limited() -> None:
    assert hasattr(public_records_module.create_record_public, "__wrapped__")
    src = inspect.getsource(public_records_module)
    assert '@limiter.limit("20/minute")' in src
    # The limiter must come from the shared singleton module (the import
    # line also carries get_client_ip since the submitted_ip audit round).
    assert public_records_module.limiter is __import__(
        "app.core.rate_limit", fromlist=["limiter"]
    ).limiter


def test_only_post_records_route_exists_no_read_list_update_delete() -> None:
    """round 13 added /records/with-photos (also POST-only, for the
    4-photo inspection flow) — the invariant this guards is "no read/list/
    update/delete", not "exactly one path", so assert every route is
    POST-only and the path set is still fully enumerated (no surprise
    additions)."""
    methods_by_path: dict[str, set[str]] = {}
    for r in public_records_module.router.routes:
        methods_by_path.setdefault(r.path, set()).update(r.methods)
    assert methods_by_path == {
        "/records": {"POST"},
        "/records/with-photos": {"POST"},
    }


def test_response_schema_has_no_recorded_by_or_token_fields() -> None:
    fields = set(PublicRecordCreateResult.model_fields)
    assert "recorded_by_id" not in fields
    assert "recorded_by_email" not in fields
    assert "recorded_by_name" not in fields
    assert "inspection_session_token" not in fields
    # submitted_by_code is retired (round 8-3G) — never on the public
    # response either.
    assert "submitted_by_code" not in fields
    assert fields == {
        "id", "plot_id", "plot_code", "plot_name",
        "supplier_id", "supplier_code", "supplier_name",
        "record_date", "submitted_by_name", "created_at",
        # round 8-4A — offline receipt: the client's own key echoed back plus
        # the accepted capture time. Both nullable; neither leaks anything an
        # online caller couldn't already see about its own submission.
        "client_submission_id", "captured_at",
    }


def test_endpoint_does_not_log_token_or_code() -> None:
    """round 13: token handling moved into _verify_and_resolve (shared with
    the new with-photos endpoint) — check every function that touches the
    token/claims, not just the original route body."""
    for fn in (
        public_records_module.create_record_public.__wrapped__,
        public_records_module.create_record_with_photos_public.__wrapped__,
        # round 8-4A split the token/claims handling across these helpers —
        # each must also be log-free.
        public_records_module._resolve_or_replay,
        public_records_module._decode_and_resolve_plot,
        public_records_module._resolve_active_cycle_bound,
        public_records_module._verify_and_resolve,
        public_records_module._finish_creating_record,
    ):
        src = inspect.getsource(fn)
        assert "print(" not in src
        assert "logger." not in src
        assert ".log(" not in src


def test_get_current_user_still_only_accepts_access_tokens() -> None:
    from app.auth import dependencies as auth_dependencies

    src = inspect.getsource(auth_dependencies.get_current_user)
    assert 'claims.get("type") != "access"' in src


def test_public_record_rls_context_is_not_a_fastapi_dependency() -> None:
    """Must be called explicitly inside the route body (after the token's
    supplier_id is known), not wired as Depends() — the docstring in
    scope.py explains why. This locks that design decision down.

    round 13: this call lives in a shared helper now, not in
    create_record_public's own body. round 8-4A: the route delegates to
    _resolve_or_replay, and the RLS call was split into _decode_and_resolve_plot
    (still called directly, never via Depends)."""
    route = _route()
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert not any(q.startswith("set_public_record_rls_context") for q in qualnames)
    assert not any(q.startswith("_verify_and_resolve") for q in qualnames)
    assert not any(q.startswith("_resolve_or_replay") for q in qualnames)
    assert not any(q.startswith("_decode_and_resolve_plot") for q in qualnames)

    route_src = inspect.getsource(public_records_module.create_record_public.__wrapped__)
    assert "await _resolve_or_replay(db, payload)" in route_src

    helper_src = inspect.getsource(public_records_module._decode_and_resolve_plot)
    assert "await set_public_record_rls_context(db, token_supplier_id)" in helper_src


def test_public_record_rls_context_reuses_set_rls_config_scoped_to_supplier() -> None:
    from app.api.deps import scope as scope_module

    src = inspect.getsource(scope_module.set_public_record_rls_context)
    assert "_set_rls_config(db, " in src
    assert '"supplier"' in src
    assert '"all"' not in src
    assert "CREATE POLICY" not in inspect.getsource(scope_module)


def test_endpoint_enforces_plot_supplier_consistency_before_insert() -> None:
    """round 13: this check lives in a shared helper, so both endpoints get
    the same guard. round 8-4A: it moved into _decode_and_resolve_plot (the
    common front half of both the online and offline paths)."""
    src = inspect.getsource(public_records_module._decode_and_resolve_plot)
    assert "plot.supplier_id != token_supplier_id" in src
