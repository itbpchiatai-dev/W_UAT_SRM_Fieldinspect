"""Round 8-9A — plot inspection password policy, bcrypt hashing, and the HMAC
blind-index digest (app/auth/plot_access_password.py).

DB-less. The pepper is injected by patching get_settings inside the helper
module, so no real secret is ever read, printed, or required here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.auth.plot_access_password import (
    PLOT_ACCESS_PASSWORD_MAX_LENGTH,
    PLOT_ACCESS_PASSWORD_MIN_LENGTH,
    PlotAccessPasswordPolicyError,
    PlotAccessPepperMissingError,
    build_plot_access_password_lookup_digest,
    hash_plot_access_password,
    validate_plot_access_password,
    verify_plot_access_password,
)

_P = "app.auth.plot_access_password"
# Test-only fake peppers — not secrets, never read from .env.
_PEPPER_A = "a" * 40
_PEPPER_B = "b" * 40


def _with_pepper(pepper: str):
    return patch(
        f"{_P}.get_settings",
        return_value=SimpleNamespace(PLOT_ACCESS_PASSWORD_PEPPER=pepper),
    )


# --- policy -----------------------------------------------------------------

def test_policy_bounds_are_four_to_twenty() -> None:
    assert PLOT_ACCESS_PASSWORD_MIN_LENGTH == 4
    assert PLOT_ACCESS_PASSWORD_MAX_LENGTH == 20


@pytest.mark.parametrize(
    "pin",
    [
        "1357", "9024", "135790", "482913", "102030",
        "1" * PLOT_ACCESS_PASSWORD_MAX_LENGTH,      # exactly 20 — the ceiling
        "1234567890" * 2,                            # 20 digits, mixed
    ],
)
def test_accepts_four_to_twenty_ascii_digits(pin: str) -> None:
    assert validate_plot_access_password(pin) == pin


@pytest.mark.parametrize(
    "easy",
    [
        # Round 8-9B.0 — every one of these used to be rejected as "too easy to
        # guess". They are now VALID by product decision: field users share the
        # code by voice, and a rule that rejects what they pick just gets it
        # written down somewhere worse.
        "0000", "1111", "9999",           # repeated, minimum length
        "111111", "000000", "555555",     # repeated, 6 digits
        "1234", "4321", "0123",           # sequences, minimum length
        "123456", "987654", "012345",     # sequences, 6 digits
        "12345678901234567890",           # sequence, maximum length
    ],
)
def test_accepts_repeated_and_sequential_codes(easy: str) -> None:
    assert validate_plot_access_password(easy) == easy


def test_trims_surrounding_whitespace_before_validating() -> None:
    assert validate_plot_access_password("  135790\n") == "135790"
    assert validate_plot_access_password("\t1357 ") == "1357"


@pytest.mark.parametrize(
    "bad",
    [
        "",                       # empty
        "1",                      # 1 digit
        "12",                     # 2 digits
        "123",                    # 3 digits — one below the floor
        "1" * 21,                 # 21 digits — one above the ceiling
        "1" * 64,                 # far too long
        "13579a",                 # letter
        "abcd",                   # letters only
        "13 790",                 # inner space (trim is edge-only)
        "1 234",                  # inner space at the boundary length
        "12-34",                  # dash
        "๑๓๕๗",                   # Thai digits — str.isdigit()/\\d would accept these
        "١٣٥٧",                   # Arabic-Indic digits
        "１２３４",                # full-width digits
    ],
)
def test_rejects_wrong_length_or_non_ascii_digits(bad: str) -> None:
    with pytest.raises(PlotAccessPasswordPolicyError):
        validate_plot_access_password(bad)


def test_policy_error_never_echoes_the_submitted_code() -> None:
    """A validation message reaches the admin UI verbatim — it must never
    contain the value that was typed."""
    for bad in ("123", "1" * 21, "13579a", "abcdefgh", "１２３４"):
        with pytest.raises(PlotAccessPasswordPolicyError) as exc:
            validate_plot_access_password(bad)
        assert bad not in str(exc.value)
        assert bad not in repr(exc.value)


def test_there_is_only_one_static_policy_message() -> None:
    """No branch may hint at WHICH rule failed — one message for every
    rejection, and it states the rule without quoting the input."""
    messages = set()
    for bad in ("", "123", "1" * 21, "13579a", "12 34", "๑๓๕๗"):
        with pytest.raises(PlotAccessPasswordPolicyError) as exc:
            validate_plot_access_password(bad)
        messages.add(str(exc.value))
    assert messages == {"รหัสยืนยันแปลงต้องเป็นตัวเลข 0-9 จำนวน 4 ถึง 20 หลัก"}


# --- bcrypt hash / verify ---------------------------------------------------

def test_hash_is_bcrypt_and_never_the_plaintext() -> None:
    pin = "135790"
    hashed = hash_plot_access_password(pin)
    assert hashed != pin
    assert pin not in hashed
    assert hashed.startswith("$2b$")


def test_hash_rejects_a_malformed_code_so_it_can_never_reach_the_database() -> None:
    for bad in ("123", "1" * 21, "13579a", "๑๓๕๗"):
        with pytest.raises(PlotAccessPasswordPolicyError):
            hash_plot_access_password(bad)


@pytest.mark.parametrize("pin", ["1357", "0000", "1234", "135790", "1" * 20])
def test_hash_and_verify_round_trip_across_the_whole_length_range(pin: str) -> None:
    hashed = hash_plot_access_password(pin)
    assert hashed.startswith("$2b$")
    assert pin not in hashed
    assert verify_plot_access_password(pin, hashed) is True


def test_verify_true_for_correct_false_for_wrong() -> None:
    hashed = hash_plot_access_password("135790")
    assert verify_plot_access_password("135790", hashed) is True
    assert verify_plot_access_password("135791", hashed) is False
    assert verify_plot_access_password("1357", hashed) is False


def test_verify_malformed_or_empty_hash_returns_false_never_raises() -> None:
    for broken in ("", "not-a-bcrypt-hash", "$2b$12$tooshort", "   "):
        assert verify_plot_access_password("135790", broken) is False


def test_existing_six_digit_credentials_still_verify() -> None:
    """Round 8-9B.0 compatibility: every credential set under the OLD exact-6
    policy must keep working. verify never re-runs the policy, so this holds
    even for values the old policy itself would have rejected."""
    import bcrypt

    for legacy_pin in (b"135790", b"482913", b"111111"):
        stored = bcrypt.hashpw(legacy_pin, bcrypt.gensalt(rounds=4)).decode("utf-8")
        assert verify_plot_access_password(legacy_pin.decode(), stored) is True


def test_verify_does_not_re_run_the_current_policy() -> None:
    """A future policy change must never lock out a plot retroactively — even a
    3-digit credential (illegal to SET today) still verifies if it is what the
    row actually holds."""
    import bcrypt

    stored = bcrypt.hashpw(b"123", bcrypt.gensalt(rounds=4)).decode("utf-8")
    assert verify_plot_access_password("123", stored) is True
    with pytest.raises(PlotAccessPasswordPolicyError):
        validate_plot_access_password("123")   # ...but it can't be SET any more


# --- HMAC blind index -------------------------------------------------------

def test_digest_is_deterministic_for_the_same_pepper() -> None:
    with _with_pepper(_PEPPER_A):
        first = build_plot_access_password_lookup_digest("135790")
        second = build_plot_access_password_lookup_digest("135790")
    assert first == second


def test_digest_is_lowercase_hex_64() -> None:
    with _with_pepper(_PEPPER_A):
        digest = build_plot_access_password_lookup_digest("135790")
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_digest_differs_when_the_pepper_differs() -> None:
    with _with_pepper(_PEPPER_A):
        a = build_plot_access_password_lookup_digest("135790")
    with _with_pepper(_PEPPER_B):
        b = build_plot_access_password_lookup_digest("135790")
    assert a != b


def test_digest_differs_per_pin_and_is_never_the_plaintext() -> None:
    with _with_pepper(_PEPPER_A):
        a = build_plot_access_password_lookup_digest("135790")
        b = build_plot_access_password_lookup_digest("135791")
    assert a != b
    assert "135790" not in a


def test_digest_is_not_an_unkeyed_sha256() -> None:
    """The minimum policy is 4 digits (10^4), so an unkeyed SHA-256 would be a
    trivially precomputed rainbow table — the digest must be HMAC-keyed with
    the dedicated pepper."""
    import hashlib

    with _with_pepper(_PEPPER_A):
        digest = build_plot_access_password_lookup_digest("135790")
        short = build_plot_access_password_lookup_digest("1357")
    assert digest != hashlib.sha256(b"135790").hexdigest()
    assert short != hashlib.sha256(b"1357").hexdigest()


@pytest.mark.parametrize("pin", ["1357", "0000", "1234", "135790", "1" * 20])
def test_digest_is_deterministic_across_the_whole_length_range(pin: str) -> None:
    with _with_pepper(_PEPPER_A):
        assert (
            build_plot_access_password_lookup_digest(pin)
            == build_plot_access_password_lookup_digest(pin)
        )
        assert pin not in build_plot_access_password_lookup_digest(pin)


def test_digest_trims_the_same_way_validation_does() -> None:
    """Set and lookup must agree over a stray space typed on a phone."""
    with _with_pepper(_PEPPER_A):
        assert (
            build_plot_access_password_lookup_digest(" 135790 ")
            == build_plot_access_password_lookup_digest("135790")
        )


@pytest.mark.parametrize("missing", ["", "   ", None])
def test_missing_pepper_is_a_controlled_failure_not_a_crash(missing) -> None:
    with _with_pepper(missing):
        with pytest.raises(PlotAccessPepperMissingError):
            build_plot_access_password_lookup_digest("135790")


def test_missing_pepper_error_never_echoes_the_pin() -> None:
    with _with_pepper(""):
        with pytest.raises(PlotAccessPepperMissingError) as exc:
            build_plot_access_password_lookup_digest("135790")
    assert "135790" not in str(exc.value)


def test_helper_never_falls_back_to_the_jwt_secret() -> None:
    from pathlib import Path

    import app.auth.plot_access_password as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    # JWT_SECRET_KEY may only appear in prose explaining why it is NOT used.
    assert "settings.JWT_SECRET_KEY" not in src
    assert "get_settings().JWT_SECRET_KEY" not in src


# --- round 8-9A.1: SecretStr pepper -----------------------------------------

def test_digest_is_identical_for_a_secretstr_and_a_plain_pepper() -> None:
    """Settings now holds the pepper as SecretStr. That is a masking change
    only — every digest already written to the database must still verify."""
    from pydantic import SecretStr

    with _with_pepper(_PEPPER_A):
        as_plain = build_plot_access_password_lookup_digest("135790")
    with _with_pepper(SecretStr(_PEPPER_A)):
        as_secret = build_plot_access_password_lookup_digest("135790")
    assert as_plain == as_secret


def test_missing_secretstr_pepper_is_still_a_controlled_failure() -> None:
    from pydantic import SecretStr

    for blank in (SecretStr(""), SecretStr("   ")):
        with _with_pepper(blank):
            with pytest.raises(PlotAccessPepperMissingError):
                build_plot_access_password_lookup_digest("135790")


def test_pepper_never_appears_in_an_exception_or_repr() -> None:
    """The unwrapped pepper must not ride out on an error path."""
    from pydantic import SecretStr

    with _with_pepper(SecretStr(_PEPPER_A)):
        # a failure raised AFTER the pepper was read
        with pytest.raises(PlotAccessPasswordPolicyError) as exc:
            hash_plot_access_password("123")   # 3 digits — below the floor
    assert _PEPPER_A not in str(exc.value) + repr(exc.value)

    with _with_pepper(""):
        with pytest.raises(PlotAccessPepperMissingError) as missing:
            build_plot_access_password_lookup_digest("135790")
    assert "PLOT_ACCESS_PASSWORD_PEPPER" in str(missing.value)   # names it only


def test_no_pepper_accessor_is_exported_from_the_helper() -> None:
    """Unwrapping happens inside this module and nowhere else — nothing that
    can hand the pepper out is part of its public surface."""
    import app.auth.plot_access_password as mod

    # PlotAccessPepperMissingError is exported (callers map it to 503); the
    # thing that can actually REACH the pepper is not.
    assert "_pepper_key_bytes" not in mod.__all__
    assert mod._pepper_key_bytes.__name__.startswith("_")
    assert all(
        "pepper" not in name.lower() or name.endswith("Error") for name in mod.__all__
    )


def test_pepper_key_bytes_is_the_only_unwrap_site() -> None:
    from pathlib import Path

    import app.auth.plot_access_password as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert src.count("get_secret_value") == 1


def test_plot_access_policy_is_separate_from_the_login_password_policy() -> None:
    """The 12-char login policy would reject every legal numeric code, so this
    module must not reuse it — in either direction."""
    from app.auth.password import validate_password

    for pin in ("1357", "135790", "1" * 20):
        with pytest.raises(Exception):
            validate_password(pin)      # login policy rejects it...
        assert validate_plot_access_password(pin) == pin   # ...this one does not
