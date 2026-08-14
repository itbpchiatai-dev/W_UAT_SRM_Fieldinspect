"""Round-4 HIGH-3 regression — the safe-redirect.ts logic is mirrored
here as a source-level grep. Browser-side unit tests live next to the
.ts file (when vitest is wired); this test makes sure the helper is
imported by the two consumers (Login + RequireAuth)."""
from __future__ import annotations

from pathlib import Path

import pytest


def _scaffold_root() -> Path:
    # tests/security/test_safe_redirect.py → repo backend root → parent
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "app" / "main.py").exists():
            return ancestor.parent  # backend's parent = project root
    raise RuntimeError("Could not locate project root from test file")


def _read(*parts: str) -> str:
    p = _scaffold_root().joinpath("frontend", "src", *parts)
    if not p.exists():
        pytest.skip(f"frontend file not present: {p}")
    return p.read_text(encoding="utf-8")


def test_safe_redirect_helper_exists_and_blocks_known_bypasses() -> None:
    src = _read("lib", "safe-redirect.ts")
    # Rejection rules the helper MUST encode — each is a real bypass surface.
    for needle in [
        "startsWith(\'//\')",  # protocol-relative
        "javascript:",            # javascript: URL
        "data:",                  # data: URL
        "vbscript:",              # legacy MSIE
        "includes(\'\\\\\')",  # backslash confusion
        "SAFE_RETURN_MAX_LEN",    # length cap
    ]:
        assert needle in src, f"safe-redirect.ts missing guard: {needle}"


def test_login_uses_safe_return() -> None:
    src = _read("pages", "Login.tsx")
    assert "from \'../lib/safe-redirect\'" in src
    assert "safeReturn(params.get(\'return\')" in src


def test_require_auth_uses_safe_return() -> None:
    src = _read("components", "RequireAuth.tsx")
    assert "from \'../lib/safe-redirect\'" in src
    assert "safeReturn(" in src
