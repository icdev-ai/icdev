#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""GAO AI Accountability Assessor.

Assesses compliance with GAO-21-519SP — Artificial Intelligence:
An Accountability Framework for Federal Agencies. Covers 4 principles:
Governance, Data, Performance, Monitoring.

Pattern: tools/compliance/base_assessor.py (BaseAssessor ABC).
ADR D307: All Phase 48 assessors use BaseAssessor.
ADR D313: Reuses existing ICDEV data as evidence.

Usage:
    python tools/compliance/gao_ai_assessor.py --project-id proj-123
    python tools/compliance/gao_ai_assessor.py --project-id proj-123 --gate
    python tools/compliance/gao_ai_assessor.py --project-id proj-123 --json
"""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class GAOAIAssessor(BaseAssessor):
    FRAMEWORK_ID = "gao_ai"
    FRAMEWORK_NAME = "GAO-21-519SP (AI Accountability)"
    TABLE_NAME = "gao_ai_assessments"
    CATALOG_FILENAME = "gao_ai_accountability.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """GAO AI Accountability automated checks.

        Checks existing ICDEV data for GAO evidence:
        - GAO-GOV-1: Governance structure (agent config, authority matrix)
        - GAO-GOV-4: Risk management (risk assessments exist)
        - GAO-DATA-2: Data provenance (PROV records or AI BOM)
        - GAO-DATA-3: Data security (encryption and access controls)
        - GAO-PERF-1: Performance metrics (ai_telemetry, token tracking)
        - GAO-PERF-3: Explainability (XAI/SHAP assessments)
        - GAO-PERF-4: Audit trail (audit_trail records)
        - GAO-MON-1: Continuous monitoring (ai_telemetry, behavioral drift)
        - GAO-MON-2: Feedback collection (Phase 49)
        - GAO-MON-3: Incident detection (ai_incident_log) (Phase 49)
        - GAO-MON-4: Reassessment schedule (Phase 49)
        - GAO-GOV-2: Legal compliance (Phase 49)
        - GAO-GOV-3: Ethics framework (Phase 49)
        """
        results = {}
        conn = None

        try:
            if self.db_path.exists():
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                project_id = project.get("id", "")

                # GAO-PERF-4: Audit trail
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM audit_trail
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAO-PERF-4"] = "satisfied"
                except Exception:
                    pass

                # GAO-PERF-1: Performance metrics (ai_telemetry)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_telemetry
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAO-PERF-1"] = "satisfied"
                        results["GAO-MON-1"] = "satisfied"
                except Exception:
                    pass

                # GAO-PERF-3: Explainability (XAI/SHAP)
                try:
                    for table in ["xai_assessments", "shap_attributions"]:
                        rows = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()
                        if rows and rows["cnt"] > 0:
                            results["GAO-PERF-3"] = "satisfied"
                            break
                except Exception:
                    pass

                # GAO-DATA-2: Data provenance
                try:
                    for table in ["prov_entities", "ai_bom"]:
                        rows = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()
                        if rows and rows["cnt"] > 0:
                            results["GAO-DATA-2"] = "satisfied"
                            break
                except Exception:
                    pass

                # GAO-GOV-4: Risk management (any AI assessments)
                try:
                    for table in ["nist_ai_rmf_assessments", "atlas_assessments"]:
                        rows = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()
                        if rows and rows["cnt"] > 0:
                            results["GAO-GOV-4"] = "satisfied"
                            break
                except Exception:
                    pass

                # GAO-MON-3: Incident detection via ai_incident_log (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_incident_log
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAO-MON-3"] = "satisfied"
                except Exception:
                    pass

                # GAO-MON-2: Feedback collection (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM audit_trail
                           WHERE project_id = ? AND event_type LIKE '%feedback%'""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAO-MON-2"] = "satisfied"
                except Exception:
                    pass

                # GAO-MON-4: Reassessment schedule (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_reassessment_schedule
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAO-MON-4"] = "satisfied"
                except Exception:
                    pass

                # GAO-GOV-2: Legal compliance (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_ethics_reviews
                           WHERE project_id = ? AND legal_compliance_matrix = 1""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAO-GOV-2"] = "satisfied"
                except Exception:
                    pass

                # GAO-GOV-3: Ethics framework (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_ethics_reviews
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAO-GOV-3"] = "satisfied"
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        # File-based checks
        if project_dir:
            project_path = Path(project_dir)

            # GAO-GOV-1: Governance structure
            for f in project_path.rglob("*.yaml"):
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore").lower()
                    if ("agent" in content and "authority" in content) or \
                       ("governance" in content and "ai" in content):
                        results["GAO-GOV-1"] = "satisfied"
                        break
                except Exception:
                    continue

            # GAO-DATA-3: Data security
            for f in project_path.rglob("*.yaml"):
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore").lower()
                    if ("encrypt" in content or "fips" in content) and \
                       ("data" in content or "secret" in content):
                        results["GAO-DATA-3"] = "satisfied"
                        break
                except Exception:
                    continue

        return results


if __name__ == "__main__":
    GAOAIAssessor().run_cli()
