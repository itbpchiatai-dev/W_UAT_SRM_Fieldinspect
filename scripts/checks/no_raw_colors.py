#!/usr/bin/env python3
"""Pre-commit check: frontend must use brand color TOKENS, not raw colors.

Enforces the design system's visual contract (docs/design-system.md §1, §5,
§8): colour comes from semantic tokens (`bg-primary`, `text-foreground`,
`border-border`, `bg-accent` …) so every page stays on-brand regardless of
its layout. Raw Tailwind palette colours (`bg-blue-500`) and arbitrary hex
classes (`text-[#ff0000]`) bypass the brand and are flagged.

This is a SOFT gate — it WARNS and blocks the commit, but the author can keep
a colour on purpose by adding a `brand-allow` marker on the same line (an
explicit confirmation, e.g. a vendor logo). SVG `fill="#..."` attributes and
`.css` token files are not checked.

Usage:
    python scripts/checks/no_raw_colors.py FILE [FILE ...]

Exit code:
    0 = all OK (or every finding has a `brand-allow` confirmation)
    1 = unconfirmed raw colour found
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Tailwind's default chromatic palette — anything from here is off-brand.
# (neutrals black/white/transparent/current are intentionally NOT listed:
# they're low-risk utilities and would be noisy.)
_PALETTE = (
    "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|"
    "emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose"
)
# Utility prefixes that take a colour.
_PREFIX = (
    "bg|text|border|ring|ring-offset|from|via|to|fill|stroke|divide|"
    "outline|decoration|placeholder|caret|shadow|accent"
)

# `bg-blue-500`, `text-red-600/50`, `hover:border-green-400` …
_CHROMATIC = re.compile(
    rf"(?:^|[\s\"'`:])((?:{_PREFIX})-(?:{_PALETTE})(?:-\d{{1,3}})?)\b"
)
# Arbitrary hex in an arbitrary-value class: `bg-[#ff0000]`, `text-[#0af]`.
_ARBITRARY_HEX = re.compile(
    rf"((?:{_PREFIX})-\[#[0-9a-fA-F]{{3,8}}\])"
)

_ALLOW_MARKER = "brand-allow"
_EXTS = {".tsx", ".ts", ".jsx", ".js"}


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: cannot read: {exc}"]

    for n, line in enumerate(lines, 1):
        if _ALLOW_MARKER in line:
            continue  # author confirmed this line on purpose
        hits = [m.group(1) for m in _CHROMATIC.finditer(line)]
        hits += [m.group(1) for m in _ARBITRARY_HEX.finditer(line)]
        for hit in hits:
            errors.append(f"{path}:{n}: raw colour '{hit}' — use a brand token")
    return errors


def main() -> int:
    # The findings + help text contain em-dashes and Thai; force utf-8 so a
    # cp1252 Windows console (or a pre-commit subprocess) doesn't crash with
    # UnicodeEncodeError before the message is written.
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
        if path.suffix not in _EXTS:
            continue
        all_errors.extend(check_file(path))

    if all_errors:
        print("\n".join(all_errors), file=sys.stderr)
        print(
            "\nสีต้องมาจาก brand token (bg-primary / text-foreground / bg-accent …)\n"
            "ไม่ใช่สี Tailwind ดิบหรือ hex — เพื่อให้ทุกหน้าอยู่ในกรอบสีของ CT.\n"
            "  • วิธีแก้: เปลี่ยนไปใช้ token (ดู docs/design-system.md §1)\n"
            "  • ถ้าตั้งใจใช้สีนี้จริง (เช่น โลโก้ vendor): เติม `brand-allow`\n"
            "    เป็นคอมเมนต์บนบรรทัดนั้น เพื่อยืนยัน แล้ว commit ได้",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
