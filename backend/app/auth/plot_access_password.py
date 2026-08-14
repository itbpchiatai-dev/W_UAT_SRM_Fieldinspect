"""Plot inspection password ("รหัสยืนยันแปลง") — policy, bcrypt hashing, and
the HMAC blind-index digest (round 8-9A).

Deliberately SEPARATE from app.auth.password (the user-login policy). That one
is a >=12-character human password with character-class rules; this one is a
numeric code typed on a phone in a field by whoever holds the plot's inspection
phone number. Reusing validate_password() here would reject every legal code,
so the two policies stay independent by design — same bcrypt primitive,
different rules.

Policy (round 8-9B.0, approved by the product owner): ASCII digits only, 4 to
20 of them. Deliberately NO complexity/guessability rule — repeated digits
(0000, 1111) and straight runs (1234, 987654) are all ACCEPTED. Field users
share and retype this code by voice and on a numeric keypad; a rule that
rejects the codes they actually pick pushes them to write it down somewhere
worse. The security budget is spent instead on rate limiting, the bcrypt cost,
and the keyed blind index below — none of which the user has to remember.

Threat model / storage (see the round 8-9A brief + docs/security.md §2, §9):
  * password_hash — bcrypt, cost 12. NEVER plaintext, never in a response, a
    log, a JWT, a Record, or a report.
  * password_lookup_digest — HMAC-SHA256(server pepper, code), lowercase
    hex(64). A BLIND INDEX so round 8-9C can resolve "phone + password" to the
    matching plots with ONE indexed lookup instead of bcrypt-verifying every
    candidate plot. It is keyed (HMAC with a dedicated server-side pepper),
    never a bare SHA-256 — an unkeyed digest of a code whose minimum keyspace
    is only 10^4 would be a trivially precomputed rainbow table.
    The pepper is its OWN secret (PLOT_ACCESS_PASSWORD_PEPPER); reusing
    JWT_SECRET_KEY would couple token forgery and credential lookup into one
    blast radius, so config.py rejects that at boot.
  * The digest is NOT an authenticator. Round 8-9C must still bcrypt-verify the
    candidate rows it narrows down to — an index is not a proof.

Every error message here is a static constant: no exception ever echoes the
submitted code back to the caller or into a log.
"""
from __future__ import annotations

import hashlib
import hmac
import re

import bcrypt

from app.core.config import get_settings

# Locked policy for round 8-9B.0: ASCII digits, 4 to 20 inclusive. The old
# exact-6 constant (PLOT_ACCESS_PASSWORD_LENGTH) is GONE rather than aliased —
# leaving it would let another module keep believing the length is fixed.
PLOT_ACCESS_PASSWORD_MIN_LENGTH = 4
PLOT_ACCESS_PASSWORD_MAX_LENGTH = 20

# `[0-9]` (not `\d`, not str.isdigit()) — both of those also accept Thai digits
# ๐-๙, full-width １２３４, and other Unicode decimal forms, which would store a
# code the field user can never retype on a numeric keypad. Anchored with
# \A/\Z, so internal whitespace or a dash is rejected too.
_ASCII_PIN_RE = re.compile(
    r"\A[0-9]{%d,%d}\Z" % (PLOT_ACCESS_PASSWORD_MIN_LENGTH, PLOT_ACCESS_PASSWORD_MAX_LENGTH)
)

# The ONE policy message. Generic and code-free — Thai, since it reaches the
# admin UI directly. There is deliberately no "too easy to guess" message any
# more: repeated and sequential codes are legal (see the module docstring).
_MSG_FORMAT = "รหัสยืนยันแปลงต้องเป็นตัวเลข 0-9 จำนวน 4 ถึง 20 หลัก"


# Contract the FUTURE public verification flow (round 8-9C) must satisfy. It is
# written down here, next to the primitives, because every one of these rules is
# a property of how these two functions are called — not of the functions
# themselves, so nothing in this module can enforce them on its own.
# test_public_credential_verification_contract_8_9c.py asserts this block still
# exists and that no public endpoint has quietly started verifying without it.
#
#   1. NARROW with the blind digest. build_plot_access_password_lookup_digest()
#      + the repository's phone/digest lookup select CANDIDATE rows only.
#   2. VERIFY with bcrypt. verify_plot_access_password() must run against each
#      candidate's stored hash before anything is authorized.
#   3. NEVER trust the digest alone. A digest match is an index hit, not proof:
#      it is unsalted per-row by construction, so treating it as an
#      authenticator would make every plot sharing a PIN interchangeable.
#   4. Run bcrypt OFF the event loop (asyncio.to_thread), exactly as the admin
#      PUT does — a ~250ms blocking call on the loop is a self-inflicted DoS on
#      a public, unauthenticated endpoint.
#   5. RATE-LIMIT BEFORE hashing. The limiter must reject the request before it
#      reaches bcrypt, or the hash cost becomes the attacker's amplifier. This
#      matters MORE since round 8-9B.0: the minimum policy is 4 digits (10^4),
#      so the rate limiter — not the code itself — is what makes online
#      guessing infeasible.
#   6. ONE generic error for every failure — unknown phone, wrong password, no
#      credential, inactive plot. Distinguishable errors turn the endpoint into
#      a phone-enumeration oracle.
#   7. NEVER persist the PIN. Not in a token, a session claim, a Record, a
#      report, or a log line — the same rule the admin PUT already follows.


class PlotAccessPasswordPolicyError(ValueError):
    """A candidate plot inspection password failed the policy check. The
    message is always a static string — it never contains the submitted PIN."""


class PlotAccessPepperMissingError(RuntimeError):
    """PLOT_ACCESS_PASSWORD_PEPPER is not configured, so no blind-index digest
    can be built. Endpoints map this to a controlled 503 (a deployment gap),
    never a 500 — and never fall back to another secret."""


def validate_plot_access_password(password: str) -> str:
    """Validate a candidate code and return its normalized (edge-trimmed) form.

    Surrounding whitespace is trimmed BEFORE validation (a phone keyboard
    happily appends a space); everything else must already be 4-20 ASCII
    digits and nothing else — internal whitespace, a dash, a letter, or a
    non-ASCII digit form all fail. There is NO guessability rule: 0000, 1111,
    1234 and 987654 are all valid by design.

    Raises PlotAccessPasswordPolicyError, whose message is a static constant
    that never echoes the input.
    """
    pin = (password or "").strip()

    if not _ASCII_PIN_RE.match(pin):
        raise PlotAccessPasswordPolicyError(_MSG_FORMAT)
    return pin


def hash_plot_access_password(password: str) -> str:
    """Validate then bcrypt (cost 12, same primitive/rounds as app.auth.password
    — only the policy differs). Raises PlotAccessPasswordPolicyError if the code
    fails the policy, so a malformed value can never reach the database."""
    pin = validate_plot_access_password(password)
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_plot_access_password(password: str, password_hash: str) -> bool:
    """Constant-time bcrypt comparison of a submitted code against a stored hash.

    Deliberately does NOT re-run the policy: this verifies an EXISTING
    credential, and a policy change must never lock out a plot whose password
    was legal when it was set. That is exactly what keeps every 6-digit
    credential set before round 8-9B.0 working unchanged — and what would keep
    them working if the length rule ever tightened again. Returns False (never
    raises) for an empty or malformed stored hash and for an over-long
    submitted value — a corrupted row is an auth failure, not a 500.
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            (password or "").strip().encode("utf-8"), password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def _pepper_key_bytes() -> bytes:
    """The dedicated blind-index pepper, ENCODED, or
    PlotAccessPepperMissingError.

    Round 8-9A.1: Settings holds the pepper as a Pydantic SecretStr, so this is
    the ONE place in the codebase that unwraps it, and it hands back key bytes
    that go straight into hmac.new() — the value is never returned to a caller,
    never stored on a module global, and never interpolated into a message.
    A plain str is still accepted so a test can inject a fake pepper without
    importing Settings.

    Never falls back to JWT_SECRET_KEY or any other secret (config.py also
    rejects a pepper that equals JWT_SECRET_KEY at boot). Deliberately not in
    __all__ — nothing outside this module may reach the pepper.
    """
    raw = get_settings().PLOT_ACCESS_PASSWORD_PEPPER
    unwrap = getattr(raw, "get_secret_value", None)
    pepper = ((unwrap() if unwrap is not None else raw) or "").strip()
    if not pepper:
        # Names the setting, never a value.
        raise PlotAccessPepperMissingError(
            "PLOT_ACCESS_PASSWORD_PEPPER is not configured"
        )
    return pepper.encode("utf-8")


def build_plot_access_password_lookup_digest(password: str) -> str:
    """HMAC-SHA256(pepper, trimmed code) as lowercase hex(64) — the blind index.

    Trims exactly like validate_plot_access_password so the digest written when
    a password is SET and the digest computed when one is LOOKED UP can never
    disagree over a stray space — hash and digest always normalize the same
    value. Deterministic for a given (pepper, code); a
    different pepper yields a different digest, so rotating the pepper
    invalidates every stored digest (a deliberate, documented consequence —
    see the round 8-9B/8-9C rollout notes).

    NOT an authenticator: it narrows candidates, and the caller must still
    bcrypt-verify. Raises PlotAccessPepperMissingError when unconfigured.
    """
    return hmac.new(
        _pepper_key_bytes(),
        (password or "").strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_phone_lockout_fingerprint(phone_normalized: str) -> str:
    """HMAC-SHA256 fingerprint of a canonical phone, for brute-force counters
    (round 8-9C). Lowercase hex(64).

    A failure limiter has to bucket attempts per phone, but a raw phone in a
    Redis key is PII sitting in shared infrastructure (docs/security.md §9) and
    a bare SHA-256 of a 10-digit Thai mobile is a ~10^8 rainbow table anyone
    with the keyspace can invert. So this is keyed with the same dedicated
    server pepper as the credential blind index, and DOMAIN-SEPARATED from it
    by a constant prefix: the two HMACs can never collide or be used to probe
    each other, even though they share a key.

    Not a credential and not reversible: it exists only as a counter bucket,
    is never logged, never returned, and never stored in a row.
    """
    return hmac.new(
        _pepper_key_bytes(),
        b"plot-access-lockout:v1:" + (phone_normalized or "").strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


__all__ = [
    "PLOT_ACCESS_PASSWORD_MIN_LENGTH",
    "PLOT_ACCESS_PASSWORD_MAX_LENGTH",
    "PlotAccessPasswordPolicyError",
    "PlotAccessPepperMissingError",
    "validate_plot_access_password",
    "hash_plot_access_password",
    "verify_plot_access_password",
    "build_plot_access_password_lookup_digest",
    "build_phone_lockout_fingerprint",
]
