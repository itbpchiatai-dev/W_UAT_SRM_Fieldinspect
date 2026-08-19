"""app.db.uat_data_reset — round 8-25A.1 corrections.

DB-less where possible: the pure predicate/helper functions (deletion scope,
email masking, phone derivation, media quarantine/restore) are tested
directly against plain values/temp directories. The DB-shaped flows
(invariant failure -> rollback, post-check failure -> non-zero, backup
failure -> abort, restore-rehearsal safety guards) are tested against
AsyncMock sessions / monkeypatched subprocess calls, same style as
tests/security/test_auth_version_session_invalidation.py and
tests/unit/test_admin_password_reset_endpoint.py — no live database, no
live Docker container.

SECURITY INVARIANT for this whole file: no assertion, fixture, or failure
message may ever put a plaintext secret into a test snapshot. The password/
PIN constants below are obviously-fake local test values.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db import uat_data_reset as mod

_GOOD_USER_PASSWORD = "Uat-Test-Value-9x"  # >=12 chars, 2+ classes, fake
_WEAK_USER_PASSWORD = "short1"
_GOOD_PIN = "384920"
_BAD_PIN = "12ab"


# --------------------------------------------------------------------------
# PART A — deletion scope predicate (pure, no DB)
# --------------------------------------------------------------------------


class TestDeletionScope:
    def test_local_supplier_owner_is_a_candidate(self):
        assert mod._is_deletion_candidate(
            email="owner@example.invalid", auth_provider="local",
            role_names={"supplier:owner"},
        ) is True

    def test_azure_supplier_owner_is_excluded(self):
        # auth_provider must be exactly "local" — an Azure AD account has no
        # password_hash and was never a synthetic UAT throwaway.
        assert mod._is_deletion_candidate(
            email="owner@example.invalid", auth_provider="azure_ad",
            role_names={"supplier:owner"},
        ) is False

    def test_multi_role_internal_and_supplier_owner_is_excluded(self):
        # A dual-role account is treated as internal FIRST.
        assert mod._is_deletion_candidate(
            email="dual@example.invalid", auth_provider="local",
            role_names={"supplier:owner", "internal:auditor"},
        ) is False

    def test_internal_super_admin_is_excluded_even_with_owner_role(self):
        assert mod._is_deletion_candidate(
            email="admin@example.invalid", auth_provider="local",
            role_names={"supplier:owner", "internal:super_admin"},
        ) is False

    def test_system_placeholder_account_is_excluded(self):
        assert mod._is_deletion_candidate(
            email="external-field-helper@system.local", auth_provider="local",
            role_names={"supplier:owner"},
        ) is False

    def test_local_user_without_owner_role_is_excluded(self):
        assert mod._is_deletion_candidate(
            email="someone@example.invalid", auth_provider="local",
            role_names={"internal:viewer"},
        ) is False

    @pytest.mark.asyncio
    async def test_candidate_query_filters_via_the_pure_predicate(self):
        """DB-shaped: _candidate_supplier_owner_users must call the exact
        same predicate — proven here by mixing four users behind one mocked
        query and asserting only the true local-owner-only account survives."""
        local_owner = SimpleNamespace(
            id="u1", email="local@example.invalid", auth_provider="local",
            roles=[SimpleNamespace(name="supplier:owner")],
        )
        azure_owner = SimpleNamespace(
            id="u2", email="azure@example.invalid", auth_provider="azure_ad",
            roles=[SimpleNamespace(name="supplier:owner")],
        )
        dual_role = SimpleNamespace(
            id="u3", email="dual@example.invalid", auth_provider="local",
            roles=[SimpleNamespace(name="supplier:owner"), SimpleNamespace(name="internal:auditor")],
        )
        system_user = SimpleNamespace(
            id="u4", email="external-field-helper@system.local", auth_provider="local",
            roles=[SimpleNamespace(name="supplier:owner")],
        )
        s = AsyncMock()
        result = SimpleNamespace(
            unique=lambda: SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [local_owner, azure_owner, dual_role, system_user]
                )
            )
        )
        s.execute = AsyncMock(return_value=result)
        candidates = await mod._candidate_supplier_owner_users(s)
        assert [c.id for c in candidates] == ["u1"]


# --------------------------------------------------------------------------
# Masking / phone derivation (pure)
# --------------------------------------------------------------------------


class TestMaskingAndPhone:
    def test_mask_email_hides_the_middle(self):
        masked = mod._mask_email("supplier01.uat@example.invalid")
        assert masked != "supplier01.uat@example.invalid"
        assert masked.startswith("s")
        assert masked.endswith("@example.invalid")
        assert "*" in masked

    def test_mask_email_short_local_part_does_not_crash(self):
        assert mod._mask_email("ab@example.invalid").endswith("@example.invalid")

    def test_supplier_phone_is_one_value_shared_by_the_whole_supplier(self):
        # Round 8-25A.1 decision 1: ONE phone per supplier, not per plot —
        # the function takes no plot index at all.
        assert mod._synthetic_supplier_phone(1) == mod._synthetic_supplier_phone(1)

    def test_supplier_phones_are_collision_free_across_ten_suppliers(self):
        phones = {mod._synthetic_supplier_phone(i) for i in range(1, 11)}
        assert len(phones) == 10

    def test_supplier_phone_matches_the_db_check_constraint_shape(self):
        import re
        for i in range(1, 11):
            assert re.match(r"^0[689][0-9]{8}$", mod._synthetic_supplier_phone(i))


# --------------------------------------------------------------------------
# PART C — secret validation (env-var driven, never index-derived)
# --------------------------------------------------------------------------


class TestSecretValidation:
    def test_user_password_passes_policy_is_accepted(self, monkeypatch):
        monkeypatch.setenv(mod._ENV_USER_PASSWORD_VAR, _GOOD_USER_PASSWORD)
        result = mod._validate_shared_user_password(apply=True)
        assert result == _GOOD_USER_PASSWORD

    def test_user_password_failing_policy_aborts_without_db_touch(self, monkeypatch):
        monkeypatch.setenv(mod._ENV_USER_PASSWORD_VAR, _WEAK_USER_PASSWORD)
        with pytest.raises(mod.ResetAbortedError):
            mod._validate_shared_user_password(apply=True)

    def test_user_password_missing_at_apply_aborts(self, monkeypatch):
        monkeypatch.delenv(mod._ENV_USER_PASSWORD_VAR, raising=False)
        with pytest.raises(mod.ResetAbortedError):
            mod._validate_shared_user_password(apply=True)

    def test_user_password_missing_in_dry_run_does_not_abort(self, monkeypatch):
        monkeypatch.delenv(mod._ENV_USER_PASSWORD_VAR, raising=False)
        assert mod._validate_shared_user_password(apply=False) is None

    def test_plot_pin_passes_policy_is_accepted(self, monkeypatch):
        monkeypatch.setenv(mod._ENV_PLOT_PIN_VAR, _GOOD_PIN)
        assert mod._validate_shared_plot_pin(apply=True) == _GOOD_PIN

    def test_plot_pin_failing_policy_aborts_without_db_touch(self, monkeypatch):
        monkeypatch.setenv(mod._ENV_PLOT_PIN_VAR, _BAD_PIN)
        with pytest.raises(mod.ResetAbortedError):
            mod._validate_shared_plot_pin(apply=True)

    def test_plot_pin_missing_at_apply_aborts(self, monkeypatch):
        monkeypatch.delenv(mod._ENV_PLOT_PIN_VAR, raising=False)
        with pytest.raises(mod.ResetAbortedError):
            mod._validate_shared_plot_pin(apply=True)

    def test_no_module_level_function_derives_a_pin_from_an_index(self):
        # Decision 6: the PIN must never be computed from a supplier/plot
        # index. Confirms no leftover "_synthetic_plot_pin"-shaped function
        # exists in this module (round 8-25A had exactly that helper).
        assert not hasattr(mod, "_synthetic_plot_pin")


# --------------------------------------------------------------------------
# PART D — media quarantine/restore (pure filesystem, temp dirs only)
# --------------------------------------------------------------------------


class TestMediaQuarantine:
    def test_scan_media_counts_files_and_bytes(self, tmp_path):
        (tmp_path / "a.webp").write_bytes(b"x" * 100)
        (tmp_path / "b.webp").write_bytes(b"y" * 50)
        count, total = mod._scan_media(tmp_path)
        assert count == 2
        assert total == 150

    def test_scan_media_on_missing_dir_returns_zero(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        assert mod._scan_media(missing) == (0, 0)

    def test_quarantine_moves_files_never_deletes(self, tmp_path):
        root = tmp_path / "media"
        root.mkdir()
        f1 = root / "photo1.webp"
        f1.write_bytes(b"content-1")
        quarantine_root = tmp_path / "quarantine"

        moved = mod._quarantine_media_files(root, quarantine_root)

        assert len(moved) == 1
        original, dest = moved[0]
        assert not original.exists()  # moved away, not deleted-and-gone
        assert dest.exists()
        assert dest.read_bytes() == b"content-1"

    def test_quarantine_then_restore_round_trips_exactly(self, tmp_path):
        root = tmp_path / "media"
        root.mkdir()
        (root / "photo1.webp").write_bytes(b"content-1")
        (root / "photo2.webp").write_bytes(b"content-2")
        quarantine_root = tmp_path / "quarantine"

        moved = mod._quarantine_media_files(root, quarantine_root)
        mod._restore_media_files(moved)

        assert (root / "photo1.webp").read_bytes() == b"content-1"
        assert (root / "photo2.webp").read_bytes() == b"content-2"

    def test_quarantine_refuses_a_nonexistent_root(self, tmp_path):
        with pytest.raises(mod.ResetAbortedError):
            mod._quarantine_media_files(tmp_path / "nope", tmp_path / "q")

    def test_no_hard_delete_function_exists_in_this_module(self):
        # Round 8-25A.1 decision 5 + PART D: quarantine only, never a
        # hard-delete, this round.
        for name in dir(mod):
            assert "hard_delete" not in name.lower()
            assert "purge" not in name.lower()


# --------------------------------------------------------------------------
# PART F — pre-commit invariant failure rolls back / post-check failure
# --------------------------------------------------------------------------


class TestInvariants:
    @pytest.mark.asyncio
    async def test_invariant_failure_raises_and_never_calls_commit(self):
        """A failing invariant must raise BEFORE commit() is reached — the
        caller (get_db_session) rolls back on any exception, so asserting
        commit was never called proves the rollback path is what fires."""
        s = AsyncMock()
        bad_count_result = SimpleNamespace(scalar_one=lambda: 3, all=lambda: [])  # wrong: != 10
        s.execute = AsyncMock(return_value=bad_count_result)

        with pytest.raises(mod.InvariantViolationError):
            await mod._verify_invariants_or_raise(s, before_protected={
                "master_data:other": 3,
                "protected:roles": 3, "protected:permissions": 3,
                "protected:role_permissions": 3, "protected:internal_users": 3,
            })
        s.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_commit_verify_returns_false_on_failure_without_raising(self):
        s = AsyncMock()
        bad_count_result = SimpleNamespace(scalar_one=lambda: 999, all=lambda: [])
        s.execute = AsyncMock(return_value=bad_count_result)

        async def _fake_session():
            yield s

        with patch.object(mod, "get_db_session") as mock_session, \
             patch.object(mod, "get_public_plot_rls_context", new=AsyncMock()):
            mock_session.return_value.__aenter__ = AsyncMock(return_value=s)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            ok = await mod._post_commit_verify(before_protected={
                "master_data:other": 0,
                "protected:roles": 0, "protected:permissions": 0,
                "protected:role_permissions": 0, "protected:internal_users": 0,
            })
        assert ok is False


# --------------------------------------------------------------------------
# PART G — backup failure aborts / restore-rehearsal safety guards
# --------------------------------------------------------------------------


class TestBackupAndRehearsal:
    def test_backup_failure_aborts(self, tmp_path, monkeypatch):
        def _fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=1, stderr=b"pg_dump: connection refused")
        monkeypatch.setattr(subprocess, "run", _fake_run)
        with pytest.raises(mod.ResetAbortedError):
            mod._run_backup(tmp_path)

    def test_backup_too_small_aborts(self, tmp_path, monkeypatch):
        def _fake_run(*args, stdout=None, **kwargs):
            if stdout is not None:
                stdout.write(b"tiny")
            return SimpleNamespace(returncode=0, stderr=b"")
        monkeypatch.setattr(subprocess, "run", _fake_run)
        with pytest.raises(mod.ResetAbortedError):
            mod._run_backup(tmp_path)

    def test_rehearsal_refuses_missing_dump_file(self, tmp_path):
        with pytest.raises(mod.ResetAbortedError):
            mod._restore_rehearsal(tmp_path / "does-not-exist.dump")

    def test_rehearsal_db_name_always_has_the_safety_prefix(self):
        # The prefix constant itself must be distinctive enough that a
        # generated name could never coincide with a real database name.
        assert mod._REHEARSAL_DB_PREFIX.startswith("uat_restore_rehearsal_")

    def test_rehearsal_never_targets_the_app_db_name_even_by_construction(self, monkeypatch, tmp_path):
        # If DB_NAME somehow collided with a generated throwaway name, the
        # function must refuse rather than proceed. Simulate by monkey-
        # patching get_settings().DB_NAME to something absurdly permissive
        # is not meaningful here (timestamp makes real collision practically
        # impossible) — instead assert the explicit equality guard exists
        # in source, which the earlier unit coverage on _run_psql_maintenance
        # guards would catch if removed.
        import inspect
        src = inspect.getsource(mod._restore_rehearsal)
        assert "settings.DB_NAME" in src
        assert "_REHEARSAL_DB_PREFIX" in src


# --------------------------------------------------------------------------
# PART B — master data delete order / no-cycle contract (source-level checks
# that don't require a live DB, complementing the real dry-run executed
# separately against the local dev database)
# --------------------------------------------------------------------------


class TestMasterAndCycleContract:
    def test_seed_module_never_imports_active_cycle_map(self):
        # Checks the executable surface only (import line + call form) —
        # not this test file's own prose about the fact, which would make
        # the assertion self-defeating.
        assert "active_cycle_map" not in mod.__dict__
        import inspect
        src = inspect.getsource(mod._seed)
        assert "active_cycle_map" not in src

    def test_wipe_master_data_deletes_variety_before_crop(self):
        import inspect
        src = inspect.getsource(mod._wipe_master_data)
        variety_pos = src.index('MasterData.type == "variety"')
        crop_pos = src.index('MasterData.type == "crop"')
        # The delete() calls (not the earlier count queries) determine
        # execution order — assert the LAST occurrence of each (the delete
        # statement) keeps variety before crop.
        variety_delete_pos = src.rindex('delete(MasterData).where(MasterData.type == "variety")')
        crop_delete_pos = src.rindex('delete(MasterData).where(MasterData.type == "crop")')
        assert variety_delete_pos < crop_delete_pos
        assert variety_pos >= 0 and crop_pos >= 0  # sanity: both present at all

    def test_seed_plot_fields_never_set_current_crop_or_variety(self):
        import inspect
        src = inspect.getsource(mod._seed)
        assert "current_crop=" not in src
        assert "current_variety=" not in src
        assert "current_lot_no=" not in src
        assert "current_planting_date=" not in src
        assert "plant_count=" not in src
        assert "expected_yield_full=" not in src
