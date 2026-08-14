"""Reference-read permission alignment (round 5.6).

The RecordForm at /farmlog/records/new is opened with records.create, so the
read-only reference endpoints it loads (inspection-protocols, masterdata,
fielddefs) must accept records.create as well as records.read — via
require_any_permission, the same pattern as plots /lookup. The mutation
endpoints on those routers must keep their original, narrower permissions.

Source/route-table inspection — no DB fixture in this repo.
"""
from __future__ import annotations

import inspect

from app.api.v1 import fielddefs as fielddefs_module
from app.api.v1 import inspection_protocols as protocols_module
from app.api.v1 import masterdata as masterdata_module

_READ_GUARD = "require_any_permission(PermissionKey.RECORDS_READ, PermissionKey.RECORDS_CREATE)"


def _get_route(module):
    return next(r for r in module.router.routes if r.path == "" and "GET" in r.methods)


def test_inspection_protocols_read_accepts_read_or_create() -> None:
    qualnames = {dep.call.__qualname__ for dep in _get_route(protocols_module).dependant.dependencies}
    assert any(q.startswith("require_any_permission") for q in qualnames)
    assert _READ_GUARD in inspect.getsource(protocols_module)


def test_masterdata_read_accepts_read_or_create() -> None:
    qualnames = {dep.call.__qualname__ for dep in _get_route(masterdata_module).dependant.dependencies}
    assert any(q.startswith("require_any_permission") for q in qualnames)
    assert _READ_GUARD in inspect.getsource(masterdata_module)


def test_fielddefs_read_accepts_read_or_create() -> None:
    qualnames = {dep.call.__qualname__ for dep in _get_route(fielddefs_module).dependant.dependencies}
    assert any(q.startswith("require_any_permission") for q in qualnames)
    assert _READ_GUARD in inspect.getsource(fielddefs_module)


def test_masterdata_mutations_still_require_masterdata_permissions() -> None:
    src = inspect.getsource(masterdata_module)
    assert "require_permission(PermissionKey.MASTERDATA_CREATE)" in src
    assert "require_permission(PermissionKey.MASTERDATA_UPDATE)" in src
    assert "require_permission(PermissionKey.MASTERDATA_DELETE)" in src
    # The read relaxation must NOT have leaked onto a write path.
    assert "require_any_permission" in src  # the read guard
    for method_line in ("@router.post", "@router.patch", "@router.delete"):
        assert method_line in src


def test_fielddefs_mutations_still_require_fielddefs_permissions() -> None:
    src = inspect.getsource(fielddefs_module)
    assert "require_permission(PermissionKey.FIELDDEFS_CREATE)" in src
    assert "require_permission(PermissionKey.FIELDDEFS_UPDATE)" in src
    assert "require_permission(PermissionKey.FIELDDEFS_DELETE)" in src


def test_admin_protocol_update_still_requires_masterdata_update() -> None:
    from app.api.v1 import inspection_protocols_admin as admin_module

    src = inspect.getsource(admin_module)
    assert "require_permission(PermissionKey.MASTERDATA_UPDATE)" in src
    # Admin listing is gated by masterdata.read (not relaxed to records.*).
    assert "require_permission(PermissionKey.MASTERDATA_READ)" in src
