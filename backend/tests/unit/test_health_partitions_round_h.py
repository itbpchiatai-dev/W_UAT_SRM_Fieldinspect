"""GET /api/v1/health/partitions — the early warning added in round H.

When the monthly log partitions run out, every audited write in the system
starts failing and rolling back the business transaction it belongs to. That
happened on 2026-09-01 and nobody noticed for a week, because the state had
nowhere to be read from and the job that should have reported it could not
write its own failure (system_logs is partitioned too).

The two properties that matter here, and why:

  * It NEVER returns a non-2xx. It is not the container's healthcheck — that
    is /health, which deliberately touches nothing — but nothing stops an
    operator from wiring this into one. If a low count or a database hiccup
    could restart the app, this route would cause the outage it exists to
    prevent.
  * /health itself stays database-free, so a DB problem can never restart a
    container that is otherwise serving fine.

Real ASGI dispatch (httpx + ASGITransport), DB access patched out — no
database is touched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport

from app.main import app
from app.services.loggers.partition_manager import PARTITION_COVERAGE_WARN_MONTHS

_COVERAGE = "app.services.loggers.partition_manager.months_of_coverage"


class _NullSession:
    """get_db_session() is an async context manager; the route only passes
    whatever it yields to months_of_coverage, which is patched."""

    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


async def _get(path: str) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_reports_ok_with_plenty_of_runway():
    with patch("app.db.session.get_db_session", return_value=_NullSession()), \
         patch(_COVERAGE, new=AsyncMock(return_value=24)):
        response = await _get("/api/v1/health/partitions")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "monthsRemaining": 24}


async def test_warns_before_the_cliff_not_after():
    """The warning has to arrive with months to spare — by the time coverage
    is 0, the next month rollover is already an outage."""
    with patch("app.db.session.get_db_session", return_value=_NullSession()), \
         patch(_COVERAGE, new=AsyncMock(return_value=PARTITION_COVERAGE_WARN_MONTHS - 1)):
        response = await _get("/api/v1/health/partitions")

    assert response.status_code == 200
    assert response.json()["status"] == "warning"


async def test_the_exact_state_of_the_incident_reads_as_a_warning():
    """2026-09-01: the newest partition was the month that had just ended."""
    with patch("app.db.session.get_db_session", return_value=_NullSession()), \
         patch(_COVERAGE, new=AsyncMock(return_value=-1)):
        response = await _get("/api/v1/health/partitions")

    assert response.status_code == 200
    assert response.json() == {"status": "warning", "monthsRemaining": -1}


@pytest.mark.parametrize("months", [PARTITION_COVERAGE_WARN_MONTHS, 99])
async def test_threshold_boundary_is_inclusive(months):
    with patch("app.db.session.get_db_session", return_value=_NullSession()), \
         patch(_COVERAGE, new=AsyncMock(return_value=months)):
        response = await _get("/api/v1/health/partitions")

    assert response.json()["status"] == "ok"


async def test_a_database_failure_is_reported_not_raised():
    """A diagnostic that 500s when the database is unwell is a second alarm
    for a problem you already know about — and a restart trigger if anyone
    ever points a healthcheck at it."""
    with patch("app.db.session.get_db_session", side_effect=RuntimeError("no connection")):
        response = await _get("/api/v1/health/partitions")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert "no connection" in body["error"]


def test_the_route_is_not_under_the_proxys_static_health_prefix():
    """nginx answers /health itself and prefix-matches:

        location /health { return 200 "ok\\n"; ... }

    So a route at /health/partitions never reaches the backend — it returned
    a plain "ok" through the proxy on the day round H shipped, while working
    perfectly when called directly on the container. The /api/ prefix is
    proxied through, which is why this lives there.
    """
    paths = {getattr(route, "path", None) for route in app.routes}

    assert "/api/v1/health/partitions" in paths
    assert "/health/partitions" not in paths


async def test_plain_health_stays_database_free():
    """/health is the container's healthcheck. It must answer even when the
    database is completely unreachable, or a DB blip becomes a restart loop
    on an app that is otherwise fine."""
    with patch("app.db.session.get_db_session", side_effect=RuntimeError("db down")):
        response = await _get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
