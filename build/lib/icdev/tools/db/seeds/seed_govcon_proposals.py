"""Seed database with synthetic GovCon proposal data for demo purposes.

Inserts 50 fictional proposals (5 archetypes × 10) with 3 sections each
into proposal_opportunities, proposal_volumes, and proposal_sections tables.

Idempotent: deletes rows with created_by='synthetic_demo' before re-inserting.

Usage:
  python tools/db/seeds/seed_govcon_proposals.py [--json] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.govcon.synthetic_proposal_generator import generate  # noqa: E402


def _delete_existing(conn) -> dict:
    """Remove all previously seeded synthetic rows (cascade via opportunity_id)."""
    counts = {}
    # Sections and volumes lack created_by; cascade through opportunity_id
    for table in ("proposal_sections", "proposal_volumes"):
        result = conn.execute(
            f"""
            DELETE FROM {table}
            WHERE opportunity_id IN (
                SELECT id FROM proposal_opportunities WHERE created_by = 'synthetic_demo'
            )
            """
        )
        counts[table] = result.rowcount if hasattr(result, "rowcount") else 0
    result = conn.execute(
        "DELETE FROM proposal_opportunities WHERE created_by = 'synthetic_demo'"
    )
    counts["proposal_opportunities"] = result.rowcount if hasattr(result, "rowcount") else 0
    return counts


def _insert_opportunity(conn, opp: dict) -> None:
    conn.execute(
        """
        INSERT INTO proposal_opportunities (
            id, solicitation_number, title, agency, sub_agency,
            naics_code, estimated_value_low, estimated_value_high,
            proposal_type, status, bid_decision, bid_decision_rationale,
            capture_manager, proposal_manager, domain, classification,
            due_date, due_time, set_aside_type, created_by,
            created_at, updated_at, amendment_count, question_count
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            opp["id"], opp["solicitation_number"], opp["title"], opp["agency"],
            opp["sub_agency"], opp["naics_code"], opp["estimated_value_low"],
            opp["estimated_value_high"], opp["proposal_type"], opp["status"],
            opp["bid_decision"], opp["bid_decision_rationale"], opp["capture_manager"],
            opp["proposal_manager"], opp["domain"], opp["classification"],
            opp["due_date"], opp["due_time"], opp["set_aside_type"], opp["created_by"],
            opp["created_at"], opp["updated_at"], opp["amendment_count"],
            opp["question_count"],
        ),
    )


def _insert_volume(conn, vol: dict) -> None:
    conn.execute(
        """
        INSERT INTO proposal_volumes (
            id, opportunity_id, volume_number, volume_type, title,
            description, page_limit, word_limit, sort_order, status,
            classification, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            vol["id"], vol["opportunity_id"], vol["volume_number"], vol["volume_type"],
            vol["title"], vol["description"], vol["page_limit"], vol["word_limit"],
            vol["sort_order"], vol["status"], vol["classification"],
            vol["created_at"], vol["updated_at"],
        ),
    )


def _insert_section(conn, sec: dict) -> None:
    conn.execute(
        """
        INSERT INTO proposal_sections (
            id, volume_id, opportunity_id, parent_section_id,
            section_number, title, description, writer, writer_email,
            reviewer, page_limit, word_limit, current_word_count,
            current_page_count, priority, status, due_date,
            content_path, notes, sort_order, classification,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sec["id"], sec["volume_id"], sec["opportunity_id"], sec["parent_section_id"],
            sec["section_number"], sec["title"], sec["description"], sec["writer"],
            sec["writer_email"], sec["reviewer"], sec["page_limit"], sec["word_limit"],
            sec["current_word_count"], sec["current_page_count"], sec["priority"],
            sec["status"], sec["due_date"], sec["content_path"], sec["notes"],
            sec["sort_order"], sec["classification"], sec["created_at"], sec["updated_at"],
        ),
    )


def seed(dry_run: bool = False) -> dict:
    """Seed the database with synthetic GovCon proposals.

    Returns a summary dict with counts.
    """
    proposals = generate(count=50, seed=42)

    if dry_run:
        total_sections = sum(len(p["sections"]) for p in proposals)
        total_volumes = sum(len(p["volumes"]) for p in proposals)
        return {
            "dry_run": True,
            "would_insert": {
                "proposals": len(proposals),
                "volumes": total_volumes,
                "sections": total_sections,
            },
            "archetypes": list({p["opportunity"]["domain"] for p in proposals}),
        }

    conn = get_connection()

    deleted = _delete_existing(conn)

    inserted = {"proposals": 0, "volumes": 0, "sections": 0}
    for proposal in proposals:
        _insert_opportunity(conn, proposal["opportunity"])
        inserted["proposals"] += 1

        for vol in proposal["volumes"]:
            _insert_volume(conn, vol)
            inserted["volumes"] += 1

        for sec in proposal["sections"]:
            _insert_section(conn, sec)
            inserted["sections"] += 1

    conn.commit()
    return {
        "status": "ok",
        "deleted": deleted,
        "inserted": inserted,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed GovCon synthetic proposals")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = seed(dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        if args.dry_run:
            d = result["would_insert"]
            print(f"[dry-run] Would insert: {d['proposals']} proposals, "
                  f"{d['volumes']} volumes, {d['sections']} sections")
        else:
            ins = result["inserted"]
            print(f"Seeded: {ins['proposals']} proposals, "
                  f"{ins['volumes']} volumes, {ins['sections']} sections")


if __name__ == "__main__":
    _main()
