"""Round 8-9F.1 — the test suite must not inherit the developer's runtime flag.

Round 8-9F switched PUBLIC_PLOT_PASSWORD_ENFORCEMENT on in backend/.env so the
public flow could be exercised locally. `pytest` then started failing 68 tests
that had nothing to do with the change: app/core/config.py builds its Settings
at import time from that same .env, so every test which mints a legacy
(non-password-bound) inspection session suddenly hit the 401 credential
recheck.

tests/conftest.py now pins the flag for the test PROCESS ONLY. This file proves
the pin actually works, that it works for the right REASON (timing +
precedence), and that it does not take away a test's ability to opt into
enforcement=true for itself.

Nothing here reads or writes backend/.env, and nothing changes the live runtime.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tests.conftest as tests_conftest
from app.core.config import Settings, get_settings

_ENV_VAR = "PUBLIC_PLOT_PASSWORD_ENFORCEMENT"


# --- the pin itself ---------------------------------------------------------

def test_the_test_process_sees_enforcement_off() -> None:
    """The headline guarantee: `python -m pytest` needs no command-line flag,
    whatever backend/.env currently says."""
    assert get_settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT is False


def test_the_environment_variable_is_what_makes_it_deterministic() -> None:
    assert os.environ[_ENV_VAR] == "false"


def test_a_real_env_var_outranks_the_dotenv_file() -> None:
    """pydantic-settings ranks environment variables above the dotenv file.
    That precedence is the whole mechanism — if it ever changed, the pin would
    silently stop working and this test is what would notice."""
    with patch.dict(os.environ, {_ENV_VAR: "true"}):
        assert Settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT is True
    with patch.dict(os.environ, {_ENV_VAR: "false"}):
        assert Settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT is False


# --- timing (the part that is easy to get wrong) ----------------------------

def test_the_pin_ran_before_anything_imported_the_settings_module() -> None:
    """A fixture — even an autouse one — would be too late: Settings is
    constructed the moment app.core.config is imported, which happens while test
    modules are being COLLECTED. conftest.py recorded whether that had already
    happened when the pin ran; it must be False, or the pin is decorative."""
    assert tests_conftest.APP_CONFIG_IMPORTED_BEFORE_PIN is False


def test_the_pin_is_at_the_top_of_conftest_above_every_app_import() -> None:
    """Ordering inside the file matters as much as the file's own ordering: an
    `from app...` import placed above the pin would re-introduce the bug."""
    source = Path(inspect.getfile(tests_conftest)).read_text(encoding="utf-8")
    pin_at = source.index('os.environ[ENFORCEMENT_FLAG_ENV_VAR]')
    for line in source[:pin_at].splitlines():
        stripped = line.strip()
        assert not stripped.startswith("from app"), f"app import above the pin: {stripped!r}"
        assert not stripped.startswith("import app"), f"app import above the pin: {stripped!r}"


def test_settings_really_is_built_at_import_time() -> None:
    """The premise the whole fix rests on. If app/core/config.py ever stopped
    instantiating at import time, the timing constraint above would relax — and
    someone should re-read this file before assuming it still applies."""
    source = Path(inspect.getfile(sys.modules["app.core.config"])).read_text(encoding="utf-8")
    assert "settings = get_settings()" in source
    assert "@lru_cache" in source


# --- the cache -------------------------------------------------------------

def test_clearing_the_settings_cache_still_yields_the_pinned_value() -> None:
    """get_settings is @lru_cache'd. A test that clears it (or a fixture that
    does) must not accidentally re-read the developer's .env."""
    get_settings.cache_clear()
    try:
        assert get_settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT is False
    finally:
        get_settings.cache_clear()


def test_no_state_leaks_between_tests_through_the_environment() -> None:
    """A test that overrides the env var must not leave it behind. Runs the
    override and then re-checks the pinned value."""
    with patch.dict(os.environ, {_ENV_VAR: "true"}):
        assert os.environ[_ENV_VAR] == "true"
    assert os.environ[_ENV_VAR] == "false"
    assert get_settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT is False


# --- opting INTO enforcement ------------------------------------------------

def test_a_test_can_still_force_enforcement_on_for_itself() -> None:
    """The pin sets a DEFAULT, not a ceiling. Every 8-9C security test patches
    get_settings in the module under test; that must keep working."""
    module = "app.api.v1.public_inspection_access"
    with patch(f"{module}.get_settings",
               return_value=SimpleNamespace(PUBLIC_PLOT_PASSWORD_ENFORCEMENT=True)):
        import app.api.v1.public_inspection_access as public_access

        assert public_access._enforcement_on() is True
    # ...and the override is gone afterwards.
    import app.api.v1.public_inspection_access as public_access

    assert public_access._enforcement_on() is False


def test_the_enforcement_suites_still_exercise_the_true_path() -> None:
    """Sanity: the security suites drive enforcement=true by patching
    get_settings in the module under test, not by reading the environment — so
    pinning false cannot have quietly turned them into no-ops."""
    src = (Path(__file__).parent / "test_public_phone_password_enforcement.py").read_text(
        encoding="utf-8"
    )
    assert "def _enforcement(" in src
    assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT=on" in src
    assert "get_settings" in src
    # and it really does run the on-path
    assert "enforcement=True" in src or "_enforcement(True)" in src


# --- production source stays clean -----------------------------------------

def _app_package_dir() -> Path:
    return Path(inspect.getfile(sys.modules["app.core.config"])).parents[1]


def test_no_application_module_hardcodes_a_test_override() -> None:
    """The fix lives entirely in the test harness. If app source ever grows an
    'if running under pytest: enforcement = False' branch, production would be
    one typo away from silently disabling the feature."""
    for path in _app_package_dir().rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in ("PYTEST_CURRENT_TEST", '"pytest" in sys.modules', "IS_TESTING"):
            assert needle not in source, f"{path} branches on the test harness ({needle})"


def test_the_application_never_writes_the_enforcement_flag() -> None:
    """Reading the flag is the app's job; setting it is the operator's."""
    for path in _app_package_dir().rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert 'os.environ["PUBLIC_PLOT_PASSWORD_ENFORCEMENT"]' not in source
        assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT =" not in source


def test_conftest_does_not_touch_the_dotenv_file() -> None:
    """The pin is process-local. conftest must never read, rewrite or delete
    backend/.env — the developer's runtime configuration is not ours to edit.

    Asserted on CODE only: the comment block above the pin necessarily talks
    about .env and dotenv precedence, and prose is not behaviour."""
    source = Path(inspect.getfile(tests_conftest)).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    for banned in ("open(", "write_text", "read_text", "unlink", "Path(", "shutil"):
        assert banned not in code, f"conftest must not touch files: {banned}"
