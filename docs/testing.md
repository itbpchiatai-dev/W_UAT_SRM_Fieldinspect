# docs/testing.md

> Testing reference: pytest (backend) + Vitest (frontend)
> Coverage targets, test pyramid, fixtures, async patterns

---

## 1. Test Pyramid

```
        /\
       /E2E\         5-10%
      /------\
     /Integ.  \      20-30%
    /----------\
   /  Unit      \    60-70%
  /--------------\
```

- **Unit:** services, repositories, utils (no DB, no HTTP)
- **Integration:** API routes + real DB (test database)
- **E2E:** Playwright across full stack (limited scope, critical user flows only)

---

## 2. Coverage Targets

| Layer | Minimum | Target |
|---|---|---|
| Services | 80% | 90% |
| Repositories | 70% | 85% |
| API routes | 70% | 85% |
| Utility/helpers | 90% | 95% |
| **Overall backend** | **80%** | **85%** |
| Frontend components | 60% | 75% |
| Frontend hooks/utils | 80% | 90% |
| **Overall frontend** | **70%** | **80%** |

⚠️ Coverage % is **not** the goal — meaningful tests are. AI ห้าม fake tests เพื่อเพิ่ม coverage

---

## 3. Backend (pytest)

### 3.1 Setup

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "-ra",
    "--strict-markers",
    "--cov=app",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-fail-under=80",
]
markers = [
    "unit: unit tests (no I/O)",
    "integration: integration tests (DB, HTTP)",
    "slow: tests that take > 1s",
]
```

### 3.2 Folder Structure

```
backend/tests/
├── conftest.py              # shared fixtures
├── unit/
│   ├── test_security.py
│   ├── test_validators.py
│   └── services/
│       └── test_product_service.py
├── integration/
│   ├── conftest.py          # DB-specific fixtures
│   ├── test_auth_routes.py
│   └── test_product_routes.py
└── factories.py             # data factories
```

### 3.3 Conftest (shared)

**`tests/conftest.py`:**

```python
import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.main import create_app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Override settings for tests.

    Note: TEST_DATABASE_URL must be set in the test environment.
    The standard pattern is to use `${PROJECT_SLUG}_test` as the test DB name,
    where PROJECT_SLUG comes from project.config. Example:
        TEST_DATABASE_URL=postgresql+asyncpg://user:pw@localhost:5432/my_project_test
    """
    import os
    return Settings(
        APP_NAME="test",
        APP_ENV="dev",
        DB_HOST="localhost",
        DB_PORT=5432,
        DB_NAME=os.environ.get("TEST_DB_NAME", "test_db"),
        DB_USER="test",
        DB_PASSWORD="test",
        JWT_SECRET_KEY="test-secret-key-do-not-use-in-prod-" + "x" * 32,
        CLAUDE_API_KEY="test-key",
        AZURE_AD_TENANT_ID="00000000-0000-0000-0000-000000000001",
        AZURE_AD_CLIENT_ID="00000000-0000-0000-0000-000000000002",
        AZURE_AD_CLIENT_SECRET="test",
    )


@pytest_asyncio.fixture
async def app(test_settings: Settings):
    """Create app with overridden settings."""

    def override_settings():
        return test_settings

    application = create_app()
    application.dependency_overrides[get_settings] = override_settings
    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
```

### 3.4 Integration Conftest (DB)

**`tests/integration/conftest.py`:**

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base


@pytest_asyncio.fixture(scope="session")
async def db_engine(test_settings):
    engine = create_async_engine(test_settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """Transactional fixture — rolls back after each test."""
    connection = await db_engine.connect()
    transaction = await connection.begin()
    sessionmaker = async_sessionmaker(bind=connection, expire_on_commit=False)
    session = sessionmaker()

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def authenticated_client(client, db_session, test_settings):
    """Create user + return client with auth header."""
    from app.core.security import create_access_token
    from app.db.models.user import User

    user = User(
        email="test@example.com",
        full_name="Test User",
        auth_provider="azure_ad",
        roles=["internal:admin"],
        is_active=True,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = create_access_token(
        subject=str(user.id), extra_claims={"auth_provider": "azure_ad"}
    )
    client.headers["Authorization"] = f"Bearer {token}"
    yield client, user
```

### 3.5 Data Factories

**`tests/factories.py`:**

```python
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from app.db.models.business_unit import BusinessUnit
from app.db.models.product import Product
from app.db.models.user import User


def make_user(**overrides) -> User:
    defaults = dict(
        email=f"user-{uuid4().hex[:8]}@example.com",
        full_name="Test User",
        auth_provider="azure_ad",
        roles=["internal:viewer"],
        is_active=True,
        email_verified=True,
    )
    defaults.update(overrides)
    return User(**defaults)


def make_business_unit(**overrides) -> BusinessUnit:
    defaults = dict(
        code=f"BU{uuid4().hex[:6].upper()}",
        name="Test Business Unit",
    )
    defaults.update(overrides)
    return BusinessUnit(**defaults)


def make_product(*, business_unit_id, created_by, **overrides) -> Product:
    defaults = dict(
        sku=f"SKU-{uuid4().hex[:8].upper()}",
        name="Test Product",
        category="test",
        price=Decimal("100.00"),
        status="active",
        business_unit_id=business_unit_id,
        created_by=created_by,
    )
    defaults.update(overrides)
    return Product(**defaults)
```

### 3.6 Unit Test Example

**`tests/unit/test_security.py`:**

```python
import pytest

from app.core.security import (
    create_access_token,
    decode_jwt,
    hash_password,
    verify_password,
)


@pytest.mark.unit
class TestPasswordHashing:
    def test_hash_verify_roundtrip(self):
        plain = "MySecurePass123"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("MySecurePass123")
        assert verify_password("WrongPass", hashed) is False

    def test_different_hashes_for_same_input(self):
        """bcrypt should use random salt"""
        h1 = hash_password("pass")
        h2 = hash_password("pass")
        assert h1 != h2


@pytest.mark.unit
class TestJWT:
    def test_create_and_decode(self):
        token = create_access_token(subject="user-123")
        payload = decode_jwt(
            token, "test-secret-key-do-not-use-in-prod-" + "x" * 32, "HS256"
        )
        assert payload["sub"] == "user-123"
        assert payload["type"] == "access"
```

### 3.7 Integration Test Example

**`tests/integration/test_product_routes.py`:**

```python
import pytest

from tests.factories import make_business_unit, make_product


@pytest.mark.integration
@pytest.mark.asyncio
class TestProductRoutes:
    async def test_list_products_requires_auth(self, client):
        response = await client.get("/api/v1/products")
        assert response.status_code == 401

    async def test_list_products_empty(self, authenticated_client):
        client, user = authenticated_client
        response = await client.get("/api/v1/products")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["pagination"]["hasMore"] is False

    async def test_create_product_admin(self, authenticated_client, db_session):
        client, user = authenticated_client
        bu = make_business_unit()
        db_session.add(bu)
        await db_session.commit()

        user.business_unit_ids = [str(bu.id)]
        await db_session.commit()

        payload = {
            "name": "Test Product",
            "sku": "TEST-001",
            "businessUnitId": str(bu.id),
            "category": "general",
            "price": "150.50",
            "status": "draft",
        }
        response = await client.post("/api/v1/products", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert body["sku"] == "TEST-001"
        assert body["price"] == "150.50"
```

### 3.8 Mocking External Services

**`tests/unit/test_azure_ad.py`:**

```python
from unittest.mock import AsyncMock, patch

import pytest

from app.integrations.azure_ad import verify_azure_ad_token


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_azure_ad_token_success():
    fake_claims = {"email": "test@example.com", "name": "Test User"}
    with patch("app.integrations.azure_ad._get_jwks", new=AsyncMock(return_value={"keys": []})), \
         patch("app.integrations.azure_ad.jwt.decode", return_value=fake_claims):
        result = await verify_azure_ad_token("fake-token")
        assert result["email"] == "test@example.com"
```

---

## 4. Frontend (Vitest)

### 4.1 Setup

`vite.config.ts` (extended):

```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.d.ts', 'src/test/**', 'src/main.tsx'],
      thresholds: {
        lines: 70,
        functions: 70,
        branches: 65,
        statements: 70,
      },
    },
  },
});
```

### 4.2 Setup File

**`src/test/setup.ts`:**

```typescript
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
});
```

### 4.3 Component Test Example

**`src/features/products/ProductList.test.tsx`:**

```tsx
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi } from 'vitest';
import { I18nextProvider } from 'react-i18next';

import i18n from '@/i18n';
import { ProductList } from './ProductList';

vi.mock('@/api/products', () => ({
  listProducts: vi.fn().mockResolvedValue({
    data: [
      {
        id: '1',
        name: 'Product A',
        sku: 'A-001',
        price: '100.00',
        status: 'active',
      },
    ],
    pagination: { nextCursor: null, hasMore: false },
  }),
}));

function renderWithProviders(ui: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </I18nextProvider>,
  );
}

describe('ProductList', () => {
  it('renders products after fetch', async () => {
    renderWithProviders(<ProductList />);
    expect(await screen.findByText('Product A')).toBeInTheDocument();
  });
});
```

### 4.4 Hook Test Example

**`src/hooks/useDebounce.test.ts`:**

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useDebounce } from './useDebounce';

describe('useDebounce', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('returns initial value immediately', () => {
    const { result } = renderHook(() => useDebounce('hello', 500));
    expect(result.current).toBe('hello');
  });

  it('debounces value updates', () => {
    const { result, rerender } = renderHook(({ v }) => useDebounce(v, 500), {
      initialProps: { v: 'a' },
    });

    rerender({ v: 'b' });
    expect(result.current).toBe('a');

    act(() => vi.advanceTimersByTime(500));
    expect(result.current).toBe('b');
  });
});
```

---

## 5. E2E (Playwright) — Optional

ใช้ Playwright สำหรับ **critical user flows** เท่านั้น (login, place order, etc.) ไม่ทำ E2E สำหรับทุก feature

```typescript
// e2e/login.spec.ts
import { test, expect } from '@playwright/test';

test('user can login with email/password', async ({ page }) => {
  await page.goto('http://localhost:5173/login');
  await page.getByLabel('Email').fill('test@example.com');
  await page.getByLabel('Password').fill('TestPass123');
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/dashboard/);
});
```

---

## 6. CI Integration

Run tests ทุก PR — see [`docs/cicd.md`](./cicd.md)

Commands:

```bash
# Backend
cd backend
pytest                                # all tests
pytest -m unit                        # unit only
pytest -m integration                 # integration only
pytest --cov=app --cov-report=html    # with coverage report

# Frontend
cd frontend
npm test                              # run once
npm run test:watch                    # watch mode
npm run test:coverage                 # with coverage
```

---

## 7. Test Naming Conventions

### Backend (Python)

- File: `test_<module_name>.py`
- Class: `Test<ThingBeingTested>`
- Function: `test_<scenario>_<expected_outcome>`

```python
class TestProductService:
    async def test_create_product_with_valid_payload_returns_product(self): ...
    async def test_create_product_without_bu_access_raises_permission_error(self): ...
```

### Frontend (TypeScript)

```typescript
describe('ProductList', () => {
  it('renders products after successful fetch', () => { ... });
  it('shows loading state while fetching', () => { ... });
  it('shows error message on fetch failure', () => { ... });
});
```

---

## 8. Hard Rules

1. **ห้าม fake/skip test เพื่อให้ CI ผ่าน** — fix the underlying issue or document and ask
2. **ห้าม test ที่ขึ้นกับ external service จริง** — mock all external HTTP/AI calls
3. **ห้าม commit test ที่ใช้ production credentials**
4. **ห้าม use `time.sleep()`** ใน async tests — use `freezegun`/`time-machine` หรือ fake timers
5. **ทุก integration test ต้อง rollback หลังจบ** — ใช้ transactional fixture
6. **ทุก new endpoint ต้องมี integration test** อย่างน้อย 1 happy path + 1 auth failure
7. **ทุก new service method ต้องมี unit test**

---

## 9. Quick Reference: เมื่อ AI ได้รับ task

| Adding | Test Required |
|---|---|
| New endpoint | Integration test: happy path + auth failure + validation error |
| New service method | Unit test: happy path + each error branch |
| New repository method | Integration test ผ่าน DB |
| New React component | Component test: renders, user interactions |
| New custom hook | Hook test |
| Bug fix | Regression test ที่ reproduce bug ก่อน apply fix |
