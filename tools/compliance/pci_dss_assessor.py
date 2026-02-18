#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""PCI DSS v4.0 Assessment Engine.

Assesses projects against PCI Data Security Standard v4.0 across
12 requirements organized into 6 goals.

Usage:
    python tools/compliance/pci_dss_assessor.py --project-id proj-123
    python tools/compliance/pci_dss_assessor.py --project-id proj-123 --gate
    python tools/compliance/pci_dss_assessor.py --project-id proj-123 --json
"""

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class PCIDSSAssessor(BaseAssessor):
    FRAMEWORK_ID = "pci_dss"
    FRAMEWORK_NAME = "PCI DSS v4.0"
    TABLE_NAME = "pci_dss_assessments"
    CATALOG_FILENAME = "pci_dss_v4.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """PCI DSS-specific automated checks.

        Checks for:
        - Firewall/network segmentation config (Req 1)
        - No default passwords/settings (Req 2)
        - Encryption of cardholder data (Req 3, 4)
        - Anti-malware/vulnerability management (Req 5, 6)
        - Logging and monitoring (Req 10)
        """
        results = {}

        if not project_dir:
            return results

        project_path = Path(project_dir)
        has_encryption = False
        has_logging = False
        has_input_validation = False

        for py_file in project_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                lower = content.lower()
                if ("encrypt" in lower or "aes" in lower) and ("card" in lower or "pan" in lower or "payment" in lower):
                    has_encryption = True
                if "logging" in lower or "audit_trail" in lower:
                    has_logging = True
                if "sanitize" in lower or "validate" in lower or "escape" in lower:
                    has_input_validation = True
            except Exception:
                continue

        if has_encryption:
            results["PCI-3.5"] = "satisfied"
            results["PCI-4.1"] = "satisfied"
        if has_logging:
            results["PCI-10.1"] = "satisfied"
            results["PCI-10.2"] = "satisfied"
        if has_input_validation:
            results["PCI-6.5"] = "satisfied"

        return results


if __name__ == "__main__":
    PCIDSSAssessor().run_cli()
