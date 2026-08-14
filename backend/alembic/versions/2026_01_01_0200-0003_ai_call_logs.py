"""ai_call_logs (partitioned by month)

Revision ID: 0003_ai_call_logs
Revises: 0002_system_logs
Create Date: 2026-01-01 02:00:00.000000

v3.0 logging foundation — every Claude / OpenAI / provider call.
See docs/logging.md §3 + AGENTS.md §14 + §16.
"""
from __future__ import annotations

from alembic import op

# revision identifiers used by Alembic
revision = "0003_ai_call_logs"
down_revision = "0002_system_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''
        CREATE TABLE ai_call_logs (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id UUID,
            endpoint VARCHAR(200),
            request_id VARCHAR(64),
            provider VARCHAR(20) NOT NULL DEFAULT 'anthropic',
            model VARCHAR(50) NOT NULL,
            operation VARCHAR(30) NOT NULL,
            prompt TEXT NOT NULL,
            system_prompt TEXT,
            response TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd NUMERIC(10, 6),
            duration_ms INTEGER,
            status VARCHAR(20) NOT NULL,
            error_type VARCHAR(100),
            error_message TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);

        CREATE INDEX ix_ai_logs_created ON ai_call_logs (created_at);
        CREATE INDEX ix_ai_logs_user_created ON ai_call_logs (user_id, created_at);
        CREATE INDEX ix_ai_logs_model_created ON ai_call_logs (model, created_at);
        CREATE INDEX ix_ai_logs_status_created ON ai_call_logs (status, created_at);
    ''')

    # Initial partition (current month).
    op.execute('''
        DO $$
        DECLARE
            start_date date := date_trunc('month', CURRENT_DATE);
            end_date date := start_date + interval '1 month';
            partition_name text := 'ai_call_logs_' || to_char(start_date, 'YYYY_MM');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF ai_call_logs '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, start_date, end_date
            );
        END $$;
    ''')


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_call_logs CASCADE")
