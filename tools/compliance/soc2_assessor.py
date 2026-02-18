#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""SOC 2 Type II Trust Service Criteria Assessment Engine.

Assesses projects against AICPA SOC 2 Trust Service Criteria across
5 categories: Security, Availability, Processing Integrity,
Confidentiality, and Privacy.

Usage:
    python tools/compliance/soc2_assessor.py --project-id proj-123
    python tools/compliance/soc2_assessor.py --project-id proj-123 --gate
    python tools/compliance/soc2_assessor.py --project-id proj-123 --json
"""

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class SOC2Assessor(BaseAssessor):
    FRAMEWORK_ID = "soc2"
    FRAMEWORK_NAME = "SOC 2 Type II (Trust Service Criteria)"
    TABLE_NAME = "soc2_assessments"
    CATALOG_FILENAME = "soc2_trust_criteria.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """SOC 2-specific automated checks.

        Checks for:
        - Logical access controls (CC6.1-CC6.8)
        - Change management processes (CC8.1)
        - System monitoring (CC7.1-CC7.5)
        """
        results = {}

        if not project_dir:
            return results

        project_path = Path(project_dir)
        has_auth = False
        has_monitoring = False
        has_change_mgmt = False

        for py_file in project_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                lower = content.lower()
                if "authentication" in lower or "authorize" in lower:
                    has_auth = True
                if "monitor" in lower or "alert" in lower or "health_check" in lower:
                    has_monitoring = True
                if "version" in lower and "migration" in lower:
                    has_change_mgmt = True
            except Exception:
                continue

        if has_auth:
            results["CC6.1"] = "satisfied"
        if has_monitoring:
            results["CC7.2"] = "satisfied"
        if has_change_mgmt:
            results["CC8.1"] = "satisfied"

        return results


if __name__ == "__main__":
    SOC2Assessor().run_cli()
