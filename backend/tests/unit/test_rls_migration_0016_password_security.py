"""Git UAT initial push — security fix to migration 0016 (RLS / srm_app
role creation). Source inspection (the local backend/alembic package shadows
the installed alembic, so the module can't be imported standalone — same
approach as test_rls_uuid_guard_migration.py / test_plot_cycles_rls_migration.py).

Before the FIRST fix, `upgrade()` did:
    app_password = os.environ.get("DB_APP_PASSWORD", "srm_app_dev_2026")
    op.execute(f\"\"\"...CREATE ROLE srm_app LOGIN PASSWORD '{app_password}'...\"\"\")

— a hardcoded fallback password (committed to git history, identical on
every fresh deploy that forgot to set DB_APP_PASSWORD) f-string-concatenated
straight into a DDL string. That fix moved to a `:pw` SQLAlchemy bind
parameter on the CREATE/ALTER ROLE statement itself.

A SECOND fix (found deploying to a real UAT server — `bind.execute(stmt,
{"pw": app_password})` against `CREATE ROLE srm_app LOGIN PASSWORD :pw`)
discovered that bind parameter never actually worked on real Postgres:
`PASSWORD $1` is a syntax error on every version — Postgres' own grammar for
CREATE/ALTER ROLE's PASSWORD clause only accepts a string literal, never a
bind parameter, in that position. The current code instead passes
`app_password` as an ordinary bind parameter to `SELECT quote_literal(:pw)`
and only f-string-interpolates Postgres' OWN safely-quoted/escaped output
(`quoted_password`) into the CREATE/ALTER ROLE text — so the raw secret still
never round-trips through Python string formatting; the security PROPERTY
these tests protect (`app_password` never directly spliced into SQL text) is
unchanged, only the mechanism achieving it.
"""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_06_29_0400-0016_rls.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")

_OLD_HARDCODED_PASSWORD = "srm_app_dev_2026"


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def _strip_comments(text: str) -> str:
    """Drop '#'-comment lines so a source-grep can't self-trip on prose
    (this file's own docstring/comments talk ABOUT the removed DO-block
    pattern and about never logging the password)."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0016_rls"
    assert down == "0015_records"
    assert len(revision) <= 32


def test_no_hardcoded_password_fallback_anywhere() -> None:
    # Regression guard: the old fallback string must never reappear, in
    # upgrade(), downgrade(), or a comment/docstring.
    assert _OLD_HARDCODED_PASSWORD not in _SRC


def test_env_read_has_no_non_blank_default() -> None:
    """`os.environ.get("DB_APP_PASSWORD", ...)` must default to blank (never
    a real-looking value) — the fail-fast check right after it is what turns
    a blank/missing value into a hard error, not a silently-accepted default."""
    m = re.search(r'os\.environ\.get\("DB_APP_PASSWORD",\s*"([^"]*)"\)', _upgrade())
    assert m is not None, "expected os.environ.get(\"DB_APP_PASSWORD\", \"...\") in upgrade()"
    assert m.group(1) == ""


def test_upgrade_fails_fast_on_blank_password() -> None:
    up = _upgrade()
    assert "if not app_password:" in up
    assert "raise RuntimeError(" in up
    # The raise must come before any DB call — get_bind()/execute() only
    # appear after the guard clause in source order.
    assert up.index("if not app_password:") < up.index("op.get_bind()")


def test_password_never_f_string_or_percent_formatted_into_sql() -> None:
    """The RAW secret (`app_password`) must never itself be spliced into SQL
    via str formatting — `quoted_password` (Postgres' own escaped output of
    `quote_literal()`, never the raw value) legitimately IS f-string-
    interpolated into the CREATE/ALTER ROLE text; that's the point of the
    second fix (see module docstring) and is exactly as safe as a bind
    parameter, since Postgres itself produced the escaping."""
    up = _upgrade()
    # The exact vulnerable pattern the first fix removed: the raw password
    # variable spliced into a quoted SQL string via str formatting.
    assert "PASSWORD '{app_password}" not in up
    assert "PASSWORD %s" not in up
    assert "PASSWORD %" not in up
    assert "{app_password}" not in up
    # f-strings ARE used to build the CREATE/ALTER ROLE statements (the
    # second fix) — but only ever with quoted_password, never app_password.
    for line in up.splitlines():
        if "ROLE" in line and "PASSWORD" in line and re.search(r'f["\']', line):
            assert "app_password" not in line, f"raw app_password spliced into a ROLE/PASSWORD f-string: {line!r}"


def test_password_bound_as_query_parameter() -> None:
    """`app_password` (the raw secret) is bound as an ordinary query
    parameter to `SELECT quote_literal(:pw)` — never spliced into SQL text
    itself, anywhere. Only quote_literal()'s safely-escaped RETURN VALUE
    (`quoted_password`) is used to build the CREATE/ALTER ROLE statements —
    see the module docstring for why a `:pw` bind parameter can't target the
    ROLE statement's PASSWORD clause directly."""
    up = _upgrade()
    assert 'sa.text("SELECT quote_literal(:pw)"), {"pw": app_password}' in up
    assert "CREATE ROLE srm_app LOGIN PASSWORD {quoted_password} NOINHERIT" in up
    assert "ALTER ROLE srm_app WITH PASSWORD {quoted_password}" in up
    # app_password itself never appears inside ANY sa.text(...) call's own
    # literal SQL text — only as a parameter dict value passed alongside one.
    for m in re.finditer(r"sa\.text\((.*?)\)", up, re.DOTALL):
        assert "app_password" not in m.group(1)
    # And never inside the plain-string CREATE/ALTER ROLE statements either.
    for line in up.splitlines():
        if "ROLE" in line and "PASSWORD" in line:
            assert "app_password" not in line


def test_no_do_block_wrapping_role_creation() -> None:
    """A DO $$ ... $$ block's body is opaque to the outer SQL parser, so a
    bind parameter on the outer statement can never reach inside it — the
    exists-check now runs as an ordinary top-level (parameterizable)
    statement instead."""
    up = _strip_comments(_upgrade())
    assert "DO $$" not in up
    assert "role_exists" in up


def test_upgrade_still_creates_the_same_role_and_grants() -> None:
    """The fix only changes HOW the password is sourced/bound — WHAT gets
    created (role name, NOINHERIT, grants) is unchanged."""
    up = _upgrade()
    assert "srm_app" in up
    assert "NOINHERIT" in up
    assert "GRANT USAGE ON SCHEMA public TO srm_app;" in up
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO srm_app;" in up
    assert "ENABLE ROW LEVEL SECURITY" in up
    assert "FORCE ROW LEVEL SECURITY" in up


def test_downgrade_unchanged_and_never_touches_password() -> None:
    down = _downgrade()
    assert "DROP ROLE IF EXISTS srm_app;" in down
    assert "PASSWORD" not in down
    assert "os.environ" not in down


def test_password_value_never_logged_or_echoed() -> None:
    up = _strip_comments(_upgrade())
    assert "print(" not in up
    assert "logger." not in up
    assert "logging." not in up
