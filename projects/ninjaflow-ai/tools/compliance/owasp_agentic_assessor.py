#!/usr/bin/env python3
# CUI // SP-CTI
"""OWASP Agentic AI Security Assessor — BaseAssessor subclass for all 8 gaps (D264).

Automated compliance checks for OWASP Agentic AI threats T01-T17,
verifying behavioral drift detection, tool chain validation, output
safety, threat model, trust scoring, MCP RBAC, behavioral red teaming,
and NIST crosswalk coverage.

Pattern: tools/compliance/atlas_assessor.py (BaseAssessor subclass)
ADRs: D264 (assessor via BaseAssessor pattern), D116 (BaseAssessor ABC)

CLI:
    python tools/compliance/owasp_agentic_assessor.py --project-id proj-123 --json
    python tools/compliance/owasp_agentic_assessor.py --project-id proj-123 --gate
"""

import sys
from pathlib import Path
from typing import Dict, Optional

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.compliance.base_assessor import BaseAssessor

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class OWASPAgenticAssessor(BaseAssessor):
    """OWASP Agentic AI Security assessor — 17 automated checks (D264)."""

    FRAMEWORK_ID = "owasp_agentic"
    FRAMEWORK_NAME = "OWASP Agentic AI Security v1.0"
    TABLE_NAME = "owasp_agentic_assessments"
    CATALOG_FILENAME = "owasp_agentic_threats.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """Run automated checks for all 17 OWASP Agentic threats.

        Maps T01-T17 to file existence and configuration checks.
        """
        checks = {}

        # --- Gap 1: Behavioral Drift Detection (T01, T07, T13) ---
        drift_check = self._check_behavioral_drift()
        checks["T01"] = drift_check
        checks["T07"] = drift_check  # Misaligned behaviors → drift detection
        checks["T13"] = drift_check  # Rogue agents → behavioral monitoring

        # --- Gap 2: Tool Chain Validation (T02, T11, T16) ---
        chain_check = self._check_tool_chain()
        checks["T02"] = chain_check  # Tool misuse
        checks["T11"] = chain_check  # RCE via tool chain
        checks["T16"] = chain_check  # Inter-agent protocol abuse

        # --- Gap 3: Output Content Safety (T05, T12) ---
        output_check = self._check_output_safety()
        checks["T05"] = output_check  # Cascading hallucinations
        checks["T12"] = output_check  # Communication poisoning

        # --- Gap 4: Formal Threat Model (T04, T15) ---
        threat_check = self._check_threat_model()
        checks["T04"] = threat_check  # Resource overload → threat model
        checks["T15"] = threat_check  # Human manipulation → threat model

        # --- Gap 5: Trust Scoring (T03, T09) ---
        trust_check = self._check_trust_scoring()
        checks["T03"] = trust_check  # Privilege compromise
        checks["T09"] = trust_check  # Identity spoofing

        # --- Gap 6: MCP RBAC (T06, T14) ---
        rbac_check = self._check_mcp_rbac()
        checks["T06"] = rbac_check   # Intent breaking / prompt injection
        checks["T14"] = rbac_check   # Human attacks on MAS

        # --- Gap 7: Behavioral Red Teaming (T10) ---
        checks["T10"] = self._check_behavioral_red_team()  # HITL overwhelming

        # --- Gap 8: NIST Crosswalk (T08, T17) ---
        crosswalk_check = self._check_nist_crosswalk()
        checks["T08"] = crosswalk_check  # Repudiation
        checks["T17"] = crosswalk_check  # Supply chain compromise

        return checks

    # --- Individual gap checks ---

    def _check_behavioral_drift(self) -> str:
        """Gap 1: Verify behavioral drift detection is configured and active."""
        telemetry = BASE_DIR / "tools" / "security" / "ai_telemetry_logger.py"
        if not telemetry.exists():
            return "not_satisfied"
        content = telemetry.read_text(encoding="utf-8", errors="ignore")
        if "detect_behavioral_drift" not in content:
            return "partially_satisfied"

        config = BASE_DIR / "args" / "owasp_agentic_config.yaml"
        if not config.exists():
            return "partially_satisfied"
        cfg_content = config.read_text(encoding="utf-8", errors="ignore")
        if "behavioral_drift" not in cfg_content:
            return "partially_satisfied"

        return "satisfied"

    def _check_tool_chain(self) -> str:
        """Gap 2: Verify tool chain validator exists with rules."""
        tcv = BASE_DIR / "tools" / "security" / "tool_chain_validator.py"
        if not tcv.exists():
            return "not_satisfied"

        config = BASE_DIR / "args" / "owasp_agentic_config.yaml"
        if not config.exists():
            return "partially_satisfied"
        cfg_content = config.read_text(encoding="utf-8", errors="ignore")
        if "TC-001" not in cfg_content:
            return "partially_satisfied"

        return "satisfied"

    def _check_output_safety(self) -> str:
        """Gap 3: Verify output validator exists with classification patterns."""
        ov = BASE_DIR / "tools" / "security" / "agent_output_validator.py"
        if not ov.exists():
            return "not_satisfied"

        config = BASE_DIR / "args" / "owasp_agentic_config.yaml"
        if not config.exists():
            return "partially_satisfied"
        cfg_content = config.read_text(encoding="utf-8", errors="ignore")
        if "output_validation" not in cfg_content:
            return "partially_satisfied"

        return "satisfied"

    def _check_threat_model(self) -> str:
        """Gap 4: Verify formal agentic threat model exists."""
        threat_model = BASE_DIR / "goals" / "agentic_threat_model.md"
        if not threat_model.exists():
            return "not_satisfied"
        content = threat_model.read_text(encoding="utf-8", errors="ignore")
        if "STRIDE" not in content and "T01" not in content:
            return "partially_satisfied"
        return "satisfied"

    def _check_trust_scoring(self) -> str:
        """Gap 5: Verify trust scoring exists with configured thresholds."""
        scorer = BASE_DIR / "tools" / "security" / "agent_trust_scorer.py"
        if not scorer.exists():
            return "not_satisfied"

        config = BASE_DIR / "args" / "owasp_agentic_config.yaml"
        if not config.exists():
            return "partially_satisfied"
        cfg_content = config.read_text(encoding="utf-8", errors="ignore")
        if "trust_scoring" not in cfg_content:
            return "partially_satisfied"

        return "satisfied"

    def _check_mcp_rbac(self) -> str:
        """Gap 6: Verify MCP per-tool RBAC is configured."""
        authorizer = BASE_DIR / "tools" / "security" / "mcp_tool_authorizer.py"
        if not authorizer.exists():
            return "not_satisfied"

        config = BASE_DIR / "args" / "owasp_agentic_config.yaml"
        if not config.exists():
            return "partially_satisfied"
        cfg_content = config.read_text(encoding="utf-8", errors="ignore")
        if "mcp_authorization" not in cfg_content:
            return "partially_satisfied"
        if "role_tool_matrix" not in cfg_content:
            return "partially_satisfied"

        return "satisfied"

    def _check_behavioral_red_team(self) -> str:
        """Gap 7: Verify behavioral red teaming is available."""
        scanner = BASE_DIR / "tools" / "security" / "atlas_red_team.py"
        if not scanner.exists():
            return "not_satisfied"
        content = scanner.read_text(encoding="utf-8", errors="ignore")
        if "BEHAVIORAL_TECHNIQUES" not in content:
            return "not_satisfied"
        if "BRT-001" not in content:
            return "partially_satisfied"
        return "satisfied"

    def _check_nist_crosswalk(self) -> str:
        """Gap 8: Verify OWASP threats have NIST 800-53 crosswalk mapping."""
        catalog_path = BASE_DIR / "context" / "compliance" / self.CATALOG_FILENAME
        if not catalog_path.exists():
            return "not_satisfied"

        import json
        try:
            with open(catalog_path) as f:
                data = json.load(f)
            reqs = data.get("requirements", [])
            if not reqs:
                return "not_satisfied"
            # Check that at least 80% have crosswalk entries
            with_crosswalk = sum(
                1 for r in reqs
                if r.get("nist_800_53_crosswalk") and len(r["nist_800_53_crosswalk"]) > 0
            )
            coverage = with_crosswalk / len(reqs) if reqs else 0
            if coverage >= 0.8:
                return "satisfied"
            elif coverage >= 0.5:
                return "partially_satisfied"
            return "not_satisfied"
        except Exception:
            return "not_satisfied"


def main():
    assessor = OWASPAgenticAssessor()
    assessor.run_cli()


if __name__ == "__main__":
    main()
