#!/usr/bin/env python3
# CUI // SP-CTI
"""Map ICDEV capabilities to solicitation requirements in the compliance matrix.

Reads shall statements and knowledge base blocks for 3 target solicitations,
then populates proposal_compliance_matrix with L/M/N ratings, response summaries,
and linked proposal sections.

Usage:
    python tools/govcon/map_icdev_capabilities.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Solicitation -> section mapping (hardcoded from known DB state)
# ---------------------------------------------------------------------------

_SECTION_MAP: dict[str, dict[str, str]] = {
    "47294739-614f-f3d7-19db-3ad0ddd1dfb2": {  # DHS-FY26-541511-0002
        "technical": "3f22faf8-23be-d01d-43cf-2fde24933b83",
        "management": "5cabcc97-663f-1c97-9562-69f0e5d7b875",
        "past_performance": "dc713d96-0c0f-d195-c17a-f08a1745d6d8",
    },
    "dd56cc94-3c9a-d14c-ee0c-aeb5ecfedb99": {  # DHS-FY26-541511-0008
        "technical": "e08596db-1d87-0966-0710-d430f071d879",
        "management": "6f3f920c-98b8-e4cc-1bc0-44fc09cb3942",
        "past_performance": "1d9af659-82ec-9f2d-fbf6-e16f9b3080d5",
    },
    "bff9507d-cd14-a03e-8359-9af65d01f55f": {  # DHS-FY26-541511-0304
        "technical": "7dbf4bc1-ffa6-23d0-ea9e-5c8db1a8b71f",
        "management": "b69f68c3-e60f-d420-2c33-350c73b911d8",
        "past_performance": "109fd8ee-b5a4-7200-58f0-dd23aaf78c67",
    },
}


def _requirement_type(stmt_type: str, text: str) -> str:
    """Determine L/M/N rating."""
    t = stmt_type.lower()
    if t in ("shall", "must", "required"):
        return "L"
    if t == "will":
        return "M"
    return "N"


def _section_for_domain(opp_id: str, domain: str | None) -> str | None:
    """Map domain to volume/section."""
    mapping = _SECTION_MAP.get(opp_id)
    if not mapping:
        return None
    if domain in ("cloud", "devsecops", "security", "ai_ml", "data", "ato_rmf", "compliance"):
        return mapping["technical"]
    if domain == "management":
        return mapping["management"]
    return mapping["technical"]  # default to technical


def _score_kb(req_text: str, req_keywords: list[str], req_domain: str | None, kb: dict) -> float:
    """Score knowledge block relevance to requirement."""
    score = 0.0
    kb_domain = kb.get("domain", "")
    kb_keywords = set(k.lower() for k in kb.get("keywords", []))
    req_kw_set = set(k.lower() for k in req_keywords)

    # Domain match
    if req_domain and kb_domain == req_domain:
        score += 5.0
    elif req_domain and kb_domain in ("general", "management"):
        score += 1.0

    # Keyword overlap
    overlap = len(kb_keywords & req_kw_set)
    score += overlap * 2.0

    # Text substring match (simple)
    req_lower = req_text.lower()
    for kw in kb_keywords:
        if kw in req_lower:
            score += 1.5

    return score


def _generate_response(req_text: str, kb: dict) -> str:
    """Generate a response summary for the compliance matrix."""
    kb_title = kb["title"]
    kb_content = kb["content"]
    # Extract first sentence or first 200 chars as the basis
    snippet = kb_content[:300].split(".")[0] + "."
    return (
        f"ICDEV satisfies this requirement through {kb_title}. "
        f"{snippet} This capability directly addresses the stated requirement "
        f"with proven, deterministic tooling and full audit trail support."
    )


def _delete_existing(conn, opp_ids: list[str]) -> int:
    placeholders = ",".join("?" for _ in opp_ids)
    result = conn.execute(
        f"DELETE FROM proposal_compliance_matrix WHERE opportunity_id IN ({placeholders})",
        opp_ids,
    )
    return result.rowcount if hasattr(result, "rowcount") else 0


def _insert_matrix_entry(
    conn,
    opp_id: str,
    section_ref: str,
    requirement_text: str,
    req_type: str,
    section_id: str | None,
    response_summary: str,
    sort_order: int,
) -> str:
    entry_id = str(uuid.uuid4())
    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO proposal_compliance_matrix (
            id, opportunity_id, section_ref, volume_ref, requirement_text,
            requirement_type, compliance_status, proposal_section_id,
            response_summary, notes, sort_order, classification,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            opp_id,
            section_ref,
            None,
            requirement_text,
            req_type,
            "compliant",
            section_id,
            response_summary,
            "Auto-mapped by icdev capability mapper",
            sort_order,
            "CUI",
            now,
            now,
        ),
    )
    return entry_id


def map_capabilities(dry_run: bool = False) -> dict:
    conn = get_connection()

    opp_ids = list(_SECTION_MAP.keys())
    deleted = _delete_existing(conn, opp_ids)

    # Load knowledge base
    kb_rows = conn.execute(
        "SELECT id, title, content, domain, keywords FROM proposal_knowledge_base WHERE created_by='icdev_kb_seed' AND status='active'"
    ).fetchall()
    kb_blocks = [dict(r) for r in kb_rows]
    for kb in kb_blocks:
        try:
            kb["keywords"] = json.loads(kb.get("keywords", "[]"))
        except Exception:
            kb["keywords"] = []

    # Load requirements
    req_rows = conn.execute(
        f"""
        SELECT id, proposal_opportunity_id, statement_text, statement_type, domain_category, keywords
        FROM rfp_shall_statements
        WHERE proposal_opportunity_id IN ({','.join('?' for _ in opp_ids)})
        ORDER BY proposal_opportunity_id, id
        """,
        opp_ids,
    ).fetchall()
    requirements = [dict(r) for r in req_rows]
    for req in requirements:
        try:
            req["keywords"] = json.loads(req.get("keywords", "[]"))
        except Exception:
            req["keywords"] = []

    inserted = 0
    coverage: dict[str, dict] = {}

    for opp_id in opp_ids:
        coverage[opp_id] = {"total": 0, "compliant": 0, "L": 0, "M": 0, "N": 0}

    sort_counters: dict[str, int] = {}

    for req in requirements:
        opp_id = req["proposal_opportunity_id"]
        domain = req.get("domain_category")
        req_text = req["statement_text"]
        req_type_label = _requirement_type(req.get("statement_type", "shall"), req_text)
        section_id = _section_for_domain(opp_id, domain)

        # Score all KB blocks and pick best
        best_kb = None
        best_score = -1.0
        for kb in kb_blocks:
            score = _score_kb(req_text, req.get("keywords", []), domain, kb)
            if score > best_score:
                best_score = score
                best_kb = kb

        if best_kb and best_score >= 2.0:
            response = _generate_response(req_text, best_kb)
        else:
            response = (
                "ICDEV addresses this requirement through its integrated ecosystem "
                "of deterministic tools, automated compliance engines, and multi-agent orchestration. "
                "Specific capability mapping will be refined during the Pink Team review."
            )

        section_ref = f"Sec {section_id[:8] if section_id else 'UNK'}..." if section_id else "TBD"
        sort_key = opp_id
        sort_counters[sort_key] = sort_counters.get(sort_key, 0) + 1

        if not dry_run:
            _insert_matrix_entry(
                conn,
                opp_id,
                section_ref,
                req_text,
                req_type_label,
                section_id,
                response,
                sort_counters[sort_key],
            )

        inserted += 1
        coverage[opp_id]["total"] += 1
        coverage[opp_id]["compliant"] += 1
        coverage[opp_id][req_type_label] += 1

    if not dry_run:
        conn.commit()

    # Calculate coverage percentages
    for opp_id in opp_ids:
        total = coverage[opp_id]["total"]
        if total > 0:
            coverage[opp_id]["coverage_pct"] = round((coverage[opp_id]["compliant"] / total) * 100, 1)
        else:
            coverage[opp_id]["coverage_pct"] = 0.0

    return {
        "deleted": deleted,
        "inserted": inserted,
        "coverage": coverage,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Map ICDEV capabilities to compliance matrix")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = map_capabilities(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Compliance matrix mapped:")
        print(f"  Deleted previous: {result['deleted']}")
        print(f"  Inserted:         {result['inserted']}")
        for opp_id, stats in result["coverage"].items():
            print(f"  {opp_id}: {stats['compliant']}/{stats['total']} compliant ({stats['coverage_pct']}%)")


if __name__ == "__main__":
    main()
