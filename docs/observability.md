# docs/observability.md

> Observability reference — structured logging, metrics, traces, error tracking
> Default stack: structlog + Prometheus + OpenTelemetry + Sentry

---

## 1. Three Pillars

1. **Logs** — discrete events (what happened)
2. **Metrics** — aggregated numbers over time (how often, how fast, how many)
3. **Traces** — request flow through services (where time was spent)

Plus: **error tracking** (Sentry) for grouped exception alerts.

---

## 2. Structured Logging (structlog)

### 2.1 Configuration

(See `docs/backend.md` Section 12 for setup snippet)

Production output (JSON line-delimited):

```json
{
  "event": "product_created",
  "timestamp": "2026-05-20T10:30:00.000Z",
  "level": "info",
  "logger": "app.services.product_service",
  "request_id": "abc-123",
  "user_id": "uuid-xxx",
  "product_id": "uuid-yyy",
  "duration_ms": 45
}
```

### 2.2 Required Fields per Log

| Field | Always | When applicable |
|---|---|---|
| `event` | ✅ snake_case event name | |
| `timestamp` | ✅ ISO 8601 UTC | |
| `level` | ✅ debug/info/warning/error | |
| `logger` | ✅ module path | |
| `request_id` | | ✅ if within HTTP request |
| `user_id` | | ✅ if authenticated context |
| `duration_ms` | | ✅ for operations measuring latency |

### 2.3 Log Levels

| Level | Use For |
|---|---|
| `DEBUG` | Development diagnostics; off in production |
| `INFO` | Normal business events (login, create, etc.) |
| `WARNING` | Recoverable issues (retry succeeded, deprecated path used) |
| `ERROR` | Unrecoverable issues for this request (DB connection failed, integration timeout) |
| `CRITICAL` | System-wide issues (rarely used; usually replaced by ERROR + alert) |

### 2.4 Request ID Middleware

```python
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
import structlog


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = request_id
        return response


app.add_middleware(RequestIdMiddleware)
```

### 2.5 Audit Logs vs Application Logs

- **App logs** (stdout): operational events, debugging
- **Audit logs** (DB table): compliance, security incidents (see `docs/security.md` Section 10)

Both ใช้ structlog แต่ audit logs ต้อง persist ใน DB

---

## 3. Metrics (Prometheus)

### 3.1 Setup

```bash
pip install prometheus-fastapi-instrumentator
```

```python
from prometheus_fastapi_instrumentator import Instrumentator

def create_app() -> FastAPI:
    app = FastAPI(...)
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    return app
```

This exposes:
- `http_requests_total` — counter per method/path/status
- `http_request_duration_seconds` — histogram
- `http_requests_in_progress` — gauge

### 3.2 Custom Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

# Example: replace "resource_created_total" with your domain resource name
# and adjust labels to match your model's attributes
resource_created_total = Counter(
    "resource_created_total",
    "Total resources created",
    ["category"],  # example label — change to match your domain
)

ai_completion_duration_seconds = Histogram(
    "ai_completion_duration_seconds",
    "Time spent in AI API calls",
    ["model"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)

active_sessions = Gauge(
    "active_sessions",
    "Number of active sessions",
)


async def create_resource(...):
    resource = await repo.create(...)
    resource_created_total.labels(category=resource.category).inc()
    return resource


async def call_claude(prompt: str):
    with ai_completion_duration_seconds.labels(model=settings.CLAUDE_MODEL).time():
        result = await anthropic_client.messages.create(...)
    return result
```

### 3.3 Standard Metrics to Track

| Metric | Type | Labels |
|---|---|---|
| `http_requests_total` | Counter | method, path, status |
| `http_request_duration_seconds` | Histogram | method, path |
| `db_query_duration_seconds` | Histogram | operation |
| `db_pool_size` | Gauge | — |
| `ai_completion_total` | Counter | model, success |
| `ai_completion_duration_seconds` | Histogram | model |
| `ai_tokens_used_total` | Counter | model, type (input/output) |
| `auth_login_total` | Counter | provider, success |
| `background_task_duration_seconds` | Histogram | task_name |

### 3.4 Scraping

Prometheus server scrapes `/metrics` endpoint of each backend instance. Configure in Prometheus config:

```yaml
scrape_configs:
  - job_name: 'app-backend'
    scrape_interval: 15s
    static_configs:
      - targets: ['backend:8000']
```

---

## 4. Distributed Tracing (OpenTelemetry)

### 4.1 Setup

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy opentelemetry-instrumentation-httpx opentelemetry-exporter-otlp
```

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource


def setup_tracing(service_name: str, otlp_endpoint: str | None) -> None:
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    if otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
        )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)
    HTTPXClientInstrumentor().instrument()
```

Service name มาจาก `settings.APP_NAME` (มาจาก `project.config`)

### 4.2 Custom Spans

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)


async def complex_business_operation(payload):
    with tracer.start_as_current_span("validate_payload") as span:
        span.set_attribute("payload.size", len(payload))
        validate(payload)

    with tracer.start_as_current_span("call_ai") as span:
        result = await call_claude(payload)
        span.set_attribute("ai.model", settings.CLAUDE_MODEL)
        span.set_attribute("ai.tokens", result.usage.output_tokens)

    return result
```

### 4.3 Backend

Send traces to:
- **Jaeger** (self-hosted)
- **Tempo** (Grafana stack)
- **Honeycomb** / **Datadog** / **New Relic** (SaaS)

---

## 5. Error Tracking (Sentry)

### 5.1 Setup

```bash
pip install "sentry-sdk[fastapi]"
```

```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration


def setup_sentry(settings: Settings) -> None:
    if not settings.SENTRY_DSN:
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        release=settings.APP_VERSION,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,        # 10% for performance monitoring
        send_default_pii=False,        # PII protection!
        before_send=_scrub_pii,
    )


def _scrub_pii(event, hint):
    """Remove PII from event before sending to Sentry."""
    if "request" in event and "headers" in event["request"]:
        event["request"]["headers"].pop("authorization", None)
        event["request"]["headers"].pop("cookie", None)
    return event
```

### 5.2 Frontend (React)

```bash
npm install @sentry/react
```

```tsx
import * as Sentry from '@sentry/react';

Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN,
  environment: import.meta.env.MODE,
  tracesSampleRate: 0.1,
  beforeSend(event) {
    delete event.user?.email;
    return event;
  },
});
```

### 5.3 What Goes to Sentry

✅ **Yes:**
- Unhandled exceptions
- Manual `sentry_sdk.capture_exception(exc)` for expected-but-notable errors
- Performance traces (sampled)

❌ **No:**
- Expected 4xx errors (validation, auth) — too noisy
- PII (email, password, tokens, full names) — **scrub before sending**

### 5.4 Frontend Error Boundary Integration

```tsx
import * as Sentry from '@sentry/react';

const FallbackComponent = ({ error }: { error: Error }) => (
  <div>Error: {error.message}</div>
);

export const App = Sentry.withErrorBoundary(AppRoot, {
  fallback: FallbackComponent,
  showDialog: false,
});
```

---

## 6. Health Checks (Recap)

```python
@app.get("/health")
async def liveness(): return {"status": "ok"}


@app.get("/health/ready")
async def readiness(db: DbDep):
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503)
    return {"status": "ready"}
```

### 6.1 Detailed Health Check (Optional)

```python
@app.get("/health/detailed")
async def detailed_health(db: DbDep):
    checks = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "message": str(e)[:200]}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("https://api.anthropic.com/v1/health")
            checks["claude_api"] = {"status": "ok" if response.is_success else "degraded"}
    except Exception as e:
        checks["claude_api"] = {"status": "degraded", "message": str(e)[:200]}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}
```

---

## 7. Performance Budgets

### 7.1 API Latency

| Percentile | Target |
|---|---|
| p50 | < 100ms |
| p95 | < 500ms |
| p99 | < 1000ms |
| Max | < 5000ms (timeout) |

ยกเว้น endpoint ที่ call AI หรือ external API:
- p95 < 5s
- Max < 30s

### 7.2 Frontend Web Vitals

| Metric | Good | Needs Improvement |
|---|---|---|
| LCP (Largest Contentful Paint) | < 2.5s | < 4s |
| FID (First Input Delay) | < 100ms | < 300ms |
| CLS (Cumulative Layout Shift) | < 0.1 | < 0.25 |
| INP (Interaction to Next Paint) | < 200ms | < 500ms |

Track ผ่าน `web-vitals` library:

```typescript
import { onCLS, onINP, onLCP, onFCP, onTTFB } from 'web-vitals';

const sendToAnalytics = (metric: any) => {
  apiClient.post('/metrics/web-vitals', metric);
};

onCLS(sendToAnalytics);
onINP(sendToAnalytics);
onLCP(sendToAnalytics);
onFCP(sendToAnalytics);
onTTFB(sendToAnalytics);
```

---

## 8. Alerting Rules (Suggested)

### 8.1 Production-Critical Alerts

| Alert | Condition | Severity |
|---|---|---|
| High error rate | 5xx rate > 1% over 5 min | P1 |
| API down | `/health` failing for 2+ min | P1 |
| DB connection failure | DB readiness fails 3+ times in 5 min | P1 |
| AI API failures | Claude API error rate > 10% over 10 min | P2 |
| High latency | p95 > 2s for 5+ min | P2 |
| Disk space | < 20% free | P2 |
| Memory usage | > 90% for 10+ min | P2 |
| Failed login spike | Failed logins > 50/min from single IP | P3 (security) |

### 8.2 Routing

- **P1:** PagerDuty/phone → on-call engineer
- **P2:** Slack/Teams alert + email
- **P3:** Email + dashboard

---

## 9. Dashboard Recommendations

### 9.1 Backend Dashboard (Grafana)

Panels:
1. Request rate (RPS) — by endpoint
2. Error rate — % over total
3. Latency — p50/p95/p99
4. DB pool usage
5. AI calls — count + tokens used + cost estimate
6. Background task duration
7. Active sessions

### 9.2 Frontend Dashboard

Panels:
1. Web Vitals over time
2. JS error rate (from Sentry)
3. Slow pages (LCP > 4s)
4. API call latency (from frontend perspective)

---

## 10. Log Aggregation

ในระบบ production แนะนำ:

| Tool | Pros |
|---|---|
| **Loki + Grafana** | Open source, integrates with metrics |
| **ELK Stack** (Elasticsearch+Logstash+Kibana) | Powerful search, complex queries |
| **CloudWatch Logs** | If on AWS |
| **Azure Monitor** | If on Azure |
| **Datadog / New Relic** | All-in-one SaaS |

Retention:
- Application logs: 30 วัน
- Audit logs (DB): 2 ปี+ (compliance)
- Metrics: 90 วัน raw, 1 ปี aggregated

---

## 11. Cost Tracking (AI specifically)

### 11.1 Two-Layer Strategy

AI tracking ใช้ **2 ระบบพร้อมกัน** — ไม่ใช่เลือกอย่างใดอย่างหนึ่ง:

| Layer | ใช้เพื่อ | เก็บที่ |
|---|---|---|
| **Prometheus Counter** | Real-time dashboard, alert on spike | `/metrics` endpoint |
| **`ai_call_logs` table** | Per-request detail, billing reconciliation, debug, audit | PostgreSQL |

ทั้งสองต้องทำงานพร้อมกันใน `call_claude_messages()` wrapper เดียวกัน (ดู `docs/logging.md` Section 3.3)

### 11.2 Prometheus Counter (aggregate/realtime)

```python
from prometheus_client import Counter, Histogram

ai_tokens_used_total = Counter(
    "ai_tokens_used_total",
    "AI tokens consumed",
    ["model", "type"],  # type = "input" | "output"
)

ai_completion_duration_seconds = Histogram(
    "ai_completion_duration_seconds",
    "Time spent in AI API calls",
    ["model"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)
```

ใช้สำหรับ: Grafana dashboard (token rate per minute), alert ถ้า token spike ผิดปกติ

### 11.3 DB Log (`ai_call_logs`) — per-request detail

เก็บทุก call: full prompt, response, cost per call, user_id, endpoint — ดู `docs/logging.md` Section 3

ใช้สำหรับ: billing reconciliation, ตามรอย "ใครเรียก AI อะไร เมื่อไหร่", debug ตอน AI ตอบผิด

### 11.4 ทั้งสองต้องอยู่ใน wrapper เดียวกัน

```python
# app/integrations/claude_ai.py (ย่อ)
async def call_claude_messages(*, prompt, user, db, model, ...):
    started_at = time.monotonic()
    try:
        response = await client.messages.create(...)
        duration_ms = int((time.monotonic() - started_at) * 1000)

        # Layer 1: Prometheus (aggregate)
        ai_tokens_used_total.labels(model=model, type="input").inc(response.usage.input_tokens)
        ai_tokens_used_total.labels(model=model, type="output").inc(response.usage.output_tokens)
        ai_completion_duration_seconds.labels(model=model).observe(duration_ms / 1000)

        # Layer 2: DB (per-request detail) — see docs/logging.md §3
        await AiCallLogger(db).log(
            user=user, model=model, prompt=prompt,
            response=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            duration_ms=duration_ms, status="success",
        )
        await db.commit()
        return response
    except Exception as exc:
        # log both layers on error too (see docs/logging.md §3.3)
        raise
```

**ห้ามเรียก `client.messages.create()` โดยตรง** จาก service/endpoint — ต้องผ่าน wrapper นี้เท่านั้น

Daily/weekly review: query `ai_call_logs` aggregate by model + user + endpoint → identify cost outliers

---

## 12. Hard Rules

1. **ห้าม log PII** — mask before logging (see `docs/security.md` Section 9)
2. **ห้าม log secrets** — passwords, tokens, API keys
3. **ห้าม use `print()`** ใน production code — use structlog
4. **ห้าม send PII to Sentry** — scrub via `before_send`
5. **ห้าม disable error tracking** ใน production
6. **ทุก backend endpoint ต้องมี `/metrics`** — for Prometheus scraping
7. **ทุก HTTP request ต้องมี request_id** — for trace correlation

---

## 13. Quick Reference: เมื่อ AI ได้รับ task

| Task | Add |
|---|---|
| New endpoint | Auto-instrumented by `Instrumentator` — no extra code |
| Slow operation suspected | Add `tracer.start_as_current_span()` |
| Important business event | Add `logger.info("event_name", **context)` |
| Recoverable error | Add `logger.warning(...)` |
| Cost-sensitive operation | Add custom `Counter` |
| User-facing error | Sentry captures automatically; ensure no PII leak |
| New AI integration | Track tokens + duration via existing counters |
