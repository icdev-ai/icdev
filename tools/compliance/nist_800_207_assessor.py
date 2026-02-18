#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""NIST SP 800-207 (Zero Trust Architecture) Assessment Engine.

Assesses projects against NIST SP 800-207 ZTA requirements organized by
the 7 ZTA pillars plus architecture principles. Maps into the NIST 800-53
US hub (ADR D118) — ZTA is an architecture guide, not a standalone control
catalog, so its requirements crosswalk directly to 800-53 controls.

ADR D118: NIST 800-207 maps into existing NIST 800-53 US hub.
ADR D120: ZTA maturity model uses DoD 7-pillar scoring.
ADR D123: ZTA posture score feeds into cATO monitor.

Usage:
    python tools/compliance/nist_800_207_assessor.py --project-id proj-123
    python tools/compliance/nist_800_207_assessor.py --project-id proj-123 --gate
    python tools/compliance/nist_800_207_assessor.py --project-id proj-123 --json
    python tools/compliance/nist_800_207_assessor.py --project-id proj-123 --project-dir /path/to/code --json
"""

import sys
from pathlib import Path
from typing import Dict, Optional

# Ensure base module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor


class NIST800207Assessor(BaseAssessor):
    FRAMEWORK_ID = "nist_800_207"
    FRAMEWORK_NAME = "NIST SP 800-207 (Zero Trust Architecture)"
    TABLE_NAME = "nist_800_207_assessments"
    CATALOG_FILENAME = "nist_800_207_zta.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """ZTA-specific automated checks.

        Detects ZTA implementation indicators in project artifacts:
        - mTLS configurations (Istio PeerAuthentication, Linkerd Server)
        - NetworkPolicies (default-deny, micro-segmentation)
        - Service accounts and RBAC manifests
        - Audit logging configurations
        - Encryption settings (TLS 1.2+, FIPS)
        - Identity provider integration
        - Container security contexts (non-root, read-only rootfs)
        - SBOM and attestation configs
        """
        results = {}

        if not project_dir:
            return results

        project_path = Path(project_dir)

        # Scan YAML/JSON files for ZTA indicators
        yaml_files = list(project_path.rglob("*.yaml")) + list(project_path.rglob("*.yml"))
        py_files = list(project_path.rglob("*.py"))
        tf_files = list(project_path.rglob("*.tf"))

        has_mtls = False
        has_network_policy = False
        has_default_deny = False
        has_rbac = False
        has_audit_log = False
        has_encryption = False
        has_container_security = False
        has_service_mesh = False
        has_identity_provider = False
        has_sbom = False

        for f in yaml_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore").lower()

                # mTLS detection
                if "peerauthentication" in content or "mtls" in content:
                    has_mtls = True
                if "strict" in content and "mtls" in content:
                    has_mtls = True

                # NetworkPolicy detection
                if "networkpolicy" in content:
                    has_network_policy = True
                if "default-deny" in content or "deny-all" in content:
                    has_default_deny = True

                # RBAC
                if "clusterrole" in content or "rolebinding" in content:
                    has_rbac = True

                # Service mesh
                if "istio" in content or "linkerd" in content:
                    has_service_mesh = True

                # Container security
                if "runasnonroot" in content and "readonlyrootfilesystem" in content:
                    has_container_security = True

                # Encryption
                if "tls" in content and ("1.2" in content or "1.3" in content):
                    has_encryption = True
                if "fips" in content and "140" in content:
                    has_encryption = True

                # Identity provider
                if "oidc" in content or "oauth" in content or "saml" in content:
                    has_identity_provider = True
                if "icam" in content or "cac" in content or "piv" in content:
                    has_identity_provider = True

            except Exception:
                continue

        for f in py_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore").lower()
                if "audit" in content and "log" in content:
                    has_audit_log = True
                if "sbom" in content or "cyclonedx" in content:
                    has_sbom = True
            except Exception:
                continue

        for f in tf_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore").lower()
                if "guardduty" in content or "securityhub" in content:
                    has_audit_log = True
                if "flow_log" in content or "vpc_flow" in content:
                    has_audit_log = True
                if "waf" in content:
                    has_network_policy = True
            except Exception:
                continue

        # Map detected indicators to ZTA requirements

        # Architecture
        if has_service_mesh:
            results["ZTA-ARCH-1"] = "satisfied"
            results["ZTA-ARCH-2"] = "satisfied"

        # Identity pillar
        if has_identity_provider:
            results["ZTA-ID-1"] = "satisfied"
            results["ZTA-ID-2"] = "partially_satisfied"
        if has_rbac:
            results["ZTA-ID-3"] = "partially_satisfied"
            results["ZTA-ID-4"] = "partially_satisfied"

        # Device pillar
        if has_container_security:
            results["ZTA-DEV-3"] = "satisfied"

        # Network pillar
        if has_network_policy:
            results["ZTA-NET-1"] = "satisfied"
        if has_mtls:
            results["ZTA-NET-2"] = "satisfied"
        if has_default_deny:
            results["ZTA-NET-3"] = "satisfied"

        # Application pillar
        if has_container_security:
            results["ZTA-APP-2"] = "satisfied"
        if has_mtls:
            results["ZTA-APP-3"] = "satisfied"
        if has_sbom:
            results["ZTA-APP-4"] = "partially_satisfied"

        # Data pillar
        if has_encryption:
            results["ZTA-DATA-2"] = "satisfied"

        # Visibility pillar
        if has_audit_log:
            results["ZTA-VIS-1"] = "satisfied"
            results["ZTA-VIS-2"] = "partially_satisfied"

        return results


if __name__ == "__main__":
    NIST800207Assessor().run_cli()
