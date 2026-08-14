"""records — FarmLog field inspection records (20 fixed fields + custom_fields jsonb).

Revision ID: 0015_records
Revises: 0014_plots
Create Date: 2026-06-29 03:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0015_records"
down_revision = "0014_plots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE records (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plot_id         UUID NOT NULL REFERENCES plots(id) ON DELETE RESTRICT,
            supplier_id     UUID NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
            recorded_by_id  UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            record_date     DATE NOT NULL,
            crop_type       VARCHAR(100),
            growth_stage    VARCHAR(100),
            area_rai        NUMERIC(10,2) CHECK (area_rai >= 0),
            plant_height_cm NUMERIC(8,2)  CHECK (plant_height_cm >= 0),

            pest_found      BOOLEAN NOT NULL DEFAULT FALSE,
            pest_detail     TEXT,
            pest_severity   SMALLINT CHECK (pest_severity BETWEEN 1 AND 5),

            disease_found   BOOLEAN NOT NULL DEFAULT FALSE,
            disease_detail  TEXT,
            disease_severity SMALLINT CHECK (disease_severity BETWEEN 1 AND 5),

            weed_severity   SMALLINT CHECK (weed_severity BETWEEN 0 AND 5),
            fertilizer_used VARCHAR(255),
            fertilizer_amount_kg NUMERIC(10,2) CHECK (fertilizer_amount_kg >= 0),

            irrigation_method   VARCHAR(100),
            weather_condition   VARCHAR(100),
            recommendation      TEXT,
            notes               TEXT,

            custom_fields   JSONB NOT NULL DEFAULT '{}',
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX ix_records_plot_id       ON records (plot_id);
        CREATE INDEX ix_records_supplier_id   ON records (supplier_id);
        CREATE INDEX ix_records_recorded_by   ON records (recorded_by_id);
        CREATE INDEX ix_records_record_date   ON records (record_date DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS records;")
