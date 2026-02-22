#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Absorption Engine -- validates learned capabilities over a stability window before genome absorption.

72-hour stability window (D212): capabilities must demonstrate consistent performance
without regressions before being absorbed into the parent genome.

ADR D6: All absorption decisions are recorded in the append-only audit trail.
ADR D213: Child-reported behaviors flow through LearningCollector -> evaluation ->
    stability check -> absorption into parent genome.

A capability is considered stable when:
    1. It has been observed for >= STABILITY_WINDOW_HOURS (72h)
    2. Its error rate trend is non-increasing over the window
    3. It has not degraded any child's compliance posture

Usage:
    python tools/registry/absorption_engine.py --check --capability-id "42" --json
    python tools/registry/absorption_engine.py --absorb --capability-id "42" --absorbed-by "isso@mil" --json
    python tools/registry/absorption_engine.py --candidates --json
    python tools/registry/absorption_engine.py --history --json
    python tools/registry/absorption_engine.py --history --limit 50 --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1

try:
    from tools.registry.genome_manager import GenomeManager
    _HAS_GENOME = True
except ImportError:
    _HAS_GENOME = False


# =========================================================================
# CONSTANTS
# =========================================================================
STABILITY_WINDOW_HOURS = 72  # D212


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix="abs"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="absorption-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="icdev-genome",
            )
        except Exception:
            pass


# =========================================================================
# ABSORPTION ENGINE
# =========================================================================
class AbsorptionEngine:
    """Validates learned capabilities over a stability window before genome absorption.

    72-hour stability window (D212): capabilities must demonstrate consistent
    performance without regressions before being absorbed into the parent genome.

    The absorption pipeline:
        1. check_stability() -- verify the capability has been stable for the window
        2. absorb()          -- promote the capability into the parent genome
        3. get_absorption_candidates() -- find capabilities ready for absorption
        4. get_absorption_history() -- audit trail of past absorptions
    """

    STABILITY_WINDOW_HOURS = STABILITY_WINDOW_HOURS

    def __init__(self, db_path=None):
        """Initialize AbsorptionEngine.

        Args:
            db_path: Path to SQLite database. Defaults to data/icdev.db.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_tables()

    def _get_conn(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self):
        """Ensure required tables exist.

        The primary tables (child_learned_behaviors, child_telemetry,
        capability_evaluations, propagation_log, genome_versions) are created
        by migration 006. This method only creates them if missing (idempotent).
        """
        ddl = """
        CREATE TABLE IF NOT EXISTS child_learned_behaviors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            child_id TEXT NOT NULL,
            behavior_type TEXT NOT NULL
                CHECK(behavior_type IN ('optimization', 'error_recovery',
                                        'compliance_shortcut', 'performance_tuning',
                                        'security_pattern', 'workflow_improvement',
                                        'configuration', 'other')),
            description TEXT NOT NULL,
            evidence_json TEXT DEFAULT '{}',
            confidence REAL DEFAULT 0.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
            evaluated INTEGER DEFAULT 0,
            absorbed INTEGER DEFAULT 0,
            discovered_at TEXT DEFAULT (datetime('now')),
            evaluated_at TEXT,
            absorbed_at TEXT,
            classification TEXT DEFAULT 'CUI'
        );

        CREATE TABLE IF NOT EXISTS child_telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            child_id TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            health_status TEXT NOT NULL DEFAULT 'unknown',
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

        CREATE TABLE IF NOT EXISTS capability_evaluations (
            id TEXT PRIMARY KEY,
            capability_id TEXT NOT NULL,
            capability_name TEXT,
            score REAL NOT NULL,
            dimensions_json TEXT NOT NULL,
            outcome TEXT NOT NULL
                CHECK(outcome IN ('auto_queue', 'recommend', 'log', 'archive')),
            rationale TEXT,
            evaluator TEXT NOT NULL DEFAULT 'system',
            source_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS propagation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capability_name TEXT NOT NULL,
            genome_version TEXT NOT NULL,
            source_type TEXT NOT NULL
                CHECK(source_type IN ('genome', 'child_learned', 'marketplace',
                                      'manual', 'rollback', 'absorption',
                                      'cross_pollination')),
            source_child_id TEXT,
            target_child_id TEXT NOT NULL,
            propagation_status TEXT DEFAULT 'pending'
                CHECK(propagation_status IN ('pending', 'in_progress', 'success',
                                              'failed', 'rolled_back', 'skipped')),
            evaluation_id INTEGER,
            staging_env_id TEXT,
            error_details TEXT,
            initiated_by TEXT DEFAULT 'evolution-engine',
            initiated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            classification TEXT DEFAULT 'CUI'
        );

        CREATE TABLE IF NOT EXISTS genome_versions (
            id TEXT PRIMARY KEY,
            version TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            genome_data TEXT NOT NULL,
            change_type TEXT NOT NULL DEFAULT 'minor'
                CHECK(change_type IN ('major', 'minor', 'patch')),
            change_summary TEXT,
            parent_version TEXT,
            created_by TEXT NOT NULL DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        try:
            conn = self._get_conn()
            conn.executescript(ddl)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Table creation failed: {e}", file=sys.stderr)

    def check_stability(self, capability_id: str) -> dict:
        """Check if a learned behavior has been stable for the required window.

        Queries child_telemetry and capability_evaluations to determine whether
        the capability has been observed for at least STABILITY_WINDOW_HOURS,
        the error rate trend is non-increasing, and compliance has not degraded.

        Args:
            capability_id: Row ID (integer as string) in child_learned_behaviors.

        Returns:
            Dict with keys:
                stable (bool): Whether the capability passes the stability check.
                hours_observed (float): Hours since the behavior was discovered.
                error_rate_trend (str): 'decreasing', 'flat', or 'increasing'.
                compliance_impact (str): 'positive', 'neutral', or 'negative'.
                reason (str): Human-readable explanation if not stable.
        """
        conn = self._get_conn()
        try:
            # Fetch the learned behavior record
            row = conn.execute(
                "SELECT * FROM child_learned_behaviors WHERE id = ?",
                (capability_id,),
            ).fetchone()

            if not row:
                return {
                    "stable": False,
                    "hours_observed": 0.0,
                    "error_rate_trend": "unknown",
                    "compliance_impact": "unknown",
                    "reason": f"Capability {capability_id} not found",
                }

            behavior = dict(row)
            child_id = behavior["child_id"]
            discovered_at = behavior.get("discovered_at", "")

            # Calculate hours since discovery
            hours_observed = 0.0
            if discovered_at:
                try:
                    disc_dt = datetime.fromisoformat(
                        discovered_at.replace("Z", "+00:00")
                    )
                    now_dt = datetime.now(timezone.utc)
                    delta = now_dt - disc_dt
                    hours_observed = round(delta.total_seconds() / 3600.0, 2)
                except (ValueError, TypeError):
                    pass

            # Check minimum window
            if hours_observed < self.STABILITY_WINDOW_HOURS:
                remaining = round(self.STABILITY_WINDOW_HOURS - hours_observed, 2)
                return {
                    "stable": False,
                    "capability_id": capability_id,
                    "child_id": child_id,
                    "hours_observed": hours_observed,
                    "hours_required": self.STABILITY_WINDOW_HOURS,
                    "hours_remaining": remaining,
                    "error_rate_trend": "insufficient_data",
                    "compliance_impact": "unknown",
                    "reason": (
                        f"Stability window not met: {hours_observed:.1f}h observed, "
                        f"{self.STABILITY_WINDOW_HOURS}h required "
                        f"({remaining:.1f}h remaining)"
                    ),
                }

            # Analyze error rate trend from telemetry
            error_rate_trend = self._analyze_error_rate_trend(conn, child_id)

            # Analyze compliance impact from telemetry
            compliance_impact = self._analyze_compliance_impact(conn, child_id)

            # Determine overall stability
            stable = True
            reasons = []

            if error_rate_trend == "increasing":
                stable = False
                reasons.append("Error rate is increasing over the stability window")

            if compliance_impact == "negative":
                stable = False
                reasons.append("Compliance posture has degraded")

            # Check if already absorbed
            if behavior.get("absorbed", 0) == 1:
                stable = False
                reasons.append("Capability already absorbed")

            result = {
                "stable": stable,
                "capability_id": capability_id,
                "child_id": child_id,
                "behavior_type": behavior.get("behavior_type", "unknown"),
                "description": behavior.get("description", ""),
                "confidence": behavior.get("confidence", 0.0),
                "hours_observed": hours_observed,
                "hours_required": self.STABILITY_WINDOW_HOURS,
                "error_rate_trend": error_rate_trend,
                "compliance_impact": compliance_impact,
                "reason": "; ".join(reasons) if reasons else "All stability checks passed",
                "checked_at": _now(),
            }

            _audit(
                "absorption.stability_check",
                f"Stability check for capability {capability_id}: "
                f"{'stable' if stable else 'not stable'}",
                result,
            )

            return result

        finally:
            conn.close()

    def _analyze_error_rate_trend(self, conn, child_id: str) -> str:
        """Analyze error rate trend from telemetry over the stability window.

        Args:
            conn: Database connection.
            child_id: Child app ID.

        Returns:
            One of 'decreasing', 'flat', 'increasing', or 'insufficient_data'.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self.STABILITY_WINDOW_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute(
            """SELECT error_rate, collected_at
               FROM child_telemetry
               WHERE child_id = ? AND collected_at >= ?
               ORDER BY collected_at ASC""",
            (child_id, cutoff),
        ).fetchall()

        if len(rows) < 2:
            return "insufficient_data"

        rates = [r["error_rate"] or 0.0 for r in rows]

        # Compare first half average to second half average
        mid = len(rates) // 2
        first_half_avg = sum(rates[:mid]) / max(mid, 1)
        second_half_avg = sum(rates[mid:]) / max(len(rates) - mid, 1)

        # Allow a small tolerance (0.01) for noise
        tolerance = 0.01
        if second_half_avg > first_half_avg + tolerance:
            return "increasing"
        elif second_half_avg < first_half_avg - tolerance:
            return "decreasing"
        else:
            return "flat"

    def _analyze_compliance_impact(self, conn, child_id: str) -> str:
        """Analyze compliance score trend from telemetry over the stability window.

        Args:
            conn: Database connection.
            child_id: Child app ID.

        Returns:
            One of 'positive', 'neutral', or 'negative'.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self.STABILITY_WINDOW_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute(
            """SELECT compliance_scores_json, collected_at
               FROM child_telemetry
               WHERE child_id = ? AND collected_at >= ?
               ORDER BY collected_at ASC""",
            (child_id, cutoff),
        ).fetchall()

        if len(rows) < 2:
            return "neutral"

        def _avg_score(scores_json: str) -> float:
            """Average all compliance scores in a JSON dict."""
            try:
                scores = json.loads(scores_json or "{}")
                if not scores:
                    return 0.0
                vals = [float(v) for v in scores.values() if isinstance(v, (int, float))]
                return sum(vals) / len(vals) if vals else 0.0
            except (json.JSONDecodeError, TypeError, ValueError):
                return 0.0

        first_avg = _avg_score(rows[0]["compliance_scores_json"])
        last_avg = _avg_score(rows[-1]["compliance_scores_json"])

        tolerance = 0.02
        if last_avg > first_avg + tolerance:
            return "positive"
        elif last_avg < first_avg - tolerance:
            return "negative"
        else:
            return "neutral"

    def absorb(self, capability_id: str, absorbed_by: str = "system") -> Optional[dict]:
        """Absorb a stable capability into the parent genome.

        The capability must pass the stability check first. On success:
            1. Creates a new genome version via GenomeManager (if available)
            2. Marks the child_learned_behaviors record as absorbed=1
            3. Records the absorption in propagation_log (append-only, D6)

        Args:
            capability_id: Row ID in child_learned_behaviors.
            absorbed_by: Identity of the person/system performing absorption.

        Returns:
            Dict with absorption result, or None on failure.
        """
        # Step 1: Check stability
        stability = self.check_stability(capability_id)
        if not stability.get("stable", False):
            return {
                "absorbed": False,
                "capability_id": capability_id,
                "reason": stability.get("reason", "Stability check failed"),
                "stability_check": stability,
            }

        conn = self._get_conn()
        try:
            # Fetch the behavior record
            row = conn.execute(
                "SELECT * FROM child_learned_behaviors WHERE id = ?",
                (capability_id,),
            ).fetchone()

            if not row:
                return {
                    "absorbed": False,
                    "capability_id": capability_id,
                    "reason": "Capability not found",
                }

            behavior = dict(row)

            # Step 2: Create new genome version (if GenomeManager available)
            new_genome_version = None
            if _HAS_GENOME:
                try:
                    gm = GenomeManager(db_path=self.db_path)
                    current = gm.get_current()
                    current_data = {}
                    if current:
                        current_data = current.get("genome_data", {})
                        if isinstance(current_data, str):
                            try:
                                current_data = json.loads(current_data)
                            except json.JSONDecodeError:
                                current_data = {}

                    # Add the absorbed capability to the genome data
                    capabilities = current_data.get("capabilities", {})
                    cap_key = (
                        behavior.get("behavior_type", "other")
                        + "_"
                        + str(capability_id)
                    )
                    capabilities[cap_key] = {
                        "description": behavior.get("description", ""),
                        "source_child": behavior.get("child_id", ""),
                        "confidence": behavior.get("confidence", 0.0),
                        "behavior_type": behavior.get("behavior_type", "other"),
                        "absorbed_at": _now(),
                        "absorbed_by": absorbed_by,
                    }
                    current_data["capabilities"] = capabilities

                    version_result = gm.create_version(
                        genome_data=current_data,
                        created_by=absorbed_by,
                        change_type="minor",
                        change_summary=(
                            f"Absorbed capability {capability_id} "
                            f"({behavior.get('behavior_type', 'other')}) "
                            f"from child {behavior.get('child_id', 'unknown')}"
                        ),
                    )
                    if version_result and "error" not in version_result:
                        new_genome_version = version_result.get("version")
                except Exception as e:
                    print(
                        f"Warning: Genome version creation failed: {e}",
                        file=sys.stderr,
                    )

            # Step 3: Mark as absorbed (append-only: we only update absorbed flag)
            now = _now()
            conn.execute(
                """UPDATE child_learned_behaviors
                   SET absorbed = 1, absorbed_at = ?
                   WHERE id = ?""",
                (now, capability_id),
            )

            # Step 4: Record in propagation_log (append-only, D6)
            conn.execute(
                """INSERT INTO propagation_log
                   (capability_name, genome_version, source_type, source_child_id,
                    target_child_id, propagation_status, initiated_by,
                    initiated_at, completed_at, classification)
                   VALUES (?, ?, 'absorption', ?, 'parent-genome', 'success',
                           ?, ?, ?, 'CUI')""",
                (
                    behavior.get("description", f"behavior-{capability_id}")[:200],
                    new_genome_version or "unversioned",
                    behavior.get("child_id", "unknown"),
                    absorbed_by,
                    now,
                    now,
                ),
            )

            conn.commit()

            result = {
                "absorbed": True,
                "capability_id": capability_id,
                "child_id": behavior.get("child_id", ""),
                "behavior_type": behavior.get("behavior_type", ""),
                "description": behavior.get("description", ""),
                "new_genome_version": new_genome_version,
                "absorbed_by": absorbed_by,
                "absorbed_at": now,
            }

            _audit(
                "absorption.completed",
                f"Absorbed capability {capability_id} into genome "
                f"(version: {new_genome_version or 'unversioned'})",
                result,
            )

            return result

        except Exception as e:
            return {
                "absorbed": False,
                "capability_id": capability_id,
                "reason": str(e),
            }
        finally:
            conn.close()

    def get_absorption_candidates(self) -> list:
        """Find capabilities that have passed the stability window and are ready for absorption.

        Queries child_learned_behaviors for records that:
            - Have evaluated=1 (already evaluated by CapabilityEvaluator)
            - Have absorbed=0 (not yet absorbed)
            - Were discovered more than STABILITY_WINDOW_HOURS ago

        Returns:
            List of candidate dicts with stability metrics.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self.STABILITY_WINDOW_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, child_id, behavior_type, description,
                          evidence_json, confidence, discovered_at
                   FROM child_learned_behaviors
                   WHERE evaluated = 1
                     AND absorbed = 0
                     AND discovered_at <= ?
                   ORDER BY confidence DESC, discovered_at ASC""",
                (cutoff,),
            ).fetchall()

            candidates = []
            for row in rows:
                record = dict(row)
                # Calculate hours observed
                hours_observed = 0.0
                disc = record.get("discovered_at", "")
                if disc:
                    try:
                        disc_dt = datetime.fromisoformat(
                            disc.replace("Z", "+00:00")
                        )
                        delta = datetime.now(timezone.utc) - disc_dt
                        hours_observed = round(delta.total_seconds() / 3600.0, 2)
                    except (ValueError, TypeError):
                        pass

                record["hours_observed"] = hours_observed
                record["hours_required"] = self.STABILITY_WINDOW_HOURS
                record["window_passed"] = hours_observed >= self.STABILITY_WINDOW_HOURS

                # Parse evidence
                try:
                    record["evidence"] = json.loads(record.pop("evidence_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    record["evidence"] = {}

                candidates.append(record)

            return candidates

        finally:
            conn.close()

    def get_absorption_history(self, limit: int = 20) -> list:
        """Return recent absorptions from propagation_log.

        Queries the propagation_log for entries where source_type='absorption',
        ordered by most recent first.

        Args:
            limit: Maximum number of records to return. Default 20.

        Returns:
            List of absorption record dicts.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, capability_name, genome_version, source_child_id,
                          target_child_id, propagation_status, initiated_by,
                          initiated_at, completed_at
                   FROM propagation_log
                   WHERE source_type = 'absorption'
                   ORDER BY initiated_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

            return [dict(row) for row in rows]

        finally:
            conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "ICDEV Absorption Engine -- 72-hour stability window (D212) "
            "before genome absorption"
        )
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check", action="store_true",
        help="Check stability of a capability",
    )
    group.add_argument(
        "--absorb", action="store_true",
        help="Absorb a stable capability into the parent genome",
    )
    group.add_argument(
        "--candidates", action="store_true",
        help="List capabilities ready for absorption",
    )
    group.add_argument(
        "--history", action="store_true",
        help="Show absorption history",
    )

    parser.add_argument(
        "--capability-id", help="Capability ID (row ID in child_learned_behaviors)"
    )
    parser.add_argument(
        "--absorbed-by", default="system",
        help="Identity of the person/system performing absorption",
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="History limit (default: 20)"
    )

    args = parser.parse_args()

    try:
        engine = AbsorptionEngine(db_path=args.db_path)

        if args.check:
            if not args.capability_id:
                parser.error("--check requires --capability-id")
            result = engine.check_stability(args.capability_id)

        elif args.absorb:
            if not args.capability_id:
                parser.error("--absorb requires --capability-id")
            result = engine.absorb(
                capability_id=args.capability_id,
                absorbed_by=args.absorbed_by,
            )
            if result is None:
                result = {"error": "Absorption returned None"}

        elif args.candidates:
            result = engine.get_absorption_candidates()

        elif args.history:
            result = engine.get_absorption_history(limit=args.limit)

        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if args.check and isinstance(result, dict):
                stable = result.get("stable", False)
                print(f"Stability Check: {'STABLE' if stable else 'NOT STABLE'}")
                print("=" * 60)
                print(f"  Capability ID:    {result.get('capability_id', 'N/A')}")
                print(f"  Child ID:         {result.get('child_id', 'N/A')}")
                print(f"  Hours Observed:   {result.get('hours_observed', 0):.1f}")
                print(f"  Hours Required:   {result.get('hours_required', STABILITY_WINDOW_HOURS)}")
                if result.get("hours_remaining"):
                    print(f"  Hours Remaining:  {result['hours_remaining']:.1f}")
                print(f"  Error Rate Trend: {result.get('error_rate_trend', 'N/A')}")
                print(f"  Compliance:       {result.get('compliance_impact', 'N/A')}")
                print(f"  Reason:           {result.get('reason', 'N/A')}")

            elif args.absorb and isinstance(result, dict):
                absorbed = result.get("absorbed", False)
                print(f"Absorption: {'SUCCESS' if absorbed else 'FAILED'}")
                print("=" * 60)
                print(f"  Capability ID:      {result.get('capability_id', 'N/A')}")
                if absorbed:
                    print(f"  Genome Version:     {result.get('new_genome_version', 'N/A')}")
                    print(f"  Absorbed By:        {result.get('absorbed_by', 'N/A')}")
                    print(f"  Behavior Type:      {result.get('behavior_type', 'N/A')}")
                else:
                    print(f"  Reason:             {result.get('reason', 'N/A')}")

            elif args.candidates and isinstance(result, list):
                print("Absorption Candidates")
                print("=" * 80)
                if not result:
                    print("  No candidates found")
                for cand in result:
                    print(
                        f"  ID={cand.get('id', '?'):6s}  "
                        f"child={cand.get('child_id', '?'):16s}  "
                        f"type={cand.get('behavior_type', '?'):20s}  "
                        f"conf={cand.get('confidence', 0):.2f}  "
                        f"hours={cand.get('hours_observed', 0):.0f}"
                    )

            elif args.history and isinstance(result, list):
                print("Absorption History")
                print("=" * 80)
                if not result:
                    print("  No absorption history found")
                for entry in result:
                    print(
                        f"  {entry.get('initiated_at', '?'):22s}  "
                        f"cap={entry.get('capability_name', '?')[:30]:30s}  "
                        f"genome={entry.get('genome_version', '?'):10s}  "
                        f"status={entry.get('propagation_status', '?')}"
                    )

            else:
                print(json.dumps(result, indent=2, default=str))

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
