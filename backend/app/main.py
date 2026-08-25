from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.installed_routers import ROUTERS
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.rate_limit import bootstrap_rate_limiting
from app.core.scheduler import start_scheduler, stop_scheduler
from app.db.session import close_db, init_db

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
        return {
            "status": "ok",
            "project": settings.APP_NAME,
            "env": settings.APP_ENV,
            "version": "0.1.0",
        }

    return app


app = create_app()
