#!/usr/bin/env python3
"""Pre-commit check: FastAPI endpoint params may not be annotated as `dict`.

Enforces AGENTS.md §B Enforcement Matrix: "No `dict` param in endpoint".
Raw `dict` body params skip Pydantic validation, silently accept any
payload, and give no OpenAPI schema — every input should be a
Pydantic model (CamelBaseModel subclass) instead.

Allowed:
    @router.post("/products")
    async def create(payload: ProductCreate): ...

Blocked:
    @router.post("/products")
    async def create(payload: dict): ...
    async def create(payload: dict[str, Any]): ...

Detection rule:
    A function is treated as an endpoint when it carries any decorator
    whose final attribute matches an HTTP method: get / post / put /
    patch / delete / options / head. We look for @router.<method>(...)
    or @app.<method>(...) — bare names like @get(...) are not common in
    FastAPI codebases and are ignored to keep the check noise-free.

Usage:
    python scripts/checks/no_dict_in_endpoint.py FILE [FILE ...]

Exit code:
    0 = all OK
    1 = an endpoint declares a dict-typed param
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Path component that marks a file as part of the API layer.
API_MARKER = "api"

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def is_api_file(path: Path) -> bool:
    """Match files under any `api/` directory (e.g. backend/app/api/v1/)."""
    return API_MARKER in path.parts


def _decorator_method(deco: ast.expr) -> str | None:
    """If `deco` is @<something>.<method>(...) or @<method>(...), return method.

    Returns None for non-call decorators.
    """
    call = deco
    if isinstance(call, ast.Call):
        func = call.func
    else:
        # Bare reference like @router.get (no parens) — fine to still check
        func = call

    if isinstance(func, ast.Attribute):
        return func.attr.lower()
    if isinstance(func, ast.Name):
        return func.id.lower()
    return None


def _is_endpoint(func: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """An endpoint is a function whose decorator's final attribute is an HTTP verb."""
    for deco in func.decorator_list:
        method = _decorator_method(deco)
        if method in HTTP_METHODS:
            return True
    return False


def _annotation_is_dict(annotation: ast.expr | None) -> bool:
    """Return True if the annotation is raw `dict` or `dict[...]`."""
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name) and annotation.id == "dict":
        return True
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        if isinstance(base, ast.Name) and base.id == "dict":
            return True
    # `typing.Dict` — older style; still bad
    if isinstance(annotation, ast.Attribute) and annotation.attr in {"Dict", "dict"}:
        return True
    return False


def _iter_params(func: ast.AsyncFunctionDef | ast.FunctionDef) -> list[ast.arg]:
    args = func.args
    return [
        *args.posonlyargs,
        *args.args,
        *args.kwonlyargs,
    ]


def check_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: cannot parse: {exc}"]

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_endpoint(node):
            continue
        for arg in _iter_params(node):
            if _annotation_is_dict(arg.annotation):
                lineno = arg.lineno or node.lineno
                errors.append(
                    f"{path}:{lineno}: endpoint '{node.name}' param "
                    f"'{arg.arg}' is annotated `dict` — use a Pydantic model "
                    f"(CamelBaseModel subclass) instead"
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
        if not is_api_file(path):
            continue
        all_errors.extend(check_file(path))

    if all_errors:
        print("\n".join(all_errors), file=sys.stderr)
        print(
            "\nReplace raw dict params with a Pydantic schema so FastAPI can\n"
            "validate input + emit OpenAPI. See AGENTS.md §12 + §B.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
