"""Password-strength policy ("Easy + blocklist") + drift guard.

Two copies of the policy exist by necessity:
 - scripts/password_policy.py   — used by the setup wizards (this machine)
 - app/auth/password.py         — emitted by scripts/scaffold.py, enforced
                                  at runtime on the END-USER's machine

They can't share an import (different machines), so this test extracts the
scaffolded template, execs it, and asserts the two agree on a battery of
inputs — catching the exact regression that let `123456123456` through.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from password_policy import password_strength_error  # noqa: E402

# Inputs that MUST be rejected, with the reported bug front and center.
WEAK = [
    "123456123456",     # the reported bug — repeated pattern / all-numeric
    "123456789012",     # number sequence
    "aaaaaaaaaaaa",     # all-same char
    "111111111111",
    "Password1234",     # common-password blocklist (base "password")
    "qwertyuiopas",     # keyboard sequence
    "short1A",          # too short
    "mydogpriness",     # all-letter, 12 chars → fails the ≥2-class rule
]
# Inputs that MUST pass — note the EASY ones (≥2 classes, no composition pain).
STRONG = [
    "Tr0ub4dour&3xtra",
    "Chai-Tai!Secure7",
    "wX9$km2!qPds",
    "baanselling99",    # letters + digits — easy for an external user
    "khaohom rice 7",   # passphrase: letters + space + digit
    "vendor-cp-2026",   # letters + symbol + digit
]


@pytest.mark.parametrize("pw", WEAK)
def test_wizard_rejects_weak(pw: str) -> None:
    assert password_strength_error(pw) is not None


@pytest.mark.parametrize("pw", STRONG)
def test_wizard_accepts_strong(pw: str) -> None:
    assert password_strength_error(pw) is None


def test_context_term_blocked() -> None:
    assert password_strength_error("AdminStrong9!xx", context_terms=["adminstrong9"]) is not None


def _load_scaffolded_validate_password():
    """Extract + exec the password.py template emitted by scaffold.py."""
    src = (SCRIPTS / "scaffold.py").read_text(encoding="utf-8")
    start = src.index('auth_dir / "password.py", ' + "'''" + "\\\n") + len(
        'auth_dir / "password.py", ' + "'''" + "\\\n"
    )
    end = src.index("'''" + ")", start)
    template = src[start:end]
    # Stub bcrypt so exec doesn't need the real dependency.
    fake = types.ModuleType("bcrypt")
    fake.hashpw = lambda *a, **k: b"x"
    fake.gensalt = lambda **k: b"x"
    fake.checkpw = lambda *a, **k: False
    sys.modules["bcrypt"] = fake
    ns: dict = {}
    exec(compile(template, "<scaffolded password.py>", "exec"), ns)
    return ns["validate_password"], ns["PasswordPolicyError"]


@pytest.mark.parametrize("pw", WEAK + STRONG)
def test_runtime_matches_wizard(pw: str) -> None:
    """The scaffolded runtime copy must agree with the wizard copy."""
    validate_password, PasswordPolicyError = _load_scaffolded_validate_password()
    wizard_rejects = password_strength_error(pw) is not None
    try:
        validate_password(pw)
        runtime_rejects = False
    except PasswordPolicyError:
        runtime_rejects = True
    assert wizard_rejects == runtime_rejects, f"policy drift on {pw!r}"
