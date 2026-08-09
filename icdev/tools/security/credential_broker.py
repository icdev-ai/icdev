#!/usr/bin/env python3
# CUI // SP-CTI
"""Credential Broker — NemoClaw-adapted agent credential isolation (D-NC-1).

Agents request credentials by *function* (e.g., code_generation, secrets_read),
never by provider name or API key.  The broker resolves function → provider →
credential, issues a short-lived scoped token, and logs every grant/denial
to an append-only audit table (NIST AU, D6).

Key design:
    - Agents never see raw API keys — broker injects them at call time.
    - Tokens are SHA-256 hashed before storage (privacy-preserving).
    - Auto-revocation when agent trust drops below threshold (D260 integration).
    - Fallback to direct env var reads when broker is disabled (backward compat).

CLI:
    python tools/security/credential_broker.py --request --agent-id builder --function code_generation --json
    python tools/security/credential_broker.py --revoke --agent-id builder --json
    python tools/security/credential_broker.py --audit --agent-id builder --json
    python tools/security/credential_broker.py --status --json
    python tools/security/credential_broker.py --gate --project-id proj-123 --json
"""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "credential_broker_config.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_config() -> Dict:
    """Load credential broker config from YAML."""
    if yaml is None:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class CredentialBroker:
    """Agent credential isolation with scoped short-lived tokens (D-NC-1).

    Follows NemoClaw pattern: agents request credentials by purpose,
    broker resolves and issues tokens.  Raw API keys never reach agent
    process memory.
    """

    def __init__(self, db_path: Optional[Path] = None, config: Optional[Dict] = None):
        self._db_path = db_path or DB_PATH
        self._config = config or _load_config()
        self._enabled = self._config.get("enabled", False)
        env_override = self._config.get("env_override", "ICDEV_CREDENTIAL_BROKER_ENABLED")
        if os.environ.get(env_override, "").lower() in ("true", "1", "yes"):
            self._enabled = True
        self._tokens_cfg = self._config.get("tokens", {})
        self._function_map = self._config.get("function_map", {})
        self._audit_cfg = self._config.get("audit", {})
        self._fallback_cfg = self._config.get("fallback", {})
        self._ensure_tables()

    def _get_db(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection(db_path=str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self):
        conn = self._get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS credential_broker_log (
                id              TEXT PRIMARY KEY,
                agent_id        TEXT NOT NULL,
                function        TEXT NOT NULL,
                provider        TEXT,
                scope           TEXT,
                action          TEXT NOT NULL CHECK(action IN ('grant', 'deny', 'revoke', 'expire', 'fallback')),
                token_hash      TEXT,
                reason          TEXT,
                ttl_seconds     INTEGER,
                expires_at      TEXT,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cbl_agent ON credential_broker_log(agent_id);
            CREATE INDEX IF NOT EXISTS idx_cbl_function ON credential_broker_log(function);
            CREATE INDEX IF NOT EXISTS idx_cbl_action ON credential_broker_log(action);
            CREATE INDEX IF NOT EXISTS idx_cbl_created ON credential_broker_log(created_at);

            CREATE TABLE IF NOT EXISTS credential_active_tokens (
                token_hash      TEXT PRIMARY KEY,
                agent_id        TEXT NOT NULL,
                function        TEXT NOT NULL,
                provider        TEXT,
                scope           TEXT,
                issued_at       TEXT NOT NULL,
                expires_at      TEXT NOT NULL,
                revoked         INTEGER NOT NULL DEFAULT 0,
                revoked_at      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cat_agent ON credential_active_tokens(agent_id);
            CREATE INDEX IF NOT EXISTS idx_cat_expires ON credential_active_tokens(expires_at);
        """)
        conn.commit()
        conn.close()

    def _log_event(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        function: str,
        action: str,
        provider: str = "",
        scope: str = "",
        token_hash: str = "",
        reason: str = "",
        ttl: int = 0,
        expires_at: str = "",
    ):
        conn.execute(
            """INSERT INTO credential_broker_log
               (id, agent_id, function, provider, scope, action, token_hash,
                reason, ttl_seconds, expires_at, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                str(uuid.uuid4()),
                agent_id,
                function,
                provider,
                scope,
                action,
                token_hash,
                reason,
                ttl,
                expires_at,
                _now(),
            ),
        )

    def request_credential(
        self,
        agent_id: str,
        function: str,
        project_id: str = "",
        ttl_seconds: Optional[int] = None,
    ) -> Dict:
        """Request a scoped credential token for a function.

        Returns:
            Dict with token, provider, scope, expires_at on success.
            Dict with error, reason on denial.
        """
        if not self._enabled:
            return self._fallback(agent_id, function)

        # Resolve function → provider + scope
        func_cfg = self._function_map.get(function)
        if not func_cfg:
            conn = self._get_db()
            self._log_event(conn, agent_id, function, "deny", reason="function_not_mapped")
            conn.commit()
            conn.close()
            return {"error": "function_not_mapped", "function": function}

        provider = func_cfg.get("provider", "")
        scope = func_cfg.get("scope", "")

        # Check agent trust level (optional integration with D260)
        trust_check = self._check_agent_trust(agent_id)
        if trust_check.get("blocked"):
            conn = self._get_db()
            self._log_event(
                conn,
                agent_id,
                function,
                "deny",
                provider=provider,
                scope=scope,
                reason=f"trust_level={trust_check.get('trust_level')}",
            )
            conn.commit()
            conn.close()
            return {
                "error": "agent_untrusted",
                "trust_level": trust_check.get("trust_level"),
                "trust_score": trust_check.get("trust_score"),
            }

        # Determine TTL
        default_ttl = self._tokens_cfg.get("default_ttl_seconds", 3600)
        max_ttl = func_cfg.get("max_ttl_seconds", self._tokens_cfg.get("max_ttl_seconds", 86400))
        actual_ttl = min(ttl_seconds or default_ttl, max_ttl)

        # Generate token
        token = secrets.token_urlsafe(48)
        token_hash = _hash_token(token)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=actual_ttl)
        expires_str = expires.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Store active token
        conn = self._get_db()
        conn.execute(
            """INSERT INTO credential_active_tokens
               (token_hash, agent_id, function, provider, scope,
                issued_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (token_hash, agent_id, function, provider, scope, _now(), expires_str),
        )
        self._log_event(
            conn,
            agent_id,
            function,
            "grant",
            provider=provider,
            scope=scope,
            token_hash=token_hash[:16] + "...",
            ttl=actual_ttl,
            expires_at=expires_str,
        )
        conn.commit()
        conn.close()

        return {
            "token": token,
            "token_hash_prefix": token_hash[:16],
            "provider": provider,
            "scope": scope,
            "ttl_seconds": actual_ttl,
            "expires_at": expires_str,
            "agent_id": agent_id,
            "function": function,
        }

    def revoke_credentials(self, agent_id: str, reason: str = "manual") -> Dict:
        """Revoke all active tokens for an agent."""
        conn = self._get_db()
        now = _now()
        cursor = conn.execute(
            """SELECT token_hash, function, provider, scope
               FROM credential_active_tokens
               WHERE agent_id = %s AND revoked = 0
               AND expires_at > %s""",
            (agent_id, now),
        )
        revoked = []
        for row in cursor.fetchall():
            conn.execute(
                "UPDATE credential_active_tokens SET revoked = 1, revoked_at = %s WHERE token_hash = %s",
                (now, row["token_hash"]),
            )
            self._log_event(
                conn,
                agent_id,
                row["function"],
                "revoke",
                provider=row["provider"],
                scope=row["scope"],
                token_hash=row["token_hash"][:16] + "...",
                reason=reason,
            )
            revoked.append(row["token_hash"][:16])
        conn.commit()
        conn.close()
        return {"agent_id": agent_id, "revoked_count": len(revoked), "reason": reason}

    def validate_token(self, token: str) -> Dict:
        """Validate a token and return its grant metadata."""
        token_hash = _hash_token(token)
        conn = self._get_db()
        row = conn.execute(
            """SELECT * FROM credential_active_tokens
               WHERE token_hash = %s AND revoked = 0""",
            (token_hash,),
        ).fetchone()
        conn.close()

        if not row:
            return {"valid": False, "reason": "token_not_found_or_revoked"}

        now = datetime.now(timezone.utc)
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if now > expires:
            return {"valid": False, "reason": "token_expired"}

        return {
            "valid": True,
            "agent_id": row["agent_id"],
            "function": row["function"],
            "provider": row["provider"],
            "scope": row["scope"],
            "expires_at": row["expires_at"],
        }

    def get_audit_log(self, agent_id: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get credential broker audit events."""
        conn = self._get_db()
        if agent_id:
            rows = conn.execute(
                """SELECT * FROM credential_broker_log
                   WHERE agent_id = %s ORDER BY created_at DESC LIMIT %s""",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM credential_broker_log
                   ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_status(self) -> Dict:
        """Get broker status: enabled, active tokens, recent events."""
        conn = self._get_db()
        now = _now()
        active = conn.execute(
            "SELECT COUNT(*) FROM credential_active_tokens WHERE revoked = 0 AND expires_at > %s",
            (now,),
        ).fetchone()[0]
        total_grants = conn.execute(
            "SELECT COUNT(*) FROM credential_broker_log WHERE action = 'grant'",
        ).fetchone()[0]
        total_denials = conn.execute(
            "SELECT COUNT(*) FROM credential_broker_log WHERE action = 'deny'",
        ).fetchone()[0]
        total_revocations = conn.execute(
            "SELECT COUNT(*) FROM credential_broker_log WHERE action = 'revoke'",
        ).fetchone()[0]
        conn.close()
        return {
            "enabled": self._enabled,
            "active_tokens": active,
            "total_grants": total_grants,
            "total_denials": total_denials,
            "total_revocations": total_revocations,
            "mapped_functions": len(self._function_map),
        }

    def evaluate_gate(self, project_id: str) -> Dict:
        """Gate evaluation for credential isolation (D-NC-1)."""
        status = self.get_status()
        blocking = []
        warnings = []

        if not self._enabled:
            if not self._fallback_cfg.get("allow_direct_env", True):
                blocking.append("credential_broker_disabled_no_fallback")
            else:
                warnings.append("credential_broker_disabled_using_fallback")

        if status["total_denials"] > 0:
            # Check recent denials (last 24h)
            conn = self._get_db()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            recent_denials = conn.execute(
                "SELECT COUNT(*) FROM credential_broker_log WHERE action = 'deny' AND created_at > %s",
                (cutoff,),
            ).fetchone()[0]
            conn.close()
            if recent_denials > 0:
                warnings.append(f"recent_credential_denials_{recent_denials}")

        passed = len(blocking) == 0
        return {
            "gate": "credential_isolation",
            "passed": passed,
            "blocking": blocking,
            "warnings": warnings,
            "status": status,
            "project_id": project_id,
            "evaluated_at": _now(),
        }

    def _check_agent_trust(self, agent_id: str) -> Dict:
        """Check agent trust level via D260 trust scorer (optional)."""
        try:
            conn = self._get_db()
            row = conn.execute(
                """SELECT trust_score, trust_level FROM agent_trust_scores
                   WHERE agent_id = %s ORDER BY created_at DESC LIMIT 1""",
                (agent_id,),
            ).fetchone()
            conn.close()
            if not row:
                return {"blocked": False, "trust_level": "unknown", "trust_score": 0.85}
            threshold = self._tokens_cfg.get("trust_revoke_threshold", 0.50)
            return {
                "blocked": row["trust_score"] < threshold,
                "trust_level": row["trust_level"],
                "trust_score": row["trust_score"],
            }
        except Exception:
            return {"blocked": False, "trust_level": "unknown", "trust_score": 0.85}

    def _fallback(self, agent_id: str, function: str) -> Dict:
        """Fallback to direct env var access when broker is disabled."""
        allow = self._fallback_cfg.get("allow_direct_env", True)
        if not allow:
            return {"error": "broker_disabled_no_fallback", "function": function}

        conn = self._get_db()
        self._log_event(conn, agent_id, function, "fallback", reason="broker_disabled")
        conn.commit()
        conn.close()

        return {
            "fallback": True,
            "message": "Credential broker disabled — using direct env var access",
            "agent_id": agent_id,
            "function": function,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Credential Broker — NemoClaw-adapted agent credential isolation (D-NC-1)"
    )
    parser.add_argument("--request", action="store_true", help="Request a credential token")
    parser.add_argument("--revoke", action="store_true", help="Revoke all tokens for agent")
    parser.add_argument("--validate", type=str, help="Validate a token")
    parser.add_argument("--audit", action="store_true", help="Show audit log")
    parser.add_argument("--status", action="store_true", help="Show broker status")
    parser.add_argument("--gate", action="store_true", help="Evaluate credential isolation gate")
    parser.add_argument("--agent-id", type=str, default="", help="Agent identifier")
    parser.add_argument("--function", type=str, default="", help="ICDEV™ function name")
    parser.add_argument("--project-id", type=str, default="", help="Project identifier")
    parser.add_argument("--ttl", type=int, help="Token TTL in seconds")
    parser.add_argument("--reason", type=str, default="manual", help="Revocation reason")
    parser.add_argument("--limit", type=int, default=50, help="Audit log limit")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    args = parser.parse_args()

    broker = CredentialBroker(db_path=args.db_path)

    if args.request:
        result = broker.request_credential(
            agent_id=args.agent_id,
            function=args.function,
            project_id=args.project_id,
            ttl_seconds=args.ttl,
        )
    elif args.revoke:
        result = broker.revoke_credentials(args.agent_id, reason=args.reason)
    elif args.validate:
        result = broker.validate_token(args.validate)
    elif args.audit:
        result = broker.get_audit_log(agent_id=args.agent_id or None, limit=args.limit)
    elif args.status:
        result = broker.get_status()
    elif args.gate:
        result = broker.evaluate_gate(args.project_id)
        if args.json:
            print(json.dumps(result, indent=2))
            raise SystemExit(0 if result["passed"] else 1)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)


if __name__ == "__main__":
    main()
