#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""CMMI for Development (CMMI-DEV) Level 3 Assessment Engine.

Assesses projects against CMMI-DEV v1.3 Level 3 (Defined) requirements.
Covers all 18 Process Areas (7 Level 2 + 11 Level 3) and their
Specific Practices. Automated checks probe the project for evidence of
configuration management, testing, documentation, risk tracking, and
requirements management artifacts.

Usage:
    python tools/compliance/cmmi_level3_assessor.py --project-id proj-123
    python tools/compliance/cmmi_level3_assessor.py --project-id proj-123 --gate
    python tools/compliance/cmmi_level3_assessor.py --project-id proj-123 --json
    python tools/compliance/cmmi_level3_assessor.py --project-id proj-123 --export all
"""

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class CMMILevel3Assessor(BaseAssessor):
    FRAMEWORK_ID = "cmmi_level3"
    FRAMEWORK_NAME = "CMMI for Development (CMMI-DEV) v1.3 — Level 3"
    TABLE_NAME = "cmmi_level3_assessments"
    CATALOG_FILENAME = "cmmi_level3_practices.json"

    # CMMI uses "Satisfied/Partially Satisfied/Not Satisfied" terminology
    STATUS_VALUES = (
        "not_assessed",
        "satisfied",
        "partially_satisfied",
        "not_satisfied",
        "not_applicable",
        "risk_accepted",
    )

    # Map project-dir signals to specific practice IDs that can be auto-detected
    _CM_INDICATORS = {
        "git": ["CM-SP1.1", "CM-SP1.2", "CM-SP2.2"],
        "requirements.txt": ["SAM-SP1.1"],
        "CHANGELOG": ["CM-SP1.3"],
        "CHANGELOG.md": ["CM-SP1.3"],
        ".gitignore": ["CM-SP1.2"],
    }

    def get_automated_checks(
        self,
        project: Dict,
        project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """CMMI-DEV Level 3 automated checks.

        Probes for artifacts that provide evidence of each process area:
        - CM: git repository, version control markers
        - PP/PMC: project plan docs, risk register files
        - REQM/RD: requirements docs, SRS files, traceability artifacts
        - TS: design documentation, architecture files
        - VER/VAL: test directories, test plans, test result artifacts
        - RSKM: risk register, RSKM plan files
        - OT: training records, training plan docs
        - MA: measurement data files, metrics reports
        - PPQA: audit records, QA plan
        - OPF/OPD: process documentation, standard process library
        """
        results: Dict[str, str] = {}

        if not project_dir:
            return results

        project_path = Path(project_dir)
        if not project_path.exists():
            return results

        # Collect all file names and content snippets for signal detection
        all_files = list(project_path.rglob("*"))
        file_names_lower = {f.name.lower() for f in all_files if f.is_file()}
        dir_names_lower = {f.name.lower() for f in all_files if f.is_dir()}

        def _has_file(*patterns: str) -> bool:
            return any(
                any(p in name for p in patterns)
                for name in file_names_lower
            )

        def _has_dir(*patterns: str) -> bool:
            return any(
                any(p in name for p in patterns)
                for name in dir_names_lower
            )

        def _file_contains(filename_pattern: str, *keywords: str) -> bool:
            for f in all_files:
                if f.is_file() and filename_pattern.lower() in f.name.lower():
                    try:
                        content = f.read_text(encoding="utf-8", errors="ignore").lower()
                        if all(kw.lower() in content for kw in keywords):
                            return True
                    except Exception:
                        continue
            return False

        def _any_py_contains(*keywords: str) -> bool:
            for f in project_path.rglob("*.py"):
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore").lower()
                    if all(kw in content for kw in keywords):
                        return True
                except Exception:
                    continue
            return False

        # ------------------------------------------------------------------
        # Configuration Management (CM) — git repo presence
        # ------------------------------------------------------------------
        has_git = (project_path / ".git").exists() or _has_dir(".git")
        if has_git:
            results["CM-SP1.1"] = "satisfied"
            results["CM-SP1.2"] = "satisfied"
            results["CM-SP2.2"] = "satisfied"

        has_changelog = _has_file("changelog", "release_notes", "history")
        if has_changelog:
            results["CM-SP1.3"] = "satisfied"

        has_cm_audit = _has_file("audit", "cm_audit", "config_audit")
        if has_cm_audit:
            results["CM-SP3.2"] = "satisfied"

        # ------------------------------------------------------------------
        # Requirements Management (REQM) — requirements artifacts
        # ------------------------------------------------------------------
        has_requirements = _has_file("requirements.md", "srs", "req_spec", "user_stories")
        if has_requirements:
            results["REQM-SP1.1"] = "satisfied"
            results["REQM-SP1.2"] = "satisfied"
            results["RD-SP1.1"] = "satisfied"
            results["RD-SP1.2"] = "satisfied"

        has_traceability = _has_file("traceability", "rtm", "requirements_matrix")
        if has_traceability:
            results["REQM-SP1.4"] = "satisfied"
            results["RD-SP2.2"] = "satisfied"

        has_srs = _has_file("srs", "requirements_spec", "system_requirements")
        if has_srs:
            results["RD-SP2.1"] = "satisfied"
            results["RD-SP3.3"] = "satisfied"

        # ------------------------------------------------------------------
        # Project Planning (PP) — planning docs
        # ------------------------------------------------------------------
        has_project_plan = _has_file("project_plan", "project-plan", "wbs", "work_breakdown")
        if has_project_plan:
            results["PP-SP1.1"] = "satisfied"
            results["PP-SP2.1"] = "satisfied"
            results["PP-SP3.3"] = "satisfied"
            results["IPM-SP1.1"] = "partially_satisfied"

        has_schedule = _has_file("schedule", "milestone", "gantt", "sprint_plan")
        if has_schedule:
            results["PP-SP2.1"] = "satisfied"
            results["IPM-SP1.4"] = "partially_satisfied"

        # ------------------------------------------------------------------
        # Risk Management (RSKM) — risk register
        # ------------------------------------------------------------------
        has_risk_register = _has_file("risk_register", "risk-register", "risk_log", "risks.md", "risks.csv")
        if has_risk_register:
            results["RSKM-SP1.3"] = "satisfied"
            results["RSKM-SP2.1"] = "satisfied"
            results["PP-SP2.2"] = "satisfied"
            results["PMC-SP1.3"] = "satisfied"

        has_risk_mitigation = _has_file("risk_mitigation", "mitigation_plan", "risk_response")
        if has_risk_mitigation:
            results["RSKM-SP3.1"] = "satisfied"
            results["RSKM-SP3.2"] = "partially_satisfied"

        # ------------------------------------------------------------------
        # Verification (VER) / Validation (VAL) — test artifacts
        # ------------------------------------------------------------------
        has_tests = _has_dir("test", "tests", "spec", "specs") or _has_file("test_", "_test.py", "spec.py")
        if has_tests:
            results["VER-SP1.1"] = "satisfied"
            results["VER-SP3.1"] = "satisfied"
            results["VAL-SP1.1"] = "satisfied"
            results["VAL-SP2.1"] = "partially_satisfied"

        has_test_plan = _has_file("test_plan", "test-plan", "test_strategy", "testing_plan")
        if has_test_plan:
            results["VER-SP1.3"] = "satisfied"
            results["VAL-SP1.3"] = "satisfied"

        has_peer_review = _has_file("peer_review", "code_review", "review_checklist", "pr_template")
        if has_peer_review:
            results["VER-SP2.1"] = "satisfied"
            results["VER-SP2.2"] = "satisfied"

        # ------------------------------------------------------------------
        # Technical Solution (TS) — design documentation
        # ------------------------------------------------------------------
        has_design = _has_file("design", "architecture", "hld", "lld", "design_doc")
        if has_design:
            results["TS-SP1.2"] = "satisfied"
            results["TS-SP1.1"] = "partially_satisfied"

        has_interface_spec = _has_file("icd", "interface_spec", "api_spec", "interface_control")
        if has_interface_spec:
            results["TS-SP1.3"] = "satisfied"
            results["PI-SP2.1"] = "satisfied"
            results["RD-SP2.2"] = "partially_satisfied"

        has_user_docs = _has_file("readme", "user_manual", "user_guide", "operations_guide")
        if has_user_docs:
            results["TS-SP2.2"] = "satisfied"

        # ------------------------------------------------------------------
        # Product Integration (PI) — build/CI artifacts
        # ------------------------------------------------------------------
        has_ci = _has_file(".gitlab-ci.yml", ".github", "jenkinsfile", "pipeline.yml", "ci.yml")
        if has_ci:
            results["PI-SP3.2"] = "satisfied"
            results["PI-SP1.2"] = "satisfied"

        has_integration_plan = _has_file("integration_plan", "integration_sequence", "int_plan")
        if has_integration_plan:
            results["PI-SP1.1"] = "satisfied"

        # ------------------------------------------------------------------
        # Measurement and Analysis (MA) — metrics/reporting
        # ------------------------------------------------------------------
        has_metrics = _has_file("metrics", "measurement", "kpi", "dashboard")
        if has_metrics:
            results["MA-SP1.1"] = "satisfied"
            results["MA-SP2.1"] = "satisfied"
            results["MA-SP2.2"] = "partially_satisfied"

        # ------------------------------------------------------------------
        # PPQA — quality assurance artifacts
        # ------------------------------------------------------------------
        has_qa = _has_file("qa_plan", "quality_plan", "ppqa", "process_audit")
        if has_qa:
            results["PPQA-SP1.1"] = "satisfied"
            results["PPQA-SP1.2"] = "partially_satisfied"

        # ------------------------------------------------------------------
        # Organizational Training (OT) — training records
        # ------------------------------------------------------------------
        has_training = _has_file("training_plan", "training_records", "training_matrix", "onboarding")
        if has_training:
            results["OT-SP1.1"] = "satisfied"
            results["OT-SP1.3"] = "partially_satisfied"
            results["PP-SP2.5"] = "satisfied"

        # ------------------------------------------------------------------
        # OPD/OPF — process documentation
        # ------------------------------------------------------------------
        has_process_docs = _has_dir("process", "processes", "standards") or _has_file(
            "standard_process", "process_library", "process_assets"
        )
        if has_process_docs:
            results["OPD-SP1.1"] = "satisfied"
            results["OPD-SP1.2"] = "partially_satisfied"
            results["OPF-SP1.1"] = "partially_satisfied"

        # ------------------------------------------------------------------
        # Supplier Agreement (SAM) — vendor/supplier docs
        # ------------------------------------------------------------------
        has_supplier = _has_file("vendor", "supplier", "sow", "mou", "contract")
        if has_supplier:
            results["SAM-SP1.3"] = "satisfied"
            results["SAM-SP2.1"] = "partially_satisfied"

        # ------------------------------------------------------------------
        # Python code signals — audit logging, encryption
        # ------------------------------------------------------------------
        has_audit_code = _any_py_contains("audit_trail") or _any_py_contains("audit", "log")
        if has_audit_code:
            results["PPQA-SP2.2"] = "satisfied"
            results["CM-SP3.1"] = "satisfied"

        return results


if __name__ == "__main__":
    CMMILevel3Assessor().run_cli()
