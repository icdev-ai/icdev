#!/usr/bin/env python3

# CUI // SP-CTI
"""GovCon-Specific Presidio Recognizers.

Builds custom Presidio PatternRecognizer instances for government contracting:
    - DoD contract numbers (PIID format)
    - Solicitation numbers
    - CAGE codes (with context boosting)
    - UEI / DUNS numbers
    - Dollar amounts and labor rates
    - Indirect rate percentages
    - Program names (deny-list)
    - Protected organizations (deny-list)

These recognizers are registered with the Presidio AnalyzerEngine in detector.py.
When Presidio is not installed, detector.py falls back to raw regex matching
using the same patterns from args/redaction_govcon.yaml.

CLI:
    python tools/redaction/govcon_recognizers.py --list --json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.redaction.govcon_recognizers")


def build_govcon_recognizers(govcon_config: Dict[str, Any]) -> list:
    """Build Presidio recognizers from GovCon config.

    Args:
        govcon_config: Parsed args/redaction_govcon.yaml.

    Returns:
        List of Presidio EntityRecognizer instances.
    """
    try:
        from presidio_analyzer import PatternRecognizer, Pattern
    except ImportError:
        logger.warning("presidio_analyzer not installed — cannot build recognizers")
        return []

    recognizers = []

    # --- Contract patterns ---
    for pdef in govcon_config.get("contract_patterns", []) or []:
        name = pdef.get("name", "unknown")
        pattern_str = pdef.get("pattern", "")
        confidence = pdef.get("confidence", 0.8)
        context_words = pdef.get("context_words", [])

        if not pattern_str:
            continue

        entity_type = f"GOVCON_{name.upper()}"
        patterns = [Pattern(name=name, regex=pattern_str, score=confidence)]

        rec = PatternRecognizer(
            supported_entity=entity_type,
            patterns=patterns,
            context=context_words if context_words else None,
            name=f"icdev_govcon_{name}",
        )
        recognizers.append(rec)

    # --- Pricing patterns ---
    for pdef in govcon_config.get("pricing_patterns", []) or []:
        name = pdef.get("name", "pricing")
        pattern_str = pdef.get("pattern", "")
        confidence = pdef.get("confidence", 0.8)
        context_words = pdef.get("context_words", [])

        if not pattern_str:
            continue

        entity_type = f"GOVCON_{name.upper()}"
        patterns = [Pattern(name=name, regex=pattern_str, score=confidence)]

        rec = PatternRecognizer(
            supported_entity=entity_type,
            patterns=patterns,
            context=context_words if context_words else None,
            name=f"icdev_govcon_{name}",
        )
        recognizers.append(rec)

    # --- Program names (deny-list recognizer) ---
    program_names = govcon_config.get("program_names", []) or []
    if program_names:
        rec = PatternRecognizer(
            supported_entity="GOVCON_PROGRAM_NAME",
            deny_list=program_names,
            name="icdev_program_name_deny_list",
        )
        recognizers.append(rec)

    # --- Protected organizations (deny-list recognizer) ---
    protected_orgs = govcon_config.get("protected_organizations", []) or []
    if protected_orgs:
        rec = PatternRecognizer(
            supported_entity="GOVCON_PROTECTED_ORG",
            deny_list=protected_orgs,
            name="icdev_protected_org_deny_list",
        )
        recognizers.append(rec)

    # --- Agency names from surrogate map ---
    agency_surrogates = govcon_config.get("agency_surrogates", {}) or {}
    if agency_surrogates:
        agency_names = list(agency_surrogates.keys())
        rec = PatternRecognizer(
            supported_entity="GOVCON_AGENCY_NAME",
            deny_list=agency_names,
            name="icdev_agency_name_deny_list",
        )
        recognizers.append(rec)

    logger.info("Built %d GovCon recognizers", len(recognizers))
    return recognizers


def list_recognizers(govcon_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List recognizer definitions without requiring Presidio."""
    result = []

    for pdef in govcon_config.get("contract_patterns", []) or []:
        result.append(
            {
                "name": pdef.get("name", "unknown"),
                "type": "pattern",
                "entity": f"GOVCON_{pdef.get('name', '').upper()}",
                "confidence": pdef.get("confidence", 0.8),
                "context_words": pdef.get("context_words", []),
            }
        )

    for pdef in govcon_config.get("pricing_patterns", []) or []:
        result.append(
            {
                "name": pdef.get("name", "pricing"),
                "type": "pattern",
                "entity": f"GOVCON_{pdef.get('name', '').upper()}",
                "confidence": pdef.get("confidence", 0.8),
                "context_words": pdef.get("context_words", []),
            }
        )

    programs = govcon_config.get("program_names", []) or []
    if programs:
        result.append(
            {
                "name": "program_names",
                "type": "deny_list",
                "entity": "GOVCON_PROGRAM_NAME",
                "term_count": len(programs),
            }
        )

    orgs = govcon_config.get("protected_organizations", []) or []
    if orgs:
        result.append(
            {
                "name": "protected_orgs",
                "type": "deny_list",
                "entity": "GOVCON_PROTECTED_ORG",
                "term_count": len(orgs),
            }
        )

    agencies = govcon_config.get("agency_surrogates", {}) or {}
    if agencies:
        result.append(
            {
                "name": "agency_names",
                "type": "deny_list",
                "entity": "GOVCON_AGENCY_NAME",
                "term_count": len(agencies),
            }
        )

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ GovCon Recognizers")
    parser.add_argument("--list", action="store_true", help="List recognizer definitions")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Load config
    try:
        import yaml

        config_path = BASE_DIR / "args" / "redaction_govcon.yaml"
        with open(config_path, encoding="utf-8") as f:
            govcon_config = yaml.safe_load(f) or {}
    except Exception:
        govcon_config = {}

    if args.list:
        result = list_recognizers(govcon_config)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for r in result:
                print(f"  {r['entity']} ({r['type']}) — {r['name']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
