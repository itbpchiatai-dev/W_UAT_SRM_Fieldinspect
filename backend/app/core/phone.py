"""Thai mobile-number normalization (round 8-3A).

A plot's "access phone" is used as an Access Key for the public inspection
flow (round 8-3B) — so every phone entering the system must be canonicalized
to ONE shape before it is stored or compared, or the same real number written
two different ways ("084-555-2162" vs "+66845552162") would look like two
different keys.

Canonical form: a 10-digit Thai mobile number, ^0[689][0-9]{8}$ (e.g.
"0845552162"). Accepts the common human/import spellings and folds the +66 /
66 country code to a leading 0; anything that doesn't resolve to the canonical
shape is a clean ValueError (the caller turns it into a 422).

PII: the raw and normalized values are phone numbers — this module NEVER logs
them, and the ValueError message is deliberately generic (no phone echoed),
so a validation failure can't leak a number into logs (docs/security.md §9.2).
Standard library only (no new dependency).
"""
from __future__ import annotations

import re

# Canonical Thai mobile: leading 0, then 6/8/9, then 8 more digits.
CANONICAL_MOBILE_RE = re.compile(r"^0[689][0-9]{8}$")

# Formatting characters an operator/import might type: spaces, dashes,
# parentheses, and the non-breaking dot some spreadsheets emit.
_FORMATTING_RE = re.compile(r"[\s()\-.]")


def normalize_thai_mobile(raw: str) -> str:
    """Canonicalize a Thai mobile number to ^0[689][0-9]{8}$.

    Trims, removes spaces/dashes/parentheses/dots, folds a +66 or 66 country
    code to a leading 0, and validates the result. Raises ValueError (generic
    message, never echoing the input) on any value that isn't a valid Thai
    mobile number — never silently truncates or "fixes" a malformed number.
    """
    if raw is None:
        raise ValueError("phone number is required")
    stripped = _FORMATTING_RE.sub("", raw.strip())
    if not stripped:
        raise ValueError("phone number is required")

    # Country-code prefixes → leading 0. Only these two shapes are folded; a
    # bare local number already starts with 0 and is left untouched.
    if stripped.startswith("+66"):
        stripped = "0" + stripped[3:]
    elif stripped.startswith("66"):
        stripped = "0" + stripped[2:]

    if not CANONICAL_MOBILE_RE.fullmatch(stripped):
        # Generic message — do NOT include the raw/normalized value (PII).
        raise ValueError("invalid Thai mobile number")
    return stripped
