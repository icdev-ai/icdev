# CUI // SP-CTI
"""Migration 210: SSO provider and session tables for enterprise SSO (SAML/OIDC)."""
from tools.db.storage import get_connection


def up():
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sso_providers (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                protocol TEXT NOT NULL CHECK(protocol IN ('saml','oidc')),
                entity_id TEXT,
                metadata_url TEXT,
                client_id TEXT,
                client_secret_enc TEXT,
                attr_mapping TEXT,
                claims_mapping TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sso_providers_tenant ON sso_providers(tenant_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sso_sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                user_id TEXT,
                name_id TEXT,
                session_index TEXT,
                id_token TEXT,
                access_token_enc TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sso_sessions_tenant ON sso_sessions(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sso_sessions_provider ON sso_sessions(provider_id)"
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    up()
    print("Migration 210 (SSO tables) applied.")
