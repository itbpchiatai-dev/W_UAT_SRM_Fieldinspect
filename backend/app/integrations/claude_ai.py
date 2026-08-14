"""Claude (Anthropic) integration — the ONLY place AsyncAnthropic is imported.

Every Claude call must go through call_claude_messages() so it lands in
ai_call_logs with PII masking, token counts, and (once Pattern C lands)
cost. Direct `from anthropic import ...` outside this package is blocked
by scripts/checks/no_direct_ai_sdk.py.

See AGENTS.md §16 + docs/patterns/ai.md.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.loggers.ai_call_logger import AiCallLogger

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover — anthropic optional until AI feature lands
    AsyncAnthropic = None  # type: ignore[assignment,misc]

_client: Any | None = None


def _get_client() -> Any:
    global _client
    if _client is None:
        if AsyncAnthropic is None:
            raise RuntimeError(
                "anthropic SDK not installed. Add 'anthropic' to pyproject.toml "
                "and reinstall before calling Claude."
            )
        _client = AsyncAnthropic(api_key=get_settings().CLAUDE_API_KEY)
    return _client


async def call_claude_messages(
    *,
    prompt: str,
    db: AsyncSession,
    user: Any | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    endpoint: str | None = None,
    request_id: str | None = None,
    use_case: str | None = None,
) -> str:
    """Send a single-message Claude request and log to ai_call_logs.

    Returns the assistant text. Re-raises on error after logging.

    Transaction ownership: this function does NOT commit. When called from
    a FastAPI endpoint with `db: DbDep`, the surrounding get_db dependency
    auto-commits on success / rolls back on exception. Non-request callers
    (APScheduler jobs, CLI scripts using `async with get_db_session()`)
    MUST commit themselves after invoking — otherwise the ai_call_logs row
    is lost.
    """
    settings = get_settings()
    resolved_model = model or settings.CLAUDE_MODEL
    started_at = time.monotonic()
    logger = AiCallLogger(db)

    try:
        response = await _get_client().messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            system=system_prompt or "",
            messages=[{"role": "user", "content": prompt}],
        )
        duration_ms = int((time.monotonic() - started_at) * 1000)
        response_text = response.content[0].text if response.content else ""

        await logger.log(
            user=user,
            model=resolved_model,
            operation="messages",
            prompt=prompt,
            system_prompt=system_prompt,
            response=response_text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            duration_ms=duration_ms,
            status="success",
            endpoint=endpoint,
            request_id=request_id,
            metadata={"use_case": use_case} if use_case else {},
        )
        # Commit is owned by the caller (get_db dependency for requests,
        # explicit commit for non-request contexts — see docstring).
        return response_text

    except Exception as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        msg = str(exc).lower()
        if "rate" in msg:
            status = "rate_limited"
        elif "timeout" in msg:
            status = "timeout"
        else:
            status = "error"

        await logger.log(
            user=user,
            model=resolved_model,
            operation="messages",
            prompt=prompt,
            system_prompt=system_prompt,
            duration_ms=duration_ms,
            status=status,
            error=exc,
            endpoint=endpoint,
            request_id=request_id,
            metadata={"use_case": use_case} if use_case else {},
        )
        # Don't commit on the error path either. Re-raising propagates the
        # failure to get_db, which will roll back the whole request
        # transaction (including the error-log row above — acceptable; the
        # caller can still see the failure via the raised exception, and
        # logging-vs-business consistency wins over keeping a partial log).
        # Non-request callers should commit before letting the exception
        # escape if they want the error row durable.
        raise
