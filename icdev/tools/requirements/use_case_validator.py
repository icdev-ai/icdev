# CUI // SP-CTI
"""Use Case YAML Validator — structural audit of args/use_cases.yaml.

Checks every template_requirement entry against the DB CHECK constraints
in intake_requirements (tools/db/init_icdev_db.py lines 1506-1510) so that
invalid type/priority/text/criteria values are caught before they reach the DB.

Usage:
    python tools/requirements/use_case_validator.py [--json] [--fix] [--yaml-path PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
YAML_PATH = BASE_DIR / "args" / "use_cases.yaml"

# ---------------------------------------------------------------------------
# Constants — must stay in sync with intake_requirements CHECK constraints
# (tools/db/init_icdev_db.py lines 1506-1510)
# ---------------------------------------------------------------------------

VALID_TYPES: frozenset[str] = frozenset({
    "functional", "non_functional", "interface", "security",
    "performance", "compliance", "data", "constraint",
    "operational", "transitional",
})

VALID_PRIORITIES: frozenset[str] = frozenset({"critical", "high", "medium", "low"})

KNOWN_CANVASES: frozenset[str] = frozenset({
    "migration_canvas", "simulation", "supply_chain", "compliance",
    "kanban", "knowledge", "rag", "audit", "security", "boundary",
    "data", "cpmp", "monitoring", "pipeline", "quality", "migration",
})

# Narrow typo corrections auto-applied by --fix (structural only)
_TYPE_CORRECTIONS: dict[str, str] = {
    "non-functional": "non_functional",
    "nonfunctional": "non_functional",
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_template_req(uc_id: str, idx: int, req: dict) -> list[dict]:
    """Return violation dicts for one template_requirement entry."""
    violations: list[dict] = []

    t = req.get("type", "")
    if t not in VALID_TYPES:
        violations.append({
            "uc_id": uc_id,
            "req_index": idx,
            "field": "type",
            "value": t,
            "error": (
                f"invalid requirement_type {t!r}; "
                f"must be one of {sorted(VALID_TYPES)}"
            ),
        })

    p = req.get("priority", "")
    if p not in VALID_PRIORITIES:
        violations.append({
            "uc_id": uc_id,
            "req_index": idx,
            "field": "priority",
            "value": p,
            "error": (
                f"invalid priority {p!r}; "
                f"must be one of {sorted(VALID_PRIORITIES)}"
            ),
        })

    if not (req.get("text") or "").strip():
        violations.append({
            "uc_id": uc_id,
            "req_index": idx,
            "field": "text",
            "value": None,
            "error": "empty text",
        })

    if "criteria" not in req:
        violations.append({
            "uc_id": uc_id,
            "req_index": idx,
            "field": "criteria",
            "value": None,
            "error": "missing criteria field",
        })
    elif not (req.get("criteria") or "").strip():
        violations.append({
            "uc_id": uc_id,
            "req_index": idx,
            "field": "criteria",
            "value": None,
            "error": "empty criteria",
        })

    return violations


def _validate_canvas_wiring(uc_id: str, uc: dict) -> list[dict]:
    """Return warning dicts for unknown canvas_wiring values (non-fatal)."""
    warnings: list[dict] = []
    for canvas in uc.get("canvas_wiring") or []:
        if canvas not in KNOWN_CANVASES:
            warnings.append({
                "uc_id": uc_id,
                "req_index": None,
                "field": "canvas_wiring",
                "value": canvas,
                "error": f"unknown canvas_wiring value {canvas!r} (warning only)",
                "severity": "warning",
            })
    return warnings


def _validate_use_case(uc: dict) -> list[dict]:
    """Return all violation + warning dicts for one use case."""
    uc_id = uc.get("id", "<unknown>")
    findings: list[dict] = []
    for idx, req in enumerate(uc.get("template_requirements") or []):
        findings.extend(_validate_template_req(uc_id, idx, req))
    findings.extend(_validate_canvas_wiring(uc_id, uc))
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_use_cases(yaml_path: Path | None = None) -> dict:
    """Load args/use_cases.yaml and validate all use cases.

    Returns:
        {
            "status": "ok" | "error",
            "use_case_count": int,
            "total_requirements": int,
            "violations": [...],   # errors only (severity not set)
            "warnings": [...],     # warnings (severity=warning)
            "summary": str,
        }
    """
    try:
        import yaml as _yaml  # optional dep; stdlib not needed at module level
    except ImportError:
        return {
            "status": "error",
            "use_case_count": 0,
            "total_requirements": 0,
            "violations": [],
            "warnings": [],
            "summary": "PyYAML not installed — cannot parse use_cases.yaml",
        }

    path = Path(yaml_path) if yaml_path else YAML_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            data = _yaml.safe_load(fh) or {}
    except Exception as exc:
        return {
            "status": "error",
            "use_case_count": 0,
            "total_requirements": 0,
            "violations": [],
            "warnings": [],
            "summary": f"Failed to load {path}: {exc}",
        }

    cases: list[dict] = data.get("use_cases", [])
    total_reqs = sum(len(uc.get("template_requirements") or []) for uc in cases)

    all_findings: list[dict] = []
    for uc in cases:
        all_findings.extend(_validate_use_case(uc))

    violations = [f for f in all_findings if f.get("severity") != "warning"]
    warnings = [f for f in all_findings if f.get("severity") == "warning"]

    status = "ok" if not violations else "error"
    vcount = len(violations)
    wcount = len(warnings)
    summary = (
        f"{len(cases)} use cases, {total_reqs} requirements — "
        f"{vcount} violation(s), {wcount} warning(s)."
    )

    return {
        "status": status,
        "use_case_count": len(cases),
        "total_requirements": total_reqs,
        "violations": violations,
        "warnings": warnings,
        "summary": summary,
    }


def _apply_fixes(yaml_path: Path) -> int:
    """Auto-correct narrow type typos in template_requirements.

    Returns the number of fields corrected. Rewrites the file in-place.
    Only corrects entries in _TYPE_CORRECTIONS — never touches text/criteria.
    """
    try:
        import yaml as _yaml
    except ImportError:
        return 0

    with open(yaml_path, encoding="utf-8") as fh:
        data = _yaml.safe_load(fh) or {}

    cases: list[dict] = data.get("use_cases", [])
    fixes = 0
    for uc in cases:
        for req in uc.get("template_requirements") or []:
            t = req.get("type", "")
            if t in _TYPE_CORRECTIONS:
                req["type"] = _TYPE_CORRECTIONS[t]
                fixes += 1

    if fixes:
        with open(yaml_path, "w", encoding="utf-8") as fh:
            _yaml.dump(data, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return fixes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate args/use_cases.yaml template_requirements structure."
    )
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Output results as JSON")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-correct narrow type typos (writes YAML in-place)")
    parser.add_argument("--yaml-path", metavar="PATH", default=None,
                        help=f"Path to use_cases.yaml (default: {YAML_PATH})")
    args = parser.parse_args()

    yaml_path = Path(args.yaml_path) if args.yaml_path else YAML_PATH

    if args.fix:
        fixes = _apply_fixes(yaml_path)
        if args.json_out:
            print(json.dumps({"fixes_applied": fixes}))
        else:
            print(f"Applied {fixes} fix(es) to {yaml_path}")
        if fixes == 0:
            return  # nothing changed — skip re-validation

    result = validate_use_cases(yaml_path)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(result["summary"])
        if result["violations"]:
            print("\nVIOLATIONS:")
            for v in result["violations"]:
                loc = f"{v['uc_id']}[{v['req_index']}].{v['field']}"
                print(f"  {loc}: {v['error']}")
        if result["warnings"]:
            print("\nWARNINGS:")
            for w in result["warnings"]:
                loc = f"{w['uc_id']}.{w['field']}"
                print(f"  {loc}: {w['error']}")

    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
