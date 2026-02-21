#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV Compliance Configurator — maps compliance objectives to modules.

Given a set of compliance framework identifiers (e.g., fedramp_high, cmmc,
hipaa), this tool determines which ICDEV modules are required, which DB
table groups need to be initialized, and which security gate overrides apply.

It also detects applicable frameworks from data categories (CUI, PHI, PCI,
etc.) using the mapping from ``args/classification_config.yaml``.

Dependencies: Python stdlib only (json, pathlib, argparse, datetime).

CLI::

    python tools/installer/compliance_configurator.py --list-postures
    python tools/installer/compliance_configurator.py --configure fedramp_high,cmmc --json
    python tools/installer/compliance_configurator.py --detect-from-data CUI,PHI --json
    python tools/installer/compliance_configurator.py --validate --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST_PATH = BASE_DIR / "args" / "installation_manifest.yaml"
DEFAULT_REGISTRY_PATH = BASE_DIR / "data" / "installation.json"
CLASSIFICATION_CONFIG_PATH = BASE_DIR / "args" / "classification_config.yaml"
FRAMEWORK_REGISTRY_PATH = BASE_DIR / "args" / "framework_registry.yaml"

# ---------------------------------------------------------------------------
# Framework-to-module mapping (ADR D116 pattern)
# ---------------------------------------------------------------------------

FRAMEWORK_MODULE_MAP: Dict[str, List[str]] = {
    "fedramp_moderate": ["compliance_base", "fedramp_moderate"],
    "fedramp_high": ["compliance_base", "fedramp_high"],
    "cmmc": ["compliance_base", "cmmc"],
    "cjis": ["compliance_base", "cjis"],
    "hipaa": ["compliance_base", "hipaa"],
    "hitrust": ["compliance_base", "hitrust"],
    "soc2": ["compliance_base", "soc2"],
    "pci_dss": ["compliance_base", "pci_dss"],
    "iso27001": ["compliance_base", "iso27001"],
    "fips_199_200": ["compliance_base", "fips_199_200"],
    "oscal": ["compliance_base", "oscal"],
    "emass": ["compliance_base", "emass"],
    "cato": ["compliance_base", "cato"],
    "cssp": ["compliance_base", "cssp"],
    "sbd_ivv": ["compliance_base", "sbd_ivv"],
}

# ---------------------------------------------------------------------------
# Data category to framework mapping (from classification_config.yaml)
# ---------------------------------------------------------------------------

DATA_CATEGORY_FRAMEWORK_MAP: Dict[str, List[str]] = {
    "CUI": ["fedramp_moderate", "cmmc", "fips_199_200"],
    "PHI": ["hipaa", "hitrust"],
    "PCI": ["pci_dss", "soc2"],
    "CJIS": ["cjis", "fips_199_200"],
    "FTI": ["fedramp_moderate", "soc2"],
    "PII": ["soc2", "iso27001"],
    "SECRET": ["fedramp_high", "fips_199_200", "cmmc"],
}

# ---------------------------------------------------------------------------
# CUI enablement rules: framework IDs that imply CUI markings
# ---------------------------------------------------------------------------

_CUI_FRAMEWORKS = {
    "fedramp_moderate", "fedramp_high", "cmmc", "cssp", "emass",
    "cato", "fips_199_200", "oscal", "sbd_ivv", "cjis",
}

# ---------------------------------------------------------------------------
# Posture descriptions for --list-postures
# ---------------------------------------------------------------------------

POSTURE_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "fedramp_moderate": {
        "name": "FedRAMP Moderate",
        "description": "Cloud services processing controlled but non-classified government data (IL2-IL4)",
        "impact_level": "IL4",
        "sector": "Federal civilian / DoD non-classified",
    },
    "fedramp_high": {
        "name": "FedRAMP High",
        "description": "Cloud services processing high-impact government data (IL5) or classified (IL6)",
        "impact_level": "IL5-IL6",
        "sector": "DoD / IC / Federal high-value assets",
    },
    "cmmc": {
        "name": "CMMC Level 2/3",
        "description": "Defense Industrial Base contractors handling CUI or classified information",
        "impact_level": "IL4-IL6",
        "sector": "Defense contractors (DIB)",
    },
    "cjis": {
        "name": "CJIS Security Policy",
        "description": "Law enforcement systems accessing FBI CJIS data (NCIC, III, NICS)",
        "impact_level": "IL4",
        "sector": "Law enforcement / Criminal justice",
    },
    "hipaa": {
        "name": "HIPAA Security Rule",
        "description": "Healthcare systems processing Protected Health Information (PHI)",
        "impact_level": "IL4",
        "sector": "Healthcare / Covered entities",
    },
    "hitrust": {
        "name": "HITRUST CSF v11",
        "description": "Healthcare and enterprise information security certification",
        "impact_level": "IL4",
        "sector": "Healthcare / Enterprise",
    },
    "soc2": {
        "name": "SOC 2 Type II",
        "description": "Service organizations demonstrating trust service criteria compliance",
        "impact_level": "IL2-IL4",
        "sector": "Commercial SaaS / Service organizations",
    },
    "pci_dss": {
        "name": "PCI DSS v4.0",
        "description": "Systems processing, storing, or transmitting payment card data",
        "impact_level": "IL2-IL4",
        "sector": "Financial / E-commerce / Payments",
    },
    "iso27001": {
        "name": "ISO/IEC 27001:2022",
        "description": "International information security management system certification",
        "impact_level": "IL2-IL4",
        "sector": "International / Global operations",
    },
    "fips_199_200": {
        "name": "FIPS 199/200 Categorization",
        "description": "Federal security categorization and minimum security requirements",
        "impact_level": "IL2-IL6",
        "sector": "All federal systems",
    },
    "oscal": {
        "name": "OSCAL Generation",
        "description": "Machine-readable compliance documentation (NIST OSCAL format)",
        "impact_level": "IL2-IL6",
        "sector": "All ATO-seeking systems",
    },
    "emass": {
        "name": "eMASS Integration",
        "description": "DoD Enterprise Mission Assurance Support Service RMF workflow",
        "impact_level": "IL4-IL6",
        "sector": "DoD systems requiring RMF ATO",
    },
    "cato": {
        "name": "cATO Monitoring",
        "description": "Continuous Authority to Operate evidence monitoring and freshness",
        "impact_level": "IL4-IL6",
        "sector": "DoD / Federal continuous monitoring",
    },
    "cssp": {
        "name": "DoD CSSP (DI 8530.01)",
        "description": "Cybersecurity Service Provider compliance for DoD networks",
        "impact_level": "IL4-IL6",
        "sector": "DoD network operations / CSSP",
    },
    "sbd_ivv": {
        "name": "Secure by Design + IV&V",
        "description": "CISA Secure by Design principles and IEEE 1012 IV&V certification",
        "impact_level": "IL2-IL6",
        "sector": "All software-intensive systems",
    },
}

# ---------------------------------------------------------------------------
# DB table groups per framework (for selective initialization)
# ---------------------------------------------------------------------------

_FRAMEWORK_DB_TABLES: Dict[str, List[str]] = {
    "compliance_base": [
        "compliance_controls", "control_implementations", "ssp_documents",
        "poam_items", "stig_findings", "sbom_components", "audit_trail",
    ],
    "fedramp_moderate": ["fedramp_assessments"],
    "fedramp_high": ["fedramp_assessments"],
    "cmmc": ["cmmc_assessments"],
    "cjis": ["cjis_assessments"],
    "hipaa": ["hipaa_assessments"],
    "hitrust": ["hitrust_assessments"],
    "soc2": ["soc2_assessments"],
    "pci_dss": ["pci_dss_assessments"],
    "iso27001": ["iso27001_assessments"],
    "fips_199_200": ["fips199_categorizations", "project_information_types", "fips200_assessments"],
    "oscal": ["oscal_documents"],
    "emass": ["emass_sync_log"],
    "cato": ["cato_evidence"],
    "cssp": ["cssp_assessments", "incident_response_plans"],
    "sbd_ivv": ["sbd_assessments", "ivv_assessments"],
}

# ---------------------------------------------------------------------------
# Security gate overrides per framework
# ---------------------------------------------------------------------------

_FRAMEWORK_GATE_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "fedramp_moderate": {
        "fedramp_gate": True,
        "encryption_fips_140_2": True,
    },
    "fedramp_high": {
        "fedramp_gate": True,
        "encryption_fips_140_2": True,
        "fips_validated_crypto": True,
    },
    "cmmc": {
        "cmmc_gate": True,
        "evidence_freshness_days": 90,
    },
    "cjis": {
        "cjis_gate": True,
        "advanced_auth_required": True,
        "encryption_fips_140_2": True,
        "fingerprint_background_check": True,
    },
    "hipaa": {
        "hipaa_gate": True,
        "encryption_fips_140_2": True,
        "breach_notification_days": 60,
    },
    "pci_dss": {
        "pci_dss_gate": True,
        "mask_pan": True,
        "no_cvv_storage": True,
    },
    "cato": {
        "cato_gate": True,
        "evidence_freshness_days": 30,
    },
    "fips_199_200": {
        "fips_199_gate": True,
        "fips_200_gate": True,
    },
}


# ---------------------------------------------------------------------------
# ComplianceConfigurator
# ---------------------------------------------------------------------------

class ComplianceConfigurator:
    """Maps compliance objectives to ICDEV module requirements.

    Args:
        manifest_path: Path to installation manifest YAML.
        registry_path: Path to installation registry JSON.
    """

    def __init__(
        self,
        manifest_path: Optional[Path] = None,
        registry_path: Optional[Path] = None,
    ) -> None:
        self.manifest_path = manifest_path or DEFAULT_MANIFEST_PATH
        self.registry_path = registry_path or DEFAULT_REGISTRY_PATH
        self._registry = self._load_registry()

    def _load_registry(self) -> Dict[str, Any]:
        """Load existing installation registry."""
        if self.registry_path.exists():
            with open(self.registry_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return {"modules": {}, "compliance_posture": [], "cui_enabled": False}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def configure_posture(self, frameworks: List[str]) -> Dict[str, Any]:
        """Given framework IDs, compute required modules, DB tables, gates, CUI config.

        Args:
            frameworks: List of framework identifiers (e.g., ``["fedramp_high", "cmmc"]``).

        Returns:
            Dict with ``required_modules``, ``db_table_groups``,
            ``security_gate_overrides``, ``cui_enabled``, and ``impact_level``.
        """
        required_modules: Dict[str, bool] = {}
        db_tables: List[str] = []
        gate_overrides: Dict[str, Any] = {}
        unknown_frameworks: List[str] = []
        cui_enabled = False
        impact_levels: List[str] = []

        for fw in frameworks:
            fw_lower = fw.strip().lower()

            # Get required modules
            modules = FRAMEWORK_MODULE_MAP.get(fw_lower)
            if modules is None:
                unknown_frameworks.append(fw_lower)
                continue

            for mod in modules:
                required_modules[mod] = True

            # Get DB tables
            for mod in modules:
                tables = _FRAMEWORK_DB_TABLES.get(mod, [])
                for t in tables:
                    if t not in db_tables:
                        db_tables.append(t)

            # Get gate overrides
            overrides = _FRAMEWORK_GATE_OVERRIDES.get(fw_lower, {})
            gate_overrides.update(overrides)

            # CUI determination
            if fw_lower in _CUI_FRAMEWORKS:
                cui_enabled = True

            # Impact level from posture description
            posture = POSTURE_DESCRIPTIONS.get(fw_lower, {})
            il = posture.get("impact_level", "")
            if il and il not in impact_levels:
                impact_levels.append(il)

        # Determine highest impact level
        impact_level = "IL2"
        for il in impact_levels:
            if "IL6" in il:
                impact_level = "IL6"
                break
            elif "IL5" in il:
                impact_level = "IL5"
            elif "IL4" in il and impact_level not in ("IL5", "IL6"):
                impact_level = "IL4"

        result = {
            "frameworks": frameworks,
            "required_modules": sorted(required_modules.keys()),
            "db_table_groups": db_tables,
            "security_gate_overrides": gate_overrides,
            "cui_enabled": cui_enabled,
            "impact_level": impact_level,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if unknown_frameworks:
            result["unknown_frameworks"] = unknown_frameworks
            result["warning"] = (
                f"Unknown framework(s): {', '.join(unknown_frameworks)}. "
                "Use --list-postures to see valid options."
            )

        return result

    def detect_from_data_categories(self, categories: List[str]) -> List[str]:
        """Given data categories, return applicable framework IDs.

        Args:
            categories: List of data category strings (e.g., ``["CUI", "PHI"]``).

        Returns:
            Sorted deduplicated list of framework IDs.
        """
        frameworks: Dict[str, bool] = {}
        for cat in categories:
            cat_upper = cat.strip().upper()
            fws = DATA_CATEGORY_FRAMEWORK_MAP.get(cat_upper, [])
            for fw in fws:
                frameworks[fw] = True

        return sorted(frameworks.keys())

    def validate_posture(
        self,
        installed_modules: Optional[Dict[str, Any]] = None,
        declared_posture: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Check if installed modules satisfy the declared compliance posture.

        Args:
            installed_modules: Dict of installed module metadata (from registry).
                Defaults to reading from registry file.
            declared_posture: List of framework IDs declared as the posture.
                Defaults to reading from registry file.

        Returns:
            Dict with ``satisfied``, ``gaps``, and ``recommendations``.
        """
        if installed_modules is None:
            installed_modules = self._registry.get("modules", {})
        if declared_posture is None:
            declared_posture = self._registry.get("compliance_posture", [])

        if not declared_posture:
            return {
                "satisfied": True,
                "gaps": [],
                "recommendations": [],
                "declared_posture": [],
                "note": "No compliance posture declared. Use --configure to set one.",
            }

        config = self.configure_posture(declared_posture)
        required = set(config["required_modules"])
        installed_set = set(
            k for k, v in installed_modules.items()
            if v.get("installed")
        )

        missing = sorted(required - installed_set)
        extra = sorted(installed_set & required)

        recommendations: List[str] = []
        if missing:
            recommendations.append(
                f"Install missing modules: {', '.join(missing)}"
            )
        if config.get("cui_enabled") and not self._registry.get("cui_enabled"):
            recommendations.append("Enable CUI markings (cui_enabled: true)")

        return {
            "satisfied": len(missing) == 0,
            "declared_posture": declared_posture,
            "required_modules": sorted(required),
            "installed_modules": sorted(installed_set),
            "gaps": missing,
            "present": extra,
            "cui_required": config.get("cui_enabled", False),
            "cui_enabled": self._registry.get("cui_enabled", False),
            "recommendations": recommendations,
        }

    def list_postures(self) -> List[Dict[str, str]]:
        """Return all available compliance postures with descriptions.

        Returns:
            List of dicts with ``id``, ``name``, ``description``,
            ``impact_level``, and ``sector``.
        """
        postures: List[Dict[str, str]] = []
        for posture_id, info in sorted(POSTURE_DESCRIPTIONS.items()):
            postures.append({
                "id": posture_id,
                "name": info["name"],
                "description": info["description"],
                "impact_level": info.get("impact_level", ""),
                "sector": info.get("sector", ""),
            })
        return postures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_human_postures(postures: List[Dict[str, str]]) -> str:
    """Render posture list as human-readable text."""
    lines = [
        "=" * 70,
        "  ICDEV Compliance Configurator — Available Postures",
        "=" * 70,
        "",
    ]
    for p in postures:
        lines.append(f"  {p['id']}")
        lines.append(f"    Name:    {p['name']}")
        lines.append(f"    Desc:    {p['description']}")
        lines.append(f"    IL:      {p['impact_level']}")
        lines.append(f"    Sector:  {p['sector']}")
        lines.append("")
    return "\n".join(lines)


def _format_human_configure(result: Dict[str, Any]) -> str:
    """Render configuration result as human-readable text."""
    lines = [
        "=" * 70,
        "  ICDEV Compliance Configuration",
        "=" * 70,
        "",
        f"  Frameworks:   {', '.join(result['frameworks'])}",
        f"  CUI Enabled:  {'yes' if result['cui_enabled'] else 'no'}",
        f"  Impact Level: {result['impact_level']}",
        "",
        "  Required Modules:",
    ]
    for mod in result["required_modules"]:
        lines.append(f"    - {mod}")
    lines.append("")
    lines.append("  DB Table Groups:")
    for tbl in result["db_table_groups"]:
        lines.append(f"    - {tbl}")
    lines.append("")
    lines.append("  Security Gate Overrides:")
    for key, val in result.get("security_gate_overrides", {}).items():
        lines.append(f"    {key}: {val}")

    if result.get("warning"):
        lines.append("")
        lines.append(f"  WARNING: {result['warning']}")

    return "\n".join(lines)


def _format_human_validate(result: Dict[str, Any]) -> str:
    """Render validation result as human-readable text."""
    lines = [
        "=" * 70,
        "  ICDEV Compliance Posture Validation",
        "=" * 70,
        "",
        f"  Satisfied:  {'YES' if result['satisfied'] else 'NO'}",
        f"  Posture:    {', '.join(result.get('declared_posture', [])) or '(none)'}",
        "",
    ]
    if result.get("gaps"):
        lines.append("  Missing Modules:")
        for gap in result["gaps"]:
            lines.append(f"    - {gap}")
        lines.append("")
    if result.get("recommendations"):
        lines.append("  Recommendations:")
        for rec in result["recommendations"]:
            lines.append(f"    - {rec}")
    if result.get("note"):
        lines.append(f"  Note: {result['note']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ICDEV Compliance Configurator — map compliance objectives to modules"
    )
    parser.add_argument(
        "--list-postures", action="store_true",
        help="Show all available compliance postures with descriptions",
    )
    parser.add_argument(
        "--configure", metavar="FRAMEWORKS",
        help="Comma-separated framework IDs to configure (e.g., fedramp_high,cmmc)",
    )
    parser.add_argument(
        "--detect-from-data", metavar="CATEGORIES",
        help="Comma-separated data categories to detect frameworks (e.g., CUI,PHI)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate installed modules against declared compliance posture",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--human", action="store_true",
        help="Human-friendly terminal output",
    )
    parser.add_argument(
        "--manifest-path", type=Path, default=None,
        help="Path to installation manifest YAML",
    )
    parser.add_argument(
        "--registry-path", type=Path, default=None,
        help="Path to installation registry JSON",
    )

    args = parser.parse_args()
    use_json = args.json

    configurator = ComplianceConfigurator(
        manifest_path=args.manifest_path,
        registry_path=args.registry_path,
    )

    try:
        if args.list_postures:
            postures = configurator.list_postures()
            if use_json:
                print(json.dumps(postures, indent=2, default=str))
            else:
                print(_format_human_postures(postures))

        elif args.configure:
            frameworks = [f.strip() for f in args.configure.split(",") if f.strip()]
            result = configurator.configure_posture(frameworks)
            if use_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_human_configure(result))

        elif args.detect_from_data:
            categories = [c.strip() for c in args.detect_from_data.split(",") if c.strip()]
            detected = configurator.detect_from_data_categories(categories)
            if use_json:
                print(json.dumps({
                    "data_categories": categories,
                    "detected_frameworks": detected,
                    "total": len(detected),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, indent=2, default=str))
            else:
                print(f"Data categories: {', '.join(categories)}")
                print(f"Detected frameworks ({len(detected)}):")
                for fw in detected:
                    desc = POSTURE_DESCRIPTIONS.get(fw, {})
                    name = desc.get("name", fw)
                    print(f"  - {fw}: {name}")

        elif args.validate:
            result = configurator.validate_posture()
            if use_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_human_validate(result))

        else:
            parser.print_help()

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
