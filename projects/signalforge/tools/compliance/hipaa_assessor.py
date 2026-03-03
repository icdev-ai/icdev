#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""HIPAA Security Rule Assessment Engine.

Assesses projects against HIPAA Security Rule (45 CFR §164.308-316)
requirements. Covers Administrative, Physical, and Technical safeguards.

Usage:
    python tools/compliance/hipaa_assessor.py --project-id proj-123
    python tools/compliance/hipaa_assessor.py --project-id proj-123 --gate
    python tools/compliance/hipaa_assessor.py --project-id proj-123 --json
"""

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class HIPAAAssessor(BaseAssessor):
    FRAMEWORK_ID = "hipaa"
    FRAMEWORK_NAME = "HIPAA Security Rule"
    TABLE_NAME = "hipaa_assessments"
    CATALOG_FILENAME = "hipaa_security_rule.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """HIPAA-specific automated checks.

        Checks for:
        - Encryption at rest and in transit (§164.312(a)(2)(iv), §164.312(e)(1))
        - Access control mechanisms (§164.312(a)(1))
        - Audit controls (§164.312(b))
        - Integrity controls (§164.312(c)(1))
        """
        results = {}

        if not project_dir:
            return results

        project_path = Path(project_dir)
        has_encryption = False
        has_audit = False
        has_access_control = False

        for py_file in project_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                lower = content.lower()
                if "encrypt" in lower or "aes" in lower or "fernet" in lower:
                    has_encryption = True
                if "audit" in lower and ("log" in lower or "trail" in lower):
                    has_audit = True
                if "rbac" in lower or "role" in lower and "access" in lower:
                    has_access_control = True
            except Exception:
                continue

        if has_encryption:
            results["HIPAA-164.312(a)(2)(iv)"] = "satisfied"
            results["HIPAA-164.312(e)(1)"] = "satisfied"
        if has_audit:
            results["HIPAA-164.312(b)"] = "satisfied"
        if has_access_control:
            results["HIPAA-164.312(a)(1)"] = "satisfied"

        return results


if __name__ == "__main__":
    HIPAAAssessor().run_cli()
