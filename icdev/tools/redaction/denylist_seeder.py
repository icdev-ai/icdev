#!/usr/bin/env python3
# CUI // SP-CTI
"""GovCon deny-list seeder (trust-mask-04).

The program-name / protected-organization deny-lists in
``args/redaction_govcon.yaml`` ship empty, so an operator's own org/partner/
customer names aren't protected before cloud-LLM egress until populated. This
tool discovers those names from the company profile (and merges them non-
destructively into the config) so the deny-lists are no longer inert.

Program names remain operator-specific (add them by hand or extend the DB
discovery below); this seeds the derivable set — protected organizations.

CLI:
    python tools/redaction/denylist_seeder.py --profile own_company --dry-run --json
    python tools/redaction/denylist_seeder.py --profile own_company --write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.redaction.denylist_seeder")

_GOVCON_CONFIG = BASE_DIR / "args" / "redaction_govcon.yaml"

# Profile fields that name organizations worth protecting on cloud egress.
_ORG_FIELDS = (
    "entity_name",
    "teaming_partners",
    "partners",
    "subcontractors",
    "key_customers",
    "customer_organizations",
    "customers",
)


def _names_from_value(val: Any) -> List[str]:
    """Extract organization name strings from a scalar / list / list-of-dicts."""
    out: List[str] = []
    if isinstance(val, str):
        if val.strip():
            out.append(val.strip())
    elif isinstance(val, dict):
        for k in ("name", "org", "organization", "entity_name"):
            if isinstance(val.get(k), str) and val[k].strip():
                out.append(val[k].strip())
                break
    elif isinstance(val, (list, tuple)):
        for item in val:
            out.extend(_names_from_value(item))
    return out


def seed_from_profile(profile: Dict[str, Any]) -> Dict[str, List[str]]:
    """Discover protected organization names from a company profile dict.

    Returns {"protected_organizations": [...]} — deduped, order-preserving.
    Never includes empty strings. Program names are not derivable from the
    profile, so they are left to the operator.
    """
    orgs: List[str] = []
    seen = set()
    for field in _ORG_FIELDS:
        for name in _names_from_value(profile.get(field)):
            key = name.lower()
            if key not in seen:
                seen.add(key)
                orgs.append(name)
    return {"protected_organizations": orgs}


def merge_denylists(existing: Dict[str, Any], seeds: Dict[str, List[str]]) -> Dict[str, Any]:
    """Non-destructively merge seed lists into an existing govcon config.

    Existing entries are preserved; seeds are unioned in (case-insensitive
    dedup, order-preserving with existing first). Returns a new dict; does not
    mutate ``existing``.
    """
    merged = dict(existing or {})
    for key, seed_vals in (seeds or {}).items():
        current = list(merged.get(key) or [])
        seen = {str(v).lower() for v in current}
        for v in seed_vals:
            if str(v).lower() not in seen:
                seen.add(str(v).lower())
                current.append(v)
        merged[key] = current
    return merged


def _load_profile(name: str) -> Dict[str, Any]:
    try:
        import yaml

        from tools.govcon import rfi_workbench as _wb

        with open(_wb._PROFILES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("profiles", {}).get(name, {})
    except Exception as exc:
        logger.warning("denylist_seeder: could not load profile %s: %s", name, exc)
        return {}


def _load_govcon_config() -> Dict[str, Any]:
    try:
        import yaml

        with open(_GOVCON_CONFIG, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed GovCon redaction deny-lists from the company profile")
    parser.add_argument("--profile", default="own_company", help="Company profile name (default: own_company)")
    parser.add_argument("--write", action="store_true", help="Merge seeds into args/redaction_govcon.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Preview discovered names, write nothing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    profile = _load_profile(args.profile)
    seeds = seed_from_profile(profile)
    result: Dict[str, Any] = {"profile": args.profile, "seeds": seeds, "written": False}

    if args.write and not args.dry_run:
        import yaml

        merged = merge_denylists(_load_govcon_config(), seeds)
        with open(_GOVCON_CONFIG, "w", encoding="utf-8") as f:
            yaml.safe_dump(merged, f, sort_keys=False, allow_unicode=True)
        result["written"] = True

    print(json.dumps(result, indent=2) if args.json else json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
