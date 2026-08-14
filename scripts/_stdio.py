"""Shared stdio helper for setup.py / init_project.py.

Extracted from setup.py — see test_setup_guards.py for the regression test
(test_setup_help_does_not_crash_on_cp1252). Both scripts print Thai +
emoji output; without this, default Windows PowerShell stdio (cp1252)
raises UnicodeEncodeError on the first ❌ / ✅ / ⚠️ character and the user
sees a Python traceback instead of the intended error message.

Idempotent — re-running on already-utf8 stdio is a no-op.
"""
from __future__ import annotations

import sys


def ensure_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to utf-8 with errors='replace' on Windows.

    No-op on non-win32 platforms (PEP 540 / locale.getpreferredencoding()
    already returns utf-8 there on every modern install).
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            # Stream isn't a TextIOWrapper (e.g. piped to non-TTY in odd
            # shells). Leaving it as-is is acceptable — the worst case is
            # the original cp1252 crash we were trying to avoid, but at
            # least we don't make things worse.
            pass


def getpass_masked(prompt: str) -> str:
    """Read a password while echoing '*' per typed character.

    stdlib getpass.getpass() on Windows shows nothing — users can't tell
    if their keystrokes registered and often think the wizard is hung.
    This variant echoes one '*' per char, supports backspace, and exits
    cleanly on Enter / Ctrl-C.

    Falls back to plain getpass.getpass when stdin isn't a TTY (CI pipes,
    automated tests) — masking only makes sense for an interactive shell.
    """
    import getpass

    if not sys.stdin.isatty():
        return getpass.getpass(prompt)

    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars: list[str] = []

    if sys.platform == "win32":
        try:
            import msvcrt  # noqa: PLC0415
        except ImportError:
            # Headless Windows / Python without msvcrt — degrade gracefully
            return getpass.getpass("")
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(chars)
            if ch == "\x03":  # Ctrl+C
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            if ch in ("\b", "\x7f"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            # Accept any printable char (incl. Thai). isprintable() rejects
            # control chars (good) but accepts space (intentional).
            if ch.isprintable():
                chars.append(ch)
                sys.stdout.write("*")
                sys.stdout.flush()
        # unreachable
    else:
        # Unix / macOS — raw mode via termios so we can read one byte at a time.
        try:
            import termios  # noqa: PLC0415
            import tty  # noqa: PLC0415
        except ImportError:
            return getpass.getpass("")
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return "".join(chars)
                if ch == "\x03":
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    raise KeyboardInterrupt
                if ch in ("\b", "\x7f"):
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue
                if ch.isprintable():
                    chars.append(ch)
                    sys.stdout.write("*")
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        # unreachable
