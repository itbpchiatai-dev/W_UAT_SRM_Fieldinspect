"""Database Connections + Query Sandbox schemas (CamelBaseModel).

Read schemas deliberately OMIT the password — it is write-only. The
stored password is Fernet-encrypted and never leaves the server.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelBaseModel

SslMode = Literal["disable", "prefer", "require", "verify-ca", "verify-full"]


class DbConnectionRead(CamelBaseModel):
    id: UUID
    name: str
    description: str | None = None
    host: str
    port: int
    database: str
    username: str
    ssl_mode: str
    is_active: bool
    allow_write: bool
    last_tested_at: datetime | None = None
    last_test_status: str | None = None
    created_at: datetime
    updated_at: datetime


class DbConnectionCreate(CamelBaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1)
    ssl_mode: SslMode = "prefer"
    is_active: bool = True
    allow_write: bool = False


class DbConnectionUpdate(CamelBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    # Only re-encrypted + stored when a non-empty value is supplied; omit to
    # keep the existing password.
    password: str | None = None
    ssl_mode: SslMode | None = None
    is_active: bool | None = None
    allow_write: bool | None = None


class DbConnectionTestResult(CamelBaseModel):
    success: bool
    message: str
    server_version: str | None = None
    latency_ms: int | None = None


class DbTable(CamelBaseModel):
    """A user table/view in a target DB — powers the sandbox table browser."""

    schema_name: str
    name: str
    type: Literal["table", "view"]


class QueryRequest(CamelBaseModel):
    sql: str = Field(min_length=1)
    # Read-only is the safe default; the UI must explicitly opt out AND the
    # connection must have allow_write=True for a write to run.
    read_only: bool = True
    # Caps the returned row set; the server also enforces a hard ceiling
    # from app_settings (db_sandbox.max_rows).
    limit: int = Field(default=100, ge=1, le=10000)

    @field_validator("sql")
    @classmethod
    def _strip(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sql must not be blank")
        return v


class QueryResult(CamelBaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int
    # For non-SELECT statements (UPDATE/INSERT/...) Postgres returns a
    # command tag + affected-row count instead of a result set.
    command: str | None = None
    read_only: bool = True
