#!/usr/bin/env python3
"""Pre-commit check: AI SDK imports must be inside app/integrations/ only.

Enforces AGENTS.md §16 Hard Rule + §B Enforcement Matrix:
    "ห้ามเรียก AsyncAnthropic / OpenAI SDK ตรงๆ จาก service หรือ endpoint —
     ต้องผ่าน app/integrations/<provider>.py ที่ log + cache + budget-check"

Usage:
    python scripts/checks/no_direct_ai_sdk.py FILE [FILE ...]

Exit code:
    0 = all OK
    1 = direct AI SDK import found outside app/integrations/
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Top-level modules that must only be imported within app/integrations/
BANNED_TOP_MODULES = {
    'anthropic',
    'openai',
}

# Path component that marks a file as exempt (integration wrapper)
INTEGRATIONS_MARKER = 'integrations'


def is_inside_integrations(path: Path) -> bool:
    """Check if path is under app/integrations/ directory."""
    return INTEGRATIONS_MARKER in path.parts


def check_file(path: Path) -> list[str]:
    """Return list of error messages for a single Python file."""
    errors: list[str] = []

    try:
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: cannot parse: {exc}"]

    for node in ast.walk(tree):
        # `import anthropic` / `import openai`
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                if top in BANNED_TOP_MODULES:
                    errors.append(
                        f"{path}:{node.lineno}: direct 'import {alias.name}' — "
                        f"use app/integrations/{top}_provider.py"
                    )

        # `from anthropic import AsyncAnthropic` / `from openai.types import ...`
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            top = node.module.split('.')[0]
            if top in BANNED_TOP_MODULES:
                errors.append(
                    f"{path}:{node.lineno}: direct 'from {node.module} import ...' — "
                    f"use app/integrations/{top}_provider.py"
                )

    return errors


def main() -> int:
    files = [Path(arg) for arg in sys.argv[1:]]
    if not files:
        return 0

    all_errors: list[str] = []
    for path in files:
        if is_inside_integrations(path):
            continue
        if not path.suffix == '.py':
            continue
        all_errors.extend(check_file(path))

    if all_errors:
        print("\n".join(all_errors), file=sys.stderr)
        print(
            "\nAI SDKs must be accessed through app/integrations/<provider>.py wrappers\n"
            "that auto-log to ai_call_logs and apply cost budgets.\n"
            "See AGENTS.md §16 and docs/patterns/ai.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
