#!/usr/bin/env python3
# CUI // SP-CTI
"""EU AI Act Risk Classifier (Phase 57, D349).

BaseAssessor subclass for EU Artificial Intelligence Act (Regulation 2024/1689).
Bridges through ISO 27001 international hub (D111). Optional — triggered only
when eu_market: true in project configuration.

Architecture Decisions:
  D349: EU AI Act classifier uses BaseAssessor ABC. Bridges through ISO 27001
        international hub (D111). Optional — triggered only when eu_market: true.

Usage:
  python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --json
  python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --gate
"""

import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance.base_assessor import BaseAssessor


class EUAIActClassifier(BaseAssessor):
    """EU AI Act risk classifier and compliance assessor.

    Classifies AI systems into risk categories per EU AI Act Annex III
    and assesses compliance with high-risk requirements (Articles 9-15).
    """

    FRAMEWORK_ID = "eu_ai_act"
    FRAMEWORK_NAME = "EU Artificial Intelligence Act (Regulation 2024/1689)"
    TABLE_NAME = "eu_ai_act_assessments"
    CATALOG_FILENAME = "eu_ai_act_annex_iii.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None
    ) -> Dict[str, str]:
        """Check EU AI Act requirements against existing ICDEV evidence.

        Maps 12 requirements to existing ICDEV controls and data:
        - EUAI-01: Risk classification → AI inventory with risk_level
        - EUAI-02: Data governance → model cards with training data docs
        - EUAI-03: Technical documentation → model cards + system cards
        - EUAI-04: Record-keeping → audit trail + AI telemetry
        - EUAI-05: Transparency → AI transparency audit results
        - EUAI-06: Human oversight → oversight plans + CAIO designation
        - EUAI-07: Accuracy/robustness → SAST + security scanning
        - EUAI-08: Risk management → NIST AI RMF assessment
        - EUAI-09: Conformity assessment → production audit
        - EUAI-10: Post-market monitoring → heartbeat + cATO evidence
        - EUAI-11: Incident reporting → AI incident log
        - EUAI-12: Fundamental rights impact → AI impact assessment
        """
        results = {}
        conn = None

        try:
            if self.db_path.exists():
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                project_id = project.get("id", "")

                # EUAI-01: Risk Classification — AI inventory registered
                results.update(self._check_table_count(
                    conn, "ai_use_case_inventory", project_id, "EUAI-01"
                ))

                # EUAI-02: Data Governance — model cards exist with data docs
                results.update(self._check_table_count(
                    conn, "model_cards", project_id, "EUAI-02"
                ))

                # EUAI-03: Technical Documentation — model + system cards
                mc = self._count_records(conn, "model_cards", project_id)
                sc = self._count_records(conn, "system_cards", project_id)
                if mc > 0 and sc > 0:
                    results["EUAI-03"] = "satisfied"
                elif mc > 0 or sc > 0:
                    results["EUAI-03"] = "partially_satisfied"

                # EUAI-04: Record-Keeping — audit trail + AI telemetry
                at = self._count_records(conn, "audit_trail", project_id)
                tel = self._count_records(conn, "ai_telemetry", project_id)
                if at > 0 and tel > 0:
                    results["EUAI-04"] = "satisfied"
                elif at > 0 or tel > 0:
                    results["EUAI-04"] = "partially_satisfied"

                # EUAI-05: Transparency — transparency assessments
                for table in ["omb_m25_21_assessments", "omb_m26_04_assessments", "gao_ai_assessments"]:
                    cnt = self._count_records(conn, table, project_id)
                    if cnt > 0:
                        results["EUAI-05"] = "satisfied"
                        break
                if "EUAI-05" not in results:
                    results.update(self._check_table_count(
                        conn, "nist_ai_600_1_assessments", project_id, "EUAI-05"
                    ))

                # EUAI-06: Human Oversight — oversight plans + CAIO
                op = self._count_records(conn, "ai_oversight_plans", project_id)
                caio = self._count_records(conn, "ai_caio_registry", project_id)
                if op > 0 and caio > 0:
                    results["EUAI-06"] = "satisfied"
                elif op > 0 or caio > 0:
                    results["EUAI-06"] = "partially_satisfied"

                # EUAI-07: Accuracy/Robustness — SAST + dep audit
                results.update(self._check_table_count(
                    conn, "stig_results", project_id, "EUAI-07"
                ))

                # EUAI-08: Risk Management — NIST AI RMF assessment
                results.update(self._check_table_count(
                    conn, "nist_ai_rmf_assessments", project_id, "EUAI-08"
                ))

                # EUAI-09: Conformity Assessment — production audits
                results.update(self._check_table_count(
                    conn, "production_audits", project_id, "EUAI-09"
                ))

                # EUAI-10: Post-Market Monitoring — cATO evidence or heartbeat
                results.update(self._check_table_count(
                    conn, "cato_evidence", project_id, "EUAI-10"
                ))

                # EUAI-11: Incident Reporting — AI incident log
                results.update(self._check_table_count(
                    conn, "ai_incident_log", project_id, "EUAI-11"
                ))

                # EUAI-12: Fundamental Rights Impact — ethics reviews
                results.update(self._check_table_count(
                    conn, "ai_ethics_reviews", project_id, "EUAI-12"
                ))

        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        return results

    def _check_table_count(
        self, conn: sqlite3.Connection, table: str, project_id: str, req_id: str
    ) -> Dict[str, str]:
        """Check if a table has records for a project, return satisfied/empty."""
        cnt = self._count_records(conn, table, project_id)
        if cnt > 0:
            return {req_id: "satisfied"}
        return {}

    def _count_records(
        self, conn: sqlite3.Connection, table: str, project_id: str
    ) -> int:
        """Count records for a project in a table. Returns 0 if table missing."""
        try:
            # Check table exists
            row = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row[0] == 0:
                return 0

            # Check if table has project_id column
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if "project_id" in cols:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
            else:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return row[0]
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    EUAIActClassifier.run_cli()
