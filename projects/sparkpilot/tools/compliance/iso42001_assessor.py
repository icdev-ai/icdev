#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ISO/IEC 42001:2023 Assessor — AI Management System assessment.

Bridged through ISO 27001 international hub (D111).
Pattern: tools/compliance/base_assessor.py (BaseAssessor ABC).

Usage:
    python tools/compliance/iso42001_assessor.py --project-id proj-123
    python tools/compliance/iso42001_assessor.py --project-id proj-123 --gate
    python tools/compliance/iso42001_assessor.py --project-id proj-123 --json
"""

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class ISO42001Assessor(BaseAssessor):
    FRAMEWORK_ID = "iso_42001"
    FRAMEWORK_NAME = "ISO/IEC 42001:2023"
    TABLE_NAME = "iso42001_assessments"
    CATALOG_FILENAME = "iso42001_controls.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """ISO 42001 automated checks bridged through ISO 27001.

        Checks for:
        - 5.1: AI leadership commitment (documented AI policy exists)
        - 6.1: AI risk assessment (risk docs or ATLAS assessment)
        - 7.1: AI resources (LLM config exists)
        - 8.1: AI operational planning (security gates configured)
        - 9.1: AI monitoring (telemetry or monitoring active)
        - 10.1: AI improvement (innovation engine or feedback signals)
        - A.2: AI impact assessment (FIPS 199 or risk assessment docs)
        - A.6: AI data management (classification_manager usage)
        """
        results = {}

        if not project_dir:
            return results

        project_path = Path(project_dir)
        has_ai_policy = False
        has_risk_assessment = False
        has_llm_config = False
        has_security_gates = False
        has_monitoring = False
        has_improvement = False
        has_classification = False

        # Check YAML configs for AI policy, security gates, LLM config
        for yaml_file in project_path.rglob("*.yaml"):
            try:
                name = yaml_file.name.lower()
                content = yaml_file.read_text(encoding="utf-8", errors="ignore")
                lower = content.lower()

                if "llm" in name or "model" in name:
                    has_llm_config = True
                if "security_gates" in name or "gate" in name:
                    has_security_gates = True
                if "ai" in lower and ("policy" in lower or "governance" in lower):
                    has_ai_policy = True
                if "monitor" in lower and ("ai" in lower or "model" in lower):
                    has_monitoring = True
                if "innovation" in name or ("improve" in lower and "ai" in lower):
                    has_improvement = True
            except Exception:
                continue

        # Check Python files for classification manager and risk assessment
        for py_file in project_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                lower = content.lower()
                if "classification" in lower and ("manager" in lower or "classif" in lower):
                    has_classification = True
                if "risk" in lower and ("assess" in lower or "register" in lower):
                    has_risk_assessment = True
            except Exception:
                continue

        if has_ai_policy:
            results["ISO42001-5.1"] = "satisfied"
            results["ISO42001-5.2"] = "satisfied"
        if has_risk_assessment:
            results["ISO42001-6.1"] = "satisfied"
            results["ISO42001-A.2"] = "satisfied"
        if has_llm_config:
            results["ISO42001-7.1"] = "satisfied"
        if has_security_gates:
            results["ISO42001-8.1"] = "satisfied"
        if has_monitoring:
            results["ISO42001-9.1"] = "satisfied"
        if has_improvement:
            results["ISO42001-10.1"] = "satisfied"
        if has_classification:
            results["ISO42001-A.6"] = "satisfied"

        return results


if __name__ == "__main__":
    ISO42001Assessor().run_cli()
