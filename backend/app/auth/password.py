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

# Round 8-23A.1 — bcrypt (this repo uses the `bcrypt` package directly, not
# passlib) only ever looks at the first 72 BYTES of the UTF-8-encoded
# password. Past that boundary bcrypt 5.0.0 does NOT silently truncate —
# bcrypt.hashpw()/checkpw() raise a bare ValueError, which is NOT a
# PasswordPolicyError (even though PasswordPolicyError subclasses
# ValueError, a `except PasswordPolicyError` does not catch its own
# parent class) and was escaping every caller as an uncaught 500.
#
# This is a BYTE limit, not a character limit: Thai text is 3 bytes/char
# in UTF-8, so as few as 25 Thai characters can exceed it while comfortably
# passing a naive `len(password) <= 200` character-count guard — exactly
# the gap that let a policy-valid, human-reasonable Thai passphrase 500 the
# admin reset endpoint (round 8-23A.1 root cause).
MAX_PASSWORD_BYTES = 72

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
    # Round 8-23A.1 — the bcrypt byte boundary, checked here (BEFORE any
    # bcrypt call) so it always surfaces as a PasswordPolicyError, never a
    # bare ValueError. Never truncate: a silently-shortened password would
    # let two different long passwords hash identically past byte 72,
    # which is confusing and defeats the point of a length check. A
    # malformed/unencodable string (e.g. a lone UTF-16 surrogate that can
    # slip through JSON decoding) is rejected the same way — encoding
    # failure is itself a reason to refuse the password, not a crash.
    try:
        encoded_len = len(password.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PasswordPolicyError(
            "Password contains characters that cannot be encoded."
        ) from exc
    if encoded_len > MAX_PASSWORD_BYTES:
        raise PasswordPolicyError(
            f"Password is too long — must be at most {MAX_PASSWORD_BYTES} bytes "
            "when UTF-8 encoded (non-ASCII characters, e.g. Thai, take up more "
            "than 1 byte each)."
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
    """bcrypt with 12 rounds (~250ms on modern hardware — slow enough).

    validate_password() (called first) already guarantees the password's
    UTF-8 encoding exists and fits within MAX_PASSWORD_BYTES, so the
    encode() below can never raise and bcrypt.hashpw() can never see an
    over-length input. Every rejection from this function is therefore a
    PasswordPolicyError — bcrypt's own bare ValueError never escapes here.
    Bcrypt rounds (12) and hash format are unchanged by this contract.
    """
    validate_password(password, context_terms=context_terms)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Fail closed, never throws — a wrong/oversized/unencodable password
    or a malformed hash on disk are all "not a match", not a crash.

    The broad `except ValueError` covers three distinct bcrypt failure
    modes with one line: a malformed hash on disk, checkpw() rejecting a
    password over MAX_PASSWORD_BYTES (login itself has no length-precheck
    the way hash_password's validate_password call does — a login attempt
    with a too-long password must still fail closed, not 500), and
    UnicodeEncodeError (a ValueError subclass) from an unencodable
    password. None of the three should ever be distinguishable to the
    caller — a login failure looks like a login failure either way.
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
