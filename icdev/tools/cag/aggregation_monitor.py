#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""CAG Layer 3 -- Real-time combination tracking as proposal sections assemble.

Scans proposals for aggregation risks by building a document-level category
matrix, then evaluating within-section, cross-section, and cross-volume
combinations against the CAG rules engine.

Proximity scoring:
    - Same paragraph:  0.9 multiplier
    - Same section:    0.7 multiplier
    - Same volume:     0.4 multiplier
    - Cross-volume:    0.2 multiplier

When a rule triggers, a cag_alerts record is created and the proposal's
cag_status is updated accordingly.

Usage:
    python tools/cag/aggregation_monitor.py --scan --proposal-id "prop-1" [--json]
    python tools/cag/aggregation_monitor.py --scan-section --section-id "sec-1" [--json]
    python tools/cag/aggregation_monitor.py --check-export --proposal-id "prop-1" [--json]
    python tools/cag/aggregation_monitor.py --history [--proposal-id "prop-1"] [--json]
    python tools/cag/aggregation_monitor.py --matrix --proposal-id "prop-1" [--json]
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

# --- Proximity multipliers (defaults, overridden by YAML if available) ---
DEFAULT_PROXIMITY = {
    "same_paragraph": 0.9,
    "same_section": 0.7,
    "same_volume": 0.4,
    "cross_volume": 0.2,
}

# --- CAG status priority (highest severity wins) ---
STATUS_PRIORITY = {
    "quarantined": 4,
    "blocked": 3,
    "alert": 2,
    "clear": 1,
    "pending": 0,
}

# Action to cag_status mapping
ACTION_TO_STATUS = {
    "quarantine": "quarantined",
    "block_and_alert": "blocked",
    "review_required": "alert",
    "alert": "alert",
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


def _gen_id(prefix="alert"):
    """Generate a unique ID."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _load_proximity_config():
    """Load proximity multipliers from cag_rules.yaml or use defaults."""
    if yaml is None or not CAG_RULES_PATH.exists():
        return DEFAULT_PROXIMITY

    try:
        with open(CAG_RULES_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        prox = data.get("proximity", {})
        return {
            "same_paragraph": prox.get("same_paragraph", DEFAULT_PROXIMITY["same_paragraph"]),
            "same_section": prox.get("same_section", DEFAULT_PROXIMITY["same_section"]),
            "same_volume": prox.get("same_volume", DEFAULT_PROXIMITY["same_volume"]),
            "cross_volume": prox.get("cross_volume", DEFAULT_PROXIMITY["cross_volume"]),
        }
    except Exception:
        return DEFAULT_PROXIMITY


def _get_tags_for_source(conn, source_type, source_id):
    """Retrieve CAG tags for a source from the database.

    Returns:
        list of tag dicts with category, confidence, paragraph_index, etc.
    """
    rows = conn.execute(
        "SELECT id, category, confidence, indicator_text, indicator_type, "
        "position_start, position_end, paragraph_index, section_context "
        "FROM cag_data_tags "
        "WHERE source_type = ? AND source_id = ? "
        "ORDER BY position_start",
        (source_type, source_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _compute_proximity_score(tags_a, tags_b, relationship, proximity_config):
    """Compute proximity score between two sets of tags.

    Args:
        tags_a: list of tag dicts from source A.
        tags_b: list of tag dicts from source B.
        relationship: one of same_paragraph, same_section, same_volume,
                      cross_volume.
        proximity_config: dict of proximity multipliers.

    Returns:
        float proximity score (0.0 to 1.0).
    """
    base = proximity_config.get(relationship, 0.2)

    # Boost based on confidence of contributing tags
    confidences_a = [t.get("confidence", 0.5) for t in tags_a] if tags_a else [0.5]
    confidences_b = [t.get("confidence", 0.5) for t in tags_b] if tags_b else [0.5]
    avg_confidence = (
        (sum(confidences_a) / len(confidences_a))
        + (sum(confidences_b) / len(confidences_b))
    ) / 2.0

    return round(base * avg_confidence, 4)


# ---------------------------------------------------------------------------
# Core scanning
# ---------------------------------------------------------------------------

def scan_proposal(proposal_id, db_path=None):
    """Full aggregation scan of an entire proposal.

    For each section, retrieves tags. Builds a document-level category matrix.
    Checks within-section, cross-section, and cross-volume combinations
    against the rules engine. Creates cag_alerts for triggered rules.

    Args:
        proposal_id: ID of the proposals record.
        db_path: Optional database path override.

    Returns:
        dict with scan results: proposal_id, alerts, category_matrix,
        scan_summary, cag_status.
    """
    # Import rules engine locally to avoid circular imports
    from tools.cag.rules_engine import evaluate_tags, get_active_rules

    proximity_config = _load_proximity_config()
    conn = _get_db(db_path)

    try:
        # Verify proposal exists
        prop = conn.execute(
            "SELECT id, title, cag_status FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not prop:
            raise ValueError(f"Proposal not found: {proposal_id}")

        # Get all sections grouped by volume
        sections = conn.execute(
            "SELECT id, volume, section_number, section_title "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()
        sections = [dict(s) for s in sections]

        # Build document-level category matrix
        # matrix[section_id] = {tags: [...], categories: set(), volume: str}
        matrix = {}
        volume_categories = {}  # volume -> set of categories
        all_categories = set()

        for section in sections:
            sid = section["id"]
            vol = section["volume"]
            tags = _get_tags_for_source(conn, "proposal_section", sid)
            cats = set(t["category"] for t in tags)

            matrix[sid] = {
                "section_id": sid,
                "volume": vol,
                "section_number": section["section_number"],
                "section_title": section["section_title"],
                "tags": tags,
                "categories": cats,
            }

            if vol not in volume_categories:
                volume_categories[vol] = set()
            volume_categories[vol].update(cats)
            all_categories.update(cats)

    finally:
        conn.close()

    # Load active rules
    active_rules = get_active_rules(db_path=db_path)

    alerts = []

    # 1. Within-section checks
    for sid, sec_data in matrix.items():
        if not sec_data["categories"]:
            continue

        # Check paragraph-level proximity within the section
        para_groups = {}
        for tag in sec_data["tags"]:
            pidx = tag.get("paragraph_index", 0)
            if pidx not in para_groups:
                para_groups[pidx] = {"tags": [], "categories": set()}
            para_groups[pidx]["tags"].append(tag)
            para_groups[pidx]["categories"].add(tag["category"])

        # Check each paragraph
        for pidx, pdata in para_groups.items():
            if len(pdata["categories"]) < 2:
                continue
            triggered = evaluate_tags(
                list(pdata["categories"]), rule_set=active_rules, db_path=db_path
            )
            for rule_result in triggered:
                prox = proximity_config["same_paragraph"]
                alerts.append({
                    "rule_id": rule_result["rule_id"],
                    "rule_name": rule_result["rule_name"],
                    "severity": rule_result["severity"],
                    "action": rule_result["action"],
                    "resulting_classification": rule_result["resulting_classification"],
                    "remediation": rule_result["remediation"],
                    "categories_triggered": rule_result["triggered_categories"],
                    "source_elements": [sid],
                    "proximity_type": "same_paragraph",
                    "proximity_score": prox,
                    "paragraph_index": pidx,
                })

        # Check section-level combination
        if len(sec_data["categories"]) >= 2:
            triggered = evaluate_tags(
                list(sec_data["categories"]), rule_set=active_rules, db_path=db_path
            )
            for rule_result in triggered:
                # Only add if not already found at paragraph level
                already = any(
                    a["rule_id"] == rule_result["rule_id"]
                    and set(a["source_elements"]) == {sid}
                    for a in alerts
                )
                if not already:
                    prox = proximity_config["same_section"]
                    alerts.append({
                        "rule_id": rule_result["rule_id"],
                        "rule_name": rule_result["rule_name"],
                        "severity": rule_result["severity"],
                        "action": rule_result["action"],
                        "resulting_classification": rule_result["resulting_classification"],
                        "remediation": rule_result["remediation"],
                        "categories_triggered": rule_result["triggered_categories"],
                        "source_elements": [sid],
                        "proximity_type": "same_section",
                        "proximity_score": prox,
                    })

    # 2. Cross-section (same volume) checks
    section_ids = list(matrix.keys())
    for i, sid_a in enumerate(section_ids):
        for sid_b in section_ids[i + 1:]:
            sec_a = matrix[sid_a]
            sec_b = matrix[sid_b]
            if sec_a["volume"] != sec_b["volume"]:
                continue
            combined_cats = sec_a["categories"] | sec_b["categories"]
            if len(combined_cats) < 2:
                continue

            triggered = evaluate_tags(
                list(combined_cats), rule_set=active_rules, db_path=db_path
            )
            for rule_result in triggered:
                # Only add if the combination requires both sections
                a_has = set(rule_result["triggered_categories"]) & sec_a["categories"]
                b_has = set(rule_result["triggered_categories"]) & sec_b["categories"]
                if not (a_has and b_has):
                    continue

                # Skip if already detected within a single section
                already = any(
                    a["rule_id"] == rule_result["rule_id"]
                    and len(a["source_elements"]) == 1
                    and a["source_elements"][0] in (sid_a, sid_b)
                    for a in alerts
                )
                if already:
                    continue

                prox = _compute_proximity_score(
                    sec_a["tags"], sec_b["tags"],
                    "same_volume", proximity_config,
                )
                alerts.append({
                    "rule_id": rule_result["rule_id"],
                    "rule_name": rule_result["rule_name"],
                    "severity": rule_result["severity"],
                    "action": rule_result["action"],
                    "resulting_classification": rule_result["resulting_classification"],
                    "remediation": rule_result["remediation"],
                    "categories_triggered": rule_result["triggered_categories"],
                    "source_elements": sorted([sid_a, sid_b]),
                    "proximity_type": "same_volume",
                    "proximity_score": prox,
                })

    # 3. Cross-volume checks
    volumes = list(volume_categories.keys())
    for i, vol_a in enumerate(volumes):
        for vol_b in volumes[i + 1:]:
            combined_cats = volume_categories[vol_a] | volume_categories[vol_b]
            if len(combined_cats) < 2:
                continue

            triggered = evaluate_tags(
                list(combined_cats), rule_set=active_rules, db_path=db_path
            )
            for rule_result in triggered:
                a_has = set(rule_result["triggered_categories"]) & volume_categories[vol_a]
                b_has = set(rule_result["triggered_categories"]) & volume_categories[vol_b]
                if not (a_has and b_has):
                    continue

                # Find contributing section IDs
                source_sids = []
                for sid, sec_data in matrix.items():
                    if sec_data["volume"] in (vol_a, vol_b):
                        if sec_data["categories"] & set(rule_result["triggered_categories"]):
                            source_sids.append(sid)

                prox = proximity_config["cross_volume"]
                alerts.append({
                    "rule_id": rule_result["rule_id"],
                    "rule_name": rule_result["rule_name"],
                    "severity": rule_result["severity"],
                    "action": rule_result["action"],
                    "resulting_classification": rule_result["resulting_classification"],
                    "remediation": rule_result["remediation"],
                    "categories_triggered": rule_result["triggered_categories"],
                    "source_elements": sorted(set(source_sids)),
                    "proximity_type": "cross_volume",
                    "proximity_score": prox,
                })

    # Deduplicate alerts by (rule_id, frozenset(source_elements))
    seen = set()
    unique_alerts = []
    for alert in alerts:
        key = (alert["rule_id"], frozenset(alert["source_elements"]))
        if key not in seen:
            seen.add(key)
            unique_alerts.append(alert)
    alerts = unique_alerts

    # Sort by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 99))

    # Store alerts and update proposal status
    conn = _get_db(db_path)
    try:
        new_cag_status = "clear"

        for alert in alerts:
            alert_id = _gen_id()
            alert["alert_id"] = alert_id

            conn.execute(
                "INSERT INTO cag_alerts "
                "(id, proposal_id, rule_id, severity, status, "
                "categories_triggered, source_elements, proximity_score, "
                "resulting_classification, remediation_suggestion, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alert_id, proposal_id, alert["rule_id"],
                    alert["severity"], "open",
                    json.dumps(alert["categories_triggered"]),
                    json.dumps(alert["source_elements"]),
                    alert.get("proximity_score"),
                    alert["resulting_classification"],
                    alert.get("remediation", ""),
                    _now(),
                ),
            )

            # Determine highest status
            alert_status = ACTION_TO_STATUS.get(alert["action"], "alert")
            if STATUS_PRIORITY.get(alert_status, 0) > STATUS_PRIORITY.get(new_cag_status, 0):
                new_cag_status = alert_status

        # Update proposal cag_status
        conn.execute(
            "UPDATE proposals SET cag_status = ?, cag_last_scan = ?, updated_at = ? "
            "WHERE id = ?",
            (new_cag_status, _now(), _now(), proposal_id),
        )

        _audit(
            conn, "cag.scan_proposal", "auto",
            f"Scanned proposal {proposal_id}: "
            f"{len(alerts)} alerts, status={new_cag_status}",
            entity_type="proposal", entity_id=proposal_id,
            details=json.dumps({
                "alert_count": len(alerts),
                "cag_status": new_cag_status,
                "categories_found": sorted(all_categories),
            }),
        )
        conn.commit()
    finally:
        conn.close()

    # Build category matrix output
    category_matrix = {}
    for sid, sec_data in matrix.items():
        category_matrix[sid] = {
            "volume": sec_data["volume"],
            "section_number": sec_data["section_number"],
            "section_title": sec_data["section_title"],
            "categories": sorted(sec_data["categories"]),
            "tag_count": len(sec_data["tags"]),
        }

    return {
        "proposal_id": proposal_id,
        "cag_status": new_cag_status,
        "total_alerts": len(alerts),
        "alerts": alerts,
        "category_matrix": category_matrix,
        "categories_found": sorted(all_categories),
        "sections_scanned": len(sections),
        "volumes_scanned": sorted(volume_categories.keys()),
        "scanned_at": _now(),
    }


def scan_section(section_id, db_path=None):
    """Scan a single proposal section for aggregation risks.

    Evaluates the section's tags against active rules. This is useful
    for real-time checking as content is being written.

    Args:
        section_id: ID of the proposal_sections record.
        db_path: Optional database path override.

    Returns:
        dict with section scan results.
    """
    from tools.cag.rules_engine import evaluate_tags, get_active_rules

    conn = _get_db(db_path)
    try:
        section = conn.execute(
            "SELECT id, proposal_id, volume, section_number, section_title "
            "FROM proposal_sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if not section:
            raise ValueError(f"Section not found: {section_id}")
        section = dict(section)

        tags = _get_tags_for_source(conn, "proposal_section", section_id)
    finally:
        conn.close()

    categories = set(t["category"] for t in tags)
    active_rules = get_active_rules(db_path=db_path)
    triggered = evaluate_tags(list(categories), rule_set=active_rules, db_path=db_path)

    return {
        "section_id": section_id,
        "proposal_id": section["proposal_id"],
        "volume": section["volume"],
        "section_number": section["section_number"],
        "categories": sorted(categories),
        "tag_count": len(tags),
        "rules_triggered": len(triggered),
        "triggered_rules": triggered,
        "scanned_at": _now(),
    }


def check_before_export(proposal_id, db_path=None):
    """Pre-export aggregation check for a proposal.

    Runs a full scan and returns pass/fail with any blocking alerts.
    A proposal passes only if there are no open CRITICAL or HIGH alerts
    with block_and_alert or quarantine actions.

    Args:
        proposal_id: ID of the proposals record.
        db_path: Optional database path override.

    Returns:
        dict with export_allowed (bool), blocking_alerts list,
        and cag_status.
    """
    scan_result = scan_proposal(proposal_id, db_path=db_path)

    blocking_alerts = [
        alert for alert in scan_result.get("alerts", [])
        if alert.get("action") in ("block_and_alert", "quarantine")
    ]

    # Also check existing unresolved alerts
    conn = _get_db(db_path)
    try:
        existing_open = conn.execute(
            "SELECT id, rule_id, severity, categories_triggered, "
            "resulting_classification, remediation_suggestion "
            "FROM cag_alerts "
            "WHERE proposal_id = ? AND status IN ('open', 'quarantined') "
            "AND severity IN ('CRITICAL', 'HIGH') "
            "ORDER BY severity",
            (proposal_id,),
        ).fetchall()
        existing_blocking = [dict(row) for row in existing_open]
    finally:
        conn.close()

    export_allowed = (
        len(blocking_alerts) == 0 and len(existing_blocking) == 0
    )

    return {
        "proposal_id": proposal_id,
        "export_allowed": export_allowed,
        "cag_status": scan_result.get("cag_status", "pending"),
        "blocking_alerts_new": len(blocking_alerts),
        "blocking_alerts_existing": len(existing_blocking),
        "blocking_alerts": blocking_alerts,
        "existing_unresolved": existing_blocking,
        "total_alerts": scan_result.get("total_alerts", 0),
        "checked_at": _now(),
    }


def get_scan_history(proposal_id=None, db_path=None):
    """Retrieve scan history from the audit trail.

    Args:
        proposal_id: Optional filter by proposal.
        db_path: Optional database path override.

    Returns:
        list of scan event dicts.
    """
    conn = _get_db(db_path)
    try:
        if proposal_id:
            rows = conn.execute(
                "SELECT id, event_type, actor, action, entity_type, entity_id, "
                "details, created_at "
                "FROM audit_trail "
                "WHERE event_type LIKE 'cag.scan%' AND entity_id = ? "
                "ORDER BY created_at DESC "
                "LIMIT 100",
                (proposal_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, event_type, actor, action, entity_type, entity_id, "
                "details, created_at "
                "FROM audit_trail "
                "WHERE event_type LIKE 'cag.scan%' "
                "ORDER BY created_at DESC "
                "LIMIT 100",
            ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        entry = dict(row)
        if entry.get("details"):
            try:
                entry["details"] = json.loads(entry["details"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(entry)

    return results


def get_document_matrix(proposal_id, db_path=None):
    """Return the current category matrix for a proposal.

    Shows which categories appear in which sections, organized by volume.

    Args:
        proposal_id: ID of the proposals record.
        db_path: Optional database path override.

    Returns:
        dict with volume-level and section-level category breakdown.
    """
    conn = _get_db(db_path)
    try:
        prop = conn.execute(
            "SELECT id, title, cag_status FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not prop:
            raise ValueError(f"Proposal not found: {proposal_id}")

        sections = conn.execute(
            "SELECT id, volume, section_number, section_title "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        matrix = {}
        volume_summary = {}
        all_categories = set()

        for section in sections:
            section = dict(section)
            sid = section["id"]
            vol = section["volume"]

            tags = _get_tags_for_source(conn, "proposal_section", sid)
            categories = sorted(set(t["category"] for t in tags))

            matrix[sid] = {
                "volume": vol,
                "section_number": section["section_number"],
                "section_title": section["section_title"],
                "categories": categories,
                "tag_count": len(tags),
                "strong_tags": sum(1 for t in tags if t.get("indicator_type") == "strong"),
                "moderate_tags": sum(1 for t in tags if t.get("indicator_type") == "moderate"),
                "manual_tags": sum(1 for t in tags if t.get("indicator_type") == "manual"),
            }

            if vol not in volume_summary:
                volume_summary[vol] = {"categories": set(), "section_count": 0, "total_tags": 0}
            volume_summary[vol]["categories"].update(categories)
            volume_summary[vol]["section_count"] += 1
            volume_summary[vol]["total_tags"] += len(tags)
            all_categories.update(categories)

    finally:
        conn.close()

    # Convert sets to sorted lists for JSON serialization
    for vol_data in volume_summary.values():
        vol_data["categories"] = sorted(vol_data["categories"])

    return {
        "proposal_id": proposal_id,
        "proposal_title": prop["title"] if prop else "Unknown",
        "cag_status": prop["cag_status"] if prop else "pending",
        "all_categories": sorted(all_categories),
        "category_count": len(all_categories),
        "volume_summary": volume_summary,
        "section_matrix": matrix,
        "generated_at": _now(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for CAG aggregation monitor."""
    parser = argparse.ArgumentParser(
        description="CAG Layer 3: Real-time aggregation monitoring"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scan", action="store_true",
        help="Full aggregation scan of a proposal",
    )
    group.add_argument(
        "--scan-section", action="store_true",
        help="Scan a single proposal section",
    )
    group.add_argument(
        "--check-export", action="store_true",
        help="Pre-export aggregation check (pass/fail)",
    )
    group.add_argument(
        "--history", action="store_true",
        help="Show scan history",
    )
    group.add_argument(
        "--matrix", action="store_true",
        help="Show document category matrix",
    )

    parser.add_argument("--proposal-id", help="Proposal ID")
    parser.add_argument("--section-id", help="Section ID (for --scan-section)")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    db = args.db_path

    try:
        if args.scan:
            if not args.proposal_id:
                parser.error("--scan requires --proposal-id")
            output = scan_proposal(args.proposal_id, db_path=db)
            output["status"] = "scanned"

        elif args.scan_section:
            if not args.section_id:
                parser.error("--scan-section requires --section-id")
            output = scan_section(args.section_id, db_path=db)
            output["status"] = "scanned"

        elif args.check_export:
            if not args.proposal_id:
                parser.error("--check-export requires --proposal-id")
            output = check_before_export(args.proposal_id, db_path=db)
            output["status"] = "checked"

        elif args.history:
            history = get_scan_history(
                proposal_id=args.proposal_id, db_path=db
            )
            output = {
                "status": "retrieved",
                "proposal_id": args.proposal_id,
                "scan_count": len(history),
                "scans": history,
            }

        elif args.matrix:
            if not args.proposal_id:
                parser.error("--matrix requires --proposal-id")
            output = get_document_matrix(args.proposal_id, db_path=db)
            output["status"] = "generated"

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
    print(f"CAG Aggregation Monitor -- {status.upper()}")
    print("=" * 60)

    if status == "scanned" and "total_alerts" in output:
        pid = output.get("proposal_id", "?")
        cag = output.get("cag_status", "?")
        total = output.get("total_alerts", 0)
        cats = output.get("categories_found", [])
        sects = output.get("sections_scanned", 0)

        print(f"  Proposal:    {pid}")
        print(f"  CAG Status:  {cag.upper()}")
        print(f"  Sections:    {sects}")
        print(f"  Categories:  {', '.join(cats) if cats else 'none'}")
        print(f"  Alerts:      {total}")

        if total > 0:
            print()
            print("  ALERTS:")
            print("  " + "-" * 56)
            for alert in output.get("alerts", []):
                sev = alert.get("severity", "?")
                name = alert.get("rule_name", "?")
                act = alert.get("action", "?")
                cls = alert.get("resulting_classification", "?")
                prox = alert.get("proximity_type", "?")
                score = alert.get("proximity_score", 0)
                tcats = alert.get("categories_triggered", [])
                srcs = alert.get("source_elements", [])
                rem = alert.get("remediation", "")

                print(f"  [{sev}] {name}")
                print(f"    Classification: {cls}")
                print(f"    Action:         {act}")
                print(f"    Proximity:      {prox} ({score:.2f})")
                print(f"    Categories:     {', '.join(tcats)}")
                print(f"    Source sections: {', '.join(srcs)}")
                if rem:
                    print(f"    Remediation:    {rem}")
                print()

    elif status == "scanned" and "rules_triggered" in output:
        sid = output.get("section_id", "?")
        vol = output.get("volume", "?")
        cats = output.get("categories", [])
        count = output.get("rules_triggered", 0)

        print(f"  Section:    {sid}")
        print(f"  Volume:     {vol}")
        print(f"  Categories: {', '.join(cats) if cats else 'none'}")
        print(f"  Rules triggered: {count}")

    elif status == "checked":
        allowed = output.get("export_allowed", False)
        pid = output.get("proposal_id", "?")
        total = output.get("total_alerts", 0)
        blocking = output.get("blocking_alerts_new", 0) + output.get("blocking_alerts_existing", 0)

        print(f"  Proposal:       {pid}")
        print(f"  Export allowed: {'YES' if allowed else 'NO -- BLOCKED'}")
        print(f"  CAG Status:     {output.get('cag_status', '?')}")
        print(f"  Total alerts:   {total}")
        print(f"  Blocking:       {blocking}")

        if not allowed:
            print()
            print("  BLOCKING ALERTS:")
            for alert in output.get("blocking_alerts", []):
                sev = alert.get("severity", "?")
                name = alert.get("rule_name", "?")
                rem = alert.get("remediation", "")
                print(f"    [{sev}] {name}")
                if rem:
                    print(f"      Fix: {rem}")

    elif status == "retrieved":
        count = output.get("scan_count", 0)
        print(f"  Scan history: {count} entries")
        for scan in output.get("scans", [])[:20]:
            ts = scan.get("created_at", "?")
            action = scan.get("action", "?")
            print(f"    {ts}  {action}")

    elif status == "generated":
        pid = output.get("proposal_id", "?")
        cats = output.get("all_categories", [])
        print(f"  Proposal:   {pid}")
        print(f"  Categories: {', '.join(cats) if cats else 'none'} ({output.get('category_count', 0)})")
        print()

        for vol, vdata in output.get("volume_summary", {}).items():
            vcats = vdata.get("categories", [])
            print(f"  Volume: {vol}")
            print(f"    Categories: {', '.join(vcats) if vcats else 'none'}")
            print(f"    Sections:   {vdata.get('section_count', 0)}")
            print(f"    Total tags: {vdata.get('total_tags', 0)}")
            print()

        print("  Section Matrix:")
        for sid, sdata in output.get("section_matrix", {}).items():
            scats = sdata.get("categories", [])
            stags = sdata.get("tag_count", 0)
            snum = sdata.get("section_number", "?")
            stitle = sdata.get("section_title", "?")
            print(f"    {snum} {stitle}: {stags} tags ({', '.join(scats) if scats else 'none'})")

    else:
        for key, val in output.items():
            if key != "status":
                print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
