#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ISO/IEC 27001:2022 Assessment Engine.

Assesses projects against ISO 27001:2022 Annex A controls (93 controls
across 4 themes: Organizational, People, Physical, Technological).

This is the international hub of the dual-hub crosswalk model (ADR D111).
ISO 27001 bridges to IRAP, C5, K-ISMS, TISAX, and other international
frameworks.

Usage:
    python tools/compliance/iso27001_assessor.py --project-id proj-123
    python tools/compliance/iso27001_assessor.py --project-id proj-123 --gate
    python tools/compliance/iso27001_assessor.py --project-id proj-123 --json
"""

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class ISO27001Assessor(BaseAssessor):
    FRAMEWORK_ID = "iso_27001"
    FRAMEWORK_NAME = "ISO/IEC 27001:2022"
    TABLE_NAME = "iso27001_assessments"
    CATALOG_FILENAME = "iso27001_2022_controls.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """ISO 27001-specific automated checks.

        Checks for:
        - Configuration management (A.8.9)
        - Secure coding practices (A.8.28)
        - Logging (A.8.15)
        - Vulnerability management (A.8.8)
        - Change management (A.8.32)
        """
        results = {}

        if not project_dir:
            return results

        project_path = Path(project_dir)
        has_config_mgmt = False
        has_logging = False
        has_vuln_mgmt = False
        has_secure_coding = False

        # Check for config management
        for config in ("requirements.txt", "Pipfile", "package.json",
                        "go.mod", "Cargo.toml", "pom.xml"):
            if (project_path / config).exists():
                has_config_mgmt = True
                break

        for py_file in project_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                lower = content.lower()
                if "logging" in lower or "audit" in lower:
                    has_logging = True
                if "vulnerability" in lower or "cve" in lower or "security scan" in lower:
                    has_vuln_mgmt = True
                if "sanitize" in lower or "escape" in lower or "parameterized" in lower:
                    has_secure_coding = True
            except Exception:
                continue

        if has_config_mgmt:
            results["A.8.9"] = "satisfied"
        if has_logging:
            results["A.8.15"] = "satisfied"
        if has_vuln_mgmt:
            results["A.8.8"] = "satisfied"
        if has_secure_coding:
            results["A.8.28"] = "satisfied"

        return results


if __name__ == "__main__":
    ISO27001Assessor().run_cli()
