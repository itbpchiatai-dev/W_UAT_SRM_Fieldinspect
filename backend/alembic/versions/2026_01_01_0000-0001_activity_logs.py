"""activity_logs (partitioned by month)

Revision ID: 0001_activity_logs
Revises:
Create Date: 2026-01-01 00:00:00.000000

v3.0 logging foundation — merged audit + user_activity into single
activity_logs table. See docs/logging.md §1 + AGENTS.md §14.
"""
from __future__ import annotations

from alembic import op

# revision identifiers used by Alembic
revision = "0001_activity_logs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() is built into PostgreSQL 13+ and needs no extension —
    # works against any reasonable Postgres target (including centralized
    # DBs where the standard's docker init-db.sql is never executed).
    op.execute('''
        CREATE TABLE activity_logs (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id UUID,
            user_email_masked VARCHAR(255),
            action_type VARCHAR(30) NOT NULL,
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(50),
            resource_id VARCHAR(100),
            is_mutation BOOLEAN NOT NULL DEFAULT FALSE,
            is_sensitive_read BOOLEAN NOT NULL DEFAULT FALSE,
            is_security_event BOOLEAN NOT NULL DEFAULT FALSE,
            risk_level VARCHAR(10) NOT NULL DEFAULT 'low',
            ip_address VARCHAR(45),
            user_agent VARCHAR(500),
            request_id VARCHAR(64),
            endpoint VARCHAR(200),
            http_method VARCHAR(10),
            http_status INTEGER,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);

        CREATE INDEX ix_activity_user_created ON activity_logs (user_id, created_at);
        CREATE INDEX ix_activity_action_type ON activity_logs (action_type, created_at);
        CREATE INDEX ix_activity_resource ON activity_logs (resource_type, resource_id);
        CREATE INDEX ix_activity_sensitive_created ON activity_logs (is_sensitive_read, created_at);
        CREATE INDEX ix_activity_security_created ON activity_logs (is_security_event, created_at);
        CREATE INDEX ix_activity_risk_created ON activity_logs (risk_level, created_at);
    ''')

    # Create initial partition (current month) so INSERT does not fail
    # before partition_manager runs. Future partitions are created by the
    # APScheduler job (docs/logging.md §5).
    op.execute('''
        DO $$
        DECLARE
            start_date date := date_trunc('month', CURRENT_DATE);
            end_date date := start_date + interval '1 month';
            partition_name text := 'activity_logs_' || to_char(start_date, 'YYYY_MM');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF activity_logs '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, start_date, end_date
            );
        END $$;
    ''')


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS activity_logs CASCADE")
