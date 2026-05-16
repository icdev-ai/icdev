#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Compliance matrix generation, tracking, and gap analysis.

Generates a compliance matrix from parsed Section L / Section M data, tracks
requirement-to-section mappings, provides coverage analysis, and supports
auto-mapping via keyword matching.

Usage:
    python tools/proposal/compliance_matrix.py --generate --proposal-id "prop-123" --json
    python tools/proposal/compliance_matrix.py --coverage --proposal-id "prop-123" --json
    python tools/proposal/compliance_matrix.py --auto-map --proposal-id "prop-123" --json
    python tools/proposal/compliance_matrix.py --gaps --proposal-id "prop-123" --json
    python tools/proposal/compliance_matrix.py --export --proposal-id "prop-123" --json
    python tools/proposal/compliance_matrix.py --map --matrix-id "abc123" --section-id "sec-1" --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
try:
    import yaml  # noqa: F401
except ImportError:  # pragma: no cover
    yaml = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None):
    """Return an SQLite connection with WAL + FK enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _uid():
    """Short UUID for primary keys."""
    return str(uuid.uuid4())[:12]


def _audit(conn, event_type, action, entity_type=None, entity_id=None, details=None):
    """Append-only audit trail entry."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, entity_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, "compliance_matrix", action, entity_type, entity_id, details, _now()),
    )


# ---------------------------------------------------------------------------
# Keyword extraction for auto-mapping
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on",
    "at", "by", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "shall", "should", "must",
    "may", "can", "could", "would", "that", "this", "these", "those",
    "it", "its", "not", "no", "from", "as", "all", "each", "every",
    "any", "into", "such", "their", "than", "other", "which", "what",
    "when", "where", "how", "who", "but", "if", "about", "also",
    "contractor", "offeror", "vendor", "government", "agency",
})


def _extract_keywords(text):
    """Extract meaningful keywords from text for matching."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _keyword_overlap_score(keywords_a, keywords_b):
    """Jaccard-like overlap score between two keyword sets."""
    if not keywords_a or not keywords_b:
        return 0.0
    intersection = keywords_a & keywords_b
    union = keywords_a | keywords_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_matrix(proposal_id, db_path=None):
    """Auto-generate compliance matrix from parsed Section L/M stored in the
    proposals table.

    Each requirement from Section L and each evaluation criterion from Section M
    becomes a row in compliance_matrices with status 'not_addressed'.

    Returns:
        dict with matrix generation results.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT section_l_parsed, section_m_parsed FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not row:
            return {"error": f"Proposal '{proposal_id}' not found"}

        l_data = json.loads(row["section_l_parsed"] or "[]")
        m_data = json.loads(row["section_m_parsed"] or "[]")

        if not l_data and not m_data:
            return {
                "error": "No parsed Section L or Section M data found. "
                         "Run section_parser.py first to parse the solicitation."
            }

        # Check for existing matrix entries to avoid duplicates
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM compliance_matrices WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()["cnt"]

        if existing > 0:
            return {
                "warning": f"Compliance matrix already has {existing} entries for this proposal. "
                           "Delete existing entries first or use --auto-map to update mappings.",
                "existing_count": existing,
            }

        entries = []

        # Section L instructions -> requirements
        for item in l_data:
            entry_id = _uid()
            req_id = item.get("id", f"L-{_uid()[:6]}")
            req_text = item.get("instruction_text", "")
            volume = item.get("volume")
            conn.execute(
                "INSERT INTO compliance_matrices "
                "(id, proposal_id, requirement_id, requirement_text, source, volume, "
                "compliance_status, created_at) "
                "VALUES (?, ?, ?, ?, 'section_l', ?, 'not_addressed', ?)",
                (entry_id, proposal_id, req_id, req_text, volume, _now()),
            )
            entries.append({
                "id": entry_id,
                "requirement_id": req_id,
                "requirement_text": req_text[:120],
                "source": "section_l",
                "volume": volume,
                "compliance_status": "not_addressed",
            })

        # Section M evaluation factors -> requirements
        for item in m_data:
            entry_id = _uid()
            req_id = item.get("id", f"M-{_uid()[:6]}")
            factor = item.get("factor", "")
            subfactors = item.get("subfactors") or []
            req_text = factor
            if subfactors:
                sf_text = "; ".join(sf.get("text", "") for sf in subfactors)
                req_text = f"{factor} [Subfactors: {sf_text}]"
            conn.execute(
                "INSERT INTO compliance_matrices "
                "(id, proposal_id, requirement_id, requirement_text, source, "
                "compliance_status, created_at) "
                "VALUES (?, ?, ?, ?, 'section_m', 'not_addressed', ?)",
                (entry_id, proposal_id, req_id, req_text, _now()),
            )
            entries.append({
                "id": entry_id,
                "requirement_id": req_id,
                "requirement_text": req_text[:120],
                "source": "section_m",
                "compliance_status": "not_addressed",
            })

        _audit(conn, "compliance.matrix_generated",
               f"Generated compliance matrix: {len(entries)} entries",
               "proposal", proposal_id,
               json.dumps({"l_count": len(l_data), "m_count": len(m_data)}))
        conn.commit()

        return {
            "proposal_id": proposal_id,
            "entries_created": len(entries),
            "section_l_entries": len(l_data),
            "section_m_entries": len(m_data),
            "entries": entries,
            "generated_at": _now(),
        }
    finally:
        conn.close()


def check_coverage(proposal_id, db_path=None):
    """Check compliance matrix coverage: addressed vs total requirements.

    Returns:
        dict with coverage percentage, counts, and gap list.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, requirement_id, requirement_text, source, volume, "
            "section_number, section_title, compliance_status, notes "
            "FROM compliance_matrices WHERE proposal_id = ? "
            "ORDER BY source, requirement_id",
            (proposal_id,),
        ).fetchall()

        if not rows:
            return {"error": f"No compliance matrix entries found for proposal '{proposal_id}'"}

        total = len(rows)
        by_status = {}
        gaps = []
        for r in rows:
            status = r["compliance_status"]
            by_status[status] = by_status.get(status, 0) + 1
            if status in ("not_addressed", "partially_addressed"):
                gaps.append({
                    "id": r["id"],
                    "requirement_id": r["requirement_id"],
                    "requirement_text": r["requirement_text"][:150],
                    "source": r["source"],
                    "compliance_status": status,
                })

        addressed = by_status.get("fully_addressed", 0)
        partial = by_status.get("partially_addressed", 0)
        not_applicable = by_status.get("not_applicable", 0)
        not_addressed = by_status.get("not_addressed", 0)

        applicable_total = total - not_applicable
        coverage = 0.0
        if applicable_total > 0:
            # Partial counts as 0.5 coverage
            coverage = round((addressed + partial * 0.5) / applicable_total * 100, 1)

        return {
            "proposal_id": proposal_id,
            "total_requirements": total,
            "fully_addressed": addressed,
            "partially_addressed": partial,
            "not_addressed": not_addressed,
            "not_applicable": not_applicable,
            "coverage_percent": coverage,
            "gaps": gaps,
            "gap_count": len(gaps),
        }
    finally:
        conn.close()


def map_requirement(matrix_id, section_id, db_path=None):
    """Map a compliance matrix requirement to a proposal section.

    Args:
        matrix_id: compliance_matrices row id.
        section_id: proposal_sections row id to link.

    Returns:
        dict with mapping confirmation.
    """
    conn = _get_db(db_path)
    try:
        # Verify matrix entry exists
        matrix_row = conn.execute(
            "SELECT id, proposal_id FROM compliance_matrices WHERE id = ?",
            (matrix_id,),
        ).fetchone()
        if not matrix_row:
            return {"error": f"Matrix entry '{matrix_id}' not found"}

        # Verify section exists
        section_row = conn.execute(
            "SELECT id, section_number, section_title, volume FROM proposal_sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if not section_row:
            return {"error": f"Section '{section_id}' not found"}

        conn.execute(
            "UPDATE compliance_matrices SET section_number = ?, section_title = ?, volume = ? "
            "WHERE id = ?",
            (section_row["section_number"], section_row["section_title"],
             section_row["volume"], matrix_id),
        )

        _audit(conn, "compliance.requirement_mapped",
               f"Mapped requirement {matrix_id} to section {section_id}",
               "compliance_matrix", matrix_id,
               json.dumps({"section_id": section_id,
                            "section_number": section_row["section_number"]}))
        conn.commit()

        return {
            "matrix_id": matrix_id,
            "section_id": section_id,
            "section_number": section_row["section_number"],
            "section_title": section_row["section_title"],
            "volume": section_row["volume"],
            "mapped": True,
        }
    finally:
        conn.close()


def auto_map(proposal_id, db_path=None):
    """Auto-map compliance matrix requirements to proposal sections using
    keyword matching between requirement text and section titles/content.

    Returns:
        dict with auto-mapping results.
    """
    conn = _get_db(db_path)
    try:
        # Load matrix entries without section mapping
        matrix_rows = conn.execute(
            "SELECT id, requirement_id, requirement_text, source, volume "
            "FROM compliance_matrices "
            "WHERE proposal_id = ? AND (section_number IS NULL OR section_number = '')",
            (proposal_id,),
        ).fetchall()

        if not matrix_rows:
            return {
                "proposal_id": proposal_id,
                "message": "No unmapped requirements found",
                "mapped_count": 0,
            }

        # Load proposal sections
        section_rows = conn.execute(
            "SELECT id, section_number, section_title, volume, content "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        if not section_rows:
            return {
                "proposal_id": proposal_id,
                "message": "No proposal sections found to map against",
                "mapped_count": 0,
            }

        # Build keyword index for sections
        section_kw = []
        for s in section_rows:
            combined = f"{s['section_title'] or ''} {s['content'] or ''}"
            keywords = _extract_keywords(combined)
            section_kw.append({
                "id": s["id"],
                "section_number": s["section_number"],
                "section_title": s["section_title"],
                "volume": s["volume"],
                "keywords": keywords,
            })

        mapped = []
        threshold = 0.08  # Minimum overlap score to consider a match

        for mrow in matrix_rows:
            req_keywords = _extract_keywords(mrow["requirement_text"])
            best_score = 0.0
            best_section = None

            for sec in section_kw:
                # Bonus if volumes match
                volume_bonus = 0.05 if (mrow["volume"] and mrow["volume"] == sec["volume"]) else 0.0
                score = _keyword_overlap_score(req_keywords, sec["keywords"]) + volume_bonus

                if score > best_score:
                    best_score = score
                    best_section = sec

            if best_section and best_score >= threshold:
                conn.execute(
                    "UPDATE compliance_matrices SET section_number = ?, section_title = ?, volume = ? "
                    "WHERE id = ?",
                    (best_section["section_number"], best_section["section_title"],
                     best_section["volume"], mrow["id"]),
                )
                mapped.append({
                    "matrix_id": mrow["id"],
                    "requirement_id": mrow["requirement_id"],
                    "section_number": best_section["section_number"],
                    "section_title": best_section["section_title"],
                    "score": round(best_score, 3),
                })

        _audit(conn, "compliance.auto_mapped",
               f"Auto-mapped {len(mapped)} of {len(matrix_rows)} requirements",
               "proposal", proposal_id,
               json.dumps({"mapped": len(mapped), "total": len(matrix_rows)}))
        conn.commit()

        return {
            "proposal_id": proposal_id,
            "total_unmapped": len(matrix_rows),
            "mapped_count": len(mapped),
            "still_unmapped": len(matrix_rows) - len(mapped),
            "mappings": mapped,
        }
    finally:
        conn.close()


def get_gap_report(proposal_id, db_path=None):
    """Generate a gap report for all not_addressed and partially_addressed
    requirements with recommendations.

    Returns:
        dict with gap report details and recommendations.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT cm.id, cm.requirement_id, cm.requirement_text, cm.source, "
            "cm.volume, cm.section_number, cm.section_title, cm.compliance_status, cm.notes "
            "FROM compliance_matrices cm "
            "WHERE cm.proposal_id = ? AND cm.compliance_status IN ('not_addressed', 'partially_addressed') "
            "ORDER BY cm.source, cm.requirement_id",
            (proposal_id,),
        ).fetchall()

        if not rows:
            return {
                "proposal_id": proposal_id,
                "gap_count": 0,
                "message": "No gaps found - all requirements are addressed or not applicable.",
            }

        gaps = []
        for r in rows:
            recommendation = _generate_gap_recommendation(
                r["requirement_text"], r["source"], r["compliance_status"],
                r["section_number"], r["volume"],
            )
            gaps.append({
                "id": r["id"],
                "requirement_id": r["requirement_id"],
                "requirement_text": r["requirement_text"][:200],
                "source": r["source"],
                "volume": r["volume"],
                "section_number": r["section_number"],
                "section_title": r["section_title"],
                "compliance_status": r["compliance_status"],
                "notes": r["notes"],
                "recommendation": recommendation,
            })

        # Summarize by source
        by_source = {}
        for g in gaps:
            src = g["source"]
            by_source[src] = by_source.get(src, 0) + 1

        # Summarize by status
        not_addressed = sum(1 for g in gaps if g["compliance_status"] == "not_addressed")
        partial = sum(1 for g in gaps if g["compliance_status"] == "partially_addressed")

        return {
            "proposal_id": proposal_id,
            "gap_count": len(gaps),
            "not_addressed_count": not_addressed,
            "partially_addressed_count": partial,
            "gaps_by_source": by_source,
            "gaps": gaps,
            "generated_at": _now(),
        }
    finally:
        conn.close()


def _generate_gap_recommendation(req_text, source, status, section_number, volume):
    """Generate a recommendation for a gap based on requirement characteristics."""
    rec_parts = []

    if status == "not_addressed":
        if section_number:
            rec_parts.append(f"Address in section {section_number}.")
        elif volume:
            rec_parts.append(f"Create a section in the {volume} volume to address this requirement.")
        else:
            rec_parts.append("Assign to an appropriate proposal section and draft responsive content.")
    else:
        rec_parts.append("Expand existing content to fully address this requirement.")

    text_lower = (req_text or "").lower()
    if "shall" in text_lower or "must" in text_lower:
        rec_parts.append("This is a mandatory requirement - non-compliance may be evaluated as a deficiency.")
    if source == "section_m":
        rec_parts.append("This is an evaluation criterion - directly impacts scoring.")
    if "experience" in text_lower or "past performance" in text_lower:
        rec_parts.append("Reference relevant past performance citations from the knowledge base.")
    if "plan" in text_lower or "approach" in text_lower:
        rec_parts.append("Provide a detailed approach with specific methodologies and tools.")

    return " ".join(rec_parts)


def export_matrix(proposal_id, format="json", db_path=None):
    """Export compliance matrix as JSON or CSV-like text.

    Args:
        proposal_id: The proposal ID.
        format: 'json' or 'csv'.

    Returns:
        dict (json) or string (csv).
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, requirement_id, requirement_text, source, volume, "
            "section_number, section_title, compliance_status, notes, created_at "
            "FROM compliance_matrices WHERE proposal_id = ? "
            "ORDER BY source, requirement_id",
            (proposal_id,),
        ).fetchall()

        entries = [dict(r) for r in rows]

        if format == "csv":
            header = "requirement_id,source,compliance_status,volume,section_number,requirement_text"
            lines = [header]
            for e in entries:
                # Escape commas in text fields
                req_text = (e["requirement_text"] or "").replace('"', '""')
                lines.append(
                    f'{e["requirement_id"]},{e["source"]},{e["compliance_status"]},'
                    f'{e.get("volume") or ""},{e.get("section_number") or ""},"{req_text}"'
                )
            return {
                "proposal_id": proposal_id,
                "format": "csv",
                "count": len(entries),
                "content": "\n".join(lines),
            }

        return {
            "proposal_id": proposal_id,
            "format": "json",
            "count": len(entries),
            "entries": entries,
            "exported_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compliance matrix generation, tracking, and gap analysis."
    )
    parser.add_argument("--generate", action="store_true", help="Generate compliance matrix from parsed L/M data")
    parser.add_argument("--coverage", action="store_true", help="Check compliance coverage percentage")
    parser.add_argument("--map", action="store_true", help="Map a requirement to a section")
    parser.add_argument("--auto-map", action="store_true", help="Auto-map requirements to sections via keyword matching")
    parser.add_argument("--gaps", action="store_true", help="Generate gap report")
    parser.add_argument("--export", action="store_true", help="Export compliance matrix")
    parser.add_argument("--proposal-id", help="Proposal ID")
    parser.add_argument("--matrix-id", help="Matrix entry ID (for --map)")
    parser.add_argument("--section-id", help="Section ID (for --map)")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="Export format")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = {}

    if not args.proposal_id and not args.map:
        parser.error("--proposal-id is required")

    if args.generate:
        result = generate_matrix(args.proposal_id)
    elif args.coverage:
        result = check_coverage(args.proposal_id)
    elif args.map:
        if not args.matrix_id or not args.section_id:
            parser.error("--map requires --matrix-id and --section-id")
        result = map_requirement(args.matrix_id, args.section_id)
    elif args.auto_map:
        result = auto_map(args.proposal_id)
    elif args.gaps:
        result = get_gap_report(args.proposal_id)
    elif args.export:
        result = export_matrix(args.proposal_id, format=args.format)
    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)
        if "warning" in result:
            print(f"WARNING: {result['warning']}")
        elif "entries_created" in result:
            print(f"Compliance matrix generated: {result['entries_created']} entries")
            print(f"  Section L: {result.get('section_l_entries', 0)}")
            print(f"  Section M: {result.get('section_m_entries', 0)}")
        elif "coverage_percent" in result:
            pct = result["coverage_percent"]
            print(f"Compliance Coverage: {pct}%")
            print(f"  Fully addressed:     {result['fully_addressed']}")
            print(f"  Partially addressed: {result['partially_addressed']}")
            print(f"  Not addressed:       {result['not_addressed']}")
            print(f"  Not applicable:      {result['not_applicable']}")
            if result["gaps"]:
                print(f"\nGaps ({result['gap_count']}):")
                for g in result["gaps"][:10]:
                    print(f"  [{g['source']}] {g['requirement_text'][:80]}")
                if result["gap_count"] > 10:
                    print(f"  ... and {result['gap_count'] - 10} more")
        elif "mapped" in result:
            print(f"Mapped requirement -> section {result['section_number']}: {result['section_title']}")
        elif "mapped_count" in result:
            print(f"Auto-mapping results:")
            print(f"  Mapped:       {result['mapped_count']}")
            print(f"  Still unmapped: {result['still_unmapped']}")
        elif "gap_count" in result:
            print(f"Gap Report ({result['gap_count']} gaps):")
            print(f"  Not addressed:       {result.get('not_addressed_count', 0)}")
            print(f"  Partially addressed: {result.get('partially_addressed_count', 0)}")
            for g in result.get("gaps", [])[:15]:
                status_mark = "[~]" if g["compliance_status"] == "partially_addressed" else "[ ]"
                print(f"  {status_mark} [{g['source']}] {g['requirement_text'][:80]}")
                print(f"       -> {g['recommendation'][:100]}")
        elif "content" in result and result.get("format") == "csv":
            print(result["content"])
        elif "count" in result:
            print(f"Exported {result['count']} entries ({result['format']})")
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
