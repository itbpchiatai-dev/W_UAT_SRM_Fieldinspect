"""Round 8-9D — GET /public/inspection-access/config.

The public page's capability probe: "is a plot password required right now, and
how long may it be". It exists because the frontend bundle and the backend are
deployed independently — a bundle that guessed the answer from a build-time env
var would either lock every field user out (no password sent to a backend that
now demands one) or render a field the backend still ignores.

DB-less like the rest of this suite: the route function is called directly.
Nothing here enables enforcement — the real flag is asserted untouched below.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.api.v1.public_inspection_access as public_access
from app.api.v1.public_inspection_access import public_inspection_access_config
from app.auth.plot_access_password import (
    PLOT_ACCESS_PASSWORD_MAX_LENGTH,
    PLOT_ACCESS_PASSWORD_MIN_LENGTH,
)

_M = "app.api.v1.public_inspection_access"
# slowapi's decorator insists on a real starlette Request, so every direct call
# goes through .__wrapped__ (the same pattern test_phone_access_endpoints.py
# uses).
_config_fn = public_inspection_access_config.__wrapped__


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"), headers={})


def _response():
    from starlette.responses import Response

    return Response()


def _enforcement(on: bool):
    return patch(f"{_M}.get_settings", return_value=SimpleNamespace(
        PUBLIC_PLOT_PASSWORD_ENFORCEMENT=on,
    ))


async def _config(*, enforcement: bool, response=None):
    with _enforcement(enforcement):
        return await _config_fn(request=_request(), response=response or _response())


# --- what it reports --------------------------------------------------------

async def test_it_reports_password_not_required_while_the_flag_is_off() -> None:
    result = await _config(enforcement=False)
    assert result.password_required is False


async def test_it_reports_password_required_when_the_flag_is_on() -> None:
    """Driven purely by a settings override — the real environment is never
    touched (see test_the_real_flag_is_never_written_by_this_endpoint)."""
    result = await _config(enforcement=True)
    assert result.password_required is True


async def test_the_length_bounds_come_from_the_shared_policy_constants() -> None:
    """Never re-typed in the endpoint: a policy change must reach the public UI
    by editing ONE number in app/auth/plot_access_password.py."""
    result = await _config(enforcement=False)
    assert result.password_min_length == PLOT_ACCESS_PASSWORD_MIN_LENGTH
    assert result.password_max_length == PLOT_ACCESS_PASSWORD_MAX_LENGTH
    assert (result.password_min_length, result.password_max_length) == (4, 20)


def test_the_endpoint_imports_the_constants_rather_than_hardcoding_them() -> None:
    src = inspect.getsource(public_inspection_access_config)
    assert "PLOT_ACCESS_PASSWORD_MIN_LENGTH" in src
    assert "PLOT_ACCESS_PASSWORD_MAX_LENGTH" in src
    # no literal 4/20 pretending to be the policy
    assert "password_min_length=4" not in src
    assert "password_max_length=20" not in src


def test_the_response_schema_carries_no_extra_field() -> None:
    from app.schemas.phone_access import PublicInspectionAccessConfigResponse

    assert set(PublicInspectionAccessConfigResponse.model_fields) == {
        "password_required", "password_min_length", "password_max_length",
    }


async def test_the_response_has_exactly_three_fields_and_no_secret() -> None:
    result = await _config(enforcement=True)
    body = result.model_dump(by_alias=True)
    assert set(body) == {"passwordRequired", "passwordMinLength", "passwordMaxLength"}
    blob = result.model_dump_json(by_alias=True)
    for banned in (
        "pepper", "hash", "digest", "credential", "phone", "plot", "supplier",
        "token", "qrKey", "count", "$2b$",
    ):
        assert banned.lower() not in blob.lower(), banned


async def test_it_never_reveals_how_many_plots_are_configured() -> None:
    """Readiness (the ADMIN endpoint, round 8-9C) reports coverage counts. This
    one must not: "3 of 40 plots have a password" is an operational detail a
    public caller has no business knowing."""
    result = await _config(enforcement=True)
    body = result.model_dump()
    # The only numbers here are the two policy bounds; nothing counts anything.
    numeric = {k for k, v in body.items() if isinstance(v, int) and not isinstance(v, bool)}
    assert numeric == {"password_min_length", "password_max_length"}
    assert not any(
        word in k for k in body for word in ("count", "total", "configured", "missing")
    )


# --- how it is served -------------------------------------------------------

def _code_only(func) -> str:
    """Source with the docstring and comments stripped — asserting on raw source
    otherwise matches PROSE explaining why something is absent (the docstring
    below literally contains the words 'get_db' and 'await')."""
    src = inspect.getsource(func)
    if '"""' in src:
        src = src[:src.index('"""')] + src[src.index('"""', src.index('"""') + 3) + 3:]
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )


def test_it_touches_no_database_at_all() -> None:
    """No get_db, no RLS dependency, no query — so it cannot be probed for
    whether any particular plot/phone/credential exists, and it keeps answering
    during a database incident (a page that can't learn the requirement has to
    fail closed, which it can only do if this endpoint is still up)."""
    code = _code_only(public_inspection_access_config)
    assert "get_db" not in code
    assert "db" not in inspect.signature(_config_fn).parameters
    assert "await" not in code  # no I/O in the body at all
    for banned in ("repo.", "select(", "db.execute", "get_public_plot_rls_context"):
        assert banned not in code


def _route(path: str, method: str):
    for r in public_access.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def test_it_is_a_public_get_with_no_auth_dependency() -> None:
    route = _route("/inspection-access/config", "GET")
    names = {getattr(d.dependency, "__name__", "") for d in route.dependencies}
    assert not any("current_user" in n or "permission" in n or "rls" in n for n in names)
    assert route.dependencies == []


def test_it_is_rate_limited_like_the_sibling_public_reads() -> None:
    """30/minute — the same ceiling public_masterdata.py and
    public_inspection_protocols.py use for an unauthenticated read."""
    assert hasattr(public_inspection_access_config, "__wrapped__")
    src = Path(inspect.getfile(public_access)).read_text(encoding="utf-8")
    block = src[src.index('"/inspection-access/config"'):src.index("async def public_inspection_access_config")]
    assert '@limiter.limit("30/minute")' in block


def test_it_is_a_read_only_get_and_never_mutates() -> None:
    route = _route("/inspection-access/config", "GET")
    assert route.methods == {"GET"}
    src = inspect.getsource(public_inspection_access_config)
    for banned in ("commit", "flush", "add(", "delete", "update("):
        assert banned not in src


# --- the flag itself --------------------------------------------------------

def test_the_real_flag_is_never_written_by_this_endpoint() -> None:
    """Reporting the flag must never become a way to CHANGE it. The endpoint
    reads it through _enforcement_on() and does nothing else with it."""
    code = _code_only(public_inspection_access_config)
    assert "_enforcement_on()" in code
    assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT" not in code
    module_src = Path(inspect.getfile(public_access)).read_text(encoding="utf-8")
    assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT =" not in module_src
    assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT=True" not in module_src


def test_the_unpatched_default_in_tests_is_off() -> None:
    """Round 8-9F.1 — this used to read "the runtime flag is still off", and it
    was a real ops guard while backend/.env carried no value for the flag.

    It cannot be that any more: round 8-9F legitimately turned enforcement ON in
    the local runtime, and tests/conftest.py now pins the flag OFF for the test
    process so a developer's .env can't decide whether the suite passes. What is
    left — and still worth asserting — is that the pin is in effect for every
    test that does not opt out, which is what keeps the enforcement=false
    branches below meaningful.

    The live runtime is verified where it belongs: against the running service
    (see the round 8-9F Final Report's capability-endpoint checks), not from a
    unit test."""
    from app.core.config import get_settings

    assert get_settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT is False


async def test_the_endpoint_reflects_the_process_flag_when_nothing_is_patched() -> None:
    """The unpatched call must agree with whatever Settings says — proving the
    endpoint reports the truth rather than a baked-in constant. Holds under the
    test pin and would hold just as well against a runtime with the flag on."""
    from app.core.config import get_settings

    result = await _config_fn(request=_request(), response=_response())
    assert result.password_required is get_settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT


# --- round 8-9E: cache hardening -------------------------------------------

async def test_the_answer_is_never_stored_by_any_cache() -> None:
    """The flag can flip at any moment. A copy of this response held anywhere
    between the app and the phone — browser cache, CDN, corporate proxy — would
    keep telling field users "no password required" after enforcement went live,
    and every one of them would be locked out by a 404 they cannot act on."""
    response = _response()
    await _config(enforcement=False, response=response)
    assert response.headers["Cache-Control"] == "no-store"


async def test_the_no_store_header_is_set_in_both_modes() -> None:
    for enforcement in (False, True):
        response = _response()
        await _config(enforcement=enforcement, response=response)
        assert response.headers["Cache-Control"] == "no-store"


def test_no_store_is_used_rather_than_a_revalidation_directive() -> None:
    """no-cache/max-age=0 still permit a STORED copy that is revalidated; a
    security posture is not a resource worth keeping on disk."""
    code = _code_only(public_inspection_access_config)
    assert '"no-store"' in code
    assert "max-age" not in code
    assert '"no-cache"' not in code
