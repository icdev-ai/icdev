#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""OMB M-26-04 Unbiased AI Assessor.

Assesses compliance with OMB Memorandum M-26-04 — Advancing Unbiased
and Transparent Artificial Intelligence in the Federal Government.
Covers: model cards, system cards, bias testing, fairness metrics,
disparity analysis, human review, appeal processes, and impact assessment.

Pattern: tools/compliance/base_assessor.py (BaseAssessor ABC).
ADR D307: All Phase 48 assessors use BaseAssessor.

Usage:
    python tools/compliance/omb_m26_04_assessor.py --project-id proj-123
    python tools/compliance/omb_m26_04_assessor.py --project-id proj-123 --gate
    python tools/compliance/omb_m26_04_assessor.py --project-id proj-123 --json
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


class OMBM2604Assessor(BaseAssessor):
    FRAMEWORK_ID = "omb_m26_04"
    FRAMEWORK_NAME = "OMB M-26-04 (Unbiased AI)"
    TABLE_NAME = "omb_m26_04_assessments"
    CATALOG_FILENAME = "omb_m26_04_unbiased_ai.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """OMB M-26-04 automated checks.

        Checks for:
        - M26-DOC-1: Model cards present
        - M26-DOC-2: System cards present
        - M26-BIAS-1: Bias testing conducted
        - M26-BIAS-2: Fairness metrics defined
        - M26-REV-1: Human review process
        - M26-REV-2: Appeal process registered (Phase 49)
        - M26-REV-3: Opt-out policy documented (Phase 49)
        - M26-REV-4: Explainability (SHAP/XAI)
        - M26-IMP-1: Impact assessment conducted (Phase 49)
        """
        results = {}
        conn = None

        try:
            if self.db_path.exists():
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                project_id = project.get("id", "")

                # M26-DOC-1: Model cards
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM model_cards
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M26-DOC-1"] = "satisfied"
                except Exception:
                    pass

                # M26-DOC-2: System cards
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM system_cards
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M26-DOC-2"] = "satisfied"
                except Exception:
                    pass

                # M26-BIAS-1, M26-BIAS-2: Fairness assessments
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM fairness_assessments
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M26-BIAS-1"] = "satisfied"
                        results["M26-BIAS-2"] = "satisfied"
                except Exception:
                    pass

                # M26-REV-4: Explainability via XAI/SHAP
                try:
                    for table in ["xai_assessments", "shap_attributions"]:
                        rows = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()
                        if rows and rows["cnt"] > 0:
                            results["M26-REV-4"] = "satisfied"
                            break
                except Exception:
                    pass

                # M26-REV-2: Appeal process registered (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_accountability_appeals
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M26-REV-2"] = "satisfied"
                except Exception:
                    pass

                # M26-REV-3: Opt-out policy documented (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_ethics_reviews
                           WHERE project_id = ? AND opt_out_policy = 1""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M26-REV-3"] = "satisfied"
                except Exception:
                    pass

                # M26-IMP-1: Impact assessment conducted (Phase 49)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_ethics_reviews
                           WHERE project_id = ? AND review_type = 'impact_assessment'""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["M26-IMP-1"] = "satisfied"
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

            # M26-REV-1: Human review process
            for f in project_path.rglob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore").lower()
                    if "human review" in content or "human-in-the-loop" in content:
                        results["M26-REV-1"] = "satisfied"
                        break
                except Exception:
                    continue

            # M26-REV-2: Appeal process
            for f in project_path.rglob("*"):
                if f.is_file() and f.suffix in (".md", ".yaml", ".json"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="ignore").lower()
                        if "appeal" in content and ("process" in content or "mechanism" in content):
                            results["M26-REV-2"] = "satisfied"
                            break
                    except Exception:
                        continue

        return results


if __name__ == "__main__":
    OMBM2604Assessor().run_cli()
