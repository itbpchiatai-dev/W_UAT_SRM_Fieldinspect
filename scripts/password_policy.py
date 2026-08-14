#!/usr/bin/env python3
"""Shared password-strength policy for the setup wizards.

Single source of truth for `setup.py` and `init_project.py` so the
bootstrap super-admin password is held to the SAME bar as the runtime
`backend/app/auth/password.py::validate_password` the scaffold emits.

The runtime copy lives inside `scripts/scaffold.py` (it ships to a
different machine and can't import this module) — keep the two in sync.
Policy: "Easy + blocklist" (chosen 2026-06-05, tuned for external users):
  - length >= MIN_PASSWORD_LENGTH
  - at least 2 of 4 character classes (upper / lower / digit / symbol) —
    deliberately low friction; a 12-char passphrase like "khaohom rice 7"
    passes. Length + blocklist carry the security (NIST SP 800-63B).
  - reject all-same-char, repeated short patterns (e.g. 123456123456),
    and whole-string keyboard/number sequences (e.g. 123456789012)
  - reject a curated common-password blocklist
  - reject anything that contains a context term (email local-part,
    project slug) — those are trivially guessable for THIS deployment
"""
from __future__ import annotations

from collections.abc import Iterable

MIN_PASSWORD_LENGTH = 12

# Curated weak/common passwords (lowercased, no trailing-digit variants —
# those are handled by _strip_trailing_digits before lookup). Small on
# purpose: this is a guardrail against the obvious, not a breach corpus.
COMMON_PASSWORDS = frozenset({
    "password", "passw0rd", "passwd", "qwerty", "qwertyuiop", "letmein",
    "iloveyou", "admin", "administrator", "root", "welcome", "monkey",
    "dragon", "abc", "abcdef", "abcdefg", "abcabc", "changeme", "change",
    "secret", "master", "superman", "trustno", "starwars", "football",
    "baseball", "sunshine", "princess", "azerty", "qazwsx", "qweasd",
    "login", "guest", "test", "demo", "chiatai", "chiataigroup",
})

# Linear keyboard / number runs — a password that is wholly a slice of one
# of these (forward or reversed, with wrap) is a pure sequence.
_SEQUENCES = (
    "0123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "qwertyuiopasdfghjklzxcvbnm",
)


def _classes_present(password: str) -> int:
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(not c.isalnum() for c in password)
    return sum((has_upper, has_lower, has_digit, has_symbol))


def _strip_trailing_digits(text: str) -> str:
    return text.rstrip("0123456789") or text


def _is_repeated_pattern(password: str) -> bool:
    """True if password is one short pattern repeated (aaaa, 123123, abab)."""
    n = len(password)
    for size in range(1, n // 2 + 1):
        if n % size == 0 and password == password[:size] * (n // size):
            return True
    return False


def _is_pure_sequence(password: str) -> bool:
    """True if the WHOLE password is a slice of a keyboard/number run."""
    pw = password.lower()
    for seq in _SEQUENCES:
        for base in (seq, seq[::-1]):
            if pw in base + base:  # `+ base` catches wrap-around (…9012…)
                return True
    return False


def password_strength_error(
    password: str, *, context_terms: Iterable[str] = ()
) -> str | None:
    """Return a human-readable reason the password is too weak, or None.

    `context_terms` are deployment-specific strings (email local-part,
    project slug) that must not appear inside the password.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."

    if _classes_present(password) < 2:
        return (
            "Password must mix at least 2 of: uppercase, lowercase, "
            "digit, symbol (an all-numeric or all-letter password is rejected)."
        )

    if _is_repeated_pattern(password):
        return "Password repeats a short pattern (e.g. 123456123456) — choose something less predictable."

    if _is_pure_sequence(password):
        return "Password is a keyboard/number sequence (e.g. 123456789012) — choose something less predictable."

    lowered = password.lower()
    if _strip_trailing_digits(lowered) in COMMON_PASSWORDS or lowered in COMMON_PASSWORDS:
        return "Password is on the common-password blocklist — choose something unique."

    for term in context_terms:
        term = (term or "").strip().lower()
        if len(term) >= 4 and term in lowered:
            return "Password must not contain your email or the project name."

    return None
