"""log partitions: let the app role maintain them, and backfill 24 months (round H).

THE INCIDENT THIS FIXES
-----------------------
On 2026-09-01 every write that records an activity log started failing on UAT
with `no partition of relation "activity_logs" found for row`. Because the
audit row is written inside the caller's transaction, the whole transaction
rolled back — and because the commit happens after the response is sent, the
API answered 200 and the user was told their import of 494 rows had succeeded
while nothing was saved. It ran for a week before anyone noticed.

The partitions were missing because the monthly scheduler job that creates
them has NEVER worked, not once:

    InsufficientPrivilegeError: permission denied for schema public
    [SQL: CREATE TABLE IF NOT EXISTS activity_logs_2026_09 PARTITION OF ...]

The app connects as the runtime role (DB_APP_USER / srm_app), which
deliberately has no CREATE on schema public — a role that serves requests from
the internet should not be able to create tables. The August partitions came
from migration 0001, so nothing failed until they ran out.

And the failure was completely silent because scheduler._audit_job writes each
job's outcome to system_logs — which is partitioned by the same rule and had
also run out. The failure report needed the thing that was broken.

WHAT THIS MIGRATION ADDS
------------------------
Two SECURITY DEFINER functions, owned by the table owner (migrations run as
that role) and EXECUTE-granted to the app role. The app can now maintain log
partitions — and ONLY log partitions — without holding CREATE on the schema.

  srm_ensure_log_partitions(months_ahead int) -> int
      Creates any missing monthly partition from the current month through
      +months_ahead for the three log tables. Returns how many it created.
      Idempotent.

  srm_drop_log_partition(partition_name text) -> boolean
      Drops ONE log partition. The retention job needs this for the same
      reason: DROP requires ownership, which the app role does not have.

Both are hardened the way a SECURITY DEFINER function must be:

  * `SET search_path = pg_catalog, public` — without it the caller's
    search_path decides what `activity_logs` resolves to, and a role that can
    create a table in an earlier schema could point the function at their own
    object and have it operated on as the owner.
  * The table name is never taken from the caller. ensure() iterates a
    hardcoded array; drop() matches its argument against
    `^(activity_logs|system_logs|ai_call_logs)_[0-9]{4}_[0-9]{2}$` and refuses
    anything else, so it can never drop a real table.
  * Only these two functions are granted; the role still cannot CREATE.

Then it calls ensure() once with a 24-month window, which backfills every
environment at deploy time — including a UAT that was repaired by hand and a
localhost that has partitions only through the current month.

WHY 24 MONTHS AND NOT 2
-----------------------
The job that tops them up runs in the app. If it ever stops working again
(new environment, changed role, container not running on the 25th), two
months of headroom means two months to the next silent outage. Two years of
headroom means the failure is a warning in the logs long before it is an
incident. ~87 KB of empty tables buys that.

Additive: creates functions and partitions, touches no existing table, no
data, no policy, no ownership. Downgrade drops the two functions but KEEPS the
partitions — dropping them would delete log rows.

Revision ID: 0055_log_partition_maintenance
Revises: 0054_records_final_yield_clean
Create Date: 2026-09-08 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0055_log_partition_maintenance"
down_revision = "0054_records_final_yield_clean"
branch_labels = None
depends_on = None

# Kept in sync with app/services/loggers/partition_manager.PARTITIONED_TABLES.
# A test asserts the two lists agree.
PARTITIONED_TABLES = ("activity_logs", "system_logs", "ai_call_logs")

MONTHS_AHEAD = 24


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION srm_ensure_log_partitions(months_ahead int DEFAULT 24)
        RETURNS int
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
        DECLARE
            t            text;
            m            date := date_trunc('month', current_date)::date;
            e            date;
            part         text;
            created      int  := 0;
            i            int;
        BEGIN
            IF months_ahead < 0 OR months_ahead > 120 THEN
                RAISE EXCEPTION 'months_ahead out of range: %', months_ahead;
            END IF;

            FOR i IN 0..months_ahead LOOP
                e := (m + interval '1 month')::date;
                FOREACH t IN ARRAY ARRAY['activity_logs', 'system_logs', 'ai_call_logs'] LOOP
                    part := t || '_' || to_char(m, 'YYYY_MM');
                    -- Every object is schema-qualified rather than left to
                    -- search_path. With pg_catalog first (which is what makes
                    -- the SET above safe against shadowing), an unqualified
                    -- CREATE TABLE lands in pg_catalog and Postgres rejects it
                    -- outright: "System catalog modifications are currently
                    -- disallowed". Found running this against a real database.
                    IF to_regclass('public.' || part) IS NULL THEN
                        EXECUTE format(
                            'CREATE TABLE public.%I PARTITION OF public.%I '
                            'FOR VALUES FROM (%L) TO (%L)',
                            part, t, m, e);
                        created := created + 1;
                    END IF;
                END LOOP;
                m := e;
            END LOOP;

            RETURN created;
        END;
        $fn$;

        CREATE OR REPLACE FUNCTION srm_drop_log_partition(partition_name text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
        BEGIN
            -- The caller supplies this name, so it is validated here rather
            -- than trusted: only a dated partition of a known log table can
            -- ever match, so this function cannot be used to drop a real table.
            IF partition_name !~ '^(activity_logs|system_logs|ai_call_logs)_[0-9]{4}_[0-9]{2}$' THEN
                RAISE EXCEPTION 'refusing to drop %: not a log partition', partition_name;
            END IF;
            IF to_regclass('public.' || partition_name) IS NULL THEN
                RETURN false;
            END IF;
            EXECUTE format('DROP TABLE public.%I', partition_name);
            RETURN true;
        END;
        $fn$;
        """
    )

    # The app role is named per environment (DB_APP_USER). Grant to whichever
    # non-owner role already has INSERT on the log tables, so this works on
    # dev, UAT and production without the migration knowing their names.
    op.execute(
        """
        DO $grant$
        DECLARE r text;
        BEGIN
            FOR r IN
                SELECT DISTINCT grantee
                  FROM information_schema.role_table_grants
                 WHERE table_name = 'activity_logs'
                   AND privilege_type = 'INSERT'
                   AND grantee <> current_user
                   AND grantee <> 'PUBLIC'
            LOOP
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION srm_ensure_log_partitions(int) TO %I', r);
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION srm_drop_log_partition(text) TO %I', r);
            END LOOP;
        END
        $grant$;
        """
    )

    # Backfill now, as the owner, so no environment leaves this deploy with a
    # gap — including one already repaired by hand (nothing is recreated).
    op.execute(f"SELECT srm_ensure_log_partitions({MONTHS_AHEAD})")


def downgrade() -> None:
    # Partitions are deliberately NOT dropped: they hold log rows, and the
    # tables predate this migration.
    op.execute(
        """
        DROP FUNCTION IF EXISTS srm_drop_log_partition(text);
        DROP FUNCTION IF EXISTS srm_ensure_log_partitions(int);
        """
    )
