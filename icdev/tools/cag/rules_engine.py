#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""CAG Layer 2 -- Declarative aggregation rules evaluation.

Loads aggregation rules from args/cag_rules.yaml and evaluates sets of
category tags against those rules to detect classification-by-compilation
per EO 13526 Section 1.7(e).

Rule trigger logic:
    - all_of:          ALL listed categories must be present
    - any_of:          AT LEAST ONE must be present
    - required + min_additional: required categories + at least N from any_of
    - min_categories:  at least N total distinct categories present

Proximity multipliers are applied to severity scoring when proximity_scores
are provided.

Usage:
    python tools/cag/rules_engine.py --load [--json]
    python tools/cag/rules_engine.py --evaluate --tags PERSONNEL,CAPABILITY,LOCATION [--json]
    python tools/cag/rules_engine.py --check --categories CAPABILITY,LOCATION,TIMING [--json]
    python tools/cag/rules_engine.py --list-rules [--rule-type universal] [--json]
    python tools/cag/rules_engine.py --add-rule --name "custom_rule" --description "desc" \\
        --trigger-categories PERSONNEL,PROGRAM --trigger-logic all_of \\
        --severity HIGH --resulting-classification CONFIDENTIAL \\
        --action review_required [--remediation "text"] [--json]
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --- Path setup ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))
CAG_RULES_PATH = BASE_DIR / "args" / "cag_rules.yaml"

# --- YAML import (graceful) ---
try:
    import yaml
except ImportError:
    yaml = None

# --- Constants ---
VALID_CATEGORIES = [
    "PERSONNEL", "CAPABILITY", "LOCATION", "TIMING", "PROGRAM",
    "VULNERABILITY", "METHOD", "SCALE", "SOURCE", "RELATIONSHIP",
]

VALID_SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

VALID_ACTIONS = ["alert", "review_required", "block_and_alert", "quarantine"]

VALID_RULE_TYPES = ["universal", "org", "scg"]

# Severity numeric weight for scoring
SEVERITY_WEIGHT = {
    "LOW": 0.25,
    "MEDIUM": 0.50,
    "HIGH": 0.75,
    "CRITICAL": 1.00,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None):
    """Open a database connection with WAL mode and foreign keys."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    """Return current UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, event_type, actor, action, entity_type=None,
           entity_id=None, details=None):
    """Write an append-only audit trail record."""
    conn.execute(
        "INSERT INTO audit_trail "
        "(event_type, actor, action, entity_type, entity_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, actor, action, entity_type, entity_id, details, _now()),
    )


def _gen_id(prefix="rule"):
    """Generate a unique ID."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

def load_rules(db_path=None):
    """Load aggregation rules from cag_rules.yaml into the cag_rules table.

    Parses universal_rules from the YAML configuration. Each rule has:
    trigger (all_of, any_of, required, min_additional, min_categories),
    severity, resulting_classification, action, and remediation.

    Existing universal rules in the database are deactivated before
    re-loading to ensure the YAML file is the source of truth.

    Args:
        db_path: Optional database path override.

    Returns:
        dict with rules_loaded count and list of rule IDs.
    """
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required for CAG rules. Install with: pip install pyyaml"
        )

    if not CAG_RULES_PATH.exists():
        raise FileNotFoundError(f"CAG rules not found: {CAG_RULES_PATH}")

    with open(CAG_RULES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    universal_rules = data.get("universal_rules", [])
    if not universal_rules:
        return {"rules_loaded": 0, "rule_ids": []}

    conn = _get_db(db_path)
    try:
        # Deactivate existing universal rules to reload cleanly
        conn.execute(
            "UPDATE cag_rules SET is_active = 0, updated_at = ? "
            "WHERE rule_type = 'universal'",
            (_now(),),
        )

        rule_ids = []
        for rule_def in universal_rules:
            rule_id = rule_def.get("id", _gen_id())
            name = rule_def.get("name", "unnamed")
            description = rule_def.get("description", "")
            severity = rule_def.get("severity", "HIGH")
            trigger = rule_def.get("trigger", {})
            resulting_classification = rule_def.get(
                "resulting_classification", "CONFIDENTIAL"
            )
            action = rule_def.get("action", "review_required")
            remediation = rule_def.get("remediation", "")

            # Build trigger_categories: union of all referenced categories
            trigger_categories = set()
            for key in ("all_of", "any_of", "required"):
                cats = trigger.get(key, [])
                if isinstance(cats, list):
                    trigger_categories.update(cats)
            trigger_categories_json = json.dumps(sorted(trigger_categories))

            # Store full trigger logic as JSON
            trigger_logic_json = json.dumps(trigger)

            # Upsert: try to update, then insert
            existing = conn.execute(
                "SELECT id FROM cag_rules WHERE id = ?", (rule_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE cag_rules SET "
                    "name = ?, description = ?, severity = ?, "
                    "trigger_categories = ?, trigger_logic = ?, "
                    "resulting_classification = ?, action = ?, "
                    "remediation = ?, is_active = 1, updated_at = ? "
                    "WHERE id = ?",
                    (
                        name, description, severity,
                        trigger_categories_json, trigger_logic_json,
                        resulting_classification, action,
                        remediation, _now(), rule_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO cag_rules "
                    "(id, rule_type, name, description, severity, "
                    "trigger_categories, trigger_logic, "
                    "resulting_classification, action, remediation, "
                    "is_active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        rule_id, "universal", name, description, severity,
                        trigger_categories_json, trigger_logic_json,
                        resulting_classification, action, remediation,
                        _now(), _now(),
                    ),
                )

            rule_ids.append(rule_id)

        _audit(
            conn, "cag.load_rules", "system",
            f"Loaded {len(rule_ids)} universal rules from cag_rules.yaml",
            entity_type="cag_rules", entity_id=None,
            details=json.dumps({"rule_ids": rule_ids}),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "rules_loaded": len(rule_ids),
        "rule_ids": rule_ids,
        "loaded_at": _now(),
    }


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

def _evaluate_trigger(trigger_logic, present_categories):
    """Check if a rule's trigger logic is satisfied by present categories.

    Args:
        trigger_logic: dict with keys like all_of, any_of, required,
                       min_additional, min_categories.
        present_categories: set of category strings present in content.

    Returns:
        bool indicating whether the rule triggers.
    """
    present = set(present_categories)

    # Case 1: min_categories -- at least N distinct categories
    if "min_categories" in trigger_logic:
        min_n = trigger_logic["min_categories"]
        valid = present.intersection(set(VALID_CATEGORIES))
        if len(valid) >= min_n:
            return True
        return False

    # Case 2: required + min_additional from any_of
    if "required" in trigger_logic and "min_additional" in trigger_logic:
        required = set(trigger_logic.get("required", []))
        any_of = set(trigger_logic.get("any_of", []))
        min_additional = trigger_logic.get("min_additional", 1)

        if not required.issubset(present):
            return False
        additional_present = present.intersection(any_of)
        if len(additional_present) >= min_additional:
            return True
        return False

    # Case 3: all_of + optional any_of (with optional min_additional)
    all_of = set(trigger_logic.get("all_of", []))
    any_of = set(trigger_logic.get("any_of", []))
    min_additional = trigger_logic.get("min_additional", 0)

    if all_of:
        if not all_of.issubset(present):
            return False
        # If any_of is also present, check min_additional
        if any_of and min_additional > 0:
            additional_present = present.intersection(any_of)
            return len(additional_present) >= min_additional
        # If any_of is present without min_additional, at least 1
        if any_of:
            return bool(present.intersection(any_of))
        return True

    # Case 4: any_of only
    if any_of:
        return bool(present.intersection(any_of))

    return False


def evaluate_tags(tags, rule_set=None, db_path=None):
    """Evaluate a list of category tags against all active rules.

    Args:
        tags: list of tag dicts (each must have 'category' key) or list
              of category strings.
        rule_set: optional list of rule dicts to evaluate against.
                  If None, loads active rules from the database.
        db_path: Optional database path override.

    Returns:
        list of dicts for each triggered rule: rule_id, rule_name, severity,
        resulting_classification, action, remediation, triggered_categories.
    """
    # Normalize tags to a set of category strings
    if tags and isinstance(tags[0], dict):
        present_categories = set(t.get("category", "") for t in tags)
    else:
        present_categories = set(tags)

    # Load rules if not provided
    if rule_set is None:
        rule_set = get_active_rules(db_path=db_path)

    triggered = []

    for rule in rule_set:
        # Parse trigger logic
        trigger_logic = rule.get("trigger_logic", "{}")
        if isinstance(trigger_logic, str):
            try:
                trigger_logic = json.loads(trigger_logic)
            except (json.JSONDecodeError, TypeError):
                continue

        if _evaluate_trigger(trigger_logic, present_categories):
            # Determine which categories actually contributed
            trigger_cats = set()
            for key in ("all_of", "any_of", "required"):
                for cat in trigger_logic.get(key, []):
                    if cat in present_categories:
                        trigger_cats.add(cat)

            triggered.append({
                "rule_id": rule.get("id", "unknown"),
                "rule_name": rule.get("name", "unknown"),
                "severity": rule.get("severity", "MEDIUM"),
                "resulting_classification": rule.get(
                    "resulting_classification", "CONFIDENTIAL"
                ),
                "action": rule.get("action", "review_required"),
                "remediation": rule.get("remediation", ""),
                "triggered_categories": sorted(trigger_cats),
            })

    # Sort by severity (CRITICAL first)
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    triggered.sort(key=lambda r: severity_order.get(r["severity"], 99))

    return triggered


def check_combination(categories, proximity_scores=None, db_path=None):
    """Quick check if a set of categories triggers any rule.

    Applies proximity multipliers to severity scoring if provided.

    Args:
        categories: list of category strings.
        proximity_scores: optional dict mapping (cat1, cat2) tuples to
                         proximity scores (0.0 to 1.0). If provided,
                         severity scores are multiplied by the average
                         proximity of triggered category pairs.
        db_path: Optional database path override.

    Returns:
        dict with triggered (bool), rules (list), max_severity,
        resulting_classification, and risk_score.
    """
    rules = get_active_rules(db_path=db_path)
    triggered = evaluate_tags(categories, rule_set=rules, db_path=db_path)

    if not triggered:
        return {
            "triggered": False,
            "rules": [],
            "max_severity": None,
            "resulting_classification": None,
            "risk_score": 0.0,
        }

    # Apply proximity multipliers
    for rule_result in triggered:
        base_weight = SEVERITY_WEIGHT.get(rule_result["severity"], 0.5)
        if proximity_scores:
            cats = rule_result["triggered_categories"]
            prox_values = []
            for i, c1 in enumerate(cats):
                for c2 in cats[i + 1:]:
                    key = tuple(sorted((c1, c2)))
                    if key in proximity_scores:
                        prox_values.append(proximity_scores[key])
            if prox_values:
                avg_proximity = sum(prox_values) / len(prox_values)
                rule_result["proximity_multiplier"] = avg_proximity
                rule_result["adjusted_score"] = base_weight * avg_proximity
            else:
                rule_result["proximity_multiplier"] = 1.0
                rule_result["adjusted_score"] = base_weight
        else:
            rule_result["proximity_multiplier"] = 1.0
            rule_result["adjusted_score"] = base_weight

    # Highest severity and classification
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    classification_order = [
        "UNCLASSIFIED",
        "CONFIDENTIAL",
        "SECRET",
        "SECRET // SI",
        "SECRET // NOFORN",
        "TOP SECRET",
    ]

    max_severity = min(triggered, key=lambda r: severity_order.get(r["severity"], 99))
    max_classification = max(
        triggered,
        key=lambda r: (
            classification_order.index(r["resulting_classification"])
            if r["resulting_classification"] in classification_order
            else 0
        ),
    )
    risk_score = max(r.get("adjusted_score", 0) for r in triggered)

    return {
        "triggered": True,
        "rules": triggered,
        "max_severity": max_severity["severity"],
        "resulting_classification": max_classification["resulting_classification"],
        "risk_score": round(risk_score, 3),
    }


# ---------------------------------------------------------------------------
# Rule management
# ---------------------------------------------------------------------------

def get_active_rules(rule_type=None, db_path=None):
    """List all active rules, optionally filtered by type.

    Args:
        rule_type: Optional filter (universal, org, scg).
        db_path: Optional database path override.

    Returns:
        list of rule dicts.
    """
    conn = _get_db(db_path)
    try:
        if rule_type:
            if rule_type not in VALID_RULE_TYPES:
                raise ValueError(
                    f"Invalid rule_type '{rule_type}'. "
                    f"Must be one of: {VALID_RULE_TYPES}"
                )
            rows = conn.execute(
                "SELECT id, rule_type, name, description, severity, "
                "trigger_categories, trigger_logic, resulting_classification, "
                "action, remediation, scg_program_id, is_active, "
                "created_at, updated_at "
                "FROM cag_rules WHERE is_active = 1 AND rule_type = ? "
                "ORDER BY severity DESC, name",
                (rule_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, rule_type, name, description, severity, "
                "trigger_categories, trigger_logic, resulting_classification, "
                "action, remediation, scg_program_id, is_active, "
                "created_at, updated_at "
                "FROM cag_rules WHERE is_active = 1 "
                "ORDER BY severity DESC, name",
            ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]


def add_org_rule(name, description, trigger_categories, trigger_logic,
                 severity, resulting_classification, action,
                 remediation=None, db_path=None):
    """Add an organization-specific aggregation rule.

    Args:
        name: Rule name (short identifier).
        description: Human-readable description.
        trigger_categories: list of categories involved.
        trigger_logic: dict with trigger logic (all_of, any_of, etc.).
        severity: One of VALID_SEVERITIES.
        resulting_classification: Classification when triggered.
        action: One of VALID_ACTIONS.
        remediation: Optional remediation guidance.
        db_path: Optional database path override.

    Returns:
        dict with new rule details.
    """
    if severity not in VALID_SEVERITIES:
        raise ValueError(
            f"Invalid severity '{severity}'. Must be one of: {VALID_SEVERITIES}"
        )
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Invalid action '{action}'. Must be one of: {VALID_ACTIONS}"
        )
    for cat in trigger_categories:
        if cat not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{cat}'. Must be one of: {VALID_CATEGORIES}"
            )

    rule_id = _gen_id()
    now = _now()
    trigger_categories_json = json.dumps(sorted(trigger_categories))

    if isinstance(trigger_logic, dict):
        trigger_logic_json = json.dumps(trigger_logic)
    else:
        trigger_logic_json = str(trigger_logic)

    conn = _get_db(db_path)
    try:
        conn.execute(
            "INSERT INTO cag_rules "
            "(id, rule_type, name, description, severity, "
            "trigger_categories, trigger_logic, resulting_classification, "
            "action, remediation, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                rule_id, "org", name, description, severity,
                trigger_categories_json, trigger_logic_json,
                resulting_classification, action, remediation or "",
                now, now,
            ),
        )
        _audit(
            conn, "cag.add_rule", "admin",
            f"Added org rule: {name} (severity={severity})",
            entity_type="cag_rules", entity_id=rule_id,
            details=json.dumps({
                "name": name,
                "severity": severity,
                "trigger_categories": sorted(trigger_categories),
                "action": action,
            }),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "rule_id": rule_id,
        "rule_type": "org",
        "name": name,
        "description": description,
        "severity": severity,
        "trigger_categories": sorted(trigger_categories),
        "trigger_logic": trigger_logic,
        "resulting_classification": resulting_classification,
        "action": action,
        "remediation": remediation or "",
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for CAG rules engine."""
    parser = argparse.ArgumentParser(
        description="CAG Layer 2: Declarative aggregation rules evaluation"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--load", action="store_true",
        help="Load rules from cag_rules.yaml into the database",
    )
    group.add_argument(
        "--evaluate", action="store_true",
        help="Evaluate a set of tags against all active rules",
    )
    group.add_argument(
        "--check", action="store_true",
        help="Quick check if categories trigger any rule",
    )
    group.add_argument(
        "--list-rules", action="store_true",
        help="List all active rules",
    )
    group.add_argument(
        "--add-rule", action="store_true",
        help="Add an organization-specific rule",
    )

    parser.add_argument("--tags", help="Comma-separated category tags (for --evaluate)")
    parser.add_argument("--categories", help="Comma-separated categories (for --check)")
    parser.add_argument("--rule-type", help="Filter by rule type (universal, org, scg)")
    parser.add_argument("--name", help="Rule name (for --add-rule)")
    parser.add_argument("--description", help="Rule description (for --add-rule)")
    parser.add_argument("--trigger-categories", help="Comma-separated trigger categories")
    parser.add_argument("--trigger-logic", help="Trigger logic as JSON string (e.g. '{\"all_of\":[\"A\",\"B\"]}')")
    parser.add_argument("--severity", help="Rule severity: LOW, MEDIUM, HIGH, CRITICAL")
    parser.add_argument("--resulting-classification", help="Resulting classification when triggered")
    parser.add_argument("--action", help="Action: alert, review_required, block_and_alert, quarantine")
    parser.add_argument("--remediation", help="Remediation guidance text")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    db = args.db_path

    try:
        if args.load:
            output = load_rules(db_path=db)
            output["status"] = "loaded"

        elif args.evaluate:
            if not args.tags:
                parser.error("--evaluate requires --tags (comma-separated categories)")
            tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]
            triggered = evaluate_tags(tag_list, db_path=db)
            output = {
                "status": "evaluated",
                "input_categories": tag_list,
                "rules_triggered": len(triggered),
                "triggered_rules": triggered,
            }

        elif args.check:
            if not args.categories:
                parser.error("--check requires --categories (comma-separated)")
            cat_list = [c.strip() for c in args.categories.split(",") if c.strip()]
            output = check_combination(cat_list, db_path=db)
            output["status"] = "checked"
            output["input_categories"] = cat_list

        elif args.list_rules:
            rules = get_active_rules(rule_type=args.rule_type, db_path=db)
            output = {
                "status": "listed",
                "rule_count": len(rules),
                "filter": args.rule_type or "all",
                "rules": rules,
            }

        elif args.add_rule:
            if not args.name:
                parser.error("--add-rule requires --name")
            if not args.description:
                parser.error("--add-rule requires --description")
            if not args.trigger_categories:
                parser.error("--add-rule requires --trigger-categories")
            if not args.trigger_logic:
                parser.error("--add-rule requires --trigger-logic")
            if not args.severity:
                parser.error("--add-rule requires --severity")
            if not args.resulting_classification:
                parser.error("--add-rule requires --resulting-classification")
            if not args.action:
                parser.error("--add-rule requires --action")

            trig_cats = [c.strip() for c in args.trigger_categories.split(",")]
            try:
                trig_logic = json.loads(args.trigger_logic)
            except json.JSONDecodeError as exc:
                parser.error(f"--trigger-logic must be valid JSON: {exc}")

            output = add_org_rule(
                name=args.name,
                description=args.description,
                trigger_categories=trig_cats,
                trigger_logic=trig_logic,
                severity=args.severity,
                resulting_classification=args.resulting_classification,
                action=args.action,
                remediation=args.remediation,
                db_path=db,
            )
            output["status"] = "added"

        else:
            parser.print_help()
            sys.exit(1)

        if args.json:
            print(json.dumps(output, indent=2, default=str))
        else:
            _print_human(output)

    except Exception as exc:
        error_out = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(error_out, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_human(output):
    """Print human-readable output."""
    status = output.get("status", "unknown")
    print(f"CAG Rules Engine -- {status.upper()}")
    print("-" * 50)

    if status == "loaded":
        print(f"  Rules loaded: {output.get('rules_loaded', 0)}")
        for rid in output.get("rule_ids", []):
            print(f"    {rid}")

    elif status == "evaluated":
        cats = output.get("input_categories", [])
        print(f"  Input categories: {', '.join(cats)}")
        count = output.get("rules_triggered", 0)
        print(f"  Rules triggered:  {count}")
        if count > 0:
            print()
            for rule in output.get("triggered_rules", []):
                sev = rule.get("severity", "?")
                name = rule.get("rule_name", "?")
                cls = rule.get("resulting_classification", "?")
                act = rule.get("action", "?")
                print(f"  [{sev}] {name}")
                print(f"    Classification: {cls}")
                print(f"    Action:         {act}")
                tcats = rule.get("triggered_categories", [])
                print(f"    Categories:     {', '.join(tcats)}")
                rem = rule.get("remediation", "")
                if rem:
                    print(f"    Remediation:    {rem}")
                print()

    elif status == "checked":
        triggered = output.get("triggered", False)
        cats = output.get("input_categories", [])
        print(f"  Input categories: {', '.join(cats)}")
        print(f"  Triggered:        {'YES' if triggered else 'NO'}")
        if triggered:
            print(f"  Max severity:     {output.get('max_severity', '?')}")
            print(f"  Classification:   {output.get('resulting_classification', '?')}")
            print(f"  Risk score:       {output.get('risk_score', 0):.3f}")

    elif status == "listed":
        count = output.get("rule_count", 0)
        filt = output.get("filter", "all")
        print(f"  Filter: {filt}")
        print(f"  Active rules: {count}")
        for rule in output.get("rules", []):
            rid = rule.get("id", "?")
            name = rule.get("name", "?")
            sev = rule.get("severity", "?")
            rtype = rule.get("rule_type", "?")
            print(f"    [{sev}] {rid} ({rtype}): {name}")

    elif status == "added":
        print(f"  Rule ID:         {output.get('rule_id', '?')}")
        print(f"  Name:            {output.get('name', '?')}")
        print(f"  Severity:        {output.get('severity', '?')}")
        print(f"  Action:          {output.get('action', '?')}")
        print(f"  Classification:  {output.get('resulting_classification', '?')}")
        tcats = output.get("trigger_categories", [])
        print(f"  Categories:      {', '.join(tcats)}")

    else:
        for key, val in output.items():
            if key != "status":
                print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
