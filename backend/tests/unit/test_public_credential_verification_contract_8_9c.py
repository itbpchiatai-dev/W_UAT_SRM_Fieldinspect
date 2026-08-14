"""Round 8-9A.1 — the contract round 8-9C must satisfy when it finally wires
"phone + password" into the public flow.

Nothing is implemented here, and nothing may be: these tests guard a written
contract and assert the public path has NOT started verifying credentials. They
exist because every rule below is a property of HOW the primitives are called,
so neither plot_access_password.py nor the repository can enforce them alone —
if 8-9C is written months later by someone reading only the function
signatures, this file is what tells them the rules.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import app.api.v1.public_inspection_access as public_access
import app.api.v1.public_plots as public_plots
import app.api.v1.public_records as public_records
import app.auth.plot_access_password as password_helper
from app.repositories import plot_access_credential_repository as credential_repo

_HELPER_SRC = Path(inspect.getfile(password_helper)).read_text(encoding="utf-8")
_PUBLIC_MODULES = (public_access, public_plots, public_records)


def _contract_block() -> str:
    """The commented contract in plot_access_password.py."""
    start = _HELPER_SRC.index("# Contract the FUTURE public verification flow")
    return _HELPER_SRC[start:_HELPER_SRC.index("class PlotAccessPasswordPolicyError")]


def test_the_contract_block_still_exists() -> None:
    assert "round 8-9C" in _contract_block()


def test_contract_requires_narrowing_with_the_blind_digest() -> None:
    block = _contract_block()
    assert "NARROW with the blind digest" in block
    assert "CANDIDATE rows only" in block


def test_contract_requires_bcrypt_verification_before_authorizing() -> None:
    block = _contract_block()
    assert "VERIFY with bcrypt" in block
    assert "before anything is authorized" in block


def test_contract_forbids_trusting_the_digest_alone() -> None:
    assert "NEVER trust the digest alone" in _contract_block()


def test_contract_requires_bcrypt_off_the_event_loop() -> None:
    block = _contract_block()
    assert "Run bcrypt OFF the event loop" in block
    assert "asyncio.to_thread" in block


def test_contract_requires_rate_limiting_before_hashing() -> None:
    assert "RATE-LIMIT BEFORE hashing" in _contract_block()


def test_contract_requires_one_generic_error_for_every_failure() -> None:
    block = _contract_block()
    assert "ONE generic error for every failure" in block
    assert "enumeration oracle" in block


def test_contract_forbids_persisting_the_pin() -> None:
    block = _contract_block()
    assert "NEVER persist the PIN" in block
    for sink in ("token", "session claim", "Record", "report", "log line"):
        assert sink in block


def test_repository_lookup_documents_that_it_is_not_an_authorization_decision() -> None:
    doc = credential_repo.lookup_active_access_rows_by_phone_and_digest.__doc__ or ""
    assert "NOT an authorization decision" in doc
    assert "verify_plot_access_password" in doc


def test_verify_helper_exists_and_is_the_one_the_contract_names() -> None:
    assert callable(password_helper.verify_plot_access_password)
    assert "verify_plot_access_password" in password_helper.__all__


def test_the_public_flow_now_honours_the_contract() -> None:
    """Round 8-9C landed: the public lookup DOES verify credentials now, so
    this test flipped from "must not call" to "must call, in the right way".

    Rules 1-4 are asserted structurally here; rules 5-7 (rate limit before
    hashing, one generic error, never persist the PIN) are asserted
    behaviourally in test_public_phone_password_enforcement.py."""
    src = Path(inspect.getfile(public_access)).read_text(encoding="utf-8")
    # 1. narrow with the blind digest
    assert "build_plot_access_password_lookup_digest" in src
    assert "lookup_active_access_rows_by_phone_and_digest" in src
    # 2. verify with bcrypt
    assert "verify_plot_access_password" in src
    # 3. never trust the digest alone — verification decides the result
    assert "_verify_candidates" in src
    # 4. bcrypt off the event loop
    assert "asyncio.to_thread" in src


def test_public_records_and_plots_do_not_verify_passwords_themselves() -> None:
    """Verification happens in ONE place (the lookup). The record path only
    re-checks the credential BINDING it was handed — it must never re-run
    bcrypt or rebuild a digest, which would be a second, divergent auth path."""
    for mod in (public_records, public_plots):
        src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
        assert "verify_plot_access_password" not in src
        assert "build_plot_access_password_lookup_digest" not in src
