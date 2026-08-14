"""Exit 0 if running Python is >= 3.12, else 1.

Called by setup.bat to avoid inline `python -c "..."` invocations that
cmd.exe misparses on some fresh Windows shells.
"""
import sys


MIN_VERSION = (3, 12)


def main() -> int:
    if sys.version_info >= MIN_VERSION:
        return 0
    found = ".".join(str(p) for p in sys.version_info[:3])
    required = ".".join(str(p) for p in MIN_VERSION)
    print(
        f"Python {required}+ required, found {found}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
