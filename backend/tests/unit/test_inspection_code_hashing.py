"""app/services/inspection_code.py — pure-function tests.

Round 8-3G: this module is HISTORICAL-MIGRATION-COMPATIBILITY ONLY now — no
live endpoint calls verify_inspection_code_plain anymore (the two
verification endpoints and suppliers.inspection_code itself are retired).
The functions themselves are unchanged and still exercised directly here so
a future edit to this file can't silently break what migrations 0023/0027
depend on."""
from __future__ import annotations

from app.services.inspection_code import (
    DEFAULT_INSPECTION_CODE,
    hash_inspection_code,
    verify_inspection_code,
    verify_inspection_code_plain,
)


def test_default_inspection_code_is_1111() -> None:
    assert DEFAULT_INSPECTION_CODE == "1111"


# --- plaintext verify (the live path) ---------------------------------

def test_plain_verify_correct_code_succeeds() -> None:
    assert verify_inspection_code_plain("1111", "1111") is True


def test_plain_verify_wrong_code_fails() -> None:
    assert verify_inspection_code_plain("2222", "1111") is False


def test_plain_verify_empty_submitted_code_fails() -> None:
    assert verify_inspection_code_plain("", "1111") is False


def test_plain_verify_empty_expected_code_fails() -> None:
    # An unset/blank stored code must never verify against anything.
    assert verify_inspection_code_plain("1111", "") is False


def test_plain_verify_is_case_and_whitespace_exact() -> None:
    assert verify_inspection_code_plain("1111 ", "1111") is False
    assert verify_inspection_code_plain("ABCD", "abcd") is False


# --- legacy bcrypt helpers (kept for the 0027 downgrade) --------------

def test_hash_does_not_contain_plaintext() -> None:
    h = hash_inspection_code("1111")
    assert "1111" not in h
    assert h != "1111"


def test_legacy_verify_correct_code_succeeds() -> None:
    h = hash_inspection_code("1111")
    assert verify_inspection_code("1111", h) is True


def test_legacy_verify_wrong_code_fails() -> None:
    h = hash_inspection_code("1111")
    assert verify_inspection_code("2222", h) is False


def test_legacy_verify_malformed_hash_fails_without_crash() -> None:
    assert verify_inspection_code("1111", "not-a-bcrypt-hash") is False
