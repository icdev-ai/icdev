#!/usr/bin/env python3
# CUI // SP-CTI
"""CSP Region Validator — compliance-driven deployment validation (D234).

Validates that a CSP region holds required compliance certifications
before deployment. Uses context/compliance/csp_certifications.json
as the authoritative certification registry (D233).

CLI: --validate, --eligible, --deployment-check, --json
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("icdev.cloud.region_validator")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CERTS_PATH = BASE_DIR / "context" / "compliance" / "csp_certifications.json"


# Impact level to required certification mapping (REQ-38-070-071)
IL_REQUIREMENTS = {
    "IL2": {"fedramp_level": "moderate"},
    "IL4": {"fedramp_level": "moderate", "fips_140_2": True},
    "IL5": {"fedramp_level": "high", "fips_140_2": True, "dod_il": "IL5"},
    "IL6": {"fedramp_level": "high", "fips_140_2": True, "dod_il": "IL6"},
}

# IL6-eligible CSPs only (REQ-38-071)
IL6_ELIGIBLE_CSPS = {"aws", "azure", "oci", "local"}

# FedRAMP level hierarchy
FEDRAMP_HIERARCHY = {"low": 1, "moderate": 2, "high": 3}


class RegionValidator:
    """Validate CSP regions against compliance certification requirements."""

    def __init__(self, certifications_path: Optional[str] = None):
        self._certs_path = Path(certifications_path) if certifications_path else DEFAULT_CERTS_PATH
        self._data: Dict = {}
        self._load()

    def _load(self):
        """Load certifications registry."""
        if not self._certs_path.exists():
            logger.warning("Certifications file not found: %s", self._certs_path)
            self._data = {}
            return
        try:
            with open(self._certs_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception as exc:
            logger.error("Failed to load certifications: %s", exc)
            self._data = {}

    def get_region_certs(self, csp: str, region: str) -> Optional[Dict]:
        """Get certification data for a specific CSP region."""
        certs = self._data.get("certifications", {})
        csp_data = certs.get(csp.lower(), {})
        return csp_data.get(region)

    def validate_region(self, csp: str, region: str,
                        required_certs: List[str]) -> Dict:
        """Validate a region holds all required certifications.

        Args:
            csp: Cloud provider (aws, azure, gcp, oci, ibm)
            region: CSP region identifier
            required_certs: List of required certifications
                           (fedramp_high, fedramp_moderate, hipaa, pci_dss,
                            cjis, fti, itar, fips_140_2, nist_800_171)

        Returns:
            {valid: bool, missing: list, available: list, region_data: dict}
        """
        region_data = self.get_region_certs(csp, region)
        if region_data is None:
            return {
                "valid": False,
                "missing": required_certs,
                "available": [],
                "region_data": None,
                "error": f"Unknown region: {csp}/{region}",
            }

        missing = []
        available = []

        for cert in required_certs:
            cert_lower = cert.lower()

            # Handle FedRAMP level checks (fedramp_high, fedramp_moderate)
            if cert_lower.startswith("fedramp_"):
                required_level = cert_lower.replace("fedramp_", "")
                actual_level = region_data.get("fedramp", "").lower()
                required_rank = FEDRAMP_HIERARCHY.get(required_level, 0)
                actual_rank = FEDRAMP_HIERARCHY.get(actual_level, 0)
                if actual_rank >= required_rank:
                    available.append(cert)
                else:
                    missing.append(cert)

            # Handle DoD IL level checks (dod_il5, dod_il4, etc.)
            elif cert_lower.startswith("dod_"):
                # Parse as dod_srg_il5, dod_il5, etc.
                il_target = cert_lower.split("_")[-1].upper()
                dod_ils = region_data.get("dod_il", [])
                if il_target in dod_ils:
                    available.append(cert)
                else:
                    missing.append(cert)

            # Handle boolean certifications (hipaa, pci_dss, cjis, etc.)
            else:
                if region_data.get(cert_lower, False):
                    available.append(cert)
                else:
                    missing.append(cert)

        return {
            "valid": len(missing) == 0,
            "missing": missing,
            "available": available,
            "region_data": region_data,
        }

    def get_eligible_regions(self, csp: str,
                             required_certs: List[str]) -> List[Dict]:
        """Get all regions for a CSP that meet certification requirements.

        Returns:
            List of {region, certifications} dicts for matching regions.
        """
        certs = self._data.get("certifications", {})
        csp_data = certs.get(csp.lower(), {})
        eligible = []

        for region_name, region_data in csp_data.items():
            result = self.validate_region(csp, region_name, required_certs)
            if result["valid"]:
                eligible.append({
                    "region": region_name,
                    "certifications": region_data,
                })

        return eligible

    def validate_deployment(self, csp: str, region: str,
                            impact_level: str,
                            frameworks: Optional[List[str]] = None) -> Dict:
        """Validate a deployment against IL requirements and framework certs.

        Combines impact level requirements with explicit framework requirements.

        Args:
            csp: Cloud provider
            region: CSP region
            impact_level: IL2, IL4, IL5, IL6
            frameworks: Optional additional framework requirements
                       (e.g., ['hipaa', 'pci_dss', 'cjis'])

        Returns:
            {valid: bool, il_valid: bool, framework_valid: bool,
             missing_il: list, missing_frameworks: list, eligible_csps: list}
        """
        il = impact_level.upper()
        result = {
            "csp": csp,
            "region": region,
            "impact_level": il,
            "frameworks": frameworks or [],
            "valid": True,
            "il_valid": True,
            "framework_valid": True,
            "missing_il": [],
            "missing_frameworks": [],
            "eligible_csps": [],
            "warnings": [],
        }

        # Check IL6 CSP restrictions (REQ-38-071)
        if il == "IL6" and csp.lower() not in IL6_ELIGIBLE_CSPS:
            result["valid"] = False
            result["il_valid"] = False
            result["missing_il"].append(
                f"CSP '{csp}' not eligible for IL6 (only AWS C2S, Azure Gov Secret, OCI DoD, Local)"
            )
            result["eligible_csps"] = sorted(IL6_ELIGIBLE_CSPS)
            return result

        # Build required certs from IL
        il_reqs = IL_REQUIREMENTS.get(il, {})
        il_certs = []
        if "fedramp_level" in il_reqs:
            il_certs.append(f"fedramp_{il_reqs['fedramp_level']}")
        if il_reqs.get("fips_140_2"):
            il_certs.append("fips_140_2")
        if "dod_il" in il_reqs:
            il_certs.append(f"dod_srg_{il_reqs['dod_il'].lower()}")

        # Validate IL certs
        if il_certs:
            il_result = self.validate_region(csp, region, il_certs)
            if not il_result["valid"]:
                result["il_valid"] = False
                result["valid"] = False
                result["missing_il"] = il_result["missing"]

        # Validate framework certs
        if frameworks:
            fw_result = self.validate_region(csp, region, frameworks)
            if not fw_result["valid"]:
                result["framework_valid"] = False
                result["valid"] = False
                result["missing_frameworks"] = fw_result["missing"]

        return result

    def list_csps(self) -> List[str]:
        """List all CSPs in the certification registry."""
        return list(self._data.get("certifications", {}).keys())

    def list_regions(self, csp: str) -> List[str]:
        """List all regions for a CSP."""
        return list(self._data.get("certifications", {}).get(csp.lower(), {}).keys())


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CSP Region Validator — compliance-driven deployment validation (D234)"
    )
    parser.add_argument("--certs-file", help="Path to csp_certifications.json")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output")

    # Common args shared by all subcommands (--json, --certs-file)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output")
    common.add_argument("--certs-file", help="Path to csp_certifications.json")

    sub = parser.add_subparsers(dest="command")

    # Validate a specific region
    val = sub.add_parser("validate", parents=[common],
                         help="Validate a region against required certs")
    val.add_argument("--csp", required=True, help="Cloud provider (aws, azure, gcp, oci, ibm)")
    val.add_argument("--region", required=True, help="CSP region identifier")
    val.add_argument("--frameworks", required=True,
                     help="Comma-separated required certifications")

    # Find eligible regions
    elig = sub.add_parser("eligible", parents=[common],
                          help="Find eligible regions for requirements")
    elig.add_argument("--csp", required=True, help="Cloud provider")
    elig.add_argument("--frameworks", required=True,
                      help="Comma-separated required certifications")

    # Deployment validation (IL + frameworks)
    deploy = sub.add_parser("deployment-check", parents=[common],
                            help="Validate deployment against IL + frameworks")
    deploy.add_argument("--csp", required=True, help="Cloud provider")
    deploy.add_argument("--region", required=True, help="CSP region")
    deploy.add_argument("--impact-level", required=True,
                        help="Impact level (IL2, IL4, IL5, IL6)")
    deploy.add_argument("--frameworks", default="",
                        help="Comma-separated additional framework requirements")

    # List CSPs/regions
    lst = sub.add_parser("list", parents=[common],
                         help="List CSPs or regions")
    lst.add_argument("--csp", help="List regions for this CSP (omit for CSP list)")

    args = parser.parse_args()

    if not args.command:
        # Support flat args for backward compat: --validate, --eligible
        parser.add_argument("--validate", action="store_true")
        parser.add_argument("--eligible", action="store_true")
        parser.add_argument("--csp", help="Cloud provider")
        parser.add_argument("--region", help="CSP region")
        parser.add_argument("--impact-level", help="Impact level")
        parser.add_argument("--frameworks", default="",
                            help="Comma-separated required certifications")
        args = parser.parse_args()

        if not hasattr(args, "validate") or (not args.validate and not args.eligible):
            parser.print_help()
            sys.exit(1)

        validator = RegionValidator(args.certs_file)
        certs_list = [c.strip() for c in args.frameworks.split(",") if c.strip()]

        if args.validate:
            result = validator.validate_region(args.csp, args.region, certs_list)
        elif args.eligible:
            result = validator.get_eligible_regions(args.csp, certs_list)

        if args.json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    validator = RegionValidator(args.certs_file)

    if args.command == "validate":
        certs_list = [c.strip() for c in args.frameworks.split(",") if c.strip()]
        result = validator.validate_region(args.csp, args.region, certs_list)

    elif args.command == "eligible":
        certs_list = [c.strip() for c in args.frameworks.split(",") if c.strip()]
        result = validator.get_eligible_regions(args.csp, certs_list)

    elif args.command == "deployment-check":
        fw_list = [c.strip() for c in args.frameworks.split(",") if c.strip()] if args.frameworks else []
        result = validator.validate_deployment(
            args.csp, args.region, args.impact_level, fw_list
        )

    elif args.command == "list":
        if args.csp:
            result = validator.list_regions(args.csp)
        else:
            result = validator.list_csps()

    else:
        parser.print_help()
        sys.exit(1)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    run_cli()
