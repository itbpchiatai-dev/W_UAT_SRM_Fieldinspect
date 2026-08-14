# docs/database.md

> Database reference สำหรับ PostgreSQL 16+ / SQLAlchemy 2.0 async / Alembic
> ครอบคลุม: naming, migrations, rollback, seed, indexes, connection pooling

---

## 1. Database Setup

### 1.0 Database Mode

กำหนด ownership ใน `project.config`:

```bash
DATABASE_MODE=new       # default — project เป็นเจ้าของ schema และ migrations
DATABASE_MODE=existing  # เชื่อม database เดิม — ห้ามแก้ schema อัตโนมัติ
```

Quick Start ปกติยังใช้ `new` โดยไม่เพิ่มคำถาม:

```bash
python scripts/setup.py
```

เมื่อต้องใช้ database เดิม:

```bash
setup-existing-db.bat
```

หรือเรียก Python โดยตรง:

```bash
python scripts/setup.py --database-mode existing
```

โหมด `existing` จะเขียน connection ลง `backend/.env` เท่านั้น และข้ามการสร้าง
database/user, Alembic migration, seed และ auto-start server เพื่อให้ตรวจ model mapping
กับ migration plan ก่อน

### 1.1 Required Extensions

ใช้ทุก project (เพิ่มเป็น first migration):

| Extension | Purpose |
|---|---|
| `uuid-ossp` | UUID generation (`uuid_generate_v4()`) |
| `pgcrypto` | Crypto functions (hashing, randomness) |
| `pg_trgm` | Trigram matching for full-text search (ภาษาไทย) |
| `pgvector` | Vector similarity (AI embeddings) — **optional**, ติดตั้งเพิ่มเมื่อต้องการ |

### 1.2 Connection (Async)

**`app/db/session.py`:**

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    global _engine, _sessionmaker
    settings = get_settings()
    _engine = create_async_engine(
        settings.database_url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=settings.APP_DEBUG,
    )
    _sessionmaker = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )


async def close_db() -> None:
    if _engine is not None:
        await _engine.dispose()


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

### 1.3 Pool Sizing Guideline

| Workload | `DB_POOL_SIZE` | `DB_MAX_OVERFLOW` |
|---|---|---|
| Small app (< 10 concurrent users) | 5 | 10 |
| Medium app (10-100 users) | 10 | 20 |
| Large app (> 100 users) | 20-50 | 40-100 |

⚠️ Total = `pool_size + max_overflow` ต้องไม่เกิน `max_connections` ของ Postgres (default 100) หารด้วยจำนวน replica

### 1.4 Existing Database Safety

- โครงสร้างเดิมเป็น authority แม้ naming ไม่ตรง standard
- map SQLAlchemy model ให้ตรง column/table เดิมโดยไม่ rename database
- แยก table ที่ app ใหม่เป็นเจ้าของด้วย PostgreSQL schema หรือ prefix ที่ชัดเจน
- Alembic ต้องจัดการเฉพาะ object ที่ app ใหม่เป็นเจ้าของ
- ห้ามรัน `alembic upgrade head` หรือ autogenerate กับ production database เดิม
  จนกว่าจะ review diff, backup/rollback plan และได้รับอนุมัติตาม AGENTS.md §4

---

## 2. Models (SQLAlchemy 2.0)

### 2.1 Base

**`app/db/base.py`:**

```python
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: dict[Any, Any] = {UUID: PgUUID(as_uuid=True)}


class UUIDMixin:
    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

### 2.2 Model Examples

**`app/db/models/user.py`:**

```python
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.product import Product


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, nullable=False)
    business_unit_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    products: Mapped[list["Product"]] = relationship(back_populates="created_by_user")
```

**`app/db/models/business_unit.py`:**

```python
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class BusinessUnit(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_units"

    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
```

**`app/db/models/product.py`:**

```python
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class Product(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    business_unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("business_units.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    created_by_user: Mapped["User"] = relationship(back_populates="products")

    __table_args__ = (
        Index("ix_products_status_business_unit", "status", "business_unit_id"),
        Index("ix_products_name_trgm", "name", postgresql_using="gin",
              postgresql_ops={"name": "gin_trgm_ops"}),
    )
```

---

## 3. Naming Conventions

### 3.1 Tables

- **plural snake_case:** `users`, `products`, `business_units`
- **Junction tables:** `<a>_<b>` alphabetical
- **Avoid reserved words:** `order` → `orders`, `user` → `users`

### 3.2 Columns

- **snake_case**
- **Foreign keys:** `<referenced_table_singular>_id`
- **Boolean:** prefix with `is_`, `has_`, `can_`
- **Timestamps:** `<verb>_at`

### 3.3 Indexes

- `ix_<table>_<column>` for single column
- `ix_<table>_<col1>_<col2>` for composite
- `uq_<table>_<column>` for unique
- `ix_<table>_<column>_trgm` for trigram

### 3.4 Constraints

- PK: `pk_<table>`
- FK: `fk_<table>_<column>_<referenced_table>`
- Unique: `uq_<table>_<column>`
- Check: `ck_<table>_<descriptive_name>`

Auto-generated by SQLAlchemy if `NAMING_CONVENTION` is set on `MetaData`

---

## 4. Repository Pattern

**`app/repositories/base.py`:**

```python
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Generic, TypeVar
from uuid import UUID

import orjson
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


def encode_cursor(data: dict) -> str:
    return urlsafe_b64encode(orjson.dumps(data)).decode("ascii")


def decode_cursor(cursor: str) -> dict:
    return orjson.loads(urlsafe_b64decode(cursor.encode("ascii")))


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, entity_id: UUID | str) -> ModelT | None:
        result = await self.db.execute(
            select(self.model).where(self.model.id == entity_id)
        )
        return result.scalar_one_or_none()

    async def create(self, data: dict) -> ModelT:
        entity = self.model(**data)
        self.db.add(entity)
        await self.db.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.db.delete(entity)
```

**`app/repositories/product_repository.py`:**

```python
from typing import Sequence

from sqlalchemy import select

from app.db.models.product import Product
from app.repositories.base import BaseRepository, decode_cursor, encode_cursor


class ProductRepository(BaseRepository[Product]):
    model = Product

    async def list_paginated(
        self,
        *,
        cursor: str | None = None,
        limit: int = 20,
        status: str | None = None,
        business_unit_ids: Sequence[str] | None = None,
    ) -> tuple[Sequence[Product], str | None]:
        stmt = select(Product).order_by(Product.created_at.desc(), Product.id.desc())

        if business_unit_ids:
            stmt = stmt.where(Product.business_unit_id.in_(business_unit_ids))
        if status:
            stmt = stmt.where(Product.status == status)

        if cursor:
            cursor_data = decode_cursor(cursor)
            stmt = stmt.where(Product.created_at < cursor_data["created_at"])

        stmt = stmt.limit(limit + 1)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        has_more = len(items) > limit
        items = items[:limit]
        next_cursor = None
        if has_more and items:
            next_cursor = encode_cursor({"created_at": items[-1].created_at.isoformat()})

        return items, next_cursor
```

---

## 5. Alembic Setup

### 5.1 Config

**`alembic.ini`** (excerpt — only non-default values):

```ini
[alembic]
script_location = alembic
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
sqlalchemy.url = driver://placeholder  # overridden in env.py

[loggers]
keys = root,sqlalchemy,alembic
```

**`alembic/env.py`:**

```python
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import business_unit, product, user  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### 5.2 Initial Migration (Extensions)

```bash
alembic revision -m "enable_extensions"
```

แล้วแก้ไฟล์ที่สร้างมา:

```python
"""enable extensions"""
from alembic import op

revision = "0001_enable_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm";')
    # pgvector — optional, เพิ่มเฉพาะถ้า project ต้องการ AI embeddings
    # ต้องใช้ image pgvector/pgvector:pg16
    # op.execute('CREATE EXTENSION IF NOT EXISTS "vector";')


def downgrade() -> None:
    # op.execute('DROP EXTENSION IF EXISTS "vector";')
    op.execute('DROP EXTENSION IF EXISTS "pg_trgm";')
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto";')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp";')
```

### 5.3 Auto-generate Migrations

```bash
alembic revision --autogenerate -m "add products table"
```

⚠️ **Always review auto-generated migrations** — Alembic ตรวจไม่เจอ:
- Index ที่เปลี่ยน
- Check constraints
- Default values บางประเภท
- Custom types

### 5.4 Migration Commands

```bash
alembic upgrade head        # Upgrade to latest
alembic upgrade +1          # Upgrade by 1
alembic downgrade -1        # Downgrade by 1
alembic current             # Show current revision
alembic history             # Show history
alembic heads               # Show pending migrations
```

---

## 6. Migration Rules

### 6.1 Mandatory

1. **ทุก migration ต้องมี downgrade** — ห้าม `pass` ใน downgrade ยกเว้น irreversible
2. **Test ก่อน apply production:** upgrade → downgrade → upgrade ต้องทำงานได้ทั้งหมด
3. **Review autogen ก่อน apply**
4. **Backward-compatible schema changes ใน production** (วิธีเต็ม + ตัวอย่างโค้ดดู §6.4):
   - **Adding columns:** ต้องมี default หรือ nullable
   - **Removing columns:** deploy 2 ครั้ง
   - **Renaming columns:** deploy 3 ครั้ง

### 6.2 Index Creation in Production

ใช้ `CONCURRENTLY`:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_products_status "
            "ON products (status)"
        )

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_products_status")
```

### 6.3 Data Migrations

แยก data migration ออกจาก schema migration

### 6.4 Expand/Contract (เปลี่ยน schema ตอน user ใช้อยู่)

หลักการกัน "deploy ทับตอน user ใช้อยู่แล้วแอปพัง": **ห้ามรวม schema change ที่ breaking กับ code change ในรอบเดียว** — แยกเป็นหลายรอบให้ schema ใหม่กับโค้ดเก่าอยู่ด้วยกันได้ระหว่างทาง

> ทำไมสำคัญ: `deploy.sh` รัน `alembic upgrade head` ขณะ container เก่ายังรับ traffic อยู่ ถ้า migration ลบ/เปลี่ยนชื่อ column ที่โค้ดเก่ายังเรียก → แอปพังทันที expand/contract ทำให้ช่วงคาบเกี่ยวนี้ปลอดภัย

**เพิ่ม column** — รอบเดียวพอ (ถ้า nullable หรือมี default):

```python
def upgrade() -> None:
    op.add_column("products", sa.Column("sku", sa.String(40), nullable=True))
```

**ลบ column — 2 รอบ (contract ทีหลัง):**

```text
รอบ 1: deploy โค้ดที่เลิกใช้ column นั้น (หยุดอ่าน/เขียน) — ยังไม่แตะ DB
รอบ 2: migration DROP COLUMN  (ตอนนี้ไม่มีโค้ดไหนใช้แล้ว)
```

**เปลี่ยนชื่อ column — 3 รอบ (อย่า `ALTER ... RENAME` ตรงๆ):**

```text
รอบ 1 (expand)  : add column ใหม่ + โค้ดเขียนทั้งเก่า+ใหม่, อ่านจากเก่า
                  backfill: UPDATE t SET new_col = old_col WHERE new_col IS NULL
รอบ 2           : โค้ดสลับมาอ่าน/เขียน column ใหม่
รอบ 3 (contract): migration DROP column เก่า
```

เคสจริงใน standard: การ normalize `users.email` (case-insensitive) ควรทำแบบ expand — รอบแรก normalize เฉพาะตอนเขียนใหม่ + backfill เงียบ ๆ แล้วค่อย enforce unique ทีหลัง แทนที่จะ migrate ทุกแถวพร้อมใส่ unique ในรอบเดียว (ซึ่งจะ abort กลางคันถ้าเจอ email ซ้ำ — ดู `patches/README.md`)

---

## 7. Seed Data

scaffold ส่ง `app/db/seed.py` แบบ no-op มาให้แล้ว — แค่ uncomment ตัวอย่างใน `seed_lookup_data()` แล้วเปลี่ยนเป็น model ของ project:

```python
# app/db/seed.py (scaffolded — no-op by default)
import asyncio

from app.db.session import close_db, init_db


async def seed_lookup_data() -> None:
    """Insert lookup / reference data. Idempotent — safe to re-run."""
    # Example:
    #
    # from sqlalchemy import select
    # from app.db.models.business_unit import BusinessUnit
    # from app.db.session import get_db_session
    #
    # async with get_db_session() as session:
    #     defaults = [("BU_HQ", "Headquarters", "Head office")]
    #     for code, name, description in defaults:
    #         existing = await session.execute(
    #             select(BusinessUnit).where(BusinessUnit.code == code)
    #         )
    #         if existing.scalar_one_or_none() is None:
    #             session.add(BusinessUnit(code=code, name=name, description=description))
    #     await session.commit()
    return None


async def main() -> None:
    await init_db()
    try:
        await seed_lookup_data()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `python -m app.db.seed`

Rule: seed scripts ต้อง **idempotent** — onboarding รัน seed ซ้ำหลัง `docker compose down -v` reset

---

## 8. Index Strategy

### 8.1 When to Add Indexes

- ทุก foreign key column → index automatic ใน SQLAlchemy
- Columns ใน `WHERE` clauses ที่ใช้บ่อย
- Columns ใน `ORDER BY` ของ paginated queries
- Columns ใน `JOIN` conditions

### 8.2 Composite Indexes

Order matters — column ที่ filter จากบ่อยที่สุดอยู่ก่อน

### 8.3 Full-text Search (pg_trgm)

```python
Index(
    "ix_products_name_trgm",
    "name",
    postgresql_using="gin",
    postgresql_ops={"name": "gin_trgm_ops"},
)
```

Query:

```python
stmt = select(Product).where(Product.name.op("%")(search_query))
stmt = select(Product).where(Product.name.ilike(f"%{search_query}%"))
```

### 8.4 Vector Search (pgvector)

```python
from pgvector.sqlalchemy import Vector

class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))

    __table_args__ = (
        Index(
            "ix_documents_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
```

Query:

```python
stmt = (
    select(Document)
    .order_by(Document.embedding.cosine_distance(query_vector))
    .limit(10)
)
```

---

## 9. Transactions

### 9.1 Service-level Transactions

Service ต้องเป็นคน `commit()`:

```python
class ProductService:
    async def create_product(self, payload: ProductCreate) -> Product:
        product = await self.repo.create(payload.model_dump())
        await self.audit_repo.log("product.created", product.id)
        await self.db.commit()
        return product
```

### 9.2 Explicit Transactions

```python
async with session.begin():
    await repo1.do_something()
    await repo2.do_something()
```

---

## 10. Query Patterns to Avoid

### 10.1 N+1 Queries

❌ ผิด:
```python
products = await repo.list_products()
for product in products:
    bu = await bu_repo.get(product.business_unit_id)
```

✅ ถูก:
```python
stmt = select(Product).options(selectinload(Product.business_unit))
```

### 10.2 SELECT *

❌ Loading whole row when only need few columns
✅ Loading specific columns:
```python
result = await db.execute(select(Product.id, Product.name, Product.price))
```

### 10.3 Unbounded Queries

❌ `SELECT * FROM products`
✅ Always paginate with cursor or limit

---

## 11. Quick Reference: เมื่อ AI ได้รับ task

1. **Adding a table:**
   - Create model in `db/models/<name>.py`
   - Import in `db/models/__init__.py` and `alembic/env.py`
   - Run `alembic revision --autogenerate -m "add <name>"`
   - Review the generated migration
   - Add repository in `repositories/<name>_repository.py`
   - Update `architecture.md` if it's a new domain area

2. **Adding column:**
   - Add to model
   - Run autogen
   - For production: ensure nullable or has default

3. **Adding index:**
   - Add to model `__table_args__`
   - Autogen migration
   - For large production tables: convert to `CONCURRENTLY` manually

4. **Adding extension:**
   - Add to first extensions migration (or new migration)
   - Update `database.md` "Required Extensions" list
