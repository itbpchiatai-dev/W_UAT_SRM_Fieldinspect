"""Alembic env — sync psycopg driver.

Why sync (not async): the runtime app uses asyncpg, but asyncpg routes
DDL through prepared statements which reject multi-statement SQL. Several
migrations bundle CREATE TABLE + CREATE INDEX + CREATE INDEX in a single
op.execute() block, and those blow up under asyncpg with
``cannot insert multiple commands into a prepared statement``.

Migrations don't need async — they're a short-lived offline tool — so we
swap +asyncpg for +psycopg in the URL here and use a plain sync engine.
The runtime app's settings.database_url stays untouched.
"""
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection, make_url
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
settings = get_settings()

# Force sync psycopg for migrations. The runtime app keeps asyncpg.
# Use make_url(...).set(drivername=...) so we normalize ANY input
# (postgresql://, postgresql+asyncpg://, postgresql+psycopg2://) to
# postgresql+psycopg without textual surgery that could corrupt a
# password containing the literal "+asyncpg" or silently no-op on a
# URL that lacked the qualifier (which would let SQLAlchemy pick the
# default psycopg2 dialect — not in our dependencies).
#
# render_as_string(hide_password=False) — NOT str(...) — because str()
# on a URL object masks the password as '***', which alembic would then
# try to authenticate with and fail. Always render with the real password
# when we're handing the URL string to a driver.
sync_url = make_url(settings.database_url).set(
    drivername="postgresql+psycopg"
).render_as_string(hide_password=False)
config.set_main_option("sqlalchemy.url", sync_url)
target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Offline / --sql mode: emit SQL without a live DB connection."""
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: connect via engine_from_config so sqlalchemy.* keys
    in the [alembic] section of alembic.ini (e.g. sqlalchemy.echo=true)
    are honored. We override only the URL with the sync-driver version."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = sync_url
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
