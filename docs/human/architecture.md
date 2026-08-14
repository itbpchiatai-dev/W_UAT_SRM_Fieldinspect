# docs/human/architecture.md

> System architecture overview — high-level view of components and data flow
>
> **For AI agents:** this file is auto-maintained. Update when modules, integrations, or data flows change. Flag changes for user review.

---

## 1. System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                         EXTERNAL ACTORS                          │
│                                                                  │
│   [Internal Users]              [External Users]                 │
│   (employees via Azure AD)      (vendors/customers via local)   │
│                                                                  │
└──────────────────┬────────────────────────────┬─────────────────┘
                   │                            │
                   ▼                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       THE APPLICATION                            │
│                                                                  │
│  ┌──────────┐         ┌──────────┐         ┌──────────────┐    │
│  │ Frontend │ ──────► │ Backend  │ ──────► │  PostgreSQL  │    │
│  │  (SPA)   │   API   │  (API)   │   SQL   │   Database   │    │
│  └──────────┘         └─────┬────┘         └──────────────┘    │
│                             │                                    │
└─────────────────────────────┼────────────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
      ┌──────────┐      ┌──────────┐      ┌──────────┐
      │ Azure AD │      │  Claude  │      │  Other   │
      │   SSO    │      │   API    │      │ External │
      │          │      │          │      │ Services │
      └──────────┘      └──────────┘      └──────────┘
```

---

## 2. Component Inventory

| Component | Tech | Responsibility |
|---|---|---|
| Frontend SPA | React 18 + TypeScript + Vite | UI, client-side routing, state management |
| Backend API | FastAPI + Python 3.12 | REST API, business logic, integrations |
| Database | PostgreSQL 16 | Persistence, full-text search (pg_trgm) |
| Auth (Internal) | Azure AD (MSAL) | Employee authentication & SSO |
| Auth (External) | Local (bcrypt + JWT) | Vendor/customer authentication |
| AI | Anthropic Claude API | LLM features |
| Container | Docker (multi-stage) | Packaging & deployment |
| Reverse Proxy | (per project: nginx/Traefik/CloudFront) | TLS termination, routing |

---

## 3. Layered Architecture

```
┌──────────────────────────────────────────────────────────┐
│  PRESENTATION (React SPA)                                │
│  • Pages, components, i18n, routing                      │
│  • State: Zustand (client) + React Query (server)        │
└────────────────────┬─────────────────────────────────────┘
                     │ HTTPS / JSON (REST)
                     ▼
┌──────────────────────────────────────────────────────────┐
│  API GATEWAY (FastAPI)                                   │
│  • Routing, OpenAPI docs                                 │
│  • Auth middleware (JWT decode + provider verification)  │
│  • Pydantic validation                                   │
│  • Rate limiting, security headers                       │
│  • Request ID & structured logging                       │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌──────────────────────────────────────────────────────────┐
│  BUSINESS LOGIC (Services)                               │
│  • Pure Python — no FastAPI deps                         │
│  • Domain operations + permission checks                 │
│  • Integration orchestration (AI calls, external APIs)   │
│  • Transaction boundaries (commit/rollback)              │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌──────────────────────────────────────────────────────────┐
│  DATA ACCESS (Repositories)                              │
│  • SQLAlchemy 2.0 async                                  │
│  • Query construction, cursor pagination                 │
│  • No business logic                                     │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌──────────────────────────────────────────────────────────┐
│  PERSISTENCE (PostgreSQL 16)                             │
│  • Tables, indexes, constraints                          │
│  • Extensions: uuid-ossp, pgcrypto, pg_trgm               │
│  • pgvector: optional (AI embeddings)                     │
└──────────────────────────────────────────────────────────┘
```

**Cross-cutting:** logging, metrics, traces, audit log apply across all layers.

---

## 4. Module Inventory (Backend)

| Module | Files | Public surface |
|---|---|---|
| `app.api` | routes/, deps, errors | HTTP endpoints |
| `app.core` | config, security, logging, scheduler | Foundational |
| `app.db` | session, base, models/ | ORM definitions |
| `app.schemas` | per-resource Pydantic schemas | DTOs between API ↔ services |
| `app.services` | per-resource business logic | Domain operations |
| `app.repositories` | per-resource data access | SQL queries |
| `app.integrations` | azure_ad, claude_ai, ... | External API wrappers |

(Update this table as modules are added.)

---

## 5. Data Model (Core Entities)

### 5.1 Domain Tables

```
┌─────────────┐       ┌──────────────────┐       ┌──────────┐
│    User     │       │  BusinessUnit    │       │ Product  │
│             │       │                  │       │          │
│ - id        │       │ - id             │◄──────│ - bu_id  │
│ - email     │       │ - code           │       │ - sku    │
│ - auth_provider     │ - name           │       │ - name   │
│ - roles[]   │       │ - description    │       │ - price  │
│ - bu_ids[]  │──────►│                  │       │ - status │
└──────┬──────┘       └──────────────────┘       └──────────┘
       │                                              ▲
       │ created_by                                   │
       └──────────────────────────────────────────────┘
```

### 5.2 Admin-Configurable Tables (AGENTS.md §11)

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  FeatureFlag     │   │   Permission     │   │   AppSetting     │
│                  │   │                  │   │                  │
│ - key            │   │ - key            │   │ - key            │
│ - enabled        │   │ - category       │   │ - value (JSONB)  │
│ - enabled_for_   │   │ - description    │   │ - value_type     │
│   roles[]        │   └────────┬─────────┘   │ - category       │
│ - enabled_for_   │            │             │ - updated_by     │
│   user_ids[]     │   ┌────────┴──────────────────────────────┐ │
└──────────────────┘   │ RolePermission    UserPermissionOverride│ │
                       │ - role            - user_id             │ │
                       │ - permission_key  - permission_key      │ │
                       │                   - granted (bool)      │ │
                       └───────────────────────────────────────── ┘
```

### 5.3 Audit & Security Tables (security.md §10)

```
┌──────────────────┐
│   AuditLog       │   Security/admin mutations — permanent retention
│ - actor_id       │
│ - action         │
│ - resource_type  │
│ - resource_id    │
│ - ip_address     │
└──────────────────┘
```

### 5.4 Log Tables (AGENTS.md §12) — partitioned by month, 60-day retention

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  SystemLog       │   │ UserActivityLog  │   │   AiCallLog      │
│ (partitioned)    │   │ (partitioned)    │   │ (partitioned)    │
│                  │   │                  │   │                  │
│ - category       │   │ - user_id        │   │ - user_id        │
│ - event          │   │ - action_type    │   │ - model          │
│ - status         │   │ - action         │   │ - prompt (masked)│
│ - duration_ms    │   │ - is_sensitive   │   │ - response       │
│ - error_message  │   │ - resource_type  │   │ - input_tokens   │
│ - metadata       │   │ - ip_address     │   │ - output_tokens  │
└──────────────────┘   └──────────────────┘   │ - cost_usd       │
                                               │ - duration_ms    │
                                               └──────────────────┘
```

**Cardinality:**
- User N:1 BusinessUnit (via `business_unit_ids` array; project-specific BUs)
- BusinessUnit 1:N Product
- AuditLog → polymorphic via `resource_type` + `resource_id`
- Log tables → no FK (write-only append; avoid join overhead)

Authentication: User.`auth_provider` distinguishes Azure AD vs local.

---

## 6. Authentication Flow

### 6.1 Internal (Azure AD)

```
1. User clicks "Sign in with Microsoft"
2. Frontend (MSAL) redirects to Azure AD
3. User authenticates with Azure AD
4. Azure AD redirects back with ID token
5. Frontend POSTs ID token to /api/v1/auth/azure-ad/login
6. Backend verifies token via Azure AD JWKS
7. Backend looks up or auto-provisions user
8. Backend issues app access token (JWT)
9. Frontend stores app token; uses in Authorization header
```

### 6.2 External (Local)

**Onboarding:**
```
1. Admin POSTs to /api/v1/auth/admin/invite-external
2. Backend creates user (inactive, no password)
3. Backend emails invite link with short-lived token
4. User visits link
5. User submits password → /api/v1/auth/accept-invite
6. Backend sets password hash, activates account, issues access token
```

**Login:**
```
1. User POSTs email/password to /api/v1/auth/local/login
2. Backend verifies bcrypt hash
3. (If 2FA enabled) Backend requires TOTP code
4. Backend issues access + refresh tokens
```

### 6.3 Hard Rule

- `auth_provider` field is immutable per user
- User cannot exist in both providers (email unique)

---

## 7. AI Integration Flow

```
1. User triggers AI action in UI
2. Frontend POSTs request to backend
3. Backend service:
   a. Validates payload
   b. Checks permissions
   c. (PII scrubbing) sanitizes input
   d. Calls Anthropic SDK
   e. Logs request_id + tokens used (no content)
4. Backend returns result to frontend
5. Backend updates metrics (tokens, cost, duration)
```

**Patterns:**
- Sync calls for short prompts (< 5s expected)
- Background tasks for long prompts (poll for result)
- Streaming for chat-style UX (Server-Sent Events)

---

## 8. Background Jobs

Scheduler: **APScheduler** running in-process with backend.

Common patterns:
- **Cron-style:** daily/hourly jobs (cleanup, report generation)
- **Interval:** periodic tasks (cache refresh, health pings)
- **Fire-and-forget:** post-response work via `BackgroundTasks` (short, < 1s)

Job inventory (per project; update as added):

| Job | Trigger | Purpose |
|---|---|---|
| `cleanup_expired_sessions` | Hourly | Remove expired session records |
| (add as you go) | | |

**Hard rule:** background tasks must be **idempotent** — they may retry on failure.

---

## 9. External Integrations

| Integration | Direction | Module | Failure Mode |
|---|---|---|---|
| Azure AD JWKS | OUT (HTTPS) | `app.integrations.azure_ad` | Cached; if both cache + fresh fetch fail → reject Azure logins |
| Anthropic API | OUT (HTTPS) | `app.integrations.claude_ai` | Retry with backoff; degrade to fallback message |
| (Email service) | OUT (SMTP/API) | `app.integrations.email` | Queue & retry; alert on > 1h delay |

For each new integration, add:
- Timeout configuration (default: 10s)
- Retry policy
- Circuit breaker (consider `pybreaker` for high-volume calls)
- Failure metric

---

## 10. Caching Strategy

**Default approach:** Postgres-based caching before introducing Redis.

| Cache | Where | TTL | Invalidation |
|---|---|---|---|
| Azure AD JWKS | In-memory (process-local) | 1 hour | TTL only |
| React Query (server data) | Browser memory | 60s stale, refetch on focus | TTL + manual invalidate |
| Static assets | nginx + browser | 1 year (`immutable`) | Filename hash (Vite) |

Add **Redis** only when:
- Need cross-process cache sharing
- Need distributed locks
- Cache hit rate target > 90% with high QPS

See `docs/human/STACK_DEVIATIONS.md` if introducing Redis.

---

## 11. Deployment Topology

### 11.1 Single-Host (Small)

```
        ┌─ Internet ─┐
              │
              ▼
        ┌────────────┐
        │  Reverse   │
        │   Proxy    │
        │ (nginx/    │
        │  Traefik)  │
        └─────┬──────┘
              │
   ┌──────────┼──────────┐
   ▼          ▼          ▼
[frontend] [backend]  [db]
              │          ▲
              └──────────┘
```

Single VM, docker-compose-based. Use for staging & small production.

### 11.2 Multi-Host (Medium)

```
[LB]  ─►  [frontend × 2]  ─►  [backend × 3]  ─►  [Postgres primary + replica]
```

Separate hosts for app vs DB; backend horizontally scaled.

### 11.3 Cloud-Native (Large)

```
[CDN]  ─►  [App Service / ECS / Cloud Run]  ─►  [Managed Postgres]
                                                  + [Read replicas]
```

Use managed services (RDS/Azure DB/Cloud SQL); container orchestration (ECS/AKS/GKE/Cloud Run).

---

## 12. Cross-Cutting Concerns

### 12.1 Observability

- **Logs:** structlog JSON → stdout → aggregator
- **Metrics:** Prometheus `/metrics` endpoint
- **Traces:** OpenTelemetry → OTLP endpoint
- **Errors:** Sentry

See `docs/observability.md`.

### 12.2 Security

- TLS at proxy
- Secrets in vault (not env files in production)
- Audit log for all sensitive actions
- Rate limiting per endpoint

See `docs/security.md`.

### 12.3 i18n

- Thai + English mandatory
- Translation files: `frontend/src/i18n/locales/{th,en}.json`
- Backend error messages: English (machine-readable codes; frontend localizes)

---

## 13. Decision Records (ADRs)

Major architectural decisions are documented in `docs/human/STACK_DEVIATIONS.md` (deviations from defaults) and in this section (decisions consistent with defaults but worth recording).

Example entry format:

### ADR-001: Use cursor-based pagination over offset-based (default)

**Date:** initial setup
**Status:** Accepted
**Context:** Large datasets cause offset-based pagination to be slow at high offsets.
**Decision:** Default to cursor-based; allow offset-based only when "page X of Y" UX is required.
**Consequences:** Frontend must handle opaque cursor strings; back-button behavior requires URL state management.

(Add new ADRs as architectural decisions are made.)

---

## 14. Future Considerations

Things to watch as the project grows (not blockers now):

- **Read replica routing:** when read QPS approaches primary limits
- **Caching layer (Redis):** when computed responses become expensive
- **Queue system (Celery/Arq):** when background jobs need distributed execution
- **Multi-tenancy:** if requirements emerge to host multiple isolated tenants
- **Event sourcing / CQRS:** if audit/replay requirements grow beyond audit_logs table
- **Mobile clients:** if a native app is added, API may need versioning strategy beyond v1

---

## 15. AI Maintenance Notes

When AI updates this file:

1. Update relevant section (component inventory, data model, integration list)
2. Add to changelog below
3. Flag to user with diff for review

### Changelog

- `2026-05-20` — Initial version generated from web-app-standard template
