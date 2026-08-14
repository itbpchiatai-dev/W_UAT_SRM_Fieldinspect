# See docs/testing.md for full patterns.
# Add fixtures here as needed. pytest is auto-imported when this file exists.
#
# --- Round 8-9F.1: runtime-flag isolation ----------------------------------
#
# This block MUST stay at the very top of this file, above every other import.
#
# app/core/config.py builds a Settings instance at IMPORT time (`settings =
# get_settings()` on its last line, behind an @lru_cache). Settings reads
# backend/.env, which on a developer machine is the LIVE runtime configuration —
# and since round 8-9F that file legitimately carries
# PUBLIC_PLOT_PASSWORD_ENFORCEMENT=true so /public/inspect can be exercised
# locally.
#
# Without the pin below, `pytest` inherits whatever the developer's runtime
# happens to be doing: 68 tests that mint a legacy (non-password-bound)
# inspection session began failing with 401 the moment enforcement went on, even
# though nothing about the code had changed. A unit suite whose result depends
# on a local .env is not a unit suite.
#
# Two properties make this work, and both are asserted in
# tests/unit/test_enforcement_flag_test_isolation.py:
#
#   1. TIMING — conftest.py at the tests/ root is imported before any test
#      module, so this runs before anything has imported app.core.config and
#      frozen a Settings object. APP_CONFIG_IMPORTED_BEFORE_PIN records that
#      fact so the test can prove it instead of assuming it.
#   2. PRECEDENCE — pydantic-settings ranks a real environment variable ABOVE
#      the dotenv file, so this wins over backend/.env without touching it.
#
# Deliberately an unconditional assignment rather than setdefault(): the point
# is that `python -m pytest` gives the same answer on every machine, in CI, and
# while a developer has enforcement switched on. A test that needs
# enforcement=true overrides get_settings() for itself — the pattern every 8-9C
# security test already uses — and never asks the environment.
#
# This changes NOTHING about production or the local runtime: app source has no
# test-only branch, and backend/.env is not modified.
import os
import sys

APP_CONFIG_IMPORTED_BEFORE_PIN = "app.core.config" in sys.modules
ENFORCEMENT_FLAG_ENV_VAR = "PUBLIC_PLOT_PASSWORD_ENFORCEMENT"
ENFORCEMENT_FLAG_TEST_DEFAULT = "false"

os.environ[ENFORCEMENT_FLAG_ENV_VAR] = ENFORCEMENT_FLAG_TEST_DEFAULT
