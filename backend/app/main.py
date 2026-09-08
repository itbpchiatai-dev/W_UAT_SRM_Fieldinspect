from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.v1.installed_routers import ROUTERS
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.rate_limit import bootstrap_rate_limiting
from app.core.scheduler import start_scheduler, stop_scheduler
from app.db.session import TransactionCommitError, close_db, init_db

# Round 8-17B Part A — a body that fails FastAPI's own request-validation
# (e.g. an unknown field, an out-of-range limit/offset) never reaches the
# endpoint function, so /plots/search-by-phone's own
# `response.headers["Cache-Control"] = "no-store"` line never runs; the
# default RequestValidationError response has no Cache-Control at all. This
# is scoped to that one path ONLY — every other route's validation-error
# response is untouched.
_NO_STORE_VALIDATION_PATHS = {"/api/v1/plots/search-by-phone"}


async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    response = await request_validation_exception_handler(request, exc)
    if request.url.path in _NO_STORE_VALIDATION_PATHS:
        response.headers["Cache-Control"] = "no-store"
    return response


# Round J — what the user is told when a commit fails.
#
# Round I made that failure reachable: the commit now runs before the response
# is sent, so instead of a 200 that saved nothing, the request fails. What it
# failed with was a bare "Internal Server Error", which tells the user neither
# that their work is gone nor that retrying is worth it.
#
# The organisation's answer to "may a transaction proceed when its audit trail
# cannot be written?" is no. So rolling the whole thing back IS the correct
# behaviour here — this message explains it rather than apologising for it.
#
# Nothing about the database reaches the client. The cause travels with the
# exception (`raise ... from exc`) into the server-side traceback, where it
# belongs; a DB error message in an API response is an information leak.
_COMMIT_FAILED_DETAIL = (
    "ไม่สามารถบันทึกข้อมูลได้ ระบบจึงยกเลิกรายการนี้ทั้งหมด "
    "เพื่อไม่ให้ข้อมูลถูกบันทึกไม่ครบถ้วน — กรุณาลองใหม่อีกครั้ง "
    "หากยังไม่สำเร็จ กรุณาแจ้งผู้ดูแลระบบ"
)


async def _commit_failed_handler(request: Request, exc: Exception) -> JSONResponse:
    logger = structlog.get_logger(__name__)
    logger.error(
        "db.commit_failed",
        path=request.url.path,
        method=request.method,
        cause=str(exc.__cause__) if exc.__cause__ else None,
    )
    return JSONResponse(status_code=500, content={"detail": _COMMIT_FAILED_DETAIL})


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.APP_LOG_LEVEL)
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()
    await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.APP_DEBUG,
        lifespan=lifespan,
        # Round 8-16D.1 — via the shared is_production contract, so a typo
        # like APP_ENV=prod can no longer quietly re-expose /docs.
        docs_url=None if settings.is_production else "/docs",
    )
    # Rate limit (slowapi) — MUST be wired before routers are
    # included so @limiter.limit decorators on /login etc. take
    # effect. Closes Deep-Audit HIGH-1.
    bootstrap_rate_limiting(app)
    # Mount project routers — edit app/api/v1/installed_routers.py to add yours.
    for router, prefix in ROUTERS:
        app.include_router(router, prefix=prefix)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(TransactionCommitError, _commit_failed_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        # Round 8-27E — a browser hides every non-simple RESPONSE header from
        # JS unless it is listed here. The Plots page reads
        # X-Excluded-Plot-Count off the template download to warn how many
        # matching plots aren't in the file. Same-origin deploys never needed
        # this (no CORS at all), but a split-origin dev setup would silently
        # read 0 — a wrong "nothing was excluded", not an error.
        expose_headers=["Content-Disposition", "X-Excluded-Plot-Count"],
        max_age=600,
    )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        # Deliberately touches nothing: this is the container's healthcheck,
        # so anything it depends on can restart-loop the app. Round H's
        # partition check is a SEPARATE route for exactly that reason.
        return {
            "status": "ok",
            "project": settings.APP_NAME,
            "env": settings.APP_ENV,
            "version": "0.1.0",
        }

    # NOT under /health — the reverse proxy answers that prefix itself:
    #
    #     location /health { return 200 "ok\n"; ... }
    #
    # nginx prefix-matches, so /health/partitions never reached the backend
    # at all and returned a plain "ok" (found the day round H shipped). The
    # /api/ prefix is proxied through, so this lives there instead. Changing
    # the nginx rule to an exact match was the alternative and was rejected:
    # /health is the container's healthcheck, and this is not worth the risk
    # of breaking it.
    @app.get("/api/v1/health/partitions", tags=["health"])
    async def health_partitions() -> dict[str, object]:
        """How many months of log partitions remain.

        Round H — when they run out, every audited write in the system starts
        failing and rolling back its caller's transaction. That happened on
        2026-09-01 and went unnoticed for a week, so the state now has a
        place to be read from.

        NOT wired into /health above, and never used as the container's
        healthcheck: this one queries the database, and letting a DB hiccup
        (or a genuinely low count) restart the app would turn a warning into
        the outage it is meant to prevent. Returns 200 either way — `status`
        carries the answer, callers decide what to do about it.
        """
        from app.db.session import get_db_session
        from app.services.loggers.partition_manager import (
            PARTITION_COVERAGE_WARN_MONTHS,
            months_of_coverage,
        )

        try:
            async with get_db_session() as db:
                months = await months_of_coverage(db)
        except Exception as exc:  # noqa: BLE001 - a diagnostic never raises
            return {"status": "unknown", "error": str(exc)}
        return {
            "status": "ok" if months >= PARTITION_COVERAGE_WARN_MONTHS else "warning",
            "monthsRemaining": months,
        }

    return app


app = create_app()
