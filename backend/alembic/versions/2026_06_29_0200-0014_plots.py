"""plots — plot master + plot_assignments join table

Revision ID: 0014_plots
Revises: 0013_user_supplier_link
Create Date: 2026-06-29 02:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0014_plots"
down_revision = "0013_user_supplier_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE plots (
            id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            supplier_id   UUID         NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
            plot_code     VARCHAR(50)  NOT NULL,
            name          VARCHAR(255) NOT NULL,
            village       VARCHAR(255),
            district      VARCHAR(255),
            province      VARCHAR(100),
            latitude      NUMERIC(10, 7),
            longitude     NUMERIC(10, 7),
            rai           NUMERIC(10, 2),
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
        CREATE UNIQUE INDEX uq_plots_supplier_code ON plots (supplier_id, plot_code);
        CREATE INDEX ix_plots_supplier_id          ON plots (supplier_id);

        CREATE TABLE plot_assignments (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            plot_id     UUID        NOT NULL REFERENCES plots(id)    ON DELETE CASCADE,
            user_id     UUID        NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_plot_assignments_plot_user UNIQUE (plot_id, user_id)
        );
        CREATE INDEX ix_plot_assignments_plot_id ON plot_assignments (plot_id);
        CREATE INDEX ix_plot_assignments_user_id ON plot_assignments (user_id);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS plot_assignments;
        DROP TABLE IF EXISTS plots;
    """)
