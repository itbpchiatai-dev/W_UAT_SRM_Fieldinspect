"""Rounds 8-16D.1 / 8-16D.2 — Settings must fail CLOSED and never echo secrets.

Three properties, all previously broken:

1. **Secret echo.** Every boot-time validator used to raise `ValueError`,
   which pydantic wraps into a `ValidationError` whose rendered message
   embeds `input_value=<raw settings dict>`. That dict is the
   PRE-construction input, so `SecretStr` does not mask it, and pydantic's
   truncation keeps the head AND tail — so whether a secret is exposed
   depends purely on where it lands in the dict. This module reproduces the
   original leak conditions and asserts nothing escapes.

2. **APP_ENV fail-open.** Every production gate compared
   `APP_ENV == "production"` inline, so `APP_ENV=prod` (or `Production`)
   silently disabled secure cookies, `/docs` gating, the rate-limit storage
   check and the whole production preflight — while looking deployed.

3. **Structured echo on missing required settings** (round 8-16D.2).
   DB_PASSWORD and JWT_SECRET_KEY have no defaults, so a missing one failed
   during FIELD parsing — before any mode="after" validator could run — and
   pydantic raised its own ValidationError. 8-16D.1 checked only `str(exc)`,
   which pydantic truncates, and so declared that path clean. It was not:
   `exc.errors()` carried the untruncated input mapping. Hence the
   assertions here check the STRUCTURED form, not just the rendered string.

No real secrets: settings are built from placeholder dicts with
`_env_file=None`, so the developer's `.env` is never read. The JWT constant
is fixed hex chosen only to satisfy the strength validator.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.core.config import (
    SUPPORTED_APP_ENVS,
    AppEnvConfigError,
    CorsConfigError,
    JwtSecretConfigError,
    MissingRequiredSettingsError,
    PlotAccessPepperConfigError,
    ProductionConfigError,
    RateLimitStorageConfigError,
    Settings,
    SettingsConfigError,
)

_FAKE_JWT = "deadbeef0123456789abcdeffedcba9876543210abcdef0123456789cafebabe"

# Distinct, searchable, and SHORT — a long value gets truncated away by
# pydantic's repr and would make a leak test pass for the wrong reason.
# Round 8-16D.1 confirmed short values in the dict tail DO survive.
_SENT_DB = "SENTDB1"
_SENT_SMTP = "SENTSMTP1"
_SENT_MFA = "SENTMFA1"
# The pepper must ALSO satisfy its own validator (>=32 chars, distinct from
# JWT_SECRET_KEY) or the "should boot" cases below fail for the wrong reason.
# The distinctive marker sits at the FRONT: pydantic truncates the middle of
# the dict repr, so any value it renders at all renders from its start.
_SENT_PEPPER = "SENTPEP1" + "0123456789abcdef" * 2   # 40 chars

_ALL_SENTINELS = (_SENT_DB, _SENT_PEPPER, _SENT_SMTP, _SENT_MFA, _FAKE_JWT)


def _env(**overrides: str) -> dict[str, str]:
    """A valid dev baseline carrying a sentinel in every secret-ish slot."""
    base = {
        "APP_ENV": "dev",
        "DB_PASSWORD": _SENT_DB,
        "JWT_SECRET_KEY": _FAKE_JWT,
        "PLOT_ACCESS_PASSWORD_PEPPER": _SENT_PEPPER,
        "SMTP_PASSWORD": _SENT_SMTP,
        "AUTH_MFA_ENCRYPTION_KEY": _SENT_MFA,
        "API_CORS_ORIGINS": "https://farmlog.example.co.th",
        "RATE_LIMIT_STORAGE_URI": "memory://",
        "TRUSTED_PROXY_IPS": "",
        "APP_DEBUG": "false",
    }
    base.update(overrides)
    return base


def _build(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    # Clear first: the security conftest pre-seeds some of these, and a
    # leftover value would silently change what is under test.
    for key in (*_env(), "PLOT_ACCESS_PASSWORD_PEPPER"):
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def _assert_no_secret_echo(exc: BaseException) -> None:
    """Assert a boot failure cannot expose settings values.

    The load-bearing check is STRUCTURAL: pydantic only attaches
    `input_value=<raw settings dict>` when it wraps the error, and it only
    wraps ValueError. So "not a ValidationError" is a deterministic
    guarantee that no echo happened, for every secret, at any dict size.

    The sentinel scan below is defence-in-depth only. It cannot stand alone:
    pydantic truncates the MIDDLE of the dict repr, so which values survive
    depends on dict size and key order — with a realistic env the sentinels
    land in the truncated region and a string search would pass even on a
    genuine leak. See test_valueerror_style_attaches_raw_input for the proof
    that the wrapping mechanism is real.
    """
    assert not isinstance(exc, ValidationError), (
        "boot failure surfaced as a pydantic ValidationError, which carries "
        "input_value=<raw settings dict> (pre-construction, so SecretStr does "
        "not mask it). Raise a SettingsConfigError subclass instead."
    )
    rendered = f"{exc!r}\n{exc!s}"
    leaked = [s for s in _ALL_SENTINELS if s in rendered]
    assert not leaked, f"boot error leaked {len(leaked)} secret value(s) into its message"

    # Round 8-16D.2 — the STRUCTURED form is the one that actually leaked.
    # `str(exc)` is truncated by pydantic and showed nothing even while
    # `exc.errors()` carried four secrets in full, which is exactly why
    # 8-16D.1's string-only check reported this path as clean. Error
    # trackers and log shippers serialise the structured form, so assert on
    # it too rather than trusting the rendered string.
    structured = getattr(exc, "errors", None)
    assert structured is None or not callable(structured), (
        "boot failure exposes .errors(); only pydantic's ValidationError does "
        "that, and its payload embeds the raw settings mapping"
    )


# --- required secret settings (round 8-16D.2) -------------------------------
#
# DB_PASSWORD and JWT_SECRET_KEY have no defaults, so a missing one used to
# fail during pydantic FIELD parsing — ahead of every mode="after" validator
# — producing a ValidationError whose `errors()[i]["input"]` held the whole
# merged settings mapping. Reproduced live: `str(exc)` showed no secrets
# (pydantic truncates it) while `exc.errors()` carried DB_PASSWORD,
# SMTP_PASSWORD, PLOT_ACCESS_PASSWORD_PEPPER and AUTH_MFA_ENCRYPTION_KEY.

def _env_without(*names: str, **overrides: str) -> dict[str, str]:
    env = _env(**overrides)
    for n in names:
        env.pop(n, None)
    return env


def test_missing_jwt_secret_is_not_a_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(MissingRequiredSettingsError) as exc:
        _build(monkeypatch, _env_without("JWT_SECRET_KEY"))
    assert "JWT_SECRET_KEY" in str(exc.value)
    _assert_no_secret_echo(exc.value)


def test_missing_db_password_is_not_a_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(MissingRequiredSettingsError) as exc:
        _build(monkeypatch, _env_without("DB_PASSWORD"))
    assert "DB_PASSWORD" in str(exc.value)
    _assert_no_secret_echo(exc.value)


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t\n "])
def test_blank_or_whitespace_jwt_secret_fails_closed(
    monkeypatch: pytest.MonkeyPatch, blank: str,
) -> None:
    """A whitespace-only value satisfies pydantic's `str` requirement, so
    without this it would sail through as a real signing key."""
    with pytest.raises(MissingRequiredSettingsError) as exc:
        _build(monkeypatch, _env(JWT_SECRET_KEY=blank))
    _assert_no_secret_echo(exc.value)


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t\n "])
def test_blank_or_whitespace_db_password_fails_closed(
    monkeypatch: pytest.MonkeyPatch, blank: str,
) -> None:
    with pytest.raises(MissingRequiredSettingsError) as exc:
        _build(monkeypatch, _env(DB_PASSWORD=blank))
    _assert_no_secret_echo(exc.value)


def test_both_required_settings_missing_reports_both_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(MissingRequiredSettingsError) as exc:
        _build(monkeypatch, _env_without("DB_PASSWORD", "JWT_SECRET_KEY"))
    message = str(exc.value)
    assert "DB_PASSWORD" in message
    assert "JWT_SECRET_KEY" in message
    # sorted() — the declaration order of _REQUIRED_SECRET_SETTINGS must not
    # bleed into output that operators and tests compare across runs.
    assert message.index("DB_PASSWORD") < message.index("JWT_SECRET_KEY")
    _assert_no_secret_echo(exc.value)


def test_missing_required_setting_never_exposes_structured_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression that 8-16D.1's string-only assertion could not see:
    the failure must not be a ValidationError at all, so there is no
    `errors()[i]["input"]` mapping for anything to be read out of."""
    with pytest.raises(MissingRequiredSettingsError) as exc:
        _build(monkeypatch, _env_without("JWT_SECRET_KEY"))

    assert not isinstance(exc.value, ValidationError)
    assert not hasattr(exc.value, "errors")
    for sentinel in _ALL_SENTINELS:
        assert sentinel not in repr(exc.value)
        assert sentinel not in str(exc.value)


def test_required_settings_preflight_runs_before_field_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering is the whole mechanism: a mode="after" validator would run
    too late, because pydantic raises its own ValidationError during field
    parsing first. Feeding a value that ALSO breaks int parsing shows the
    required-settings check wins — proving it runs before field parsing."""
    with pytest.raises(MissingRequiredSettingsError):
        _build(monkeypatch, _env_without("DB_PASSWORD", DB_PORT="not-an-int"))


def test_missing_required_setting_message_names_settings_not_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(MissingRequiredSettingsError) as exc:
        _build(monkeypatch, _env_without("DB_PASSWORD"))
    message = str(exc.value)
    assert "DB_PASSWORD" in message          # the NAME is fine to show
    assert _SENT_DB not in message           # a VALUE never is
    assert "backend/.env" in message         # actionable remedy


def test_required_settings_still_reach_the_strength_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preflight must only catch missing/blank. A PRESENT-but-weak secret
    must still fall through to _jwt_secret_strong_enough — otherwise this
    round would have quietly disabled the 8-16D.1 strength checks."""
    with pytest.raises(JwtSecretConfigError):
        _build(monkeypatch, _env(JWT_SECRET_KEY="short-but-present"))


def test_field_parse_errors_carry_only_the_offending_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audited in round 8-16D.2 and left as-is deliberately.

    A field-level coercion failure (DB_PORT="abc") is still a pydantic
    ValidationError, but its `input` is that single scalar — the value the
    operator just typed for that field — not the merged settings mapping. No
    unrelated secret is reachable through it, so converting these too would
    add ceremony without closing anything.
    """
    with pytest.raises(ValidationError) as exc:
        _build(monkeypatch, _env(DB_PORT="not-an-int"))

    inputs = [err.get("input") for err in exc.value.errors()]
    assert not any(isinstance(value, dict) for value in inputs), (
        "a field-parse error captured the raw settings mapping; that is the "
        "8-16D.2 leak shape and needs the same before-validator treatment"
    )
    structured = repr(exc.value.errors())
    for sentinel in _ALL_SENTINELS:
        assert sentinel not in structured


# --- APP_ENV contract -------------------------------------------------------

@pytest.mark.parametrize("env_name", sorted(SUPPORTED_APP_ENVS))
def test_every_supported_app_env_boots(
    monkeypatch: pytest.MonkeyPatch, env_name: str,
) -> None:
    """All five supported environments must load. `production` needs the
    fuller config its own preflight demands; the rest use the dev baseline."""
    extra = (
        {
            "RATE_LIMIT_STORAGE_URI": "redis://redis.internal:6379/0",
            "TRUSTED_PROXY_IPS": "10.1.2.0/24",
        }
        if env_name == "production"
        else {}
    )
    settings = _build(monkeypatch, _env(APP_ENV=env_name, **extra))
    assert settings.APP_ENV == env_name
    assert settings.is_production is (env_name == "production")


def test_smoke_env_is_supported_because_ci_uses_it() -> None:
    """docker-compose.smoke.yml sets APP_ENV=smoke and is driven by the
    ci.yml `docker-smoke` job plus scripts/smoke-prod.{sh,bat}. Dropping it
    from the supported set would break CI, so it is pinned here."""
    assert "smoke" in SUPPORTED_APP_ENVS


def test_ci_test_env_is_supported() -> None:
    """.github/workflows/ci.yml runs pytest with APP_ENV=test."""
    assert "test" in SUPPORTED_APP_ENVS


@pytest.mark.parametrize("bad", [
    "prod",          # the classic abbreviation
    "Production",    # case mismatch
    "PRODUCTION",
    "production ",   # trailing space
    " production",
    "",              # blank
    "staging2",
    "local",
])
def test_unknown_app_env_fails_closed(
    monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    with pytest.raises(AppEnvConfigError) as exc:
        _build(monkeypatch, _env(APP_ENV=bad))
    _assert_no_secret_echo(exc.value)


def test_typo_app_env_is_never_normalised_to_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`prod` must NOT be silently accepted as production. Auto-correcting it
    would produce a deploy that looks production-gated while every guard is
    inactive — the exact fail-open this contract closes."""
    with pytest.raises(AppEnvConfigError):
        _build(monkeypatch, _env(
            APP_ENV="prod",
            RATE_LIMIT_STORAGE_URI="redis://redis.internal:6379/0",
            TRUSTED_PROXY_IPS="10.1.2.0/24",
        ))


def test_app_env_typo_cannot_bypass_production_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before this round, APP_ENV=prod skipped the rate-limit-storage check
    AND the production preflight, so a config with memory:// storage and no
    trusted proxies booted happily. It must now be rejected outright."""
    with pytest.raises(AppEnvConfigError):
        _build(monkeypatch, _env(
            APP_ENV="prod", RATE_LIMIT_STORAGE_URI="memory://", TRUSTED_PROXY_IPS="",
        ))


# --- production gates all read one contract ---------------------------------

def test_production_gates_use_the_shared_is_production_property() -> None:
    """Secure cookies and the /docs switch must derive from `is_production`,
    not their own inline string comparison, or they can drift from the
    validators (and from each other) on the next edit."""
    import app.api.v1.auth as auth_module
    import app.main as main_module

    auth_src = inspect.getsource(auth_module)
    main_src = inspect.getsource(main_module)

    assert auth_src.count("secure=settings.is_production") == 2, (
        "both refresh-token and SSO-state cookies must gate `secure` on the "
        "shared is_production contract"
    )
    assert "settings.is_production" in main_src
    for src, name in ((auth_src, "auth.py"), (main_src, "main.py")):
        assert 'APP_ENV == "production"' not in src, f"{name} still compares APP_ENV inline"
        assert 'APP_ENV != "production"' not in src, f"{name} still compares APP_ENV inline"


# --- secret-echo regressions, one per converted validator -------------------

def test_wildcard_cors_failure_does_not_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(CorsConfigError) as exc:
        _build(monkeypatch, _env(API_CORS_ORIGINS="*"))
    _assert_no_secret_echo(exc.value)


def test_weak_jwt_failure_does_not_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(JwtSecretConfigError) as exc:
        _build(monkeypatch, _env(JWT_SECRET_KEY="short"))
    _assert_no_secret_echo(exc.value)


def test_low_entropy_jwt_failure_does_not_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(JwtSecretConfigError) as exc:
        _build(monkeypatch, _env(JWT_SECRET_KEY="x" * 64))
    _assert_no_secret_echo(exc.value)


def test_memory_storage_failure_does_not_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RateLimitStorageConfigError) as exc:
        _build(monkeypatch, _env(
            APP_ENV="production", RATE_LIMIT_STORAGE_URI="memory://",
            TRUSTED_PROXY_IPS="10.1.2.0/24",
        ))
    _assert_no_secret_echo(exc.value)


def test_bad_app_env_failure_does_not_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AppEnvConfigError) as exc:
        _build(monkeypatch, _env(APP_ENV="prod"))
    _assert_no_secret_echo(exc.value)


def test_pepper_failure_does_not_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-existing guard (round 8-9A.1) — pinned here alongside the others
    so the whole hierarchy is covered by one property."""
    with pytest.raises(PlotAccessPepperConfigError) as exc:
        _build(monkeypatch, _env(PLOT_ACCESS_PASSWORD_PEPPER=_FAKE_JWT))
    _assert_no_secret_echo(exc.value)


# --- the hierarchy itself ---------------------------------------------------

@pytest.mark.parametrize("error_type", [
    AppEnvConfigError, CorsConfigError, JwtSecretConfigError,
    RateLimitStorageConfigError, ProductionConfigError, PlotAccessPepperConfigError,
])
def test_every_config_error_is_a_non_valueerror_settings_error(error_type: type) -> None:
    """Subclassing ValueError would put the raw-input echo straight back:
    pydantic only wraps ValueError, and only wrapping attaches the dict."""
    assert issubclass(error_type, SettingsConfigError)
    assert issubclass(error_type, RuntimeError)
    assert not issubclass(error_type, ValueError)


def test_valueerror_style_attaches_raw_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof that the hierarchy is not cargo-culting.

    Subclass the REAL Settings with an old-style ValueError validator and
    show pydantic wraps it into a ValidationError that carries
    `input_value=` — the raw, pre-construction settings mapping. Without
    this test the "does not echo secrets" assertions could pass simply
    because nothing ever raised, and the whole exception hierarchy would
    look like ceremony.
    """
    from pydantic import model_validator

    from app.core.config import Settings as RealSettings

    class _LeakySettings(RealSettings):
        @model_validator(mode="after")
        def _old_style(self) -> "_LeakySettings":
            raise ValueError("simulated legacy validator failure")

    for k, v in _env().items():
        monkeypatch.setenv(k, v)

    with pytest.raises(ValidationError) as exc:
        _LeakySettings(_env_file=None)

    rendered = str(exc.value)
    assert "input_value=" in rendered, (
        "expected pydantic to attach the raw input; if this ever stops being "
        "true the ValueError ban could be relaxed — but verify on the exact "
        "pydantic version in use, not from memory"
    )
    # And the structural guard correctly rejects it.
    with pytest.raises(AssertionError):
        _assert_no_secret_echo(exc.value)


def test_no_settings_validator_raises_bare_valueerror() -> None:
    """Structural guard: a future validator that reaches for `raise ValueError`
    reintroduces the leak silently, because the message still looks correct."""
    import app.core.config as config_module

    source = inspect.getsource(config_module)
    # _is_valid_cidr CATCHES ValueError from ipaddress; it never raises one.
    assert "raise ValueError(" not in source, (
        "a Settings validator raises a bare ValueError — pydantic will wrap it "
        "and echo the raw settings dict. Raise a SettingsConfigError subclass."
    )


# --- production rate-limit URI contract -------------------------------------

@pytest.mark.parametrize("uri", [
    "",                                # blank
    "memory://",                       # per-worker
    "redis://<redis-host>:6379/0",     # unsubstituted template
    "redis-host:6379",                 # no scheme
    "redis://",                        # no host
    "memcached://cache:11211",         # unsupported backend
])
def test_production_rejects_unusable_rate_limit_uri(
    monkeypatch: pytest.MonkeyPatch, uri: str,
) -> None:
    with pytest.raises(RateLimitStorageConfigError) as exc:
        _build(monkeypatch, _env(
            APP_ENV="production", RATE_LIMIT_STORAGE_URI=uri,
            TRUSTED_PROXY_IPS="10.1.2.0/24",
        ))
    _assert_no_secret_echo(exc.value)


@pytest.mark.parametrize("uri", [
    "redis://redis.internal:6379/0",
    "rediss://redis.internal:6380/0",          # TLS
    "redis://user:pw@redis.internal:6379/0",   # credentials in URI
])
def test_production_accepts_supported_redis_uris(
    monkeypatch: pytest.MonkeyPatch, uri: str,
) -> None:
    settings = _build(monkeypatch, _env(
        APP_ENV="production", RATE_LIMIT_STORAGE_URI=uri,
        TRUSTED_PROXY_IPS="10.1.2.0/24",
    ))
    assert settings.RATE_LIMIT_STORAGE_URI == uri


def test_rate_limit_uri_error_never_echoes_the_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis URI can embed credentials (redis://user:pass@host), so the
    error must describe the RULE and never any part of the value."""
    secret_host = "supersecrethost.internal"
    secret_pw = "URIPASSWORD1"
    with pytest.raises(RateLimitStorageConfigError) as exc:
        _build(monkeypatch, _env(
            APP_ENV="production",
            RATE_LIMIT_STORAGE_URI=f"memcached://user:{secret_pw}@{secret_host}:11211",
            TRUSTED_PROXY_IPS="10.1.2.0/24",
        ))
    rendered = f"{exc.value!r}\n{exc.value!s}"
    assert secret_pw not in rendered
    assert secret_host not in rendered


def test_dev_still_accepts_memory_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole storage contract is production-only — local dev and prodlike
    must keep working with the in-memory default, unchanged."""
    settings = _build(monkeypatch, _env(APP_ENV="dev", RATE_LIMIT_STORAGE_URI="memory://"))
    assert settings.RATE_LIMIT_STORAGE_URI == "memory://"


@pytest.mark.parametrize("env_name", ["test", "smoke", "staging"])
def test_non_production_envs_are_not_subject_to_production_gates(
    monkeypatch: pytest.MonkeyPatch, env_name: str,
) -> None:
    """test/smoke/staging must boot with the same minimal config CI and the
    smoke stack actually provide — no Redis, no trusted proxies."""
    settings = _build(monkeypatch, _env(
        APP_ENV=env_name, RATE_LIMIT_STORAGE_URI="memory://", TRUSTED_PROXY_IPS="",
    ))
    assert settings.is_production is False
