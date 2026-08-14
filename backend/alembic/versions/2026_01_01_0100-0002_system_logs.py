"""system_logs (partitioned by month)

Revision ID: 0002_system_logs
Revises: 0001_activity_logs
Create Date: 2026-01-01 01:00:00.000000

v3.0 logging foundation — jobs / integrations / system events.
See docs/logging.md §2 + AGENTS.md §14.
"""
from __future__ import annotations

from alembic import op

# revision identifiers used by Alembic
revision = "0002_system_logs"
down_revision = "0001_activity_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''
        CREATE TABLE system_logs (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            category VARCHAR(30) NOT NULL,
            event VARCHAR(100) NOT NULL,
            status VARCHAR(20) NOT NULL,
            duration_ms INTEGER,
            error_message TEXT,
            error_type VARCHAR(100),
            correlation_id VARCHAR(64),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);

        CREATE INDEX ix_system_logs_created ON system_logs (created_at);
        CREATE INDEX ix_system_logs_category_event ON system_logs (category, event);
        CREATE INDEX ix_system_logs_status_created ON system_logs (status, created_at);
    ''')

    # Initial partition (current month).
    op.execute('''
        DO $$
        DECLARE
            start_date date := date_trunc('month', CURRENT_DATE);
            end_date date := start_date + interval '1 month';
            partition_name text := 'system_logs_' || to_char(start_date, 'YYYY_MM');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF system_logs '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, start_date, end_date
            );
        END $$;
    ''')


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS system_logs CASCADE")
