-- CUI // SP-CTI
-- Migration 286: notification escalation + per-user preferences (crx-not-01)
--
-- Backs the routing-rules / escalation / preferences engine added in
-- tools/notifications/{routing_rules,escalation,preferences}.py.
--
--   * notification_escalations — mutable state for unacknowledged critical
--     alerts (pending -> acknowledged / escalated). tenant_id + classification
--     columns make it RLS-eligible. NOT append-only: transitions are additionally
--     journaled to the immutable audit_trail via atomic_log_event, so this table
--     is intentionally omitted from APPEND_ONLY_TABLES.
--   * notification_preferences — per-user channel prefs, quiet hours, and digest
--     opt-in, keyed (user_id, tenant_id). Also carries classification for RLS.
--
-- Both use CREATE TABLE IF NOT EXISTS and TEXT/INTEGER-only columns so they are
-- idempotent and dialect-neutral (PostgreSQL primary, SQLite init/test fallback).
-- The runtime modules also self-create these via _ensure_schema(), so a checkout
-- that has not run this migration still degrades gracefully.

CREATE TABLE IF NOT EXISTS notification_escalations (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    component TEXT DEFAULT '',
    tenant_id TEXT,
    classification TEXT,
    channels TEXT DEFAULT '[]',
    escalation_channels TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    ack_deadline TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    escalated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_notif_esc_status ON notification_escalations (status, ack_deadline);
CREATE INDEX IF NOT EXISTS idx_notif_esc_tenant ON notification_escalations (tenant_id);

CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id TEXT NOT NULL,
    tenant_id TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    channels TEXT DEFAULT '[]',
    quiet_hours_start INTEGER,
    quiet_hours_end INTEGER,
    timezone TEXT DEFAULT 'UTC',
    digest_opt_in INTEGER DEFAULT 0,
    digest_frequency TEXT DEFAULT 'daily',
    updated_at TEXT,
    PRIMARY KEY (user_id, tenant_id)
);
