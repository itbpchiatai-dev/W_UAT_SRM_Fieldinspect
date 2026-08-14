#!/usr/bin/env python3
"""Pre-commit check: every schema must inherit from CamelBaseModel.

Enforces AGENTS.md §12 (camelCase JSON / snake_case DB) + §B Enforcement
Matrix. Files under backend/app/schemas/ may not define Pydantic classes
that inherit from raw `BaseModel` (or other BaseModel-only parents) —
the project's `CamelBaseModel` MUST be the parent so JSON aliasing is
applied uniformly.

Allowed:
    class ProductRead(CamelBaseModel): ...
    class ProductCreate(CamelBaseModel): ...
    class CamelBaseModel(BaseModel): ...        # the base itself

Blocked:
    class ProductRead(BaseModel): ...
    class ProductCreate(pydantic.BaseModel): ...

Usage:
    python scripts/checks/camel_base_model_audit.py FILE [FILE ...]

Exit code:
    0 = all OK
    1 = a schema inherits raw BaseModel instead of CamelBaseModel
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Path component that marks a file as a schema module.
SCHEMAS_MARKER = "schemas"

# Filename that defines CamelBaseModel itself — exempt from the rule.
BASE_FILENAME = "base.py"

# Bases that signal a Pydantic class but must NOT be used directly under schemas/.
RAW_PYDANTIC_BASES = {"BaseModel", "pydantic.BaseModel"}


def is_schema_file(path: Path) -> bool:
    """Match files anywhere under a `schemas/` directory."""
    return SCHEMAS_MARKER in path.parts


def _base_name(base: ast.expr) -> str:
    """Render a class-base AST node as a dotted name for matching."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        parts: list[str] = [base.attr]
        node: ast.expr = base.value
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))
    # Generics like BaseModel[T] — unwrap and recurse
    if isinstance(base, ast.Subscript):
        return _base_name(base.value)
    return ""


def check_file(path: Path) -> list[str]:
    """Return error messages for a single schema file."""
    if path.name == BASE_FILENAME:
        return []  # the file defining CamelBaseModel is exempt

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: cannot parse: {exc}"]

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {_base_name(b) for b in node.bases}
        if base_names & RAW_PYDANTIC_BASES:
            errors.append(
                f"{path}:{node.lineno}: class '{node.name}' inherits raw "
                f"BaseModel — use CamelBaseModel (app.schemas.base) instead"
            )
    return errors


def main() -> int:
    files = [Path(arg) for arg in sys.argv[1:]]
    if not files:
        return 0

    all_errors: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        if not is_schema_file(path):
            continue
        all_errors.extend(check_file(path))

    if all_errors:
        print("\n".join(all_errors), file=sys.stderr)
        print(
            "\nEvery schema must inherit CamelBaseModel so snake_case Python\n"
            "attributes serialize to camelCase JSON automatically.\n"
            "See AGENTS.md §12 and app/schemas/base.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
