"""Round 8-9C — GET /plots/inspection-access-credentials/readiness.

Read-only operator information: which plots still need an inspection password
before PUBLIC_PLOT_PASSWORD_ENFORCEMENT can safely be turned on. Nothing here
enables anything.

DB-less: the repository is patched and the route function called directly.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import app.api.v1.plots as plots_module
from app.api.v1.plots import get_plot_credential_readiness
from app.auth.permissions import PermissionKey
from app.repositories import plot_access_credential_repository as credential_repo

_P = "app.api.v1.plots"


def _supplier(code: str = "SUP001"):
    return SimpleNamespace(id=uuid4(), code=code, name=f"ซัพ {code}", is_active=True)


def _plot(code: str = "P001", supplier=None):
    return SimpleNamespace(
        id=uuid4(), plot_code=code, name=f"แปลง {code}", is_active=True,
        supplier=supplier or _supplier(),
    )


async def _readiness(rows, scope=()):
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=scope)), \
         patch(f"{_P}.credential_repo.get_credential_readiness_rows",
               AsyncMock(return_value=rows)) as mk:
        result = await get_plot_credential_readiness(
            current_user=SimpleNamespace(id=uuid4()), db=AsyncMock()
        )
    return result, mk


# --- counting ---------------------------------------------------------------

async def test_a_plot_with_an_active_credential_counts_as_configured() -> None:
    supplier = _supplier()
    result, _ = await _readiness([(_plot("P001", supplier), supplier, True)])
    assert result.eligible_plots == 1
    assert result.configured_plots == 1
    assert result.missing_credential_plots == 0
    assert result.missing_plots == []
    assert result.ready is True


async def test_a_plot_without_a_credential_is_listed_as_missing() -> None:
    supplier = _supplier()
    plot = _plot("P002", supplier)
    result, _ = await _readiness([(plot, supplier, False)])
    assert result.eligible_plots == 1
    assert result.configured_plots == 0
    assert result.missing_credential_plots == 1
    assert result.ready is False
    assert len(result.missing_plots) == 1
    missing = result.missing_plots[0]
    assert missing.plot_id == plot.id
    assert missing.plot_code == "P002"
    assert missing.supplier_code == supplier.code


async def test_a_mixed_estate_reports_both_counts() -> None:
    supplier = _supplier()
    rows = [
        (_plot("P001", supplier), supplier, True),
        (_plot("P002", supplier), supplier, False),
        (_plot("P003", supplier), supplier, False),
    ]
    result, _ = await _readiness(rows)
    assert (result.eligible_plots, result.configured_plots, result.missing_credential_plots) == (3, 1, 2)
    assert result.ready is False
    assert {p.plot_code for p in result.missing_plots} == {"P002", "P003"}


async def test_zero_eligible_plots_is_never_ready() -> None:
    """Nothing set up at all is not the same as everything done — flipping the
    flag here would be a mistake, so `ready` must be false."""
    result, _ = await _readiness([])
    assert result.eligible_plots == 0
    assert result.ready is False


# --- eligibility (the repository query's own rules) --------------------------

def test_eligibility_requires_an_active_phone_but_not_an_active_cycle() -> None:
    src = inspect.getsource(credential_repo.get_credential_readiness_rows)
    assert "PlotAccessPhone.is_active" in src        # must have an active phone
    assert "Plot.is_active" in src                   # plot active
    assert "Supplier.is_active" in src               # supplier active
    # deliberately NOT gated on a cycle — a plot between cycles still needs a
    # password before enforcement flips
    assert "active_cycle" not in src
    assert "PlotCycle" not in src


def test_duplicate_phone_rows_cannot_multiply_a_plot() -> None:
    """An EXISTS subquery counts a plot once no matter how many active phones
    it has; a join would return it N times and inflate every count."""
    src = inspect.getsource(credential_repo.get_credential_readiness_rows)
    assert ".exists()" in src
    assert "join(PlotAccessPhone" not in src


def test_it_is_one_set_based_query_with_no_n_plus_1() -> None:
    src = inspect.getsource(credential_repo.get_credential_readiness_rows)
    assert src.count("await db.execute") == 1
    assert "for " not in src.split("stmt = (")[0].split('"""')[-1]


async def test_the_endpoint_issues_exactly_one_repository_call() -> None:
    supplier = _supplier()
    rows = [(_plot(f"P{i:03d}", supplier), supplier, i % 2 == 0) for i in range(20)]
    _result, mk = await _readiness(rows)
    assert mk.await_count == 1


# --- scope ------------------------------------------------------------------

async def test_it_applies_the_callers_own_supplier_scope() -> None:
    """Uses get_supplier_scope_filter — the SAME helper every other
    supplier-scoped read uses — so a Supplier Owner sees only their own plots
    and an admin with scope 'all' sees everything. Never a new role decision."""
    sentinel = ["scope-condition"]
    supplier = _supplier()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=sentinel)) as mk_scope, \
         patch(f"{_P}.credential_repo.get_credential_readiness_rows",
               AsyncMock(return_value=[(_plot("P001", supplier), supplier, True)])) as mk_rows:
        await get_plot_credential_readiness(
            current_user=SimpleNamespace(id=uuid4()), db=AsyncMock()
        )
    mk_scope.assert_awaited_once()
    assert mk_rows.await_args.args[1] == sentinel   # forwarded verbatim


async def test_admin_scope_all_sees_every_supplier() -> None:
    sup_a, sup_b = _supplier("SUP001"), _supplier("SUP002")
    rows = [(_plot("P001", sup_a), sup_a, False), (_plot("P900", sup_b), sup_b, False)]
    result, _ = await _readiness(rows, scope=[])     # scope 'all' → no conditions
    assert {p.supplier_code for p in result.missing_plots} == {"SUP001", "SUP002"}


# --- response safety --------------------------------------------------------

async def test_the_response_carries_identity_only_and_no_secret() -> None:
    supplier = _supplier()
    result, _ = await _readiness([(_plot("P002", supplier), supplier, False)])
    body = result.model_dump_json(by_alias=True)
    for banned in (
        "phone", "credentialId", "credentialVersion", "password",
        "hash", "digest", "qrKey", "$2b$",
    ):
        assert banned not in body
    assert set(result.missing_plots[0].model_dump(by_alias=True)) == {
        "plotId", "plotCode", "plotName", "supplierId", "supplierCode", "supplierName",
    }


# --- wiring -----------------------------------------------------------------

def _route(path: str, method: str):
    for r in plots_module.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def test_it_requires_plots_read_and_rls() -> None:
    route = _route("/inspection-access-credentials/readiness", "GET")
    keys: set[str] = set()
    names: set[str] = set()
    for dep in route.dependencies:
        names.add(getattr(dep.dependency, "__name__", ""))
        for cell in getattr(dep.dependency, "__closure__", None) or ():
            if isinstance(cell.cell_contents, str):
                keys.add(cell.cell_contents)
    assert PermissionKey.PLOTS_READ in keys
    assert "get_rls_context" in names


def test_it_is_declared_before_the_plot_id_route() -> None:
    """FastAPI matches in declaration order — a literal path registered after
    /{plot_id} would be swallowed by it and 422 on the UUID parse."""
    paths = [getattr(r, "path", "") for r in plots_module.router.routes]
    assert paths.index("/inspection-access-credentials/readiness") < paths.index("/{plot_id}")


def test_readiness_never_enables_enforcement() -> None:
    """It is INFORMATION for an operator. Nothing may auto-flip the flag on a
    ready=true result."""
    src = Path(inspect.getfile(plots_module)).read_text(encoding="utf-8")
    # The flag name appears only in PROSE explaining what readiness is for —
    # never as a read or a write.
    for line in src.splitlines():
        if "PUBLIC_PLOT_PASSWORD_ENFORCEMENT" not in line:
            continue
        stripped = line.strip()
        assert stripped.startswith("#") or stripped.startswith("*") or (
            '"""' in src[: src.index(line)].rsplit("def ", 1)[-1]
        ) or "settings" not in line, f"unexpected flag use: {stripped!r}"
        assert "=" not in stripped.replace("==", ""), f"flag assigned: {stripped!r}"
