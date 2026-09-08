"""No schema that carries a credential may let Pydantic reject it (round L).

Pydantic v2 puts the offending value in `ValidationError.errors()[i]["input"]`,
and FastAPI's default RequestValidationError handler serialises exactly that
into the 422 body. So ANY Pydantic-level rejection on a field holding a
password, a phone number or an access key — a type error, a min_length, a
max_length, a validator that raises — echoes that value back to the caller, and
into any proxy or access log that records response bodies.

The project already knew this: PlotPhoneSearchRequest (8-17A.2.1/8-17B),
PlotAccessPhoneConfig (8-17C/8-17C.1) and AdminPasswordResetRequest were all
fixed the same way — `SkipValidation` plus a hand-written check that answers
with a fixed message. Round L is the audit that found the rest, by ASKING
Pydantic what it would echo rather than reading the code:

    LoginRequest             password as int    -> input == 1234567890
    PublicPhoneAccessLookup  phone 40 chars     -> input == "0810000…"
    PublicPhoneAccessLookup  password 80 chars  -> input == "pppp…"
    DbConnectionCreate       password as int    -> input == 987654321

Two of those had docstrings asserting they were safe. The phone one credited
"normalized in the endpoint, not in a validator" — true, but `Field(min_length,
max_length)` is itself a Pydantic rejection. The password one credited
SecretStr with reporting `**********` as the input; it does not, and returned
the raw string.

These tests drive the models directly, which is the layer the bug lives at.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import AdminPasswordResetRequest, LoginRequest
from app.schemas.db_connection import DbConnectionCreate, DbConnectionUpdate
from app.schemas.phone_access import PublicPhoneAccessLookupRequest
from app.schemas.plot import PlotAccessPhoneConfig

SECRET = "correct-horse-battery-staple"
SECRET_PHONE = "0812345678"


def _echoed_inputs(model, payload) -> list:
    """What FastAPI would put in the 422 body, or [] if Pydantic accepted."""
    try:
        model.model_validate(payload)
        return []
    except ValidationError as exc:
        return [err.get("input") for err in exc.errors()]


def _assert_never_echoes(model, payload, *secrets: object) -> None:
    echoed = _echoed_inputs(model, payload)
    blob = repr(echoed)
    for secret in secrets:
        # An empty string is a substring of everything and proves nothing; it
        # is still worth passing in, because the interesting half of that case
        # is whether Pydantic rejected AT ALL.
        if secret == "":
            continue
        assert repr(secret) not in blob and str(secret) not in blob, (
            f"{model.__name__} echoed {secret!r} in a validation error: {echoed!r}"
        )


# --- passwords ---------------------------------------------------------------

@pytest.mark.parametrize("value", [1234567890, [SECRET], {"p": SECRET}, SECRET * 50])
def test_login_never_echoes_the_password(value):
    _assert_never_echoes(LoginRequest, {"email": "a@b.com", "password": value}, value)


def test_login_never_echoes_the_email():
    """The other half of the credential pair, on an unauthenticated route."""
    _assert_never_echoes(LoginRequest, {"email": 42, "password": SECRET}, 42, SECRET)


@pytest.mark.parametrize("value", [1234567890, [SECRET], SECRET * 50, ""])
def test_admin_password_reset_never_echoes_the_new_password(value):
    _assert_never_echoes(AdminPasswordResetRequest, {"newPassword": value}, value)


@pytest.mark.parametrize("value", [987654321, [SECRET], SECRET * 100, ""])
def test_db_connection_create_never_echoes_the_password(value):
    _assert_never_echoes(DbConnectionCreate, {
        "name": "x", "host": "h", "port": 5432, "database": "d",
        "username": "u", "password": value,
    }, value)


@pytest.mark.parametrize("value", [987654321, [SECRET], SECRET * 100])
def test_db_connection_update_never_echoes_the_password(value):
    _assert_never_echoes(DbConnectionUpdate, {"password": value}, value)


# --- phones and access keys --------------------------------------------------

@pytest.mark.parametrize("value", [
    SECRET_PHONE * 8,          # over the old max_length=32
    812345678,                 # wrong type
    [SECRET_PHONE],
    "",                        # under the old min_length=1
])
def test_public_lookup_never_echoes_the_phone(value):
    _assert_never_echoes(PublicPhoneAccessLookupRequest, {"phone": value}, value)


@pytest.mark.parametrize("value", ["1234" * 40, 123456, [SECRET]])
def test_public_lookup_never_echoes_the_password(value):
    _assert_never_echoes(
        PublicPhoneAccessLookupRequest,
        {"phone": SECRET_PHONE, "password": value},
        value,
    )


def test_public_lookup_never_echoes_the_qr_key():
    """A qrKey is a plot access credential, not an identifier."""
    key = "QR-" + "k" * 200
    _assert_never_echoes(
        PublicPhoneAccessLookupRequest,
        {"phone": SECRET_PHONE, "qrKey": key},
        key,
    )


@pytest.mark.parametrize("payload", [
    {"primaryPhone": 812345678},
    {"primaryPhone": SECRET_PHONE, "additionalPhones": SECRET_PHONE},
    {"primaryPhone": SECRET_PHONE, "additionalPhones": [SECRET_PHONE] * 40},
])
def test_plot_access_phone_config_never_echoes_a_phone(payload):
    """Already fixed in round 8-17C.1 — pinned here so the audit covers every
    credential-carrying schema in one place."""
    _assert_never_echoes(PlotAccessPhoneConfig, payload, SECRET_PHONE, 812345678)


# --- the rule itself ---------------------------------------------------------

def test_credential_fields_are_all_skip_validation():
    """A future `Field(min_length=…)` added to any of these reintroduces the
    bug silently — the model still works, and only a malformed request reveals
    it. Check the annotation instead of waiting for that request."""
    cases = [
        (LoginRequest, "password"),
        (LoginRequest, "email"),
        (AdminPasswordResetRequest, "new_password"),
        (DbConnectionCreate, "password"),
        (DbConnectionUpdate, "password"),
        (PublicPhoneAccessLookupRequest, "phone"),
        (PublicPhoneAccessLookupRequest, "password"),
        (PublicPhoneAccessLookupRequest, "qr_key"),
        (PlotAccessPhoneConfig, "primary_phone"),
        (PlotAccessPhoneConfig, "additional_phones"),
    ]
    for model, field in cases:
        annotation = repr(model.model_fields[field].annotation)
        metadata = repr(model.model_fields[field].metadata)
        assert "SkipValidation" in annotation or "SkipValidation" in metadata, (
            f"{model.__name__}.{field} is validated by Pydantic — a rejection "
            f"would echo the value in the 422 body"
        )
