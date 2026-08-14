"""Bcrypt password hashing + strength policy ("Easy + blocklist").

Using `bcrypt` directly (not passlib) to keep the cold-path dependency
graph small — passlib pulls in a lot of legacy hash backends we never
use, and its py3.13 status is flaky.

Policy (keep in sync with scripts/password_policy.py — that copy is the
setup wizard's; this one is enforced at runtime on every set/reset).
Tuned for low friction on external (vendor/customer) users — length +
blocklist carry the security (NIST SP 800-63B), not composition rules:
  - length >= MIN_PASSWORD_LENGTH
  - at least 2 of 4 character classes (upper / lower / digit / symbol)
  - reject all-same-char, repeated short patterns (123456123456),
    whole-string keyboard/number sequences (123456789012)
  - reject a curated common-password blocklist
  - reject anything containing a context term (email local-part, etc.)
"""
from __future__ import annotations

from collections.abc import Iterable

import bcrypt

MIN_PASSWORD_LENGTH = 12

COMMON_PASSWORDS = frozenset({
    "password", "passw0rd", "passwd", "qwerty", "qwertyuiop", "letmein",
    "iloveyou", "admin", "administrator", "root", "welcome", "monkey",
    "dragon", "abc", "abcdef", "abcdefg", "abcabc", "changeme", "change",
    "secret", "master", "superman", "trustno", "starwars", "football",
    "baseball", "sunshine", "princess", "azerty", "qazwsx", "qweasd",
    "login", "guest", "test", "demo",
})

_SEQUENCES = (
    "0123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "qwertyuiopasdfghjklzxcvbnm",
)


class PasswordPolicyError(ValueError):
    """Raised when a candidate password fails the policy check."""


def _classes_present(password: str) -> int:
    return sum((
        any(c.isupper() for c in password),
        any(c.islower() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ))


def _is_repeated_pattern(password: str) -> bool:
    n = len(password)
    return any(
        n % size == 0 and password == password[:size] * (n // size)
        for size in range(1, n // 2 + 1)
    )


def _is_pure_sequence(password: str) -> bool:
    pw = password.lower()
    return any(
        pw in base + base
        for seq in _SEQUENCES
        for base in (seq, seq[::-1])
    )


def validate_password(password: str, *, context_terms: Iterable[str] = ()) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if _classes_present(password) < 2:
        raise PasswordPolicyError(
            "Password must mix at least 2 of: uppercase, lowercase, digit, "
            "symbol (an all-numeric or all-letter password is rejected)."
        )
    if _is_repeated_pattern(password):
        raise PasswordPolicyError(
            "Password repeats a short pattern (e.g. 123456123456)."
        )
    if _is_pure_sequence(password):
        raise PasswordPolicyError(
            "Password is a keyboard/number sequence (e.g. 123456789012)."
        )
    lowered = password.lower()
    stripped = lowered.rstrip("0123456789") or lowered
    if lowered in COMMON_PASSWORDS or stripped in COMMON_PASSWORDS:
        raise PasswordPolicyError(
            "Password is on the common-password blocklist."
        )
    for term in context_terms:
        term = (term or "").strip().lower()
        if len(term) >= 4 and term in lowered:
            raise PasswordPolicyError(
                "Password must not contain your email or other personal info."
            )


def hash_password(password: str, *, context_terms: Iterable[str] = ()) -> str:
    """bcrypt with 12 rounds (~250ms on modern hardware — slow enough)."""
    validate_password(password, context_terms=context_terms)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash on disk — treat as auth failure, not crash.
        return False
