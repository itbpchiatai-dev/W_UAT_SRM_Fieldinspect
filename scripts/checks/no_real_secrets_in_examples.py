#!/usr/bin/env python3
"""Pre-commit check: ensure .example files contain only placeholders.

Enforces AGENTS.md §3 rule 6:
"ห้ามใส่ real values ใน .env.example / project.config.example — ใช้ placeholder เท่านั้น"

Usage:
    python scripts/checks/no_real_secrets_in_examples.py FILE [FILE ...]

Exit code:
    0 = all OK
    1 = at least one real-looking value found

Allowed placeholder forms:
    KEY=                            (empty)
    KEY=<placeholder>               (angle brackets)
    KEY=your-...-here               (instructional)
    KEY=00000000-0000-...           (UUID zeros)
    KEY=changeme | placeholder      (literal placeholder words)
    KEY=xxx... | ***                (obvious placeholders)
    # any comments                  (ignored)

Real secret patterns (FAIL):
    KEY=sk-ant-api03-...            (Anthropic API key)
    KEY=sk-...                      (OpenAI API key)
    KEY=eyJ...                      (JWT)
    KEY=ghp_... / gho_...           (GitHub tokens)
    KEY=AKIA...                     (AWS access key)
    Any other value >= 20 chars not matching placeholder forms
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Match `KEY=value` where KEY contains a secret-related word
SECRET_KEY_PATTERN = re.compile(
    r'^\s*([A-Z_][A-Z0-9_]*?'
    r'(?:KEY|SECRET|PASSWORD|TOKEN|API_KEY|CLIENT_SECRET|PRIVATE_KEY|CREDENTIAL))'
    r'\s*=\s*(.*?)\s*$',
    re.IGNORECASE,
)

PLACEHOLDER_PATTERNS = [
    re.compile(r'^$'),                                          # empty
    re.compile(r'^<.+>$'),                                      # <placeholder>
    re.compile(r'^your[-_].+[-_]here$', re.IGNORECASE),         # your-key-here
    re.compile(r'^0{8}-0{4}-0{4}-0{4}-0{12}$'),                 # UUID all-zero
    re.compile(r'^0+$'),                                        # 000000...
    re.compile(r'^(changeme|placeholder|example|todo|fixme|tbd)$', re.IGNORECASE),
    re.compile(r'^x{3,}$', re.IGNORECASE),                      # xxx, xxxx, ...
    re.compile(r'^\*{3,}$'),                                    # ***
    re.compile(r'^(none|null|undefined)$', re.IGNORECASE),
]

KNOWN_SECRET_PREFIXES = (
    'sk-ant-',     # Anthropic API key
    'sk-proj-',    # OpenAI project key
    'sk-',         # OpenAI / generic secret prefix
    'eyJ',         # JWT (base64-encoded header always starts with this)
    'ghp_',        # GitHub Personal Access Token
    'gho_',        # GitHub OAuth token
    'ghs_',        # GitHub server-to-server
    'AKIA',        # AWS access key ID
    'ASIA',        # AWS temporary access key
)


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def is_placeholder(value: str) -> bool:
    value = strip_quotes(value)
    if not value:
        return True
    return any(p.match(value) for p in PLACEHOLDER_PATTERNS)


def looks_like_real_secret(value: str) -> bool:
    value = strip_quotes(value)

    # Known secret prefixes — always fail
    if any(value.startswith(prefix) for prefix in KNOWN_SECRET_PREFIXES):
        return True

    # Long alphanumeric/base64-like — likely real
    if len(value) >= 20 and re.match(r'^[A-Za-z0-9_+/=.\-]{20,}$', value):
        return not is_placeholder(value)

    return False


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: cannot read: {exc}"]

    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        match = SECRET_KEY_PATTERN.match(stripped)
        if not match:
            continue

        key, value = match.group(1), match.group(2)

        if is_placeholder(value):
            continue

        if looks_like_real_secret(value):
            preview = stripped if len(stripped) <= 80 else stripped[:77] + '...'
            errors.append(
                f"{path}:{lineno}: real-looking value for '{key}': {preview}"
            )

    return errors


def main() -> int:
    files = [Path(arg) for arg in sys.argv[1:]]
    if not files:
        return 0

    all_errors: list[str] = []
    for path in files:
        all_errors.extend(check_file(path))

    if all_errors:
        print("\n".join(all_errors), file=sys.stderr)
        print(
            "\n.example files must contain placeholders only.\n"
            "Use empty values, <placeholder>, your-key-here, or 'changeme'.\n"
            "See AGENTS.md §3 rule 6.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
