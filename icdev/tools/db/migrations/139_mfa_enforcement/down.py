"""Migration 139 down — remove MFA columns from dashboard_users."""
# CUI // SP-CTI

SQL = """
ALTER TABLE dashboard_users DROP COLUMN mfa_enabled;
ALTER TABLE dashboard_users DROP COLUMN totp_secret;
ALTER TABLE dashboard_users DROP COLUMN mfa_backup_codes;
ALTER TABLE dashboard_users DROP COLUMN mfa_verified_at;
"""


def down(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            try:
                conn.execute(s)
            except Exception:
                pass
    conn.commit()
