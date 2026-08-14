# docs/patterns/ai.md

> AI Patterns — implementation reference for Claude API and provider abstraction
>
> Policy + maturity gates อยู่ใน [`../../AGENTS.md`](../../AGENTS.md) §16
> Required at L2+ (recommended L1+ ถ้า AI feature เป็น core)

---

## Overview

5 mandatory patterns เมื่อ project ใช้ AI:

| Pattern | Required | Purpose |
|---|---|---|
| Provider abstraction | L2+ | swap provider ได้ (Anthropic/OpenAI/Azure OpenAI/Gemini) |
| Prompt caching | conditional | ลด cost 50-90% สำหรับ long/repeated prompts |
| Streaming response | conditional | interactive UX สำหรับ chat endpoints |
| Cost budget + circuit breaker | L2+ | คุม cost per user/day |
| Dynamic pricing | L1+ | pricing ใน `app_settings` ไม่ hardcode |

Optional:
- **MCP server** — เมื่อ app เปิดให้ AI client เรียก
- **Vector RAG** — เมื่อใช้ semantic search

---

## 1. Provider Abstraction (L2+)

### 1.1 Interface

```python
# app/integrations/ai/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AICallResult:
    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""
    raw_response: dict | None = None


class AIProvider(ABC):
    """Provider interface — all implementations must wire logging + cost budget"""

    @abstractmethod
    async def messages(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        stream: bool = False,
        cache_system: bool = False,
    ) -> AICallResult:
        """Single-turn message completion"""

    @abstractmethod
    async def stream_messages(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ):
        """Async generator yielding response chunks"""
```

### 1.2 Anthropic Implementation (Default)

```python
# app/integrations/ai/anthropic_provider.py
from anthropic import AsyncAnthropic

from app.core.config import get_settings
from app.integrations.ai.base import AIProvider, AICallResult


class AnthropicProvider(AIProvider):
    def __init__(self) -> None:
        settings = get_settings()
        self.client = AsyncAnthropic(api_key=settings.CLAUDE_API_KEY)
        self.default_model = settings.CLAUDE_MODEL

    async def messages(
        self, *, prompt, system_prompt=None, max_tokens=4096,
        stream=False, cache_system=False,
    ) -> AICallResult:
        system_param = self._build_system(system_prompt, cache_system)
        response = await self.client.messages.create(
            model=self.default_model,
            max_tokens=max_tokens,
            system=system_param,
            messages=[{"role": "user", "content": prompt}],
        )
        return AICallResult(
            text=response.content[0].text if response.content else "",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            model=self.default_model,
        )

    async def stream_messages(
        self, *, prompt, system_prompt=None, max_tokens=4096, cache_system=False,
    ):
        system_param = self._build_system(system_prompt, cache_system)
        async with self.client.messages.stream(
            model=self.default_model,
            max_tokens=max_tokens,
            system=system_param,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    @staticmethod
    def _build_system(system_prompt: str | None, cache: bool):
        if not system_prompt:
            return []
        if cache:
            return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        return [{"type": "text", "text": system_prompt}]
```

### 1.3 Factory

```python
# app/integrations/ai/__init__.py
from app.core.config import get_settings
from app.integrations.ai.anthropic_provider import AnthropicProvider
from app.integrations.ai.base import AIProvider


def get_provider() -> AIProvider:
    """Returns configured provider — default Anthropic"""
    settings = get_settings()
    provider_name = getattr(settings, "AI_PROVIDER", "anthropic")
    if provider_name == "anthropic":
        return AnthropicProvider()
    # if provider_name == "openai":
    #     return OpenAIProvider()
    raise ValueError(f"Unknown AI provider: {provider_name}")
```

---

## 2. Prompt Caching (Conditional)

### 2.1 When to Cache

✅ **Cache when:**
- System prompt **> 1024 tokens** สำหรับ Sonnet (> 2048 สำหรับ Haiku)
- Prompt **ใช้ซ้ำกัน** ข้าม requests (เช่น instructions ของ chatbot)
- Tool definitions / examples ที่ส่งทุกครั้ง

❌ **Don't cache when:**
- Short prompts (< 1024 tokens)
- Dynamic prompts (เปลี่ยนทุก request)
- Single-use prompts

### 2.2 Cost Savings

Anthropic pricing (per 1M tokens, ดู `app_settings` `ai.pricing.<model>`):

| Operation | Cost (Sonnet 4.6) |
|---|---|
| Cache write | $3.75 (1.25× input price) |
| Cache read | $0.30 (0.1× input price) |

→ Breakeven: cache hits 2 ครั้ง = คุ้ม (write $3.75 + read $0.30 < เขียนใหม่ $3.00 × 2 = $6.00)

### 2.3 Usage

```python
# Cache long system prompt
result = await provider.messages(
    prompt="What products do we have?",
    system_prompt=LONG_BUSINESS_CONTEXT,  # 5000 tokens, used in every request
    cache_system=True,
)
# First call: cache_write_tokens=5000, input_tokens=20
# Subsequent calls within 5min: cache_read_tokens=5000, input_tokens=20
```

### 2.4 Monitor Cache Hit Rate

```sql
-- AI cache hit rate (current month)
SELECT
  COUNT(*) AS total_calls,
  SUM(cache_read_tokens) AS cache_read,
  SUM(cache_write_tokens) AS cache_write,
  ROUND(100.0 * SUM(cache_read_tokens) / NULLIF(SUM(input_tokens), 0), 2) AS cache_hit_pct
FROM ai_call_logs
WHERE created_at >= date_trunc('month', CURRENT_DATE);
```

Target: > 60% cache hit สำหรับ apps ที่มี chatbot

---

## 3. Streaming Response (Conditional)

### 3.1 When to Stream

✅ **Stream when:**
- Interactive chat UI (user รอดูคำตอบ real-time)
- Long-form generation (article, code, analysis)
- Anywhere TTFB (time to first byte) matter

❌ **Don't stream when:**
- Batch summarization (background job)
- Export operation (full output needed at once)
- Embedding generation
- Function calling ที่ต้อง parse JSON

### 3.2 FastAPI Streaming Endpoint

```python
# app/api/v1/ai_chat.py
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbDep
from app.integrations.ai import get_provider
from app.services.ai_cost_service import AICostService


router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    db: DbDep,
    user: CurrentUser,
):
    cost_service = AICostService(db)
    await cost_service.check_budget(user)  # raises 429 if exceeded

    provider = get_provider()

    async def event_stream():
        async for chunk in provider.stream_messages(
            prompt=payload.message,
            system_prompt=CHATBOT_SYSTEM_PROMPT,
            cache_system=True,
        ):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### 3.3 Frontend (React)

```typescript
// src/hooks/useStreamingChat.ts
import { useState } from 'react';

export function useStreamingChat() {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);

  async function send(message: string) {
    setLoading(true);
    setText('');

    const response = await fetch('/api/v1/ai/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value);
      const lines = chunk.split('\n').filter(l => l.startsWith('data: '));
      for (const line of lines) {
        const data = line.slice(6);
        if (data === '[DONE]') {
          setLoading(false);
          return;
        }
        setText(prev => prev + data);
      }
    }
  }

  return { text, loading, send };
}
```

---

## 4. Cost Budget + Circuit Breaker (L2+)

### 4.1 Concept

แต่ละ user มี **daily budget** (USD) — track ใน `ai_call_logs.cost_usd`. ถ้าเกิน → 429 Rate Limit + log activity

Budget อ่านจาก `app_settings`:
- Default: `ai.budget.default_daily_usd`
- Per-user override: `ai.budget.user.<user_id>` (optional, super-admin only)

### 4.2 Service

```python
# app/services/ai_cost_service.py
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ai_call_log import AiCallLog
from app.db.models.user import User
from app.services.app_setting_service import AppSettingService


class AICostService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_user_budget_usd(self, user: User) -> Decimal:
        settings = AppSettingService(self.db)
        per_user = await settings.get(f"ai.budget.user.{user.id}")
        if per_user is not None:
            return Decimal(str(per_user))
        default = await settings.get("ai.budget.default_daily_usd", 1.0)
        return Decimal(str(default))

    async def get_user_spend_today(self, user: User) -> Decimal:
        today_start = datetime.combine(date.today(), datetime.min.time())
        result = await self.db.execute(
            select(func.coalesce(func.sum(AiCallLog.cost_usd), 0)).where(
                AiCallLog.user_id == user.id,
                AiCallLog.created_at >= today_start,
                AiCallLog.status == "success",
            )
        )
        return Decimal(str(result.scalar() or 0))

    async def check_budget(self, user: User) -> None:
        """Raise 429 if user has exceeded daily budget"""
        budget = await self.get_user_budget_usd(user)
        spend = await self.get_user_spend_today(user)
        if spend >= budget:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily AI budget exceeded (${budget}). Resets at midnight UTC.",
            )
```

### 4.3 Wire to Endpoints

Every AI endpoint must call `check_budget()` before invoking provider:

```python
@router.post("/ai/summarize")
async def summarize(payload, db: DbDep, user: CurrentUser):
    cost_service = AICostService(db)
    await cost_service.check_budget(user)  # MUST — first thing

    provider = get_provider()
    result = await provider.messages(prompt=payload.text)
    return {"summary": result.text}
```

### 4.4 Admin Dashboard Query

```sql
-- Users approaching budget limit (today)
WITH today_spend AS (
  SELECT user_id, SUM(cost_usd) AS spend
  FROM ai_call_logs
  WHERE created_at >= date_trunc('day', CURRENT_DATE)
    AND status = 'success'
  GROUP BY user_id
)
SELECT u.email, ts.spend, ts.spend / 1.0 * 100 AS pct_of_default_budget
FROM today_spend ts
JOIN users u ON u.id = ts.user_id
WHERE ts.spend > 0.5  -- >50% of $1 default
ORDER BY ts.spend DESC;
```

---

## 5. Dynamic Pricing (L1+)

Pricing **ห้าม hardcode** ใน code — เก็บใน `app_settings`:

```python
# Seed (super-admin only edit — requires_role="super_admin")
await app_settings.set(
    key="ai.pricing.claude-sonnet-4-6",
    value={"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    value_type="json",
    category="pricing",
    description="Claude Sonnet 4.6 pricing per 1M tokens",
    requires_role="super_admin",
    updated_by=admin_user,
)
```

ดู [`../admin-config.md`](../admin-config.md) §C.6 สำหรับ seed defaults ครบทุก model

`AiCallLogger._estimate_cost()` (logging.md §3.2) อ่าน pricing แบบ dynamic — ตัวอย่าง implementation ใน logging.md

---

## 6. MCP Server (Optional)

เมื่อ app เปิดให้ AI client (Claude Desktop, Claude Code, custom agent) เรียกใช้ resources/tools ของระบบ → expose MCP endpoint

### 6.1 Use Cases

- App ที่มี business data ที่ AI agent ควร query ได้ (เช่น product catalog, customer info)
- Workflow ที่ AI agent ต้อง trigger (เช่น create ticket, send notification)

### 6.2 Pattern

```python
# app/api/v1/mcp.py
from mcp.server import Server
from mcp.types import Tool, TextContent


server = Server("ct-app-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_products",
            description="Search products by category and status",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "status": {"type": "string", "enum": ["draft", "active", "discontinued"]},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "query_products":
        # implement: query DB, return results
        # MUST authenticate the caller via api_key (similar to registry)
        # MUST log to ActivityLogger (action_type="read_sensitive" if PII)
        ...
```

### 6.3 Hard Rules

1. **MCP endpoint ต้อง authenticate** — ใช้ `X-API-Key` หรือ OAuth (เหมือน registry)
2. **ทุก tool call ต้อง log ผ่าน `ActivityLogger`** — มี `caller_type="ai_agent"` ใน metadata
3. **MCP tools ที่ return PII ต้อง mask** หรือต้องการ explicit permission
4. **Rate limit per API key** — ป้องกัน AI agent runaway

---

## 7. Vector RAG (Optional)

เมื่อใช้ semantic search / RAG → enable `pgvector` ใน `§2 Stack Defaults` (AGENTS.md)

### 7.1 Schema Pattern

```python
# app/db/models/document_embedding.py
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.db.base import Base, TimestampMixin, UUIDMixin


class DocumentEmbedding(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "document_embeddings"

    source_type: Mapped[str] = mapped_column(String(50))  # "product" | "article" | ...
    source_id: Mapped[str] = mapped_column(String(100))
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))  # OpenAI ada-002 dimension
    # หรือ Vector(3072) สำหรับ text-embedding-3-large
```

### 7.2 Query Pattern

```python
async def semantic_search(db: AsyncSession, query: str, k: int = 10):
    # Embed query
    provider = get_provider()
    query_embedding = await provider.embed(query)  # implementation depends on provider

    # Cosine similarity search
    result = await db.execute(
        select(DocumentEmbedding).order_by(
            DocumentEmbedding.embedding.cosine_distance(query_embedding)
        ).limit(k)
    )
    return list(result.scalars().all())
```

### 7.3 Index

```sql
CREATE INDEX ix_document_embeddings_vector
  ON document_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
```

⚠️ Tune `lists` based on row count — `sqrt(num_rows)` เป็น starting point

---

## 8. Hard Rules

1. **ห้ามเรียก provider SDK ตรงๆ** จาก service/endpoint — ต้องผ่าน `AIProvider` interface
2. **ทุก AI endpoint ต้อง check budget** ก่อนเรียก provider (L2+)
3. **ทุก call ต้อง log ผ่าน `AiCallLogger`** — `AnthropicProvider` wrap logging อัตโนมัติ
4. **Pricing ห้าม hardcode** — อ่านจาก `app_settings` (Pattern C)
5. **PII ใน prompt/response ต้อง mask** ก่อน insert `ai_call_logs.prompt` (logging.md §4)
6. **Interactive endpoint ใช้ streaming** — batch ใช้ normal response
7. **Long system prompts ใช้ cache_control** — short/dynamic ไม่ต้อง

---

## 9. Quick Reference

| Task | What to use |
|---|---|
| New AI feature | `get_provider().messages(...)` หรือ `.stream_messages(...)` |
| Long repeated system prompt | `cache_system=True` |
| Interactive chat | `stream_messages()` + `StreamingResponse` |
| Background AI job | `messages()` + normal response |
| Cost-sensitive flow | `AICostService.check_budget()` ก่อนเสมอ |
| New model added | seed pricing ใน `app_settings` (super-admin) |
| AI agent calling our API | MCP server (§6) |
| Semantic search | pgvector + `DocumentEmbedding` schema |

---

## 10. References

- [`../../AGENTS.md`](../../AGENTS.md) §16 — policy
- [`../logging.md`](../logging.md) §3 — `ai_call_logs` schema + logger
- [`../admin-config.md`](../admin-config.md) §C — `app_settings` for pricing/budget
- Anthropic docs: https://docs.anthropic.com/en/api/prompt-caching
- MCP spec: https://modelcontextprotocol.io/
