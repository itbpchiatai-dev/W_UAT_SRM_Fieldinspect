#!/usr/bin/env python3
"""Pre-commit check: no server-side fetch of a USER-CONTROLLED URL without
passing it through the SSRF guard (`app/core/safe_url.py`).

Enforces AGENTS.md §3 rule 9 + §B Enforcement Matrix:
    "ห้าม fetch user-provided URLs server-side เว้นแต่ allowlisted/reviewed
     แล้ว (SSRF risk)"

Why this check is *narrow* on purpose
-------------------------------------
The scaffolded backend already makes many LEGITIMATE outbound calls — Azure
AD token/JWKS, MS Graph send-mail, Registry telemetry. Every one of those
builds its URL from `settings.*` / module constants, e.g.::

    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=data)   # <- url is settings-derived

Those are SAFE and MUST NOT be flagged. SSRF only exists when the fetched URL
traces back to *external input*. So this check flags a client call ONLY when
the URL argument's root is:

  (a) a function parameter whose NAME looks like a URL
      (url / uri / link / endpoint / target / webhook / callback / src /
       href / address / host / domain / image_url ...), OR
  (b) an attribute / subscript chain rooted at a request-ish object
      (request / payload / body / data / form / params / query / item /
       dto / req), OR a `.json()` result, OR
  (c) a local variable transitively assigned from (a) or (b).

A finding is cleared when the URL (or an intermediate) is wrapped by the
guard — `assert_safe_url(...)` / `safe_url(...)` / `validate_url(...)` — or
when the author adds an inline `# ssrf-ok` marker on the call line (an
explicit, reviewed exception, mirroring the `brand-allow` convention in
no_raw_colors.py).

This biases hard toward NO false positives on existing code: anything that
isn't *clearly* externally-tainted is left alone.

Usage:
    python scripts/checks/no_unguarded_url_fetch.py FILE [FILE ...]

Exit code:
    0 = all OK (or every finding guarded / `# ssrf-ok`)
    1 = unguarded fetch of a user-controlled URL
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# HTTP client *modules* whose top-level functions fetch a URL directly.
_CLIENT_MODULES = {"httpx", "requests", "aiohttp"}
# HTTP client *constructors* — a name bound to one of these becomes a client.
_CLIENT_CTORS = {"Client", "AsyncClient", "ClientSession", "Session"}
# Method names that take a URL as their first argument.
_FETCH_METHODS = {
    "get", "post", "put", "patch", "delete", "head", "options",
    "request", "stream", "send",
}

# Guard wrappers that make a URL safe to fetch.
_GUARD_FUNCS = {"assert_safe_url", "safe_url", "validate_url", "safe_external_url"}

# Inline opt-out marker (reviewed exception).
_ALLOW_MARKER = "ssrf-ok"

# Parameter names that almost always carry a URL.
_URLISH_NAME = re.compile(
    r"(?:^|_)(?:url|uri|urls|link|links|endpoint|target|webhook|callback|"
    r"redirect|src|href|address|domain|host|hostname|feed|image|avatar|"
    r"resource|location|next)(?:_?url|_?uri|_?link|_?endpoint|s)?$",
    re.IGNORECASE,
)

# Roots that denote externally-supplied data (request bodies, query params).
_TAINT_ROOTS = {
    "request", "req", "payload", "body", "data", "form", "params",
    "query", "item", "dto", "input", "args", "kwargs",
}


def _root_name(node: ast.AST) -> str | None:
    """Walk an attribute/subscript chain down to its root Name id."""
    cur = node
    while True:
        if isinstance(cur, ast.Attribute):
            cur = cur.value
        elif isinstance(cur, ast.Subscript):
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.func
        else:
            break
    if isinstance(cur, ast.Name):
        return cur.id
    return None


def _is_json_result(node: ast.AST) -> bool:
    """True for expressions like `resp.json()` / `(await x).json()`."""
    cur = node
    if isinstance(cur, ast.Await):
        cur = cur.value
    return (
        isinstance(cur, ast.Call)
        and isinstance(cur.func, ast.Attribute)
        and cur.func.attr == "json"
    )


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_guarded(node: ast.AST) -> bool:
    """True if the expression is wrapped by a known SSRF guard call."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None
            )
            if name in _GUARD_FUNCS:
                return True
    return False


class _FunctionScope:
    """Per-function taint table + http-client name set."""

    def __init__(self, fn: ast.AST) -> None:
        self.tainted: set[str] = set()
        self.safe: set[str] = set()
        self.clients: set[str] = set()
        self._seed_params(fn)
        self._scan_body(fn)

    def _seed_params(self, fn: ast.AST) -> None:
        args = getattr(fn, "args", None)
        if args is None:
            return
        every = [
            *getattr(args, "posonlyargs", []),
            *args.args,
            *([args.vararg] if args.vararg else []),
            *args.kwonlyargs,
            *([args.kwarg] if args.kwarg else []),
        ]
        for a in every:
            if a is None:
                continue
            name = a.arg
            if _URLISH_NAME.search(name):
                self.tainted.add(name)

    def _classify(self, value: ast.AST) -> str:
        """Return 'tainted' | 'safe' | 'unknown' for an assigned expression."""
        if _is_guarded(value):
            return "safe"
        if _is_json_result(value):
            return "tainted"
        # attribute / subscript rooted at a request-ish object
        if isinstance(value, (ast.Attribute, ast.Subscript)):
            root = _root_name(value)
            if root in _TAINT_ROOTS or root in self.tainted:
                return "tainted"
        # any reference to an already-tainted name taints the result
        used = _names_in(value)
        if used & self.tainted:
            return "tainted"
        if used & _TAINT_ROOTS:
            return "tainted"
        return "unknown"

    def _scan_body(self, fn: ast.AST) -> None:
        for node in ast.walk(fn):
            # http-client constructors: x = httpx.AsyncClient() / with .. as x
            if isinstance(node, ast.Assign):
                self._maybe_record_client(node.targets, node.value)
                val_class = self._classify(node.value)
                if val_class != "unknown":
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            (self.tainted if val_class == "tainted"
                             else self.safe).add(tgt.id)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        self._maybe_record_client(
                            [item.optional_vars], item.context_expr
                        )

    def _maybe_record_client(self, targets: list, value: ast.AST) -> None:
        if not self._is_client_ctor(value):
            return
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                self.clients.add(tgt.id)

    @staticmethod
    def _is_client_ctor(value: ast.AST) -> bool:
        v = value.value if isinstance(value, ast.Await) else value
        if isinstance(v, ast.Call):
            fn = v.func
            if isinstance(fn, ast.Attribute) and fn.attr in _CLIENT_CTORS:
                return True
            if isinstance(fn, ast.Name) and fn.id in _CLIENT_CTORS:
                return True
        return False

    def url_arg_is_tainted(self, url_node: ast.AST) -> bool:
        if _is_guarded(url_node):
            return False
        if _is_json_result(url_node):
            return True
        if isinstance(url_node, ast.Name):
            return url_node.id in self.tainted
        if isinstance(url_node, (ast.Attribute, ast.Subscript)):
            root = _root_name(url_node)
            return root in _TAINT_ROOTS or root in self.tainted
        # f-string / concat: tainted if it interpolates a tainted/request value
        used = _names_in(url_node)
        return bool(used & self.tainted) or bool(used & _TAINT_ROOTS)


def _is_fetch_call(node: ast.Call, scope: _FunctionScope) -> bool:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        if fn.attr not in _FETCH_METHODS:
            return False
        base = _root_name(fn.value)
        # httpx.get(...) / requests.post(...)
        if isinstance(fn.value, ast.Name) and fn.value.id in _CLIENT_MODULES:
            return True
        # client.get(...) where client is a recorded http client
        if base in scope.clients:
            return True
        return False
    return False


def _url_argument(node: ast.Call) -> ast.AST | None:
    for kw in node.keywords:
        if kw.arg == "url":
            return kw.value
    if node.args:
        return node.args[0]
    return None


def check_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: cannot parse: {exc}"]

    src_lines = source.splitlines()
    errors: list[str] = []

    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def scan(node: ast.AST, scope: _FunctionScope) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and _is_fetch_call(sub, scope):
                line_idx = sub.lineno - 1
                line = src_lines[line_idx] if 0 <= line_idx < len(src_lines) else ""
                if _ALLOW_MARKER in line:
                    continue
                url = _url_argument(sub)
                if url is None:
                    continue
                if scope.url_arg_is_tainted(url):
                    errors.append(
                        f"{path}:{sub.lineno}: server-side fetch of a "
                        f"user-controlled URL without an SSRF guard"
                    )

    for fn in funcs:
        scan(fn, _FunctionScope(fn))

    return errors


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    files = [Path(a) for a in sys.argv[1:]]
    if not files:
        return 0

    all_errors: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        # The guard helper itself is allowed to parse URLs.
        if path.name == "safe_url.py":
            continue
        all_errors.extend(check_file(path))

    if all_errors:
        print("\n".join(all_errors), file=sys.stderr)
        print(
            "\nกำลัง fetch URL ที่มาจาก user input ตรงๆ ฝั่ง server = SSRF risk\n"
            "(attacker ชี้ URL ไปที่ internal service หรือ cloud metadata\n"
            "169.254.169.254 เพื่อขโมย credential).\n"
            "  • วิธีแก้: ส่ง URL ผ่าน assert_safe_url() จาก app/core/safe_url.py\n"
            "    ก่อน fetch — มันจะ block private/link-local/metadata IP ให้\n"
            "  • ถ้า URL นี้ปลอดภัยจริง (มาจาก allowlist/reviewed แล้ว):\n"
            "    เติม `# ssrf-ok` เป็นคอมเมนต์บนบรรทัด fetch เพื่อยืนยัน\n"
            "ดู AGENTS.md §3 ข้อ 9 + docs/security.md §3.6",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
