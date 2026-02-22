#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Pull-Based Telemetry Collector for ICDEV Child Apps (D210).

Collects health and performance telemetry from child applications
via pull-based HTTP requests to their health endpoints. Stores
telemetry in the child_telemetry table for the evolution engine
to consume.

Architecture:
- Pull-based: Parent polls child /health endpoints (no push from child)
- Air-gap safe: Uses stdlib urllib (no requests dependency)
- Append-only: Telemetry entries are immutable (D6 pattern)

Usage:
    from tools.registry.telemetry_collector import TelemetryCollector
    collector = TelemetryCollector()
    heartbeat = collector.collect_heartbeat("child-abc", "http://localhost:8445/health")
    collector.store_heartbeat(heartbeat)
    summary = collector.get_health_summary("child-abc")
"""

import hashlib
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.security.ai_telemetry_logger import AITelemetryLogger
    _telemetry = AITelemetryLogger()
except Exception:
    _telemetry = None


class TelemetryCollector:
    """Pull-based telemetry collector for ICDEV child applications.

    Polls child app health endpoints and stores telemetry in
    the child_telemetry table. Used by the evolution engine to
    monitor child health, detect degradation, and trigger
    self-healing or capability updates.

    Args:
        db_path: Path to icdev.db (default: data/icdev.db).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        timeout: int = 10,
    ):
        self.db_path = db_path or DB_PATH
        self.timeout = timeout

    # -----------------------------------------------------------------
    # Database helpers
    # -----------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {self.db_path}\n"
                "Run: python tools/db/init_icdev_db.py"
            )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        """Ensure the child_telemetry table exists."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS child_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child_id TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                health_status TEXT NOT NULL DEFAULT 'unknown'
                    CHECK(health_status IN ('healthy', 'degraded',
                                            'unhealthy', 'unreachable',
                                            'unknown')),
                genome_version TEXT,
                uptime_hours REAL DEFAULT 0.0,
                error_rate REAL DEFAULT 0.0,
                compliance_scores_json TEXT DEFAULT '{}',
                learned_behaviors_json TEXT DEFAULT '[]',
                response_time_ms INTEGER DEFAULT 0,
                raw_response TEXT,
                endpoint_url TEXT,
                classification TEXT DEFAULT 'CUI'
            );
            CREATE INDEX IF NOT EXISTS idx_child_telemetry_child
                ON child_telemetry(child_id);
            CREATE INDEX IF NOT EXISTS idx_child_telemetry_collected
                ON child_telemetry(collected_at);
            CREATE INDEX IF NOT EXISTS idx_child_telemetry_status
                ON child_telemetry(health_status);
        """)
        conn.commit()

    # -----------------------------------------------------------------
    # Telemetry collection
    # -----------------------------------------------------------------

    def collect_heartbeat(
        self,
        child_id: str,
        endpoint_url: str,
    ) -> Dict[str, Any]:
        """Collect a heartbeat from a child app health endpoint.

        Makes an HTTP GET request to the child's health endpoint
        and parses the response into a telemetry record.

        Args:
            child_id: Child app ID.
            endpoint_url: URL to the child's /health endpoint.

        Returns:
            Dict with telemetry data ready for storage.

        Expected health endpoint response format:
            {
                "status": "healthy",
                "genome_version": "1.0.0",
                "uptime_hours": 123.4,
                "error_rate": 0.01,
                "compliance_scores": {"nist": 0.85, "fedramp": 0.72},
                "learned_behaviors": ["auto_retry", "cache_optimization"],
                "ai_metrics": {
                    "model_id": "claude-sonnet-4-20250514",
                    "prompt_hash": "abc123",
                    "response_hash": "def456",
                    "token_count": 1500
                }
            }
        """
        import time

        now = datetime.now(timezone.utc).isoformat()
        start_time = time.time()

        telemetry: Dict[str, Any] = {
            "child_id": child_id,
            "collected_at": now,
            "health_status": "unreachable",
            "genome_version": None,
            "uptime_hours": 0.0,
            "error_rate": 0.0,
            "compliance_scores_json": "{}",
            "learned_behaviors_json": "[]",
            "response_time_ms": 0,
            "raw_response": None,
            "endpoint_url": endpoint_url,
        }

        try:
            req = urllib.request.Request(
                endpoint_url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                response_time_ms = int((time.time() - start_time) * 1000)
                raw = resp.read().decode("utf-8")

            telemetry["response_time_ms"] = response_time_ms
            telemetry["raw_response"] = raw

            # Parse JSON response
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                telemetry["health_status"] = "degraded"
                return telemetry

            # Map health fields
            status = data.get("status", "unknown")
            if status in ("healthy", "degraded", "unhealthy", "unreachable"):
                telemetry["health_status"] = status
            elif status in ("ok", "up", "running"):
                telemetry["health_status"] = "healthy"
            elif status in ("error", "down", "failing"):
                telemetry["health_status"] = "unhealthy"
            else:
                telemetry["health_status"] = "unknown"

            telemetry["genome_version"] = data.get(
                "genome_version", data.get("version")
            )
            telemetry["uptime_hours"] = float(
                data.get("uptime_hours", 0.0)
            )
            telemetry["error_rate"] = float(
                data.get("error_rate", 0.0)
            )

            compliance = data.get("compliance_scores", {})
            telemetry["compliance_scores_json"] = json.dumps(compliance)

            behaviors = data.get("learned_behaviors", [])
            telemetry["learned_behaviors_json"] = json.dumps(behaviors)

            # Phase 37 integration: extract AI metrics from heartbeat
            ai_metrics = data.get("ai_metrics")
            if ai_metrics and _telemetry is not None:
                try:
                    _telemetry.log_ai_interaction(
                        project_id=child_id,
                        interaction_type="child_ai_metrics",
                        model_id=ai_metrics.get("model_id", "unknown"),
                        prompt_hash=ai_metrics.get("prompt_hash", ""),
                        response_hash=ai_metrics.get("response_hash", ""),
                        token_count=ai_metrics.get("token_count", 0),
                        metadata=ai_metrics,
                    )
                except Exception:
                    pass  # Best-effort telemetry

        except urllib.error.URLError as e:
            telemetry["health_status"] = "unreachable"
            telemetry["raw_response"] = str(e)
            telemetry["response_time_ms"] = int(
                (time.time() - start_time) * 1000
            )
        except Exception as e:
            telemetry["health_status"] = "unreachable"
            telemetry["raw_response"] = str(e)
            telemetry["response_time_ms"] = int(
                (time.time() - start_time) * 1000
            )

        return telemetry

    def store_heartbeat(self, telemetry: Dict[str, Any]) -> int:
        """Store a heartbeat telemetry record in the database.

        Args:
            telemetry: Telemetry dict from collect_heartbeat().

        Returns:
            Row ID of the inserted record.
        """
        conn = self._get_connection()
        try:
            self._ensure_table(conn)
            cursor = conn.execute(
                """INSERT INTO child_telemetry
                   (child_id, collected_at, health_status, genome_version,
                    uptime_hours, error_rate, compliance_scores_json,
                    learned_behaviors_json, response_time_ms,
                    raw_response, endpoint_url, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI')""",
                (
                    telemetry["child_id"],
                    telemetry["collected_at"],
                    telemetry["health_status"],
                    telemetry.get("genome_version"),
                    telemetry.get("uptime_hours", 0.0),
                    telemetry.get("error_rate", 0.0),
                    telemetry.get("compliance_scores_json", "{}"),
                    telemetry.get("learned_behaviors_json", "[]"),
                    telemetry.get("response_time_ms", 0),
                    telemetry.get("raw_response"),
                    telemetry.get("endpoint_url"),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_latest_heartbeat(
        self, child_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the most recent heartbeat for a child app.

        Args:
            child_id: Child app ID.

        Returns:
            Dict with latest telemetry or None if no records.
        """
        conn = self._get_connection()
        try:
            self._ensure_table(conn)
            row = conn.execute(
                """SELECT * FROM child_telemetry
                   WHERE child_id = ?
                   ORDER BY collected_at DESC
                   LIMIT 1""",
                (child_id,),
            ).fetchone()

            return dict(row) if row else None
        finally:
            conn.close()

    def get_health_summary(
        self,
        child_id: str,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """Get health summary for a child app over a time window.

        Computes average error rate, uptime, response time, and
        status distribution over the specified time window.

        Args:
            child_id: Child app ID.
            hours: Lookback window in hours (default: 24).

        Returns:
            Dict with health summary metrics.
        """
        conn = self._get_connection()
        try:
            self._ensure_table(conn)

            rows = conn.execute(
                """SELECT health_status, uptime_hours, error_rate,
                          response_time_ms, compliance_scores_json,
                          collected_at
                   FROM child_telemetry
                   WHERE child_id = ?
                   AND collected_at >= datetime('now', ?)
                   ORDER BY collected_at DESC""",
                (child_id, f"-{hours} hours"),
            ).fetchall()

            if not rows:
                return {
                    "child_id": child_id,
                    "window_hours": hours,
                    "total_heartbeats": 0,
                    "health_status": "unknown",
                    "avg_error_rate": 0.0,
                    "avg_response_time_ms": 0,
                    "status_distribution": {},
                }

            # Compute metrics
            status_counts: Dict[str, int] = {}
            total_error_rate = 0.0
            total_response_time = 0
            latest_status = rows[0]["health_status"]

            for row in rows:
                s = row["health_status"]
                status_counts[s] = status_counts.get(s, 0) + 1
                total_error_rate += row["error_rate"] or 0.0
                total_response_time += row["response_time_ms"] or 0

            total = len(rows)
            avg_error_rate = round(total_error_rate / total, 4)
            avg_response_time = round(total_response_time / total)

            # Overall health assessment
            healthy_count = status_counts.get("healthy", 0)
            if healthy_count == total:
                overall = "healthy"
            elif healthy_count / total >= 0.8:
                overall = "mostly_healthy"
            elif status_counts.get("unhealthy", 0) > 0:
                overall = "unhealthy"
            elif status_counts.get("unreachable", 0) > 0:
                overall = "unreachable"
            else:
                overall = "degraded"

            return {
                "child_id": child_id,
                "window_hours": hours,
                "total_heartbeats": total,
                "health_status": overall,
                "latest_status": latest_status,
                "avg_error_rate": avg_error_rate,
                "avg_response_time_ms": avg_response_time,
                "status_distribution": status_counts,
            }
        finally:
            conn.close()

    def get_all_children_health(self) -> List[Dict[str, Any]]:
        """Get health summary for all registered children.

        Returns:
            List of health summary dicts, one per child.
        """
        conn = self._get_connection()
        try:
            self._ensure_table(conn)

            # Get distinct child IDs from telemetry
            child_ids = conn.execute(
                "SELECT DISTINCT child_id FROM child_telemetry"
            ).fetchall()

            summaries = []
            for row in child_ids:
                summary = self.get_health_summary(row["child_id"])
                summaries.append(summary)

            return summaries
        finally:
            conn.close()
