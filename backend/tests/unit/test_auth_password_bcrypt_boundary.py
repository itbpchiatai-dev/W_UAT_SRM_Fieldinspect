"""app/auth/password.py — bcrypt 72-UTF-8-byte boundary (round 8-23A.1).

Root cause this locks in: bcrypt 5.0.0 (this repo uses the `bcrypt`
package directly, not passlib) raises a bare ValueError — never
PasswordPolicyError — when handed a password whose UTF-8 encoding exceeds
72 bytes. `PasswordPolicyError` subclasses `ValueError`, so a caller doing
`except PasswordPolicyError` does NOT catch that bare ValueError; it was
escaping hash_password() as an uncaught exception. Reproduced directly
against the installed bcrypt before this fix:

    >>> import bcrypt
    >>> bcrypt.hashpw(("รหัสผ่านยาวมากของฉันนะจ๊ะA1").encode(), bcrypt.gensalt())
    ValueError: password cannot be longer than 72 bytes, ...

That exact string is 27 characters / 77 UTF-8 bytes — it passes every
existing policy rule (length >= 12, 2+ character classes, no repeated/
sequence pattern, not on the blocklist) and would have 500'd both
Create User and Admin Reset Password before this round.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.auth.password import (
    MAX_PASSWORD_BYTES,
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)

# The exact reproduction string from the round's brief: passes every OTHER
# policy rule, 77 UTF-8 bytes.
_THAI_OVER_LIMIT = "รหัสผ่านยาวมากของฉันนะจ๊ะA1"


def test_max_password_bytes_constant_is_72() -> None:
    assert MAX_PASSWORD_BYTES == 72


# --- ASCII boundary -------------------------------------------------------

def test_ascii_password_well_within_limit_hashes_and_verifies() -> None:
    pw = "Correct-Horse-Battery-9"  # 23 bytes
    h = hash_password(pw)
    assert h.startswith("$2b$")
    assert verify_password(pw, h)


def test_ascii_password_exactly_72_bytes_works() -> None:
    # "Aa1!" (4 chars, satisfies all 4 classes) + filler to hit exactly 72.
    pw = "Aa1!" + "x" * 68
    assert len(pw.encode("utf-8")) == 72
    h = hash_password(pw)
    assert verify_password(pw, h)


def test_ascii_password_73_bytes_is_rejected() -> None:
    pw = "Aa1!" + "x" * 69
    assert len(pw.encode("utf-8")) == 73
    with pytest.raises(PasswordPolicyError):
        hash_password(pw)


# --- Unicode / Thai boundary (the actual reported bug) --------------------

def test_thai_password_under_200_chars_but_over_72_utf8_bytes_is_rejected() -> None:
    assert len(_THAI_OVER_LIMIT) < 200
    assert len(_THAI_OVER_LIMIT.encode("utf-8")) > MAX_PASSWORD_BYTES
    with pytest.raises(PasswordPolicyError):
        hash_password(_THAI_OVER_LIMIT, context_terms=["someone"])


def test_thai_password_rejection_is_policy_error_never_bare_value_error() -> None:
    """The exact regression: PasswordPolicyError IS a ValueError, but a
    caller catching only PasswordPolicyError must never see bcrypt's own
    bare ValueError leak through instead."""
    try:
        hash_password(_THAI_OVER_LIMIT, context_terms=["someone"])
        pytest.fail("expected PasswordPolicyError")
    except PasswordPolicyError:
        pass
    except ValueError:
        pytest.fail("a bare ValueError escaped instead of PasswordPolicyError")


def test_thai_password_at_exactly_72_utf8_bytes_works() -> None:
    # Thai is 3 bytes/char in UTF-8. "Aa1" (3 ASCII bytes, and 3 of the 4
    # character classes) + 23 Thai chars = 3 + 69 = exactly 72 bytes.
    pw = "Aa1" + "ก" * 23
    assert len(pw.encode("utf-8")) == MAX_PASSWORD_BYTES
    h = hash_password(pw, context_terms=["someone"])
    assert verify_password(pw, h)


def test_thai_password_one_byte_over_the_limit_is_rejected() -> None:
    """The tightest possible failing case: 73 bytes, one over."""
    pw = "Aa1!" + "ก" * 23  # 4 + 69 = 73 bytes
    assert len(pw.encode("utf-8")) == MAX_PASSWORD_BYTES + 1
    with pytest.raises(PasswordPolicyError):
        hash_password(pw, context_terms=["someone"])


# --- invalid encoding -----------------------------------------------------

def test_lone_surrogate_fails_closed_as_policy_error() -> None:
    """A lone UTF-16 surrogate (reachable via a malformed-but-JSON-legal
    \\uXXXX escape) cannot be UTF-8 encoded at all. Must be refused, never
    crash with an uncaught UnicodeEncodeError."""
    pw = "Aa1!" + "\ud800" * 10
    with pytest.raises(PasswordPolicyError):
        hash_password(pw)


def test_lone_surrogate_error_is_policy_error_not_unicode_error_to_the_caller() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password("Aa1!\ud800\ud800\ud800\ud800\ud800\ud800\ud800\ud800")


# --- no truncation ----------------------------------------------------

def test_over_limit_password_is_never_truncated_and_hashpw_is_never_called() -> None:
    """A truncate-and-hash fallback would let two different long passwords
    collide past byte 72 — explicitly forbidden. Proven by mocking
    bcrypt.hashpw and asserting it is never reached."""
    with patch("app.auth.password.bcrypt.hashpw") as mock_hashpw:
        with pytest.raises(PasswordPolicyError):
            hash_password(_THAI_OVER_LIMIT, context_terms=["someone"])
    mock_hashpw.assert_not_called()


# --- error messages never leak the password --------------------------

def test_rejection_message_never_contains_the_password() -> None:
    try:
        hash_password(_THAI_OVER_LIMIT, context_terms=["someone"])
        pytest.fail("expected PasswordPolicyError")
    except PasswordPolicyError as exc:
        assert _THAI_OVER_LIMIT not in str(exc)


def test_ascii_rejection_message_never_contains_the_password() -> None:
    pw = "Aa1!" + "x" * 69
    try:
        hash_password(pw)
        pytest.fail("expected PasswordPolicyError")
    except PasswordPolicyError as exc:
        assert pw not in str(exc)


# --- verify_password: fail-closed, never throws ------------------------

def test_verify_password_never_throws_on_an_oversized_password() -> None:
    """login (unlike hash_password) has no pre-check the way
    validate_password gates creation — verify_password must still fail
    closed on a >72-byte candidate rather than raising."""
    over_limit = "x" * 200
    real_hash = hash_password("Correct-Horse-Battery-9")
    assert verify_password(over_limit, real_hash) is False


def test_verify_password_never_throws_on_an_unencodable_password() -> None:
    real_hash = hash_password("Correct-Horse-Battery-9")
    assert verify_password("Aa1!\ud800\ud800", real_hash) is False


def test_verify_password_fails_closed_on_malformed_hash_on_disk() -> None:
    assert verify_password("whatever", "not-a-real-bcrypt-hash") is False


def test_verify_password_still_verifies_a_real_match() -> None:
    pw = "Correct-Horse-Battery-9"
    h = hash_password(pw)
    assert verify_password(pw, h) is True
    assert verify_password(pw + "x", h) is False


# --- bcrypt rounds / hash format unchanged ------------------------------

def test_hash_format_and_rounds_are_unchanged() -> None:
    h = hash_password("Correct-Horse-Battery-9")
    # $2b$12$ — bcrypt variant 2b, cost factor 12 (unchanged by this round).
    assert h.startswith("$2b$12$")


def test_other_policy_rules_are_unaffected_by_the_byte_check() -> None:
    """Regression guard: adding the byte check must not have disturbed the
    existing rule order or messages for inputs that were already rejected
    for other reasons."""
    with pytest.raises(PasswordPolicyError, match="at least 12 characters"):
        hash_password("Short1!")
    with pytest.raises(PasswordPolicyError, match="mix at least 2"):
        hash_password("alllowercaseletters")
    with pytest.raises(PasswordPolicyError, match="common-password blocklist"):
        hash_password("password1234")
