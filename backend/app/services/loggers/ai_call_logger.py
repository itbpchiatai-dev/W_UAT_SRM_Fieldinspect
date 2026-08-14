"""AiCallLogger — persists provider calls to ai_call_logs.

Called by app.integrations.<provider>.py wrappers (never directly
from services/endpoints — that bypasses the no-direct-AI-SDK check).
Prompts / responses are PII-masked before insert. Caller commits.

Cost estimation reads pricing from `app_settings` under
`ai.pricing.<model>` via AppSettingService — admin can update pricing
without code change. Missing key → `cost_usd` is left NULL (does not
raise). See AGENTS.md §16 + docs/logging.md §3.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import mask_pii_in_text
from app.db.models.ai_call_log import AiCallLog
from app.services.app_setting_service import AppSettingService


class AiCallLogger:
    """Persist AI provider calls with PII-masked prompt/response."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        *,
        user: Any | None,
        model: str,
        operation: str,
        prompt: str,
        provider: str = "anthropic",
        system_prompt: str | None = None,
        response: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        duration_ms: int | None = None,
        status: str = "success",
        error: Exception | None = None,
        endpoint: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        cost = await self._estimate_cost(
            model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        )
        entry = AiCallLog(
            id=uuid4(),
            user_id=getattr(user, "id", None) if user else None,
            endpoint=endpoint,
            request_id=request_id,
            provider=provider,
            model=model,
            operation=operation,
            prompt=mask_pii_in_text(prompt),
            system_prompt=mask_pii_in_text(system_prompt) if system_prompt else None,
            response=mask_pii_in_text(response) if response else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
            status=status,
            error_type=type(error).__name__ if error else None,
            error_message=mask_pii_in_text(str(error))[:2000] if error else None,
            extra_metadata=metadata or {},
        )
        self.db.add(entry)
        # Caller commits.

    async def _estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> Decimal | None:
        """Estimate USD cost from pricing in app_settings.

        Setting key: `ai.pricing.<model>`
        Setting value: {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}
        Units: USD per 1M tokens.

        Missing key → return None (cost_usd stays NULL). Admin seeds new
        models via the /settings/admin UI — never hardcode pricing here.
        """
        pricing = await AppSettingService(self.db).get(f"ai.pricing.{model}")
        if not pricing:
            return None
        cost = (
            Decimal(str(pricing.get("input", 0))) * Decimal(input_tokens)
            + Decimal(str(pricing.get("output", 0))) * Decimal(output_tokens)
            + Decimal(str(pricing.get("cache_read", 0))) * Decimal(cache_read_tokens)
            + Decimal(str(pricing.get("cache_write", 0))) * Decimal(cache_write_tokens)
        ) / Decimal(1_000_000)
        return cost.quantize(Decimal("0.000001"))
