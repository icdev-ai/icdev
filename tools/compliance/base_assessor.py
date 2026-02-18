#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Base Compliance Framework Assessor for ICDEV.

Provides a reusable base class for all framework-specific assessors.
Each assessor loads its framework catalog, inherits NIST 800-53
implementations via the crosswalk engine, stores results in a
framework-specific DB table, evaluates gates, and logs audit events.

ADR D113: Multi-regime deduplication via crosswalk overlap detection.
Implementing AC-2 once counts toward all frameworks that require it.

Subclasses must implement:
    - FRAMEWORK_ID: str (e.g., "cjis", "hipaa", "pci_dss")
    - FRAMEWORK_NAME: str (e.g., "FBI CJIS Security Policy v5.9.4")
    - TABLE_NAME: str (e.g., "cjis_assessments")
    - CATALOG_FILENAME: str (e.g., "cjis_security_policy.json")
    - STATUS_VALUES: tuple (valid status values for assessments)
    - get_automated_checks(project, project_dir) -> dict
"""

import argparse
import json
import sqlite3
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CATALOG_DIR = BASE_DIR / "context" / "compliance"


class BaseAssessor(ABC):
    """Abstract base class for compliance framework assessors."""

    # Subclasses MUST set these
    FRAMEWORK_ID: str = ""
    FRAMEWORK_NAME: str = ""
    TABLE_NAME: str = ""
    CATALOG_FILENAME: str = ""
    STATUS_VALUES: Tuple[str, ...] = (
        "not_assessed", "satisfied", "partially_satisfied",
        "not_satisfied", "not_applicable", "risk_accepted",
    )

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._catalog_cache: Optional[List[Dict]] = None

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

    def _get_project(self, conn: sqlite3.Connection, project_id: str) -> Dict:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found.")
        return dict(row)

    def _log_audit_event(
        self, conn: sqlite3.Connection, project_id: str,
        action: str, details: Dict, file_path: Optional[str] = None,
    ) -> None:
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details,
                    affected_files, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    f"{self.FRAMEWORK_ID}_assessed",
                    "icdev-compliance-engine",
                    action,
                    json.dumps(details),
                    json.dumps([file_path] if file_path else []),
                    "CUI",
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: Could not log audit event: {e}", file=sys.stderr)

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        """Create the framework-specific assessments table if not exists."""
        status_check = ", ".join(f"'{s}'" for s in self.STATUS_VALUES)
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                assessment_date TEXT DEFAULT (datetime('now')),
                assessor TEXT DEFAULT 'icdev-compliance-engine',
                requirement_id TEXT NOT NULL,
                requirement_title TEXT,
                family TEXT,
                status TEXT DEFAULT 'not_assessed'
                    CHECK(status IN ({status_check})),
                evidence_description TEXT,
                evidence_path TEXT,
                automation_result TEXT,
                notes TEXT,
                nist_800_53_crosswalk TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, requirement_id)
            );
            CREATE INDEX IF NOT EXISTS idx_{self.TABLE_NAME}_project
                ON {self.TABLE_NAME}(project_id);
        """)
        conn.commit()

    # -----------------------------------------------------------------
    # Catalog loading
    # -----------------------------------------------------------------

    def load_catalog(self) -> List[Dict]:
        """Load and cache the framework catalog from JSON."""
        if self._catalog_cache is not None:
            return self._catalog_cache
        catalog_path = CATALOG_DIR / self.CATALOG_FILENAME
        if not catalog_path.exists():
            raise FileNotFoundError(
                f"Catalog not found: {catalog_path}\n"
                f"Expected: context/compliance/{self.CATALOG_FILENAME}"
            )
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Support both "requirements" and "controls" root keys
        self._catalog_cache = (
            data.get("requirements")
            or data.get("controls")
            or data.get("criteria")
            or []
        )
        return self._catalog_cache

    # -----------------------------------------------------------------
    # Crosswalk integration
    # -----------------------------------------------------------------

    def _get_nist_implementations(
        self, conn: sqlite3.Connection, project_id: str,
    ) -> Dict[str, str]:
        """Return a dict of {control_id: status} from project_controls."""
        try:
            rows = conn.execute(
                """SELECT control_id, implementation_status
                   FROM project_controls WHERE project_id = ?""",
                (project_id,),
            ).fetchall()
            return {
                row["control_id"].upper(): row["implementation_status"]
                for row in rows
            }
        except Exception:
            return {}

    def _crosswalk_status(
        self, requirement: Dict, nist_impl: Dict[str, str],
    ) -> Optional[str]:
        """Determine status from crosswalked NIST implementations.

        If all crosswalked NIST controls are 'implemented', the
        requirement is 'satisfied'. If some are implemented, it's
        'partially_satisfied'. Otherwise returns None (not determined).
        """
        crosswalk = requirement.get("nist_800_53_crosswalk", [])
        if not crosswalk:
            return None
        if isinstance(crosswalk, str):
            crosswalk = [crosswalk]

        implemented = 0
        total = len(crosswalk)
        for nist_id in crosswalk:
            status = nist_impl.get(nist_id.upper(), "")
            if status == "implemented":
                implemented += 1
            elif status == "partially_implemented":
                implemented += 0.5

        if implemented >= total:
            return "satisfied"
        elif implemented > 0:
            return "partially_satisfied"
        return None

    # -----------------------------------------------------------------
    # Core assessment
    # -----------------------------------------------------------------

    @abstractmethod
    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """Return automated check results for framework requirements.

        Args:
            project: Project row dict from the DB.
            project_dir: Optional path to project source code.

        Returns:
            Dict mapping requirement_id -> status string.
            Only include requirements that can be auto-checked.
        """
        return {}

    def assess(
        self,
        project_id: str,
        project_dir: Optional[str] = None,
    ) -> Dict:
        """Run full assessment for a project against this framework.

        1. Loads catalog requirements.
        2. Inherits NIST 800-53 implementations via crosswalk.
        3. Runs automated checks.
        4. Stores results in the framework-specific DB table.
        5. Logs audit event.

        Returns:
            Assessment summary dict.
        """
        conn = self._get_connection()
        try:
            self._ensure_table(conn)
            project = self._get_project(conn, project_id)
            catalog = self.load_catalog()
            nist_impl = self._get_nist_implementations(conn, project_id)

            # Run automated checks
            auto_checks = self.get_automated_checks(project, project_dir)

            now = datetime.utcnow().isoformat()
            results = []
            status_counts = {s: 0 for s in self.STATUS_VALUES}

            for req in catalog:
                req_id = req.get("id", "")
                req_title = req.get("title", "")
                family = req.get("family", "")
                crosswalk = req.get("nist_800_53_crosswalk", [])

                # Determine status: auto-check > crosswalk > not_assessed
                status = "not_assessed"
                evidence = ""
                automation_result = ""

                # 1. Auto-check result
                if req_id in auto_checks:
                    status = auto_checks[req_id]
                    automation_result = f"Automated check: {status}"

                # 2. Crosswalk inheritance
                if status == "not_assessed":
                    cw_status = self._crosswalk_status(req, nist_impl)
                    if cw_status:
                        status = cw_status
                        evidence = "Inherited from NIST 800-53 crosswalk"

                status_counts[status] = status_counts.get(status, 0) + 1

                # Upsert to DB
                crosswalk_json = json.dumps(crosswalk) if crosswalk else None
                conn.execute(
                    f"""INSERT OR REPLACE INTO {self.TABLE_NAME}
                       (project_id, assessment_date, requirement_id,
                        requirement_title, family, status,
                        evidence_description, automation_result,
                        nist_800_53_crosswalk, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id, now, req_id, req_title, family,
                        status, evidence, automation_result,
                        crosswalk_json, now,
                    ),
                )

                results.append({
                    "requirement_id": req_id,
                    "title": req_title,
                    "family": family,
                    "status": status,
                })

            conn.commit()

            # Compute summary
            total = len(results)
            satisfied = status_counts.get("satisfied", 0)
            partial = status_counts.get("partially_satisfied", 0)
            not_satisfied = status_counts.get("not_satisfied", 0)
            not_assessed = status_counts.get("not_assessed", 0)
            coverage_pct = round(
                ((satisfied + partial * 0.5) / total * 100) if total > 0 else 0, 1
            )

            summary = {
                "framework_id": self.FRAMEWORK_ID,
                "framework_name": self.FRAMEWORK_NAME,
                "project_id": project_id,
                "assessment_date": now,
                "total_requirements": total,
                "status_counts": status_counts,
                "coverage_pct": coverage_pct,
                "results": results,
            }

            # Update project_framework_status
            gate = "not_started"
            if coverage_pct >= 100.0:
                gate = "compliant"
            elif coverage_pct > 0:
                gate = "in_progress"
            if not_satisfied > 0:
                gate = "non_compliant"

            try:
                conn.execute(
                    """INSERT OR REPLACE INTO project_framework_status
                       (project_id, framework_id, total_controls,
                        implemented_controls, coverage_pct, gate_status,
                        last_assessed, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id, self.FRAMEWORK_ID, total,
                        satisfied, coverage_pct, gate, now, now,
                    ),
                )
                conn.commit()
            except Exception:
                pass  # Table may not exist yet

            self._log_audit_event(conn, project_id, f"{self.FRAMEWORK_NAME} assessment", {
                "total": total,
                "satisfied": satisfied,
                "partially_satisfied": partial,
                "not_satisfied": not_satisfied,
                "not_assessed": not_assessed,
                "coverage_pct": coverage_pct,
                "gate_status": gate,
            })

            return summary
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # Gate evaluation
    # -----------------------------------------------------------------

    def evaluate_gate(self, project_id: str) -> Dict:
        """Evaluate whether the project passes this framework's gate.

        Returns:
            Dict with pass (bool), gate_status, blocking_issues, and
            requirement-level detail.
        """
        conn = self._get_connection()
        try:
            self._ensure_table(conn)

            rows = conn.execute(
                f"""SELECT requirement_id, requirement_title, family, status
                    FROM {self.TABLE_NAME}
                    WHERE project_id = ?""",
                (project_id,),
            ).fetchall()

            if not rows:
                return {
                    "pass": False,
                    "gate_status": "not_assessed",
                    "framework": self.FRAMEWORK_NAME,
                    "blocking_issues": ["No assessment has been run yet."],
                    "total": 0,
                    "satisfied": 0,
                }

            blocking = []
            total = len(rows)
            satisfied = 0
            for row in rows:
                status = row["status"]
                if status == "satisfied":
                    satisfied += 1
                elif status == "not_satisfied":
                    blocking.append(
                        f"{row['requirement_id']}: {row['requirement_title']} "
                        f"({row['family']})"
                    )

            coverage = round((satisfied / total * 100) if total > 0 else 0, 1)
            gate_pass = len(blocking) == 0 and coverage >= 80.0

            return {
                "pass": gate_pass,
                "gate_status": "compliant" if gate_pass else "non_compliant",
                "framework": self.FRAMEWORK_NAME,
                "framework_id": self.FRAMEWORK_ID,
                "project_id": project_id,
                "total": total,
                "satisfied": satisfied,
                "coverage_pct": coverage,
                "blocking_issues": blocking,
            }
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # CLI interface
    # -----------------------------------------------------------------

    def run_cli(self) -> None:
        """Standard CLI entry point for all framework assessors."""
        parser = argparse.ArgumentParser(
            description=f"{self.FRAMEWORK_NAME} Assessment Engine"
        )
        parser.add_argument(
            "--project-id", required=True,
            help="Project ID to assess",
        )
        parser.add_argument(
            "--project-dir",
            help="Path to project source code for automated checks",
        )
        parser.add_argument(
            "--gate", action="store_true",
            help="Evaluate gate pass/fail only",
        )
        parser.add_argument(
            "--json", action="store_true",
            help="JSON output",
        )
        parser.add_argument(
            "--human", action="store_true",
            help="Human-readable colored output",
        )
        parser.add_argument(
            "--db-path", type=Path, default=None,
            help="Database path override",
        )
        args = parser.parse_args()

        if args.db_path:
            self.db_path = args.db_path

        try:
            if args.gate:
                result = self.evaluate_gate(args.project_id)
                if args.json:
                    print(json.dumps(result, indent=2))
                else:
                    status = "PASS" if result["pass"] else "FAIL"
                    print(f"{self.FRAMEWORK_NAME} Gate: {status}")
                    print(f"  Coverage: {result['coverage_pct']}%")
                    print(f"  Satisfied: {result['satisfied']}/{result['total']}")
                    if result["blocking_issues"]:
                        print(f"  Blocking ({len(result['blocking_issues'])}):")
                        for issue in result["blocking_issues"][:10]:
                            print(f"    - {issue}")
            else:
                result = self.assess(
                    args.project_id,
                    project_dir=args.project_dir,
                )
                if args.json:
                    print(json.dumps(result, indent=2))
                else:
                    print(f"{'=' * 65}")
                    print(f"  {self.FRAMEWORK_NAME} Assessment")
                    print(f"  Project: {args.project_id}")
                    print(f"{'=' * 65}")
                    print(f"  Total requirements: {result['total_requirements']}")
                    print(f"  Coverage: {result['coverage_pct']}%")
                    for status, count in result["status_counts"].items():
                        if count > 0:
                            print(f"  {status}: {count}")
                    print(f"{'=' * 65}")

        except (FileNotFoundError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
