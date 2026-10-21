"""Append-only, hash-chained audit log; least-privilege grants; default policies.

Revision ID: 0002
Revises: 0001
"""
import os

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("PDFWS_DB_APP_ROLE", "pdfws_app")


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION pdfws_audit_canonical(e audit_events) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
      SELECT concat_ws('|', e.seq::text,
        to_char(e.ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        e.event_type, e.status, coalesce(e.document_id::text, ''), coalesce(e.version_id::text, ''),
        coalesce(e.job_id::text, ''), coalesce(e.operation, ''), coalesce(e.input_sha256, ''),
        coalesce(e.output_sha256, ''), coalesce(e.delivery_outcome, ''), coalesce(e.details::text, '{}'),
        coalesce(e.prev_hash, ''))
    $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION pdfws_audit_before_insert() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE last_seq bigint; last_hash text;
    BEGIN
      -- Serialise chain extension so seq/prev_hash are gap-free and ordered.
      PERFORM pg_advisory_xact_lock(727274);
      SELECT seq, event_hash INTO last_seq, last_hash FROM audit_events ORDER BY seq DESC NULLS LAST LIMIT 1;
      NEW.seq := coalesce(last_seq, 0) + 1;
      NEW.ts := clock_timestamp();
      NEW.prev_hash := coalesce(last_hash, repeat('0', 64));
      NEW.event_hash := encode(sha256(convert_to(pdfws_audit_canonical(NEW), 'UTF8')), 'hex');
      RETURN NEW;
    END $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION pdfws_audit_block_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP USING ERRCODE = 'insufficient_privilege';
    END $$;
    """)
    op.execute("""
    CREATE TRIGGER audit_events_chain BEFORE INSERT ON audit_events
      FOR EACH ROW EXECUTE FUNCTION pdfws_audit_before_insert();
    CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION pdfws_audit_block_change();
    CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
      FOR EACH STATEMENT EXECUTE FUNCTION pdfws_audit_block_change();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION pdfws_audit_verify()
    RETURNS TABLE(checked bigint, first_bad_seq bigint) LANGUAGE sql STABLE AS $$
      WITH ordered AS (
        SELECT e.seq, e.event_hash, e.prev_hash,
               encode(sha256(convert_to(pdfws_audit_canonical(e), 'UTF8')), 'hex') AS recomputed,
               lag(e.event_hash) OVER (ORDER BY e.seq) AS expected_prev
        FROM audit_events e
      )
      SELECT count(*)::bigint,
             min(seq) FILTER (WHERE event_hash IS DISTINCT FROM recomputed
                               OR (expected_prev IS NOT NULL AND prev_hash IS DISTINCT FROM expected_prev))
      FROM ordered
    $$;
    """)
    op.execute("""
    INSERT INTO expiration_policies (id, enabled, max_age_days, keep_latest) VALUES
      ('derived', false, 180, true),
      ('original', false, 3650, true),
      ('temp', true, 1, false)
    ON CONFLICT (id) DO NOTHING;
    """)
    # Least privilege for the runtime role: no UPDATE/DELETE/TRUNCATE on the audit log.
    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') AND current_user <> '{APP_ROLE}' THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO {APP_ROLE}';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}';
        EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {APP_ROLE}';
        EXECUTE 'REVOKE ALL ON alembic_version FROM {APP_ROLE}';
        EXECUTE 'GRANT SELECT ON alembic_version TO {APP_ROLE}';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}';
        EXECUTE 'GRANT EXECUTE ON FUNCTION pdfws_audit_verify() TO {APP_ROLE}';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_chain ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS pdfws_audit_verify()")
    op.execute("DROP FUNCTION IF EXISTS pdfws_audit_block_change()")
    op.execute("DROP FUNCTION IF EXISTS pdfws_audit_before_insert()")
    op.execute("DROP FUNCTION IF EXISTS pdfws_audit_canonical(audit_events)")
