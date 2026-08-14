"""@audited — auto-log mutation endpoints via ActivityLogger.

Decorated endpoint MUST accept `request: Request`, `user: CurrentUser`,
and `db: DbDep` as keyword arguments (standard FastAPI dependency
injection). The decorator logs AFTER successful execution; the surrounding
get_db dependency owns the commit (auto-commits on successful return,
rolls back on exception).

See docs/patterns/tooling.md §3 + AGENTS.md §14.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.loggers.activity_logger import ActivityLogger


def audited(
    *,
    action: str,
    action_type: str = "update",
    resource_type: str | None = None,
    risk_level: str = "low",
    extract_resource_id: Callable[[Any], str | None] | None = None,
) -> Callable[..., Any]:
    """Wrap an endpoint so its successful invocation is auto-logged.

    Args:
        action: dotted event name, e.g. "product.created"
        action_type: one of "create" | "update" | "delete" |
            "read_sensitive" | "export" | "login" | "login_failed" |
            "logout" | "permission_denied" | "role_change"
        resource_type: e.g. "product", "user", "permission"
        risk_level: "low" | "medium" | "high"
        extract_resource_id: optional callable mapping the endpoint result
            to a resource_id string. Defaults to `result.id` if present.

    Example:
        @router.post("/products", status_code=201)
        @audited(action="product.created", action_type="create",
                 resource_type="product")
        async def create_product(payload, db, user, request):
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request: Request | None = kwargs.get("request")
            user: Any | None = kwargs.get("user")
            db: AsyncSession | None = kwargs.get("db")

            result = await func(*args, **kwargs)

            if user is not None and db is not None:
                if extract_resource_id is not None:
                    resource_id = extract_resource_id(result)
                else:
                    rid = getattr(result, "id", None)
                    resource_id = str(rid) if rid is not None else None

                logger = ActivityLogger(db)
                await logger.log(
                    user=user,
                    action_type=action_type,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    risk_level=risk_level,
                    request=request,
                )
                # Commit is owned by the get_db dependency (auto-commits on
                # successful return). A decorator-level commit would split
                # the request transaction: the audit row + earlier writes
                # would become durable here, then a later failure would only
                # roll back the empty autobegun transaction get_db sees.

            return result

        return wrapper

    return decorator
