"""app.core.phone.normalize_thai_mobile — canonicalize Thai mobile numbers
(round 8-3A). Boundary/invalid/format-variant coverage; the canonical shape is
^0[689][0-9]{8}$ and invalid input is a clean ValueError that never echoes the
number back (PII)."""
from __future__ import annotations

import pytest

from app.core.phone import normalize_thai_mobile

_CANONICAL = "0845552162"


@pytest.mark.parametrize(
    "raw",
    [
        "0845552162",       # already canonical
        "084-555-2162",     # dashes
        "084 555 2162",     # spaces
        " 0845552162 ",     # surrounding whitespace
        "(084) 555-2162",   # parentheses + spaces + dash
        "084.555.2162",     # dots (spreadsheet exports)
        "+66845552162",     # +66 country code
        "66845552162",      # 66 country code, no plus
        "+66 84-555-2162",  # +66 with formatting
    ],
)
def test_accepts_and_canonicalizes(raw: str) -> None:
    assert normalize_thai_mobile(raw) == _CANONICAL


@pytest.mark.parametrize(
    "prefix",
    ["06", "08", "09"],
)
def test_accepts_all_valid_mobile_prefixes(prefix: str) -> None:
    number = prefix + "12345678"
    assert normalize_thai_mobile(number) == number


@pytest.mark.parametrize(
    "bad",
    [
        "0712345678",      # invalid prefix (07 not in 6/8/9)
        "0512345678",      # invalid prefix
        "084555216",       # too short (9 digits)
        "08455521620",     # too long (11 digits)
        "084abc2162",      # letters
        "",                # blank
        "   ",             # whitespace only
        "+660845552162",   # +66 then a leading 0 → 11 digits, not truncated
        "660845552162",    # 66 then a leading 0 → 11 digits
        "021234567",       # landline shape
    ],
)
def test_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_thai_mobile(bad)


def test_error_message_never_echoes_the_number() -> None:
    """docs/security.md §9.2 — a validation error must not carry the PII."""
    secret = "0812349999"
    try:
        normalize_thai_mobile(secret + "0")  # 11 digits → invalid
    except ValueError as exc:
        assert secret not in str(exc)
    else:  # pragma: no cover - the input above is always invalid
        raise AssertionError("expected ValueError")


def test_does_not_silently_truncate() -> None:
    """An 11-digit number must be rejected, never trimmed to 10."""
    with pytest.raises(ValueError):
        normalize_thai_mobile("08455521620")
