"""HIGH-2 regression — patch_user must reject self-elevation attempts.

These are static-shape assertions on the source — full request-shaped
tests live in tests/integration/ where the DB fixture exists. The goal
here is to catch a regression where someone accidentally removes the
`is_self` checks during a future cleanup pass.
"""
from __future__ import annotations

import inspect

from app.api.v1 import users as users_module


def test_patch_user_contains_self_guard() -> None:
    src = inspect.getsource(users_module.patch_user)
    assert "is_self" in src, \
        "Deep-Audit HIGH-2 — patch_user must compute is_self for the self-guard"
    assert "users.approve" in src, \
        "Deep-Audit HIGH-2 — patch_user must gate is_approved on users.approve"
    assert "Cannot change approval state on your own account" in src


def test_deactivate_user_contains_self_guard() -> None:
    src = inspect.getsource(users_module.deactivate_user)
    assert "Cannot deactivate your own account" in src


def test_bulk_approve_filters_caller_id() -> None:
    src = inspect.getsource(users_module.bulk_approve)
    assert "uid != user.id" in src, \
        "Deep-Audit HIGH-2 — bulk_approve must drop the caller\'s own id"
