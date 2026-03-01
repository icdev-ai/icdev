#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""DoD MOSA (10 U.S.C. section 4401) Assessment Engine.

Assesses projects against MOSA modular open systems approach requirements.
MOSA mandates open standards, modular architectures, and well-defined
interfaces for major defense acquisition programs.

Usage:
    python tools/compliance/mosa_assessor.py --project-id proj-123
    python tools/compliance/mosa_assessor.py --project-id proj-123 --gate
    python tools/compliance/mosa_assessor.py --project-id proj-123 --project-dir /path --json
"""

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class MOSAAssessor(BaseAssessor):
    FRAMEWORK_ID = "mosa"
    FRAMEWORK_NAME = "DoD MOSA (10 U.S.C. \u00a74401)"
    TABLE_NAME = "mosa_assessments"
    CATALOG_FILENAME = "mosa_framework.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """MOSA-specific automated checks.

        Scans for modularity and open-standards indicators:
        - OpenAPI/Swagger specs
        - Interface control directories
        - Dependency manifests
        - Interface/API test files
        - Abstract base classes (ABC/Protocol)
        - OpenAPI version headers
        - LICENSE/NOTICE files
        - README with architecture section
        """
        results = {}

        if not project_dir:
            return results

        pp = Path(project_dir)

        # OpenAPI / Swagger specs -> MOSA-STD-2
        specs = list(pp.rglob("openapi.yaml")) + list(pp.rglob("openapi.yml"))
        specs += list(pp.rglob("swagger.yaml")) + list(pp.rglob("swagger.yml"))
        specs += list(pp.rglob("openapi.json")) + list(pp.rglob("swagger.json"))
        if specs:
            results["MOSA-STD-2"] = "satisfied"

        # interfaces/ or icd/ directories -> MOSA-INT-1
        if (pp / "interfaces").is_dir() or (pp / "icd").is_dir():
            results["MOSA-INT-1"] = "partially_satisfied"

        # Dependency manifests -> MOSA-ARCH-5
        dep_files = ["requirements.txt", "package.json", "go.mod",
                      "Cargo.toml", "pyproject.toml"]
        if any((pp / f).exists() for f in dep_files) or list(pp.rglob("*.csproj")):
            results["MOSA-ARCH-5"] = "satisfied"

        # Interface / API test files -> MOSA-INT-4
        api_tests = list(pp.rglob("test_interface*")) + list(pp.rglob("test_api*"))
        api_tests += list(pp.rglob("*_integration_test*"))
        if api_tests:
            results["MOSA-INT-4"] = "partially_satisfied"

        # Abstract base classes (ABC / Protocol) -> MOSA-ARCH-1
        for py_file in pp.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if ("ABC" in content and "abstractmethod" in content) or "Protocol" in content:
                    results["MOSA-ARCH-1"] = "satisfied"
                    break
            except Exception:
                continue

        # OpenAPI version headers in YAML -> MOSA-INT-2
        for yf in list(pp.rglob("*.yaml")) + list(pp.rglob("*.yml")):
            try:
                content = yf.read_text(encoding="utf-8", errors="ignore")
                if "openapi:" in content.lower():
                    results["MOSA-INT-2"] = "partially_satisfied"
                    break
            except Exception:
                continue

        # LICENSE or NOTICE files -> MOSA-DR-3
        if (pp / "LICENSE").exists() or (pp / "NOTICE").exists():
            results["MOSA-DR-3"] = "partially_satisfied"

        # README with architecture section -> MOSA-ARCH-4
        for readme in [pp / "README.md", pp / "README.rst", pp / "README.txt"]:
            if readme.exists():
                try:
                    text = readme.read_text(encoding="utf-8", errors="ignore").lower()
                    if "architecture" in text:
                        results["MOSA-ARCH-4"] = "partially_satisfied"
                except Exception:
                    pass
                break

        return results


if __name__ == "__main__":
    MOSAAssessor().run_cli()
