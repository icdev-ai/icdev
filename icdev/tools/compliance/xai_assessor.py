#!/usr/bin/env python3

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""XAI Compliance Assessor — Explainable AI assessment (D289).

Evaluates project observability and explainability posture against
NIST AI RMF, DoD RAI "Traceable" principle, and ISO 42001 requirements.

10 automated checks:
  XAI-001: Tracing active (OTel or SQLite tracer configured)
  XAI-002: MCP instrumentation enabled (tool call spans exist)
  XAI-003: A2A distributed tracing active (cross-agent spans linked)
  XAI-004: Provenance graph populated (prov_entities > 0)
  XAI-005: Content tracing policy documented
  XAI-006: SHAP analysis run within 30 days
  XAI-007: Decision rationale recorded (decision_recorder entries exist)
  XAI-008: Trace retention configured and enforced
  XAI-009: AI telemetry active (recent entries)
  XAI-010: Agent trust scoring active (trust scores computed)

CLI:
    python tools/compliance/xai_assessor.py --project-id proj-123 --json
    python tools/compliance/xai_assessor.py --project-id proj-123 --gate
"""

import sqlite3
from tools.db.storage import get_connection
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from tools.compliance.base_assessor import BaseAssessor

logger = get_logger("icdev.compliance.xai_assessor")


class XAIAssessor(BaseAssessor):
    """Explainable AI compliance assessor (D289).

    Implements BaseAssessor pattern (D116) with 10 automated checks
    covering observability, traceability, and explainability.
    Crosswalks through NIST 800-53 US hub to FedRAMP/CMMC cascade.
    """

    FRAMEWORK_ID = "xai"
    FRAMEWORK_NAME = "Observability & Explainable AI (Phase 46)"
    TABLE_NAME = "xai_assessments"
    CATALOG_FILENAME = "xai_requirements.json"

    def get_automated_checks(
        self,
        project: Dict,
        project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """Run 10 automated XAI checks against project.

        Returns:
            Dict mapping check_id -> status.
        """
        project_id = project.get("id", "")
        results = {}

        # XAI-001: Tracing active
        results["XAI-001"] = self._check_tracing_active(project_id)

        # XAI-002: MCP instrumentation enabled
        results["XAI-002"] = self._check_mcp_instrumentation(project_id)

        # XAI-003: A2A distributed tracing active
        results["XAI-003"] = self._check_a2a_tracing(project_id)

        # XAI-004: Provenance graph populated
        results["XAI-004"] = self._check_provenance_populated(project_id)

        # XAI-005: Content tracing policy documented
        results["XAI-005"] = self._check_content_policy()

        # XAI-006: SHAP analysis recent
        results["XAI-006"] = self._check_shap_recent(project_id)

        # XAI-007: Decision rationale recorded
        results["XAI-007"] = self._check_decision_rationale(project_id)

        # XAI-008: Trace retention configured
        results["XAI-008"] = self._check_retention_configured()

        # XAI-009: AI telemetry active
        results["XAI-009"] = self._check_ai_telemetry(project_id)

        # XAI-010: Agent trust scoring active
        results["XAI-010"] = self._check_trust_scoring(project_id)

        return results

    def _check_tracing_active(self, project_id: str) -> str:
        """XAI-001: Check if tracing is active (any spans exist)."""
        try:
            conn = get_connection()
            count = conn.execute(
                "SELECT COUNT(*) FROM otel_spans WHERE project_id = %s",
                (project_id,),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "not_satisfied"
        except sqlite3.Error:
            return "not_assessed"

    def _check_mcp_instrumentation(self, project_id: str) -> str:
        """XAI-002: Check for MCP tool call spans."""
        try:
            conn = get_connection()
            count = conn.execute(
                "SELECT COUNT(*) FROM otel_spans WHERE project_id = %s AND name = 'mcp.tool_call'",
                (project_id,),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "not_satisfied"
        except sqlite3.Error:
            return "not_assessed"

    def _check_a2a_tracing(self, project_id: str) -> str:
        """XAI-003: Check for cross-agent span linking."""
        try:
            conn = get_connection()
            # Look for spans with parent_span_id (indicates linked hierarchy)
            count = conn.execute(
                """SELECT COUNT(*) FROM otel_spans
                   WHERE project_id = %s AND parent_span_id IS NOT NULL""",
                (project_id,),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "partially_satisfied"
        except sqlite3.Error:
            return "not_assessed"

    def _check_provenance_populated(self, project_id: str) -> str:
        """XAI-004: Check provenance graph has entities."""
        try:
            conn = get_connection()
            count = conn.execute(
                "SELECT COUNT(*) FROM prov_entities WHERE project_id = %s",
                (project_id,),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "not_satisfied"
        except sqlite3.Error:
            return "not_assessed"

    def _check_content_policy(self) -> str:
        """XAI-005: Check content tracing policy config exists."""
        config_path = Path(__file__).resolve().parent.parent.parent / "args" / "observability_tracing_config.yaml"
        if config_path.exists():
            return "satisfied"
        return "not_satisfied"

    def _check_shap_recent(self, project_id: str) -> str:
        """XAI-006: Check SHAP analysis run within 30 days."""
        try:
            conn = get_connection()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            count = conn.execute(
                """SELECT COUNT(*) FROM shap_attributions
                   WHERE project_id = %s AND analyzed_at > %s""",
                (project_id, cutoff),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "not_satisfied"
        except sqlite3.Error:
            return "not_assessed"

    def _check_decision_rationale(self, project_id: str) -> str:
        """XAI-007: Check decision_records exist for project."""
        try:
            conn = get_connection()
            count = conn.execute(
                "SELECT COUNT(*) FROM decision_records WHERE project_id = %s",
                (project_id,),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "not_satisfied"
        except sqlite3.Error:
            return "not_assessed"

    def _check_retention_configured(self) -> str:
        """XAI-008: Check retention config exists in YAML."""
        try:
            import yaml

            config_path = Path(__file__).resolve().parent.parent.parent / "args" / "observability_tracing_config.yaml"
            if not config_path.exists():
                return "not_satisfied"
            with open(config_path) as f:
                config = yaml.safe_load(f)
            retention = config.get("retention", {})
            if retention.get("sqlite_retention_days"):
                return "satisfied"
            return "not_satisfied"
        except Exception:
            return "not_assessed"

    def _check_ai_telemetry(self, project_id: str) -> str:
        """XAI-009: Check AI telemetry has recent entries."""
        try:
            conn = get_connection()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            count = conn.execute(
                """SELECT COUNT(*) FROM ai_telemetry
                   WHERE project_id = %s AND logged_at > %s""",
                (project_id, cutoff),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "not_satisfied"
        except sqlite3.Error:
            return "not_assessed"

    def _check_trust_scoring(self, project_id: str) -> str:
        """XAI-010: Check agent trust scores have been computed."""
        try:
            conn = get_connection()
            count = conn.execute(
                "SELECT COUNT(*) FROM agent_trust_scores WHERE project_id = %s",
                (project_id,),
            ).fetchone()[0]
            conn.close()
            return "satisfied" if count > 0 else "not_satisfied"
        except sqlite3.Error:
            return "not_assessed"


if __name__ == "__main__":
    assessor = XAIAssessor()
    assessor.run_cli()
