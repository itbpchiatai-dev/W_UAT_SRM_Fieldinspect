"""Shared env bootstrap for the security test suite.

pytest auto-loads conftest.py before collecting test files, so we have
a guaranteed before-import seam to set the env vars that pydantic-settings
expects (Round-4 HIGH-1 made JWT_SECRET_KEY mandatory + length-validated).
"""
from __future__ import annotations

import os

os.environ.setdefault("DB_PASSWORD", "test-only")
# 64-char hex passes the Round-4 HIGH-1 validator (>=32 chars + >=8 distinct).
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "deadbeef0123456789abcdeffedcba9876543210abcdef0123456789cafebabe",
)
os.environ.setdefault("API_CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("APP_ENV", "dev")  # dev → memory:// storage allowed
