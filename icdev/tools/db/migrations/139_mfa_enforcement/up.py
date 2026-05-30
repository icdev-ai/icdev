"""Migration 139 — MFA Enforcement for all dashboard users.

Adds TOTP MFA columns to dashboard_users and updates auth_log event types.
"""
# CUI // SP-CTI

SQL = """
-- Add MFA columns to dashboard_users
ALTER TABLE dashboard_users ADD COLUMN mfa_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dashboard_users ADD COLUMN totp_secret TEXT;
ALTER TABLE dashboard_users ADD COLUMN mfa_backup_codes TEXT;
ALTER TABLE dashboard_users ADD COLUMN mfa_verified_at TIMESTAMP;

-- Expand auth_log event types for MFA events
-- SQLite doesn't support ALTER TABLE on CHECK constraints; we rely on
-- application-level validation for new event types:
--   'mfa_setup', 'mfa_verified', 'mfa_failed', 'mfa_reset', 'mfa_backup_used'
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            try:
                conn.execute(s)
            except Exception:
                # Columns may already exist on re-runs; ignore
                pass
    conn.commit()
