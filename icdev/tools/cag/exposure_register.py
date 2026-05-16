#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""CAG Layer 4 (partial) -- Cross-proposal aggregation tracking.

Tracks which security-relevant categories have been exposed across
multiple proposals over time to prevent the mosaic effect where
individually unclassified proposals combine to reveal classified
information (EO 13526 Section 1.7(e)).

Exposures are grouped by capability_group (e.g., "SIGINT_collection",
"network_defense") to track cumulative disclosure within related
program areas.

Usage:
    python tools/cag/exposure_register.py --register --proposal-id "prop-1" \\
        --capability-group "SIGINT_collection" \\
        --categories CAPABILITY,METHOD --audience "NSA/CSS" [--json]
    python tools/cag/exposure_register.py --check --capability-group "SIGINT_collection" \\
        --new-categories LOCATION,TIMING [--json]
    python tools/cag/exposure_register.py --report [--capability-group "SIGINT_collection"] [--json]
    python tools/cag/exposure_register.py --scan-cross --proposal-id "prop-1" [--json]
    python tools/cag/exposure_register.py --groups [--json]
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
DEFAULT_LOOKBACK_DAYS = 730  # 2 years

VALID_CATEGORIES = [
    "PERSONNEL", "CAPABILITY", "LOCATION", "TIMING", "PROGRAM",
    "VULNERABILITY", "METHOD", "SCALE", "SOURCE", "RELATIONSHIP",
]


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


def _gen_id(prefix="exp"):
    """Generate a unique ID."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_lookback_days():
    """Load lookback period from cag_rules.yaml or use default."""
    if yaml is None or not CAG_RULES_PATH.exists():
        return DEFAULT_LOOKBACK_DAYS
    try:
        with open(CAG_RULES_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("cross_proposal", {}).get(
            "lookback_days", DEFAULT_LOOKBACK_DAYS
        )
    except Exception:
        return DEFAULT_LOOKBACK_DAYS


def _get_alert_threshold():
    """Load alert threshold from cag_rules.yaml or use default."""
    if yaml is None or not CAG_RULES_PATH.exists():
        return 0.6
    try:
        with open(CAG_RULES_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("cross_proposal", {}).get("alert_threshold", 0.6)
    except Exception:
        return 0.6


def _lookback_cutoff(lookback_days=None):
    """Return the ISO-8601 cutoff date for the lookback window."""
    days = lookback_days or _get_lookback_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.isoformat()


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def register_exposure(proposal_id, capability_group, categories_exposed,
                      audience=None, db_path=None):
    """Record which categories were exposed in a proposal.

    Stores the exposure in cag_exposure_register and computes the
    cumulative categories for the capability group.

    Args:
        proposal_id: ID of the proposals record.
        capability_group: Logical grouping (e.g., "SIGINT_collection").
        categories_exposed: list of category strings exposed in this proposal.
        audience: Optional audience description (e.g., agency name).
        db_path: Optional database path override.

    Returns:
        dict with exposure record details and cumulative state.
    """
    if not proposal_id:
        raise ValueError("proposal_id is required")
    if not capability_group:
        raise ValueError("capability_group is required")
    if not categories_exposed:
        raise ValueError("categories_exposed must be a non-empty list")

    for cat in categories_exposed:
        if cat not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{cat}'. Must be one of: {VALID_CATEGORIES}"
            )

    exposure_id = _gen_id()
    now = _now()
    cutoff = _lookback_cutoff()

    conn = _get_db(db_path)
    try:
        # Verify proposal exists
        prop = conn.execute(
            "SELECT id FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if not prop:
            raise ValueError(f"Proposal not found: {proposal_id}")

        # Compute cumulative categories within lookback window
        prior_rows = conn.execute(
            "SELECT categories_exposed FROM cag_exposure_register "
            "WHERE capability_group = ? AND exposure_date >= ? "
            "ORDER BY exposure_date",
            (capability_group, cutoff),
        ).fetchall()

        cumulative = set(categories_exposed)
        for row in prior_rows:
            prior_cats = row["categories_exposed"]
            if prior_cats:
                try:
                    cumulative.update(json.loads(prior_cats))
                except (json.JSONDecodeError, TypeError):
                    pass

        cumulative_sorted = sorted(cumulative)

        # Check if cumulative triggers any aggregation rule
        from tools.cag.rules_engine import check_combination
        combo_result = check_combination(cumulative_sorted, db_path=db_path)
        alert_generated = combo_result.get("triggered", False)

        # Determine cumulative classification
        if alert_generated:
            cumulative_classification = combo_result.get(
                "resulting_classification", "UNCLASSIFIED"
            )
        else:
            cumulative_classification = "UNCLASSIFIED"

        # Insert exposure record
        conn.execute(
            "INSERT INTO cag_exposure_register "
            "(id, capability_group, proposal_id, categories_exposed, "
            "audience, exposure_date, classification_at_exposure, "
            "cumulative_categories, cumulative_classification, "
            "alert_generated, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                exposure_id, capability_group, proposal_id,
                json.dumps(sorted(categories_exposed)),
                audience, now, "UNCLASSIFIED",
                json.dumps(cumulative_sorted),
                cumulative_classification,
                1 if alert_generated else 0,
                now,
            ),
        )

        # If alert generated, create a cag_alert record
        alert_details = None
        if alert_generated:
            alert_id = f"alert-{uuid.uuid4().hex[:12]}"
            max_rule = combo_result.get("rules", [{}])[0] if combo_result.get("rules") else {}
            rule_id = max_rule.get("rule_id", "cross-proposal")

            conn.execute(
                "INSERT INTO cag_alerts "
                "(id, proposal_id, rule_id, severity, status, "
                "categories_triggered, source_elements, proximity_score, "
                "resulting_classification, remediation_suggestion, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alert_id, proposal_id, rule_id,
                    max_rule.get("severity", "HIGH"), "open",
                    json.dumps(cumulative_sorted),
                    json.dumps({"capability_group": capability_group, "type": "cross_proposal"}),
                    combo_result.get("risk_score", 0),
                    cumulative_classification,
                    f"Cross-proposal aggregation in '{capability_group}'. "
                    f"Cumulative categories: {', '.join(cumulative_sorted)}. "
                    f"{max_rule.get('remediation', '')}",
                    now,
                ),
            )
            alert_details = {
                "alert_id": alert_id,
                "severity": max_rule.get("severity", "HIGH"),
                "classification": cumulative_classification,
                "risk_score": combo_result.get("risk_score", 0),
            }

        _audit(
            conn, "cag.register_exposure", "auto",
            f"Registered exposure: {capability_group} in proposal {proposal_id}, "
            f"categories={sorted(categories_exposed)}, "
            f"cumulative={cumulative_sorted}, alert={alert_generated}",
            entity_type="cag_exposure_register", entity_id=exposure_id,
            details=json.dumps({
                "capability_group": capability_group,
                "categories_exposed": sorted(categories_exposed),
                "cumulative": cumulative_sorted,
                "alert_generated": alert_generated,
            }),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "exposure_id": exposure_id,
        "proposal_id": proposal_id,
        "capability_group": capability_group,
        "categories_exposed": sorted(categories_exposed),
        "audience": audience,
        "cumulative_categories": cumulative_sorted,
        "cumulative_count": len(cumulative_sorted),
        "cumulative_classification": cumulative_classification,
        "alert_generated": alert_generated,
        "alert_details": alert_details,
        "registered_at": now,
    }


def check_cumulative(capability_group, new_categories, db_path=None):
    """Check if adding new categories triggers an aggregation rule.

    Looks back over the configured lookback period (default 730 days)
    to compute what the cumulative exposure would be if the new
    categories were added.

    Args:
        capability_group: Logical grouping to check.
        new_categories: list of new category strings being considered.
        db_path: Optional database path override.

    Returns:
        dict with would_trigger (bool), cumulative categories,
        and triggered rules.
    """
    if not capability_group:
        raise ValueError("capability_group is required")
    if not new_categories:
        raise ValueError("new_categories must be a non-empty list")

    for cat in new_categories:
        if cat not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{cat}'. Must be one of: {VALID_CATEGORIES}"
            )

    cutoff = _lookback_cutoff()

    conn = _get_db(db_path)
    try:
        prior_rows = conn.execute(
            "SELECT categories_exposed FROM cag_exposure_register "
            "WHERE capability_group = ? AND exposure_date >= ? "
            "ORDER BY exposure_date",
            (capability_group, cutoff),
        ).fetchall()
    finally:
        conn.close()

    # Build cumulative set
    existing = set()
    for row in prior_rows:
        cats = row["categories_exposed"]
        if cats:
            try:
                existing.update(json.loads(cats))
            except (json.JSONDecodeError, TypeError):
                pass

    proposed = existing | set(new_categories)

    # Check against rules
    from tools.cag.rules_engine import check_combination
    combo_result = check_combination(sorted(proposed), db_path=db_path)

    # Also check existing alone to see if the NEW categories cause the trigger
    existing_result = check_combination(sorted(existing), db_path=db_path)

    newly_triggered = []
    if combo_result.get("triggered", False):
        new_rule_ids = set(
            r["rule_id"] for r in combo_result.get("rules", [])
        )
        old_rule_ids = set(
            r["rule_id"] for r in existing_result.get("rules", [])
        )
        for rule in combo_result.get("rules", []):
            if rule["rule_id"] not in old_rule_ids:
                newly_triggered.append(rule)

    return {
        "capability_group": capability_group,
        "existing_categories": sorted(existing),
        "new_categories": sorted(new_categories),
        "cumulative_categories": sorted(proposed),
        "would_trigger": len(newly_triggered) > 0,
        "newly_triggered_rules": newly_triggered,
        "all_triggered_rules": combo_result.get("rules", []),
        "max_severity": combo_result.get("max_severity"),
        "resulting_classification": combo_result.get("resulting_classification"),
        "risk_score": combo_result.get("risk_score", 0),
        "lookback_days": _get_lookback_days(),
        "checked_at": _now(),
    }


def get_exposure_report(capability_group=None, db_path=None):
    """Generate a report of all exposures with cumulative analysis.

    Args:
        capability_group: Optional filter by capability group.
        db_path: Optional database path override.

    Returns:
        dict with exposure entries and cumulative summaries per group.
    """
    cutoff = _lookback_cutoff()
    conn = _get_db(db_path)
    try:
        if capability_group:
            rows = conn.execute(
                "SELECT e.id, e.capability_group, e.proposal_id, "
                "e.categories_exposed, e.audience, e.exposure_date, "
                "e.cumulative_categories, e.cumulative_classification, "
                "e.alert_generated, e.created_at, "
                "p.title AS proposal_title "
                "FROM cag_exposure_register e "
                "LEFT JOIN proposals p ON e.proposal_id = p.id "
                "WHERE e.capability_group = ? AND e.exposure_date >= ? "
                "ORDER BY e.exposure_date DESC",
                (capability_group, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT e.id, e.capability_group, e.proposal_id, "
                "e.categories_exposed, e.audience, e.exposure_date, "
                "e.cumulative_categories, e.cumulative_classification, "
                "e.alert_generated, e.created_at, "
                "p.title AS proposal_title "
                "FROM cag_exposure_register e "
                "LEFT JOIN proposals p ON e.proposal_id = p.id "
                "WHERE e.exposure_date >= ? "
                "ORDER BY e.capability_group, e.exposure_date DESC",
                (cutoff,),
            ).fetchall()
    finally:
        conn.close()

    entries = []
    group_summaries = {}

    for row in rows:
        entry = dict(row)
        # Parse JSON fields
        for field in ("categories_exposed", "cumulative_categories"):
            if entry.get(field):
                try:
                    entry[field] = json.loads(entry[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        entries.append(entry)

        # Build group summaries
        grp = entry["capability_group"]
        if grp not in group_summaries:
            group_summaries[grp] = {
                "capability_group": grp,
                "proposal_count": 0,
                "cumulative_categories": set(),
                "alert_count": 0,
                "latest_exposure": None,
                "audiences": set(),
            }
        gs = group_summaries[grp]
        gs["proposal_count"] += 1
        cats = entry.get("cumulative_categories", [])
        if isinstance(cats, list):
            gs["cumulative_categories"].update(cats)
        if entry.get("alert_generated"):
            gs["alert_count"] += 1
        if entry.get("audience"):
            gs["audiences"].add(entry["audience"])
        if gs["latest_exposure"] is None or (
            entry.get("exposure_date", "") > (gs["latest_exposure"] or "")
        ):
            gs["latest_exposure"] = entry.get("exposure_date")

    # Convert sets to sorted lists for JSON serialization
    for gs in group_summaries.values():
        gs["cumulative_categories"] = sorted(gs["cumulative_categories"])
        gs["cumulative_count"] = len(gs["cumulative_categories"])
        gs["audiences"] = sorted(gs["audiences"])

    return {
        "filter": capability_group or "all",
        "lookback_days": _get_lookback_days(),
        "exposure_count": len(entries),
        "group_count": len(group_summaries),
        "exposures": entries,
        "group_summaries": list(group_summaries.values()),
        "generated_at": _now(),
    }


def scan_cross_proposal(proposal_id, db_path=None):
    """Check cumulative exposure for each capability group in a proposal.

    For each capability group mentioned in the proposal's existing
    exposure records, checks whether the cumulative exposure across
    all prior proposals triggers any aggregation rule.

    Args:
        proposal_id: ID of the proposals record.
        db_path: Optional database path override.

    Returns:
        dict with cross-proposal scan results per capability group.
    """
    conn = _get_db(db_path)
    try:
        prop = conn.execute(
            "SELECT id, title FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not prop:
            raise ValueError(f"Proposal not found: {proposal_id}")

        # Get all capability groups this proposal is registered in
        groups = conn.execute(
            "SELECT DISTINCT capability_group FROM cag_exposure_register "
            "WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchall()
        group_names = [g["capability_group"] for g in groups]

        # Also get the proposal's CAG tags to detect capability groups
        # from section content if not already registered
        section_tags = conn.execute(
            "SELECT DISTINCT category FROM cag_data_tags "
            "WHERE source_type = 'proposal_section' "
            "AND source_id IN ("
            "  SELECT id FROM proposal_sections WHERE proposal_id = ?"
            ")",
            (proposal_id,),
        ).fetchall()
        proposal_categories = sorted(set(t["category"] for t in section_tags))
    finally:
        conn.close()

    # If no registered groups, check all known groups for overlap
    if not group_names:
        conn = _get_db(db_path)
        try:
            all_groups = conn.execute(
                "SELECT DISTINCT capability_group FROM cag_exposure_register"
            ).fetchall()
            group_names = [g["capability_group"] for g in all_groups]
        finally:
            conn.close()

    results = []
    overall_risk = 0.0

    for group_name in group_names:
        if proposal_categories:
            check_result = check_cumulative(
                group_name, proposal_categories, db_path=db_path
            )
        else:
            check_result = {
                "capability_group": group_name,
                "would_trigger": False,
                "newly_triggered_rules": [],
                "risk_score": 0.0,
            }

        results.append({
            "capability_group": group_name,
            "would_trigger": check_result.get("would_trigger", False),
            "existing_categories": check_result.get("existing_categories", []),
            "proposal_categories": proposal_categories,
            "cumulative_categories": check_result.get("cumulative_categories", []),
            "newly_triggered_rules": check_result.get("newly_triggered_rules", []),
            "risk_score": check_result.get("risk_score", 0),
            "max_severity": check_result.get("max_severity"),
            "resulting_classification": check_result.get("resulting_classification"),
        })

        if check_result.get("risk_score", 0) > overall_risk:
            overall_risk = check_result["risk_score"]

    # Audit the cross-proposal scan
    conn = _get_db(db_path)
    try:
        triggered_groups = [r for r in results if r.get("would_trigger")]
        _audit(
            conn, "cag.scan_cross_proposal", "auto",
            f"Cross-proposal scan for {proposal_id}: "
            f"{len(group_names)} groups checked, "
            f"{len(triggered_groups)} triggered",
            entity_type="proposal", entity_id=proposal_id,
            details=json.dumps({
                "groups_checked": len(group_names),
                "groups_triggered": len(triggered_groups),
                "overall_risk": overall_risk,
            }),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "proposal_id": proposal_id,
        "proposal_categories": proposal_categories,
        "groups_checked": len(group_names),
        "groups_triggered": len([r for r in results if r["would_trigger"]]),
        "overall_risk_score": round(overall_risk, 3),
        "group_results": results,
        "scanned_at": _now(),
    }


def get_capability_groups(db_path=None):
    """List all tracked capability groups with cumulative status.

    Args:
        db_path: Optional database path override.

    Returns:
        list of capability group summaries.
    """
    cutoff = _lookback_cutoff()
    conn = _get_db(db_path)
    try:
        groups = conn.execute(
            "SELECT capability_group, "
            "COUNT(*) AS exposure_count, "
            "SUM(CASE WHEN alert_generated = 1 THEN 1 ELSE 0 END) AS alert_count, "
            "MAX(exposure_date) AS latest_exposure, "
            "GROUP_CONCAT(DISTINCT audience) AS audiences "
            "FROM cag_exposure_register "
            "WHERE exposure_date >= ? "
            "GROUP BY capability_group "
            "ORDER BY capability_group",
            (cutoff,),
        ).fetchall()

        results = []
        for group in groups:
            group = dict(group)

            # Get cumulative categories
            cat_rows = conn.execute(
                "SELECT categories_exposed FROM cag_exposure_register "
                "WHERE capability_group = ? AND exposure_date >= ?",
                (group["capability_group"], cutoff),
            ).fetchall()

            cumulative = set()
            for row in cat_rows:
                cats = row["categories_exposed"]
                if cats:
                    try:
                        cumulative.update(json.loads(cats))
                    except (json.JSONDecodeError, TypeError):
                        pass

            group["cumulative_categories"] = sorted(cumulative)
            group["cumulative_count"] = len(cumulative)
            group["audiences"] = (
                group["audiences"].split(",") if group.get("audiences") else []
            )
            results.append(group)

    finally:
        conn.close()

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for CAG exposure register."""
    parser = argparse.ArgumentParser(
        description="CAG Layer 4: Cross-proposal aggregation tracking"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--register", action="store_true",
        help="Register categories exposed in a proposal",
    )
    group.add_argument(
        "--check", action="store_true",
        help="Check if new categories would trigger cumulative rule",
    )
    group.add_argument(
        "--report", action="store_true",
        help="Generate exposure report",
    )
    group.add_argument(
        "--scan-cross", action="store_true",
        help="Cross-proposal scan for a specific proposal",
    )
    group.add_argument(
        "--groups", action="store_true",
        help="List all tracked capability groups",
    )

    parser.add_argument("--proposal-id", help="Proposal ID")
    parser.add_argument("--capability-group", help="Capability group name")
    parser.add_argument("--categories", help="Comma-separated categories")
    parser.add_argument("--new-categories", help="Comma-separated new categories (for --check)")
    parser.add_argument("--audience", help="Audience description")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    db = args.db_path

    try:
        if args.register:
            if not args.proposal_id:
                parser.error("--register requires --proposal-id")
            if not args.capability_group:
                parser.error("--register requires --capability-group")
            if not args.categories:
                parser.error("--register requires --categories")
            cats = [c.strip() for c in args.categories.split(",") if c.strip()]
            output = register_exposure(
                args.proposal_id, args.capability_group, cats,
                audience=args.audience, db_path=db,
            )
            output["status"] = "registered"

        elif args.check:
            if not args.capability_group:
                parser.error("--check requires --capability-group")
            if not args.new_categories:
                parser.error("--check requires --new-categories")
            cats = [c.strip() for c in args.new_categories.split(",") if c.strip()]
            output = check_cumulative(
                args.capability_group, cats, db_path=db,
            )
            output["status"] = "checked"

        elif args.report:
            output = get_exposure_report(
                capability_group=args.capability_group, db_path=db,
            )
            output["status"] = "generated"

        elif args.scan_cross:
            if not args.proposal_id:
                parser.error("--scan-cross requires --proposal-id")
            output = scan_cross_proposal(args.proposal_id, db_path=db)
            output["status"] = "scanned"

        elif args.groups:
            groups = get_capability_groups(db_path=db)
            output = {
                "status": "listed",
                "group_count": len(groups),
                "groups": groups,
            }

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
    print(f"CAG Exposure Register -- {status.upper()}")
    print("=" * 60)

    if status == "registered":
        print(f"  Exposure ID:       {output.get('exposure_id', '?')}")
        print(f"  Proposal:          {output.get('proposal_id', '?')}")
        print(f"  Capability Group:  {output.get('capability_group', '?')}")
        cats = output.get("categories_exposed", [])
        print(f"  Categories:        {', '.join(cats)}")
        aud = output.get("audience")
        if aud:
            print(f"  Audience:          {aud}")
        cum = output.get("cumulative_categories", [])
        print(f"  Cumulative:        {', '.join(cum)} ({output.get('cumulative_count', 0)})")
        print(f"  Classification:    {output.get('cumulative_classification', '?')}")
        alert = output.get("alert_generated", False)
        print(f"  Alert generated:   {'YES' if alert else 'NO'}")
        if alert and output.get("alert_details"):
            ad = output["alert_details"]
            print(f"    Alert ID:    {ad.get('alert_id', '?')}")
            print(f"    Severity:    {ad.get('severity', '?')}")
            print(f"    Risk score:  {ad.get('risk_score', 0):.3f}")

    elif status == "checked":
        grp = output.get("capability_group", "?")
        trigger = output.get("would_trigger", False)
        print(f"  Capability Group:     {grp}")
        print(f"  Would trigger:        {'YES' if trigger else 'NO'}")
        existing = output.get("existing_categories", [])
        new_cats = output.get("new_categories", [])
        cum = output.get("cumulative_categories", [])
        print(f"  Existing categories:  {', '.join(existing) if existing else 'none'}")
        print(f"  New categories:       {', '.join(new_cats)}")
        print(f"  Cumulative would be:  {', '.join(cum)}")
        if trigger:
            print(f"  Max severity:         {output.get('max_severity', '?')}")
            print(f"  Classification:       {output.get('resulting_classification', '?')}")
            print(f"  Risk score:           {output.get('risk_score', 0):.3f}")
            print()
            for rule in output.get("newly_triggered_rules", []):
                print(f"  NEW TRIGGER: [{rule.get('severity', '?')}] {rule.get('rule_name', '?')}")
                print(f"    Categories: {', '.join(rule.get('triggered_categories', []))}")
                rem = rule.get("remediation", "")
                if rem:
                    print(f"    Remediation: {rem}")

    elif status == "generated":
        filt = output.get("filter", "all")
        lookback = output.get("lookback_days", "?")
        count = output.get("exposure_count", 0)
        gcount = output.get("group_count", 0)
        print(f"  Filter:          {filt}")
        print(f"  Lookback:        {lookback} days")
        print(f"  Exposures:       {count}")
        print(f"  Groups:          {gcount}")
        print()

        for gs in output.get("group_summaries", []):
            grp = gs.get("capability_group", "?")
            pcnt = gs.get("proposal_count", 0)
            acnt = gs.get("alert_count", 0)
            cum = gs.get("cumulative_categories", [])
            latest = gs.get("latest_exposure", "?")
            print(f"  [{grp}]")
            print(f"    Proposals:   {pcnt}")
            print(f"    Alerts:      {acnt}")
            print(f"    Cumulative:  {', '.join(cum)} ({gs.get('cumulative_count', 0)})")
            print(f"    Latest:      {latest}")
            print()

    elif status == "scanned":
        pid = output.get("proposal_id", "?")
        checked = output.get("groups_checked", 0)
        triggered = output.get("groups_triggered", 0)
        risk = output.get("overall_risk_score", 0)
        pcats = output.get("proposal_categories", [])

        print(f"  Proposal:           {pid}")
        print(f"  Proposal categories: {', '.join(pcats) if pcats else 'none'}")
        print(f"  Groups checked:     {checked}")
        print(f"  Groups triggered:   {triggered}")
        print(f"  Overall risk:       {risk:.3f}")

        if triggered > 0:
            print()
            for gr in output.get("group_results", []):
                if gr.get("would_trigger"):
                    grp = gr.get("capability_group", "?")
                    sev = gr.get("max_severity", "?")
                    cls = gr.get("resulting_classification", "?")
                    cum = gr.get("cumulative_categories", [])
                    print(f"  TRIGGERED: [{sev}] {grp}")
                    print(f"    Classification: {cls}")
                    print(f"    Cumulative:     {', '.join(cum)}")
                    for rule in gr.get("newly_triggered_rules", []):
                        print(f"    Rule: {rule.get('rule_name', '?')}")
                    print()

    elif status == "listed":
        count = output.get("group_count", 0)
        print(f"  Capability groups: {count}")
        print()
        for g in output.get("groups", []):
            grp = g.get("capability_group", "?")
            ecnt = g.get("exposure_count", 0)
            acnt = g.get("alert_count", 0)
            cum = g.get("cumulative_categories", [])
            latest = g.get("latest_exposure", "?")
            print(f"  [{grp}]")
            print(f"    Exposures:   {ecnt}")
            print(f"    Alerts:      {acnt}")
            print(f"    Cumulative:  {', '.join(cum)} ({g.get('cumulative_count', 0)})")
            print(f"    Latest:      {latest}")
            print()

    else:
        for key, val in output.items():
            if key != "status":
                print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
