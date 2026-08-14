"""Round 8-9A — PLOT_ACCESS_PASSWORD_PEPPER / PUBLIC_PLOT_PASSWORD_ENFORCEMENT
boot validation.

The plot inspection password's blind index is only as strong as its pepper, and
the pepper must be its OWN secret. Three misconfigurations are fatal at boot
rather than silently degrading to a guessable index or a shared blast radius.
All values here are test-only fakes — no real secret is read or printed.
"""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import PlotAccessPepperConfigError, Settings

_JWT = "0123456789abcdef" * 4          # 64 chars, test-only
_PEPPER = "fedcba9876543210" * 4       # 64 chars, test-only, different from _JWT


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "DB_PASSWORD": "test-only",
        "API_CORS_ORIGINS": "http://localhost:5173",
        "APP_ENV": "dev",
        "JWT_SECRET_KEY": _JWT,
    }
    base.update(overrides)
    return base


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    for key in ("PLOT_ACCESS_PASSWORD_PEPPER", "PUBLIC_PLOT_PASSWORD_ENFORCEMENT"):
        monkeypatch.delenv(key, raising=False)
    for k, v in _env(**overrides).items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_defaults_are_blank_pepper_and_enforcement_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round 8-9A state: storage exists, nothing is enforced yet."""
    settings = _settings(monkeypatch)
    assert settings.PLOT_ACCESS_PASSWORD_PEPPER.get_secret_value() == ""
    assert settings.PUBLIC_PLOT_PASSWORD_ENFORCEMENT is False


# --- round 8-9A.1: SecretStr masking ----------------------------------------

def test_pepper_is_read_from_the_env_var_under_its_original_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_PEPPER)
    assert isinstance(settings.PLOT_ACCESS_PASSWORD_PEPPER, SecretStr)
    assert settings.PLOT_ACCESS_PASSWORD_PEPPER.get_secret_value() == _PEPPER


def test_repr_masks_the_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    """repr(Settings) lands in tracebacks and debug dumps verbatim."""
    settings = _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_PEPPER)
    assert _PEPPER not in repr(settings)
    assert "**********" in repr(settings)


def test_str_masks_the_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_PEPPER)
    assert _PEPPER not in str(settings)


def test_model_dump_json_masks_the_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_PEPPER)
    dumped = settings.model_dump_json()
    assert _PEPPER not in dumped
    assert "**********" in dumped


def test_boot_error_never_echoes_the_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The boot error is rendered into logs and CI output verbatim.

    A ValueError here would become a pydantic ValidationError whose `input`
    echoes the whole raw settings dict — the live pepper included, since that
    dict predates SecretStr construction. PlotAccessPepperConfigError is not a
    ValueError, so pydantic propagates it untouched. Triggered via the
    JWT-reuse rule, the one failure that actually HAS a pepper value.
    """
    with pytest.raises(PlotAccessPepperConfigError) as exc:
        _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_JWT)
    rendered = str(exc.value) + repr(exc.value) + repr(exc.value.args)
    assert _JWT not in rendered
    assert "PLOT_ACCESS_PASSWORD_PEPPER" in rendered   # names the setting only


def test_short_pepper_error_never_echoes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    short = "tiny-test-only-pepper"
    with pytest.raises(PlotAccessPepperConfigError) as exc:
        _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=short)
    assert short not in str(exc.value) + repr(exc.value)


def test_blank_pepper_is_fine_while_enforcement_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only admin credential mutations are unavailable (controlled 503) — the
    app must still boot."""
    assert _settings(monkeypatch).PUBLIC_PLOT_PASSWORD_ENFORCEMENT is False


def test_enforcement_without_a_pepper_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(PlotAccessPepperConfigError, match="PLOT_ACCESS_PASSWORD_PEPPER"):
        _settings(monkeypatch, PUBLIC_PLOT_PASSWORD_ENFORCEMENT="true")


def test_enforcement_with_a_pepper_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch,
        PUBLIC_PLOT_PASSWORD_ENFORCEMENT="true",
        PLOT_ACCESS_PASSWORD_PEPPER=_PEPPER,
    )
    assert settings.PUBLIC_PLOT_PASSWORD_ENFORCEMENT is True


def test_pepper_may_not_reuse_the_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """One leaked value must not compromise both token signing and every stored
    credential digest."""
    with pytest.raises(PlotAccessPepperConfigError, match="must not reuse JWT_SECRET_KEY"):
        _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_JWT)


def test_short_pepper_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(PlotAccessPepperConfigError, match="at least 32"):
        _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER="short-pepper")


def test_env_example_carries_a_placeholder_never_a_real_value() -> None:
    from pathlib import Path

    example = (
        Path(__file__).resolve().parents[2] / ".env.example"
    ).read_text(encoding="utf-8")
    assert "PLOT_ACCESS_PASSWORD_PEPPER=" in example
    assert "PLOT_ACCESS_PASSWORD_PEPPER=\n" in example  # empty placeholder only
    assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT=false" in example


# --- round 8-9B.2: boot-error leak audit ----------------------------------
# The three fatal config failures all render their exception into logs, CI
# output and (in dev) a terminal. None of them may carry a live secret — not
# the pepper, not JWT_SECRET_KEY, and not the raw settings dict that both live
# in before the model is built.

def _boot_error_text(exc: PlotAccessPepperConfigError) -> str:
    """Everything a caller/log handler could realistically render."""
    import traceback

    return (
        str(exc) + repr(exc) + repr(exc.args)
        + "".join(traceback.format_exception_only(type(exc), exc))
    )


def test_short_pepper_error_leaks_neither_secret_nor_the_settings_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short = "tiny-test-only-pepper"
    with pytest.raises(PlotAccessPepperConfigError) as exc:
        _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=short)
    text = _boot_error_text(exc.value)
    assert short not in text                     # the offending value
    assert _JWT not in text                      # the OTHER secret in scope
    assert "DB_PASSWORD" not in text             # no raw settings dump
    assert "input_value" not in text             # not a pydantic input echo
    assert "PLOT_ACCESS_PASSWORD_PEPPER" in text  # names the setting only


def test_jwt_reuse_error_leaks_neither_value(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(PlotAccessPepperConfigError) as exc:
        _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_JWT)
    text = _boot_error_text(exc.value)
    assert _JWT not in text
    assert "DB_PASSWORD" not in text
    assert "JWT_SECRET_KEY" in text   # names it, never its value


def test_enforcement_without_pepper_error_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(PlotAccessPepperConfigError) as exc:
        _settings(monkeypatch, PUBLIC_PLOT_PASSWORD_ENFORCEMENT="true")
    text = _boot_error_text(exc.value)
    assert _JWT not in text
    assert "DB_PASSWORD" not in text


def test_the_config_error_is_not_a_pydantic_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ValueError here would become a ValidationError whose `input` echoes
    the entire raw settings dict — every env-sourced secret in plaintext."""
    from pydantic import ValidationError

    assert not issubclass(PlotAccessPepperConfigError, ValueError)
    with pytest.raises(PlotAccessPepperConfigError) as exc:
        _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_JWT)
    assert not isinstance(exc.value, ValidationError)


def test_model_dump_and_str_never_expose_a_configured_pepper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_PEPPER)
    rendered = (
        repr(settings) + str(settings)
        + settings.model_dump_json() + str(settings.model_dump())
    )
    assert _PEPPER not in rendered
    assert "**********" in rendered


def test_a_log_record_of_the_settings_object_stays_masked(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The realistic accident: someone logs the settings object. SecretStr must
    keep the pepper out of the formatted record."""
    import logging

    settings = _settings(monkeypatch, PLOT_ACCESS_PASSWORD_PEPPER=_PEPPER)
    logger = logging.getLogger("test.plot_access_pepper")
    with caplog.at_level(logging.INFO, logger="test.plot_access_pepper"):
        logger.info("settings=%s", settings)
        logger.info("pepper_field=%r", settings.PLOT_ACCESS_PASSWORD_PEPPER)
    rendered = "".join(r.getMessage() for r in caplog.records)
    assert _PEPPER not in rendered
    assert "**********" in rendered


def test_nothing_at_startup_dumps_settings_or_the_environment() -> None:
    """Source guard: no boot path prints/logs the whole Settings object or
    os.environ, which would defeat every masking guarantee above."""
    import inspect
    from pathlib import Path

    import app.main as main_module

    src = Path(inspect.getfile(main_module)).read_text(encoding="utf-8")
    for banned in ("os.environ", "settings.model_dump()", "vars(settings)", "dict(settings"):
        assert banned not in src
