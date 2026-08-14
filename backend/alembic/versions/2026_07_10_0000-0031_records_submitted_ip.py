"""Add records.submitted_ip — client IP captured at record creation.

Audit aid ("ใครเข้ามาบันทึก"): every record-create endpoint (logged-in
JSON/multipart + public JSON/multipart) now stores the submitting
client's IP, resolved server-side via the same trusted-proxy-aware
algorithm the rate limiter uses (app/core/rate_limit.py's get_client_ip)
— never taken from the request body. varchar(45) fits the longest IPv6
textual form. Nullable: rows created before this migration have no IP,
and a failed resolution stores NULL rather than blocking the create.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_records_submitted_ip"
down_revision = "0030_weather_condition_255"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "records",
        sa.Column("submitted_ip", sa.String(length=45), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("records", "submitted_ip")
