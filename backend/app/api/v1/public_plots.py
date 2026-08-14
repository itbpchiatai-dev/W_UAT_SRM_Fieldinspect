"""Public (unauthenticated) plot endpoints.

Empty compatibility module (round 8-3G): the one endpoint this file ever
had — POST /plots/verify-inspection-code, the legacy inspection-code
verification gate — is retired. The public inspection flow is
phone-access-only now (see app/api/v1/public_inspection_access.py). Kept as
an empty router rather than deleted so `installed_routers.py`'s mount table
and every existing "no public route does X" regression test (several unit
tests import this module just to assert its router carries no matching
route) don't need touching for a retirement that changes no behavior for
them either way.
"""
from fastapi import APIRouter

router = APIRouter(tags=["public"])
