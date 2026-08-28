#!/usr/bin/env python3
# CUI // SP-CTI
"""Engineering Review Board Daemon — continuous multi-persona code analysis (D-RB-1).

Runs 7 Reflexes as managed threads within a single process.  Each Reflex
represents an engineering persona (SRE, QA, Security, Performance, UX, Docs,
Product) and operates on its own schedule, governed by the Trust Kernel.

Built on DaemonBase (extracted from Genesis v2.0) for shared scheduling,
circuit breaker, state management, and CLI infrastructure.

Architecture Decisions:
    D-RB-1: Scanner-tier only (zero Claude tokens, unlimited Ollama)
    D-RB-2: Append-only review_board_audit table (NIST AU)
    D-RB-3: Each reflex is standalone with run() -> Dict interface
    D-RB-4: 3-tier confidence model (>=0.7 auto, 0.3-0.7 suggest, <0.3 escalate)
    D-RB-5: Feature flag ICDEV_REVIEW_BOARD_ENABLED (default: false)
    D-RB-6: Dashboard page /review-board
    D-RB-7: Runs on main branch (operational, not experimental)

Usage:
    python tools/review_board/daemon.py                    # Run as daemon
    python tools/review_board/daemon.py --once             # Single pass
    python tools/review_board/daemon.py --status           # Show status
    python tools/review_board/daemon.py --reflex sre       # Run one reflex
    python tools/review_board/daemon.py --enable sre       # Enable a reflex
    python tools/review_board/daemon.py --disable sre      # Disable a reflex
    python tools/review_board/daemon.py --reset sre        # Reset circuit breaker
    python tools/review_board/daemon.py --json             # JSON output
"""

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.daemon.base import (  # noqa: E402
    BASE_DIR,
    DaemonBase,
    ReflexStateBase,
    TrustKernelBase,
    RISK_GREEN,
    generate_id,
    utcnow_iso,
    sha256_hex,
)
from tools.db.storage import get_connection as _raw_get_connection  # noqa: E402


def get_connection():
    """Get DB connection with WAL mode and busy timeout for concurrent access."""
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass  # Non-SQLite backends may not support these
    return conn


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_VERSION = "1.0.0"
CONFIG_PATH = BASE_DIR / "args" / "review_board_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "review_board" / "daemon.pid"

REFLEX_NAMES = [
    "sre",
    "qa",
    "security",
    "perf",
    "ux",
    "docs",
    "product",
    "report",
]

# Severity levels for findings
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"


# ---------------------------------------------------------------------------
# Review Board ReflexState (uses review_board_reflex_state table)
# ---------------------------------------------------------------------------
class ReviewBoardReflexState(ReflexStateBase):
    state_table = "review_board_reflex_state"


# ---------------------------------------------------------------------------
# Review Board Trust Kernel
# ---------------------------------------------------------------------------
class ReviewBoardTrustKernel(TrustKernelBase):
    """Enforces risk tiers for review board operations (D-RB-1)."""

    def can_execute(self, risk_tier: str, action: str = "run") -> Tuple[bool, str]:
        if self.allowed_actions:
            allowed = self.allowed_actions.get(risk_tier, [])
            if action not in allowed:
                return False, f"Action '{action}' not in whitelist for tier '{risk_tier}'"
        return True, "approved"


# ---------------------------------------------------------------------------
# Review Board Daemon
# ---------------------------------------------------------------------------
class ReviewBoardDaemon(DaemonBase):
    """Main daemon process managing 7 engineering persona Reflexes (D-RB-1)."""

    daemon_name = "Engineering Review Board"
    daemon_version = DAEMON_VERSION
    config_path = CONFIG_PATH
    pid_file = PID_FILE
    env_enabled_var = "ICDEV_REVIEW_BOARD_ENABLED"
    env_reflex_prefix = "ICDEV_REVIEW_BOARD_REFLEX"
    event_prefix = "review_board"
    reflex_names = REFLEX_NAMES
    id_prefix = "rb"
    service_name = "review-board"       # coordination session-id prefix (autonomy-id-05)
    service_agent = "review_board"

    def ensure_tables(self) -> None:
        """Create review board tables if they do not exist."""
        conn = get_connection()
        try:
            # Audit trail (append-only, NIST AU — D-RB-2)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_board_audit (
                    id              TEXT PRIMARY KEY,
                    event_type      TEXT NOT NULL,
                    reflex_name     TEXT,
                    risk_tier       TEXT,
                    details         TEXT,
                    success         INTEGER,
                    duration_ms     INTEGER,
                    metric_name     TEXT,
                    metric_value    REAL,
                    created_at      TEXT NOT NULL
                );
            """)
            # Reflex state (allows UPDATE for scheduling/circuit breaker)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_board_reflex_state (
                    reflex_name             TEXT PRIMARY KEY,
                    enabled                 INTEGER NOT NULL DEFAULT 1,
                    last_run_at             TEXT,
                    next_run_at             TEXT,
                    consecutive_failures    INTEGER NOT NULL DEFAULT 0,
                    circuit_breaker_open    INTEGER NOT NULL DEFAULT 0,
                    circuit_breaker_tripped_at TEXT,
                    total_runs              INTEGER NOT NULL DEFAULT 0,
                    total_successes         INTEGER NOT NULL DEFAULT 0,
                    total_failures          INTEGER NOT NULL DEFAULT 0,
                    last_metric_value       REAL,
                    last_error              TEXT,
                    updated_at              TEXT NOT NULL
                );
            """)
            # Findings (append-only, NIST AU — D-RB-2)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_board_findings (
                    id              TEXT PRIMARY KEY,
                    reflex_name     TEXT NOT NULL,
                    severity        TEXT NOT NULL CHECK(severity IN (
                        'critical', 'high', 'medium', 'low', 'info'
                    )),
                    category        TEXT NOT NULL,
                    title           TEXT NOT NULL,
                    description     TEXT,
                    recommendation  TEXT,
                    evidence        TEXT,
                    confidence      REAL DEFAULT 0.0,
                    auto_fixable    INTEGER DEFAULT 0,
                    fix_applied     INTEGER DEFAULT 0,
                    sha256          TEXT,
                    classification  TEXT DEFAULT 'CUI',
                    created_at      TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rbf_reflex
                    ON review_board_findings(reflex_name);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rbf_severity
                    ON review_board_findings(severity);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rbf_created
                    ON review_board_findings(created_at);
            """)
            conn.commit()
        finally:
            conn.close()

    def log_audit(
        self,
        event_type: str,
        reflex_name: str = None,
        risk_tier: str = None,
        details: Dict = None,
        success: bool = None,
        duration_ms: int = None,
        metric_name: str = None,
        metric_value: float = None,
        **kwargs,
    ) -> str:
        """Append an audit event (D-RB-2: append-only, NIST AU)."""
        audit_id = generate_id("rba")
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO review_board_audit
                    (id, event_type, reflex_name, risk_tier, details, success,
                     duration_ms, metric_name, metric_value, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    audit_id,
                    event_type,
                    reflex_name,
                    risk_tier,
                    json.dumps(details) if details else None,
                    1 if success else (0 if success is False else None),
                    duration_ms,
                    metric_name,
                    metric_value,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return audit_id

    def create_reflex_state(self, name: str, config: Dict[str, Any]) -> ReflexStateBase:
        return ReviewBoardReflexState(name, config)

    def create_trust_kernel(self, config: Dict[str, Any]) -> TrustKernelBase:
        return ReviewBoardTrustKernel(config)

    def run_reflex_impl(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Execute a single reflex via tools/review_board/reflexes/<name>.py."""
        risk_tier = config.get("risk_tier", RISK_GREEN)

        # ORANGE tier — log that human review is needed
        if trust.requires_human_approval(risk_tier):
            return True, 0.0, {"status": "awaiting_human_approval", "risk_tier": risk_tier}

        try:
            module = importlib.import_module(f"tools.review_board.reflexes.{name}")
            if hasattr(module, "run"):
                result = module.run(config, trust)
                # Store findings in DB
                findings = result.get("details", {}).get("findings", [])
                if findings:
                    self._store_findings(name, findings)
                return (
                    result.get("success", False),
                    result.get("metric_value", 0.0),
                    result.get("details", {}),
                )
        except ImportError:
            pass
        except Exception as e:
            return False, 0.0, {"error": str(e), "stage": "reflex_execution"}

        # Stub mode
        return (
            True,
            0.0,
            {
                "status": "stub",
                "message": f"Reflex '{name}' not yet implemented -- stub mode (success)",
            },
        )

    def _store_findings(self, reflex_name: str, findings: list) -> int:
        """Store findings in review_board_findings (append-only)."""
        conn = get_connection()
        stored = 0
        try:
            for finding in findings:
                finding_id = generate_id("rbf")
                content_hash = sha256_hex(json.dumps(finding, sort_keys=True))
                # Dedup: skip if same content hash exists in last 24h
                existing = conn.execute(
                    "SELECT id FROM review_board_findings "
                    "WHERE sha256 = %s AND created_at > datetime('now', '-24 hours')",
                    (content_hash,),
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    """
                    INSERT INTO review_board_findings
                        (id, reflex_name, severity, category, title, description,
                         recommendation, evidence, confidence, auto_fixable,
                         sha256, classification, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        finding_id,
                        reflex_name,
                        finding.get("severity", "info"),
                        finding.get("category", "general"),
                        finding.get("title", "Untitled finding"),
                        finding.get("description"),
                        finding.get("recommendation"),
                        json.dumps(finding.get("evidence")) if finding.get("evidence") else None,
                        finding.get("confidence", 0.0),
                        1 if finding.get("auto_fixable") else 0,
                        content_hash,
                        "CUI",
                        utcnow_iso(),
                    ),
                )
                stored += 1
            conn.commit()
        finally:
            conn.close()
        return stored

    def on_reflex_completed(self, name: str, result: Dict[str, Any]) -> None:
        """Post-reflex hook: notify + auto-remediate findings."""
        findings = result.get("details", {}).get("findings", [])

        # Step 1: Push notifications (D-RB-11)
        if findings:
            try:
                from tools.review_board.notifier import notify_findings

                notify_findings(findings, reflex_name=name)
            except Exception:
                pass  # Notification is best-effort

        # Step 1b: Log to main audit trail (NIST AU-2)
        try:
            from tools.review_board.compliance_bridge import log_to_audit_trail

            severity_counts = {}
            for f in findings:
                s = f.get("severity", "info")
                severity_counts[s] = severity_counts.get(s, 0) + 1
            log_to_audit_trail(
                "review_board.scan_completed",
                f"Review Board {name} reflex completed: {len(findings)} findings",
                {"reflex": name, "finding_count": len(findings), "severities": severity_counts},
            )
        except Exception:
            pass

        # Step 2: Auto-remediate fixable findings (D-RB-4)
        fixable = [f for f in findings if f.get("auto_fixable") and f.get("confidence", 0) >= 0.7]
        if not fixable:
            return
        try:
            from tools.review_board.remediation_engine import run_remediation

            remediation_result = run_remediation(dry_run=False)
            self.log_audit(
                f"{self.event_prefix}.remediation.completed",
                reflex_name=name,
                details={
                    "auto_fixed": remediation_result.get("auto_fixed", 0),
                    "verified": remediation_result.get("verified", 0),
                    "suggested": remediation_result.get("suggested", 0),
                },
                success=remediation_result.get("failed", 0) == 0,
            )
            # Notify remediation outcome
            try:
                from tools.review_board.notifier import notify_remediation

                notify_remediation(remediation_result)
            except Exception:
                pass
        except Exception as e:
            self.log_audit(
                f"{self.event_prefix}.remediation.error",
                reflex_name=name,
                details={"error": str(e)},
                success=False,
            )

    def get_extra_status(self) -> Dict[str, Any]:
        """Add review board-specific status info."""
        conn = get_connection()
        try:
            # Count findings by severity
            rows = conn.execute(
                "SELECT severity, COUNT(*) as cnt FROM review_board_findings GROUP BY severity"
            ).fetchall()
            severity_counts = {row[0]: row[1] for row in rows}

            # Count recent findings (last 24h)
            recent = conn.execute(
                "SELECT COUNT(*) FROM review_board_findings WHERE created_at > datetime('now', '-24 hours')"
            ).fetchone()

            return {
                "findings_by_severity": severity_counts,
                "findings_last_24h": recent[0] if recent else 0,
                "total_findings": sum(severity_counts.values()),
            }
        except Exception:
            return {"findings_by_severity": {}, "findings_last_24h": 0}
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level functions for external callers (dashboard, heartbeat, etc.)
# ---------------------------------------------------------------------------
def get_status(as_json: bool = False):
    """Get daemon status — callable from dashboard or CLI."""
    try:
        daemon = ReviewBoardDaemon(ReviewBoardDaemon.load_config())
        daemon.ensure_tables()
        status = daemon.get_status()
        if as_json:
            return status
        return json.dumps(status, indent=2)
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        return result if as_json else json.dumps(result, indent=2)


def get_findings(limit: int = 50, severity: str = None):
    """Get recent findings — callable from dashboard."""
    conn = get_connection()
    try:
        query = "SELECT * FROM review_board_findings"
        params = []
        if severity:
            query += " WHERE severity = ?"
            params.append(severity)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    config = ReviewBoardDaemon.load_config()
    daemon = ReviewBoardDaemon(config)
    daemon.run_cli()


if __name__ == "__main__":
    main()
