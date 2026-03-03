#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""OMB M-25-21 High-Impact AI Assessor.

Assesses compliance with OMB Memorandum M-25-21 — Advancing the
Responsible Acquisition and Governance of Artificial Intelligence.
Covers: AI inventory, high-impact classification, risk management,
human oversight, transparency, appeal processes, and annual reporting.

Pattern: tools/compliance/base_assessor.py (BaseAssessor ABC).
ADR D307: All Phase 48 assessors use BaseAssessor for automatic
gate/CLI/crosswalk.

Usage:
    python tools/compliance/omb_m25_21_assessor.py --project-id proj-123
    python tools/compliance/omb_m25_21_assessor.py --project-id proj-123 --gate
    python tools/compliance/omb_m25_21_assessor.py --project-id proj-123 --json
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


class OMBM2521Assessor(BaseAssessor):
    FRAMEWORK_ID = "omb_m25_21"
    FRAMEWORK_NAME = "OMB M-25-21 (High-Impact AI)"
    TABLE_NAME = "omb_m25_21_assessments"
    CATALOG_FILENAME = "omb_m25_21_high_impact_ai.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """OMB M-25-21 automated checks.

        Checks for:
        - M25-INV-1: AI use case inventory exists
        - M25-INV-2: Responsible official on inventory items
        - M25-INV-3: Reassessment schedule defined (Phase 49)
        - M25-CLS-1: High-impact classification completed
        - M25-RISK-1: AI risk assessment documented
        - M25-RISK-3: Continuous monitoring active
        - M25-RISK-4: AI incident response process (Phase 49)
        - M25-OVR-1: Human oversight plan exists (Phase 49)
        - M25-OVR-2: Transparency notices documented
        - M25-OVR-3: Appeal process registered (Phase 49)
        - M25-OVR-4: CAIO/Responsible official designated (Phase 49)
        """
        results = {}
        conn = None

        try:
            if self.db_path.exists():
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                project_id = project.get("id", "")

                # M25-INV-1: AI use case inventory exists
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_use_case_inventory
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-INV-1"] = "satisfied"
                except Exception:
                    pass

                # M25-INV-2: Responsible official on inventory items (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_use_case_inventory
                           WHERE project_id = ? AND responsible_official IS NOT NULL
                           AND responsible_official != ''""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-INV-2"] = "satisfied"
                except Exception:
                    pass

                # M25-CLS-1: High-impact classification
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_use_case_inventory
                           WHERE project_id = ? AND risk_level IS NOT NULL
                           AND risk_level != ''""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-CLS-1"] = "satisfied"
                except Exception:
                    pass

                # M25-RISK-1: Risk assessment (check NIST AI RMF or ATLAS)
                try:
                    for table in ["nist_ai_rmf_assessments", "atlas_assessments"]:
                        rows = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()
                        if rows and rows["cnt"] > 0:
                            results["M25-RISK-1"] = "satisfied"
                            break
                except Exception:
                    pass

                # M25-RISK-3: Continuous monitoring (check ai_telemetry)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_telemetry
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-RISK-3"] = "satisfied"
                except Exception:
                    pass

                # M25-OVR-1: Human oversight plan exists (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_oversight_plans
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-OVR-1"] = "satisfied"
                except Exception:
                    pass

                # M25-OVR-3: Appeal process registered (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_accountability_appeals
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-OVR-3"] = "satisfied"
                except Exception:
                    pass

                # M25-OVR-4: CAIO/Responsible official designated (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_caio_registry
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-OVR-4"] = "satisfied"
                except Exception:
                    pass

                # M25-INV-3: Reassessment schedule defined (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_reassessment_schedule
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-INV-3"] = "satisfied"
                except Exception:
                    pass

                # M25-RISK-4: AI incident response process (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_incident_log
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M25-RISK-4"] = "satisfied"
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

            # M25-OVR-2: Transparency notices
            for md_file in project_path.rglob("*.md"):
                try:
                    content = md_file.read_text(encoding="utf-8", errors="ignore").lower()
                    if "transparency" in content and ("notice" in content or "ai" in content):
                        results["M25-OVR-2"] = "satisfied"
                        break
                except Exception:
                    continue

            # M25-OVR-3: Appeal process
            for f in project_path.rglob("*"):
                if f.is_file() and f.suffix in (".md", ".yaml", ".json", ".py"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="ignore").lower()
                        if "appeal" in content and ("process" in content or "redress" in content):
                            results["M25-OVR-3"] = "satisfied"
                            break
                    except Exception:
                        continue

        return results


if __name__ == "__main__":
    OMBM2521Assessor().run_cli()
