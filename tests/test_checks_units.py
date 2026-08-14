"""Unit tests for the four custom pre-commit checks.

Each check should: pass on clean input, fail on dirty input, exit-code 0/1.
Tests are intentionally narrow (one positive + one negative per check)
so they stay fast and obviously correct.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKS = REPO_ROOT / "scripts" / "checks"


def _run(check: str, *files: Path) -> subprocess.CompletedProcess[str]:
    # decode as utf-8 (errors='replace') — checks may emit Thai/em-dashes, and
    # the default cp1252 decode on Windows would crash the reader thread.
    return subprocess.run(
        [sys.executable, str(CHECKS / check), *map(str, files)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# ─────────────────────────────────────────────────────────
# camel_base_model_audit
# ─────────────────────────────────────────────────────────

def test_camel_audit_pass(tmp_path: Path) -> None:
    f = tmp_path / "backend" / "app" / "schemas" / "product.py"
    f.parent.mkdir(parents=True)
    f.write_text(
        "from app.schemas.base import CamelBaseModel\n"
        "class ProductRead(CamelBaseModel):\n    id: int\n",
        encoding="utf-8",
    )
    result = _run("camel_base_model_audit.py", f)
    assert result.returncode == 0


def test_camel_audit_flags_raw_basemodel(tmp_path: Path) -> None:
    f = tmp_path / "backend" / "app" / "schemas" / "product.py"
    f.parent.mkdir(parents=True)
    f.write_text(
        "from pydantic import BaseModel\n"
        "class Bad(BaseModel):\n    id: int\n",
        encoding="utf-8",
    )
    result = _run("camel_base_model_audit.py", f)
    assert result.returncode == 1
    assert "Bad" in result.stderr


def test_camel_audit_exempts_base_py(tmp_path: Path) -> None:
    """base.py defines CamelBaseModel itself — must inherit BaseModel."""
    f = tmp_path / "backend" / "app" / "schemas" / "base.py"
    f.parent.mkdir(parents=True)
    f.write_text(
        "from pydantic import BaseModel\n"
        "class CamelBaseModel(BaseModel):\n    pass\n",
        encoding="utf-8",
    )
    assert _run("camel_base_model_audit.py", f).returncode == 0


# ─────────────────────────────────────────────────────────
# no_dict_in_endpoint
# ─────────────────────────────────────────────────────────

def test_no_dict_endpoint_pass(tmp_path: Path) -> None:
    f = tmp_path / "backend" / "app" / "api" / "v1" / "products.py"
    f.parent.mkdir(parents=True)
    f.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/x')\n"
        "async def create(payload: 'ProductCreate') -> None: ...\n",
        encoding="utf-8",
    )
    assert _run("no_dict_in_endpoint.py", f).returncode == 0


def test_no_dict_endpoint_flags_raw_dict(tmp_path: Path) -> None:
    f = tmp_path / "backend" / "app" / "api" / "v1" / "products.py"
    f.parent.mkdir(parents=True)
    f.write_text(
        "from typing import Any\nfrom fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/x')\n"
        "async def create(payload: dict[str, Any]) -> None: ...\n",
        encoding="utf-8",
    )
    result = _run("no_dict_in_endpoint.py", f)
    assert result.returncode == 1
    assert "payload" in result.stderr


def test_no_dict_endpoint_ignores_non_endpoint(tmp_path: Path) -> None:
    """Helper functions (no @router decorator) are allowed to take dict."""
    f = tmp_path / "backend" / "app" / "api" / "helpers.py"
    f.parent.mkdir(parents=True)
    f.write_text("def helper(payload: dict) -> None: pass\n", encoding="utf-8")
    assert _run("no_dict_in_endpoint.py", f).returncode == 0


# ─────────────────────────────────────────────────────────
# no_direct_ai_sdk
# ─────────────────────────────────────────────────────────

def test_no_direct_ai_sdk_pass(tmp_path: Path) -> None:
    f = tmp_path / "backend" / "app" / "services" / "thing.py"
    f.parent.mkdir(parents=True)
    f.write_text("def nothing(): pass\n", encoding="utf-8")
    assert _run("no_direct_ai_sdk.py", f).returncode == 0


def test_no_direct_ai_sdk_flags_anthropic_import(tmp_path: Path) -> None:
    f = tmp_path / "backend" / "app" / "services" / "thing.py"
    f.parent.mkdir(parents=True)
    f.write_text("from anthropic import AsyncAnthropic\n", encoding="utf-8")
    result = _run("no_direct_ai_sdk.py", f)
    assert result.returncode == 1
    assert "anthropic" in result.stderr


def test_no_direct_ai_sdk_allows_inside_integrations(tmp_path: Path) -> None:
    f = tmp_path / "backend" / "app" / "integrations" / "claude_ai.py"
    f.parent.mkdir(parents=True)
    f.write_text("from anthropic import AsyncAnthropic\n", encoding="utf-8")
    assert _run("no_direct_ai_sdk.py", f).returncode == 0


# ─────────────────────────────────────────────────────────
# no_real_secrets_in_examples
# ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("placeholder", ["", "<your-key-here>", "changeme", "***"])
def test_no_real_secrets_pass(tmp_path: Path, placeholder: str) -> None:
    f = tmp_path / "backend" / ".env.example"
    f.parent.mkdir(parents=True)
    f.write_text(f"CLAUDE_API_KEY={placeholder}\n", encoding="utf-8")
    assert _run("no_real_secrets_in_examples.py", f).returncode == 0


@pytest.mark.parametrize(
    "leak",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.sigsigsig",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
    ],
)
def test_no_real_secrets_flags_leak(tmp_path: Path, leak: str) -> None:
    f = tmp_path / "backend" / ".env.example"
    f.parent.mkdir(parents=True)
    f.write_text(f"CLAUDE_API_KEY={leak}\n", encoding="utf-8")
    assert _run("no_real_secrets_in_examples.py", f).returncode == 1


# ─────────────────────────────────────────────────────────
# no_raw_colors
# ─────────────────────────────────────────────────────────

def test_no_raw_colors_pass_on_tokens(tmp_path: Path) -> None:
    f = tmp_path / "frontend" / "src" / "Ok.tsx"
    f.parent.mkdir(parents=True)
    f.write_text(
        'export const X = () => <div className="bg-primary text-foreground '
        'border-accent text-muted-foreground" />;\n',
        encoding="utf-8",
    )
    assert _run("no_raw_colors.py", f).returncode == 0


def test_no_raw_colors_flags_raw_palette_and_hex(tmp_path: Path) -> None:
    f = tmp_path / "frontend" / "src" / "Bad.tsx"
    f.parent.mkdir(parents=True)
    f.write_text(
        'export const X = () => <div className="bg-blue-500 text-[#ff0000]" />;\n',
        encoding="utf-8",
    )
    result = _run("no_raw_colors.py", f)
    assert result.returncode == 1
    assert "bg-blue-500" in result.stderr
    assert "text-[#ff0000]" in result.stderr


def test_no_raw_colors_brand_allow_overrides(tmp_path: Path) -> None:
    f = tmp_path / "frontend" / "src" / "Vendor.tsx"
    f.parent.mkdir(parents=True)
    f.write_text(
        'const Logo = () => <i className="bg-blue-500" />; // brand-allow: vendor logo\n',
        encoding="utf-8",
    )
    assert _run("no_raw_colors.py", f).returncode == 0
