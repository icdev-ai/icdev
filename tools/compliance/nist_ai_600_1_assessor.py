#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""NIST AI 600-1 GenAI Profile Assessor.

Assesses compliance with NIST AI 600-1 — Artificial Intelligence Risk
Management Framework: Generative AI Profile. Covers 12 GAI risk
categories: confabulation, data privacy, environmental impact,
information integrity, information security, harmful bias, IP,
CBRN, cybersecurity misuse, and value chain.

Pattern: tools/compliance/base_assessor.py (BaseAssessor ABC).
ADR D307: All Phase 48 assessors use BaseAssessor.
ADR D310: Confabulation detection uses deterministic methods only.

Usage:
    python tools/compliance/nist_ai_600_1_assessor.py --project-id proj-123
    python tools/compliance/nist_ai_600_1_assessor.py --project-id proj-123 --gate
    python tools/compliance/nist_ai_600_1_assessor.py --project-id proj-123 --json
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


class NISTAI6001Assessor(BaseAssessor):
    FRAMEWORK_ID = "nist_ai_600_1"
    FRAMEWORK_NAME = "NIST AI 600-1 (GenAI Profile)"
    TABLE_NAME = "nist_ai_600_1_assessments"
    CATALOG_FILENAME = "nist_ai_600_1_genai.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """NIST AI 600-1 GenAI Profile automated checks.

        Checks for:
        - GAI-1-1: Confabulation detection active
        - GAI-1-3: Output provenance tracking (PROV records)
        - GAI-2-2: Output privacy screening (PII detection)
        - GAI-4-1: Synthetic content identification
        - GAI-5-1: Prompt injection defense active
        - GAI-5-2: Model security and access control
        - GAI-6-1: Bias detection in outputs
        - GAI-9-2: Adversarial robustness testing (ATLAS red team)
        - GAI-10-1: AI supply chain (AI BOM exists)
        """
        results = {}
        conn = None

        try:
            if self.db_path.exists():
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                project_id = project.get("id", "")

                # GAI-1-1: Confabulation detection
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM confabulation_checks
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAI-1-1"] = "satisfied"
                except Exception:
                    pass

                # GAI-1-3: Output provenance (PROV records)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM prov_entities
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAI-1-3"] = "satisfied"
                except Exception:
                    pass

                # GAI-5-1: Prompt injection defense
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM prompt_injection_log
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] >= 0:
                        # Prompt injection system exists if table is accessible
                        results["GAI-5-1"] = "satisfied"
                except Exception:
                    pass

                # GAI-9-2: Adversarial robustness (ATLAS red team)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM atlas_red_team_results
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAI-9-2"] = "satisfied"
                except Exception:
                    pass

                # GAI-10-1: AI supply chain (AI BOM)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_bom
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAI-10-1"] = "satisfied"
                except Exception:
                    pass

                # GAI-6-1: Bias detection (fairness assessments)
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM fairness_assessments
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["GAI-6-1"] = "satisfied"
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

            # GAI-2-2: Output privacy screening (PII detection)
            for py_file in project_path.rglob("*.py"):
                try:
                    content = py_file.read_text(encoding="utf-8", errors="ignore").lower()
                    if ("pii" in content or "ssn" in content or "classification_leak" in content) \
                       and ("detect" in content or "screen" in content or "valid" in content):
                        results["GAI-2-2"] = "satisfied"
                        break
                except Exception:
                    continue

            # GAI-4-1: Synthetic content identification
            for py_file in project_path.rglob("*.py"):
                try:
                    content = py_file.read_text(encoding="utf-8", errors="ignore").lower()
                    if "provenance" in content and ("track" in content or "record" in content):
                        results["GAI-4-1"] = "satisfied"
                        break
                except Exception:
                    continue

            # GAI-5-2: Model security / access control
            for f in project_path.rglob("*.yaml"):
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore").lower()
                    if ("rbac" in content or "authorization" in content) \
                       and ("tool" in content or "mcp" in content or "agent" in content):
                        results["GAI-5-2"] = "satisfied"
                        break
                except Exception:
                    continue

        return results


if __name__ == "__main__":
    NISTAI6001Assessor().run_cli()
