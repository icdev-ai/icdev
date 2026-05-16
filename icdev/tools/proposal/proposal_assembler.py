#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Proposal assembly, validation, TOC generation, acronym extraction,
compliance checking, and page budget tracking.

Assembles all proposal sections into a structured output organized by volume,
generates master and per-volume tables of contents, extracts and reconciles
acronyms against the registry, validates section completeness, checks
compliance matrix coverage, and tracks page budgets against limits.

Usage:
    python tools/proposal/proposal_assembler.py --assemble --proposal-id "PROP-123" --json
    python tools/proposal/proposal_assembler.py --validate --proposal-id "PROP-123" --json
    python tools/proposal/proposal_assembler.py --toc --proposal-id "PROP-123" --json
    python tools/proposal/proposal_assembler.py --toc --proposal-id "PROP-123" --volume technical --json
    python tools/proposal/proposal_assembler.py --acronyms --proposal-id "PROP-123" --json
    python tools/proposal/proposal_assembler.py --compliance --proposal-id "PROP-123" --json
    python tools/proposal/proposal_assembler.py --page-budget --proposal-id "PROP-123" --json
    python tools/proposal/proposal_assembler.py --status --proposal-id "PROP-123" --json
"""

import argparse
import json
import os
import re
import secrets
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _asm_id():
    """Assembly-scoped identifier."""
    return "ASM-" + secrets.token_hex(6)


def _get_db(db_path=None):
    """Return an SQLite connection with WAL + FK enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _audit(conn, event_type, action, entity_type=None, entity_id=None, details=None):
    """Append-only audit trail entry."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, entity_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, "proposal_assembler", action, entity_type, entity_id, details, _now()),
    )


def _row_to_dict(row):
    """Convert sqlite3.Row to plain dict."""
    if row is None:
        return None
    return dict(row)


def _parse_json_field(value, fallback=None):
    """Safely parse a JSON text column, returning *fallback* on failure."""
    if not value:
        return fallback if fallback is not None else []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback if fallback is not None else []


# ---------------------------------------------------------------------------
# Volume ordering — canonical sort key so volumes appear in proposal order
# ---------------------------------------------------------------------------

_VOLUME_ORDER = {
    "executive_summary": 0,
    "technical": 1,
    "management": 2,
    "past_performance": 3,
    "cost": 4,
    "attachments": 5,
}


def _volume_sort_key(vol):
    """Return a numeric sort key for a volume name."""
    return _VOLUME_ORDER.get(vol, 99)


# ---------------------------------------------------------------------------
# Section status ordering (for validation checks)
# ---------------------------------------------------------------------------

_STATUS_RANK = {
    "outline": 0,
    "drafting": 1,
    "drafted": 2,
    "reviewed": 3,
    "revised": 4,
    "final": 5,
    "locked": 6,
}


def _status_at_least(actual, minimum):
    """Return True when *actual* status is >= *minimum* in the lifecycle."""
    return _STATUS_RANK.get(actual, -1) >= _STATUS_RANK.get(minimum, 99)


# ---------------------------------------------------------------------------
# Acronym regex patterns
# ---------------------------------------------------------------------------

# Defined acronym: "Capability Maturity Model (CMM)"
_RE_DEFINED_ACRONYM = re.compile(
    r'([A-Z][a-z]+(?:\s+(?:and|for|of|the|in|on|to|&)\s+|\s+[A-Z])[A-Za-z\s&/-]*?)\s*\(([A-Z]{2,8})\)'
)

# Standalone uppercase acronym: "SAST", "CI/CD" — at least 2 consecutive uppercase letters
_RE_STANDALONE_ACRONYM = re.compile(r'\b([A-Z]{2,8})\b')

# Common false-positive filter — words that look like acronyms but are not
_ACRONYM_FALSE_POSITIVES = frozenset({
    "THE", "AND", "FOR", "BUT", "NOT", "ALL", "ARE", "WAS", "HAS", "HAD",
    "HIS", "HER", "ITS", "OUR", "WHO", "HOW", "MAY", "CAN", "DID", "GET",
    "LET", "SAY", "SHE", "HIM", "OLD", "NEW", "BIG", "FEW", "ANY", "WAY",
    "DAY", "USE", "TWO", "SET", "RUN", "END", "PUT", "OWN", "TOO", "ACT",
    "ADD", "AGO", "AGE", "AID", "AIM", "AIR", "ASK", "BAD", "BAR", "BED",
    "BIT", "BOX", "BOY", "BUS", "BUY", "CAR", "CUT", "DIE", "DOG", "DRY",
    "EAR", "EAT", "EYE", "FAR", "FAT", "FIT", "FLY", "GAS", "GOD", "GUN",
    "GUY", "HIT", "HOT", "ICE", "ILL", "JOB", "KEY", "KID", "LAW", "LAY",
    "LED", "LEG", "LIE", "LOT", "LOW", "MAP", "MET", "MIX", "NOR", "ODD",
    "OFF", "OIL", "PAY", "PER", "PIN", "PIT", "PRO", "RAW", "RED", "RID",
    "ROW", "SAD", "SAT", "SEA", "SIT", "SIX", "SKY", "SON", "TOP", "TRY",
    "VIA", "WAR", "WET", "WIN", "WON", "YES", "YET",
})


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def assemble_proposal(proposal_id, db_path=None):
    """Assemble all proposal sections into a structured output by volume.

    Steps:
        1. Load proposal record and all sections ordered by volume + section_number.
        2. Validate that every declared volume has at least one section.
        3. Group sections by volume, generate per-volume TOC entries.
        4. Generate a master TOC across all volumes.
        5. Extract and compile acronyms from all content.
        6. Calculate total word/page counts per volume.
        7. Check page limits from section_l_parsed.
        8. Check compliance matrix coverage.

    Returns:
        dict with volumes, toc, acronyms, page_counts, compliance_coverage,
        warnings, and an assembly_id.
    """
    conn = _get_db(db_path)
    try:
        # --- Load proposal ------------------------------------------------
        prop_row = conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if not prop_row:
            return {"error": f"Proposal '{proposal_id}' not found"}

        prop = _row_to_dict(prop_row)
        declared_volumes = _parse_json_field(prop.get("volumes"), [])
        section_l = _parse_json_field(prop.get("section_l_parsed"), [])

        # --- Load sections ------------------------------------------------
        section_rows = conn.execute(
            "SELECT * FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        if not section_rows:
            return {"error": f"No sections found for proposal '{proposal_id}'"}

        sections = [_row_to_dict(r) for r in section_rows]

        # --- Group by volume -----------------------------------------------
        volumes_map = defaultdict(list)
        for sec in sections:
            volumes_map[sec["volume"]].append(sec)

        warnings = []

        # Check that declared volumes each have sections
        for dv in declared_volumes:
            if dv not in volumes_map:
                warnings.append(f"Declared volume '{dv}' has no sections")

        # --- Build per-volume output --------------------------------------
        assembled_volumes = []
        running_page = 1
        all_toc_entries = []
        all_content_text = []

        for vol_name in sorted(volumes_map.keys(), key=_volume_sort_key):
            vol_sections = volumes_map[vol_name]
            vol_word_count = 0
            vol_page_count = 0
            vol_toc = []
            vol_assembled_sections = []

            for sec in vol_sections:
                content = sec.get("content") or ""
                word_count = sec.get("word_count") or len(content.split())
                page_count = sec.get("page_count") or max(1, word_count // 250)

                toc_entry = {
                    "volume": vol_name,
                    "section_number": sec.get("section_number", ""),
                    "title": sec.get("section_title", ""),
                    "page": running_page,
                    "word_count": word_count,
                    "page_count": page_count,
                    "status": sec.get("status", ""),
                }
                vol_toc.append(toc_entry)
                all_toc_entries.append(toc_entry)

                vol_assembled_sections.append({
                    "id": sec.get("id"),
                    "section_number": sec.get("section_number", ""),
                    "section_title": sec.get("section_title", ""),
                    "content": content,
                    "word_count": word_count,
                    "page_count": page_count,
                    "page_limit": sec.get("page_limit"),
                    "status": sec.get("status", ""),
                    "assigned_writer": sec.get("assigned_writer"),
                    "review_score": sec.get("review_score"),
                    "version": sec.get("version"),
                })

                all_content_text.append(content)
                vol_word_count += word_count
                vol_page_count += page_count
                running_page += page_count

            assembled_volumes.append({
                "volume": vol_name,
                "section_count": len(vol_sections),
                "word_count": vol_word_count,
                "page_count": vol_page_count,
                "toc": vol_toc,
                "sections": vol_assembled_sections,
            })

        # --- Page limit checks (from Section L) ---------------------------
        page_limit_map = {}
        for item in section_l:
            if item.get("page_limit"):
                vol = item.get("volume")
                if vol:
                    page_limit_map[vol] = int(item["page_limit"])

        for vol_data in assembled_volumes:
            limit = page_limit_map.get(vol_data["volume"])
            if limit and vol_data["page_count"] > limit:
                warnings.append(
                    f"Volume '{vol_data['volume']}' exceeds page limit: "
                    f"{vol_data['page_count']} pages vs {limit} limit"
                )

        # Also check per-section page limits
        for sec in sections:
            sec_limit = sec.get("page_limit")
            sec_pages = sec.get("page_count") or max(1, len((sec.get("content") or "").split()) // 250)
            if sec_limit and sec_pages > sec_limit:
                warnings.append(
                    f"Section '{sec.get('section_number', '?')}' exceeds page limit: "
                    f"{sec_pages} pages vs {sec_limit} limit"
                )

        # --- Acronyms (quick inline extraction) ---------------------------
        combined_text = "\n".join(all_content_text)
        defined = {}
        for match in _RE_DEFINED_ACRONYM.finditer(combined_text):
            acr = match.group(2).strip()
            exp = match.group(1).strip()
            if acr not in _ACRONYM_FALSE_POSITIVES:
                defined[acr] = exp

        standalone = set()
        for match in _RE_STANDALONE_ACRONYM.finditer(combined_text):
            acr = match.group(1)
            if acr not in _ACRONYM_FALSE_POSITIVES:
                standalone.add(acr)

        # Merge with DB acronyms table
        db_acronyms = {}
        try:
            acr_rows = conn.execute("SELECT acronym, expansion FROM acronyms").fetchall()
            for ar in acr_rows:
                db_acronyms[ar["acronym"]] = ar["expansion"]
        except sqlite3.OperationalError:
            pass  # table may not exist

        acronym_list = []
        unknown_acronyms = []
        for acr in sorted(standalone | set(defined.keys())):
            expansion = defined.get(acr) or db_acronyms.get(acr)
            if expansion:
                acronym_list.append({"acronym": acr, "expansion": expansion})
            else:
                unknown_acronyms.append(acr)

        # --- Compliance coverage ------------------------------------------
        compliance_cov = _compute_compliance_coverage(conn, proposal_id)

        # --- Totals -------------------------------------------------------
        total_words = sum(v["word_count"] for v in assembled_volumes)
        total_pages = sum(v["page_count"] for v in assembled_volumes)

        page_counts = {
            "total_words": total_words,
            "total_pages": total_pages,
            "by_volume": {
                v["volume"]: {"words": v["word_count"], "pages": v["page_count"]}
                for v in assembled_volumes
            },
        }

        assembly_id = _asm_id()
        _audit(
            conn, "proposal.assembled",
            f"Assembled proposal: {len(assembled_volumes)} volumes, {total_pages} pages",
            "proposal", proposal_id,
            json.dumps({"assembly_id": assembly_id, "volumes": len(assembled_volumes),
                         "total_pages": total_pages, "warnings": len(warnings)}),
        )
        conn.commit()

        return {
            "assembly_id": assembly_id,
            "proposal_id": proposal_id,
            "title": prop.get("title"),
            "status": prop.get("status"),
            "volumes": assembled_volumes,
            "toc": all_toc_entries,
            "acronyms": acronym_list,
            "unknown_acronyms": unknown_acronyms,
            "page_counts": page_counts,
            "compliance_coverage": compliance_cov,
            "warnings": warnings,
            "assembled_at": _now(),
        }
    finally:
        conn.close()


def _compute_compliance_coverage(conn, proposal_id):
    """Compute compliance matrix coverage for a proposal.

    Returns a summary dict.  Safe to call even if the compliance_matrices
    table is empty or missing.
    """
    try:
        rows = conn.execute(
            "SELECT compliance_status FROM compliance_matrices WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"total": 0, "coverage_pct": 0.0, "note": "compliance_matrices table not found"}

    if not rows:
        return {"total": 0, "coverage_pct": 0.0}

    total = len(rows)
    counts = defaultdict(int)
    for r in rows:
        counts[r["compliance_status"]] += 1

    fully = counts.get("fully_addressed", 0)
    partial = counts.get("partially_addressed", 0)
    not_addressed = counts.get("not_addressed", 0)
    na = counts.get("not_applicable", 0)
    applicable = total - na
    coverage = round((fully + partial * 0.5) / applicable * 100, 1) if applicable > 0 else 0.0

    return {
        "total": total,
        "fully_addressed": fully,
        "partially_addressed": partial,
        "not_addressed": not_addressed,
        "not_applicable": na,
        "coverage_pct": coverage,
    }


# ---------------------------------------------------------------------------
# validate_assembly
# ---------------------------------------------------------------------------

def validate_assembly(proposal_id, db_path=None):
    """Pre-assembly validation.

    Checks:
        - All sections have status >= 'drafted'
        - No section exceeds its page_limit
        - Compliance matrix completeness
        - Required volumes exist
        - Win themes reflected in content

    Returns:
        dict {valid, errors, warnings, section_status_summary}
    """
    conn = _get_db(db_path)
    try:
        prop_row = conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if not prop_row:
            return {"error": f"Proposal '{proposal_id}' not found"}

        prop = _row_to_dict(prop_row)
        declared_volumes = _parse_json_field(prop.get("volumes"), [])
        win_themes = prop.get("win_themes") or ""

        section_rows = conn.execute(
            "SELECT id, volume, section_number, section_title, status, "
            "word_count, page_count, page_limit, content "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        errors = []
        warnings = []

        # -- Check declared volumes have sections --------------------------
        section_volumes = {r["volume"] for r in section_rows}
        for dv in declared_volumes:
            if dv not in section_volumes:
                errors.append(f"Required volume '{dv}' has no sections")

        # -- Per-section checks -------------------------------------------
        status_summary = defaultdict(int)
        for sec in section_rows:
            status = sec["status"] or "outline"
            status_summary[status] += 1

            # Status check
            if not _status_at_least(status, "drafted"):
                errors.append(
                    f"Section {sec['section_number'] or sec['id']} "
                    f"({sec['volume']}) is '{status}' — must be at least 'drafted'"
                )

            # Page limit check
            sec_limit = sec["page_limit"]
            content = sec["content"] or ""
            sec_pages = sec["page_count"] or max(1, len(content.split()) // 250)
            if sec_limit and sec_pages > sec_limit:
                errors.append(
                    f"Section {sec['section_number'] or sec['id']} "
                    f"exceeds page limit: {sec_pages}/{sec_limit} pages"
                )

        # -- Compliance matrix completeness --------------------------------
        compliance_cov = _compute_compliance_coverage(conn, proposal_id)
        if compliance_cov.get("not_addressed", 0) > 0:
            warnings.append(
                f"{compliance_cov['not_addressed']} compliance requirements "
                f"are not addressed"
            )
        if compliance_cov.get("coverage_pct", 0) < 100.0 and compliance_cov.get("total", 0) > 0:
            warnings.append(
                f"Compliance coverage is {compliance_cov['coverage_pct']}% "
                f"(target: 100%)"
            )

        # -- Win themes reflected in content --------------------------------
        if win_themes:
            themes = [t.strip() for t in win_themes.split(",") if t.strip()]
            all_content = " ".join((sec["content"] or "") for sec in section_rows).lower()
            for theme in themes:
                theme_words = theme.lower().split()
                # Check if at least one keyword from the theme appears
                found = any(w in all_content for w in theme_words if len(w) > 3)
                if not found:
                    warnings.append(f"Win theme '{theme}' may not be reflected in proposal content")

        valid = len(errors) == 0

        return {
            "proposal_id": proposal_id,
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
            "section_status_summary": dict(status_summary),
            "compliance_coverage": compliance_cov,
            "validated_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# generate_toc
# ---------------------------------------------------------------------------

def generate_toc(proposal_id, volume=None, db_path=None):
    """Generate a Table of Contents.

    Args:
        proposal_id: The proposal ID.
        volume: Optional volume filter.  When None, generates master TOC.

    Returns:
        dict {toc_entries, total_pages}
    """
    conn = _get_db(db_path)
    try:
        if volume:
            rows = conn.execute(
                "SELECT volume, section_number, section_title, word_count, page_count "
                "FROM proposal_sections WHERE proposal_id = ? AND volume = ? "
                "ORDER BY section_number",
                (proposal_id, volume),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT volume, section_number, section_title, word_count, page_count "
                "FROM proposal_sections WHERE proposal_id = ? "
                "ORDER BY volume, section_number",
                (proposal_id,),
            ).fetchall()

        if not rows:
            label = f"volume '{volume}'" if volume else f"proposal '{proposal_id}'"
            return {"error": f"No sections found for {label}"}

        # Sort by canonical volume order, then section_number
        sorted_rows = sorted(
            [_row_to_dict(r) for r in rows],
            key=lambda r: (_volume_sort_key(r["volume"]), r.get("section_number") or ""),
        )

        toc_entries = []
        running_page = 1
        for sec in sorted_rows:
            word_count = sec.get("word_count") or 0
            page_count = sec.get("page_count") or max(1, word_count // 250) if word_count else 1
            toc_entries.append({
                "volume": sec["volume"],
                "section_number": sec.get("section_number", ""),
                "title": sec.get("section_title", ""),
                "page": running_page,
            })
            running_page += page_count

        total_pages = running_page - 1

        return {
            "proposal_id": proposal_id,
            "volume": volume,
            "toc_entries": toc_entries,
            "total_pages": total_pages,
            "generated_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# extract_acronyms
# ---------------------------------------------------------------------------

def extract_acronyms(proposal_id, db_path=None):
    """Extract all acronyms from proposal content and reconcile against DB.

    Scans every section's content for:
        - Defined acronyms: "Full Name (ACRO)"
        - Standalone uppercase tokens: "ACRO"

    Matches against the acronyms table for expansions.  Updates usage_count
    for each matched acronym.

    Returns:
        dict {acronyms: [{acronym, expansion, first_use_section}], unknown: []}
    """
    conn = _get_db(db_path)
    try:
        section_rows = conn.execute(
            "SELECT id, volume, section_number, section_title, content "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        if not section_rows:
            return {"error": f"No sections found for proposal '{proposal_id}'"}

        # Track first occurrence per acronym
        first_use = {}  # acronym -> section_number
        defined_acronyms = {}  # acronym -> expansion (from inline definitions)

        for sec in section_rows:
            content = sec["content"] or ""
            sec_num = sec["section_number"] or sec["id"]

            # Defined acronyms: "Full Name (ACRO)"
            for match in _RE_DEFINED_ACRONYM.finditer(content):
                acr = match.group(2).strip()
                exp = match.group(1).strip()
                if acr not in _ACRONYM_FALSE_POSITIVES:
                    if acr not in defined_acronyms:
                        defined_acronyms[acr] = exp
                    if acr not in first_use:
                        first_use[acr] = sec_num

            # Standalone acronyms
            for match in _RE_STANDALONE_ACRONYM.finditer(content):
                acr = match.group(1)
                if acr not in _ACRONYM_FALSE_POSITIVES and acr not in first_use:
                    first_use[acr] = sec_num

        # Load DB acronyms
        db_acronyms = {}
        try:
            acr_rows = conn.execute("SELECT id, acronym, expansion FROM acronyms").fetchall()
            for ar in acr_rows:
                db_acronyms[ar["acronym"]] = {
                    "id": ar["id"],
                    "expansion": ar["expansion"],
                }
        except sqlite3.OperationalError:
            pass  # table may not exist yet

        found_acronyms = []
        unknown_acronyms = []

        for acr in sorted(first_use.keys()):
            expansion = defined_acronyms.get(acr) or (db_acronyms.get(acr, {}).get("expansion"))
            if expansion:
                found_acronyms.append({
                    "acronym": acr,
                    "expansion": expansion,
                    "first_use_section": first_use[acr],
                })
                # Update usage_count in DB
                if acr in db_acronyms:
                    try:
                        conn.execute(
                            "UPDATE acronyms SET usage_count = COALESCE(usage_count, 0) + 1 "
                            "WHERE acronym = ?",
                            (acr,),
                        )
                    except sqlite3.OperationalError:
                        pass
            else:
                unknown_acronyms.append(acr)

        _audit(
            conn, "proposal.acronyms_extracted",
            f"Extracted {len(found_acronyms)} acronyms, {len(unknown_acronyms)} unknown",
            "proposal", proposal_id,
            json.dumps({"found": len(found_acronyms), "unknown": len(unknown_acronyms)}),
        )
        conn.commit()

        return {
            "proposal_id": proposal_id,
            "acronyms": found_acronyms,
            "unknown": unknown_acronyms,
            "total_found": len(found_acronyms),
            "total_unknown": len(unknown_acronyms),
            "extracted_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# compliance_check
# ---------------------------------------------------------------------------

def compliance_check(proposal_id, db_path=None):
    """Check compliance matrix coverage for a proposal.

    Returns:
        dict {coverage_pct, total, addressed, gaps: []}
    """
    conn = _get_db(db_path)
    try:
        try:
            rows = conn.execute(
                "SELECT id, requirement_id, requirement_text, source, volume, "
                "section_number, compliance_status "
                "FROM compliance_matrices WHERE proposal_id = ? "
                "ORDER BY source, requirement_id",
                (proposal_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {"error": "compliance_matrices table not found"}

        if not rows:
            return {
                "proposal_id": proposal_id,
                "coverage_pct": 0.0,
                "total": 0,
                "fully_addressed": 0,
                "partially_addressed": 0,
                "not_addressed": 0,
                "not_applicable": 0,
                "gaps": [],
            }

        total = len(rows)
        counts = defaultdict(int)
        gaps = []
        for r in rows:
            status = r["compliance_status"]
            counts[status] += 1
            if status in ("not_addressed", "partially_addressed"):
                gaps.append({
                    "id": r["id"],
                    "requirement_id": r["requirement_id"],
                    "requirement_text": (r["requirement_text"] or "")[:150],
                    "source": r["source"],
                    "volume": r["volume"],
                    "section_number": r["section_number"],
                    "compliance_status": status,
                })

        fully = counts.get("fully_addressed", 0)
        partial = counts.get("partially_addressed", 0)
        not_addressed = counts.get("not_addressed", 0)
        na = counts.get("not_applicable", 0)
        applicable = total - na
        coverage = round((fully + partial * 0.5) / applicable * 100, 1) if applicable > 0 else 0.0

        return {
            "proposal_id": proposal_id,
            "coverage_pct": coverage,
            "total": total,
            "fully_addressed": fully,
            "partially_addressed": partial,
            "not_addressed": not_addressed,
            "not_applicable": na,
            "gaps": gaps,
            "gap_count": len(gaps),
            "checked_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# page_budget
# ---------------------------------------------------------------------------

def page_budget(proposal_id, db_path=None):
    """Track page budget utilization per section and per volume.

    For each section with a page_limit, compares actual page_count against
    the limit and classifies as 'over', 'at', or 'under'.

    Returns:
        dict {volumes: [{volume, sections, total_pages}]}
    """
    conn = _get_db(db_path)
    try:
        section_rows = conn.execute(
            "SELECT id, volume, section_number, section_title, content, "
            "word_count, page_count, page_limit "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        if not section_rows:
            return {"error": f"No sections found for proposal '{proposal_id}'"}

        volumes_map = defaultdict(list)
        for sec in section_rows:
            volumes_map[sec["volume"]].append(_row_to_dict(sec))

        volumes_result = []
        over_count = 0
        under_count = 0
        on_count = 0

        for vol_name in sorted(volumes_map.keys(), key=_volume_sort_key):
            vol_sections = volumes_map[vol_name]
            section_budgets = []
            vol_total_pages = 0

            for sec in vol_sections:
                content = sec.get("content") or ""
                word_count = sec.get("word_count") or len(content.split())
                pages = sec.get("page_count") or max(1, word_count // 250)
                limit = sec.get("page_limit")
                vol_total_pages += pages

                if limit:
                    if pages > limit:
                        budget_status = "over"
                        over_count += 1
                    elif pages == limit:
                        budget_status = "at"
                        on_count += 1
                    else:
                        budget_status = "under"
                        under_count += 1
                else:
                    budget_status = "no_limit"

                section_budgets.append({
                    "section_number": sec.get("section_number", ""),
                    "section_title": sec.get("section_title", ""),
                    "pages": pages,
                    "limit": limit,
                    "status": budget_status,
                })

            volumes_result.append({
                "volume": vol_name,
                "sections": section_budgets,
                "total_pages": vol_total_pages,
            })

        return {
            "proposal_id": proposal_id,
            "volumes": volumes_result,
            "summary": {
                "over_limit": over_count,
                "at_limit": on_count,
                "under_limit": under_count,
            },
            "checked_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# assembly_status
# ---------------------------------------------------------------------------

def assembly_status(proposal_id, db_path=None):
    """Overall assembly readiness — combines validation, compliance, and
    page budget into a single readiness assessment.

    Returns:
        dict {ready, section_completion, compliance_coverage,
              page_budget_status, blockers}
    """
    conn = _get_db(db_path)
    try:
        prop_row = conn.execute(
            "SELECT id, title, status, volumes FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not prop_row:
            return {"error": f"Proposal '{proposal_id}' not found"}

        prop = _row_to_dict(prop_row)
    finally:
        conn.close()

    # Delegate to the other functions (they each open their own connection)
    validation = validate_assembly(proposal_id, db_path)
    if "error" in validation:
        return validation

    compliance = compliance_check(proposal_id, db_path)
    budget = page_budget(proposal_id, db_path)

    blockers = list(validation.get("errors", []))

    # Page budget blockers
    budget_summary = budget.get("summary", {})
    if budget_summary.get("over_limit", 0) > 0:
        blockers.append(
            f"{budget_summary['over_limit']} section(s) exceed page limits"
        )

    # Compliance blockers
    if compliance.get("gap_count", 0) > 0:
        blockers.append(
            f"{compliance['gap_count']} compliance requirement(s) have gaps"
        )

    ready = len(blockers) == 0

    # Section completion stats
    status_summary = validation.get("section_status_summary", {})
    total_sections = sum(status_summary.values())
    final_or_locked = status_summary.get("final", 0) + status_summary.get("locked", 0)
    completion_pct = round(final_or_locked / total_sections * 100, 1) if total_sections > 0 else 0.0

    return {
        "proposal_id": proposal_id,
        "title": prop.get("title"),
        "status": prop.get("status"),
        "ready": ready,
        "section_completion": {
            "total_sections": total_sections,
            "final_or_locked": final_or_locked,
            "completion_pct": completion_pct,
            "by_status": status_summary,
        },
        "compliance_coverage": {
            "coverage_pct": compliance.get("coverage_pct", 0.0),
            "total_requirements": compliance.get("total", 0),
            "gaps": compliance.get("gap_count", 0),
        },
        "page_budget_status": budget_summary,
        "blockers": blockers,
        "warnings": validation.get("warnings", []),
        "assessed_at": _now(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Proposal assembly, validation, TOC, acronyms, compliance, "
                    "and page budget tracking."
    )
    parser.add_argument("--assemble", action="store_true",
                        help="Assemble proposal into structured output by volume")
    parser.add_argument("--validate", action="store_true",
                        help="Pre-assembly validation checks")
    parser.add_argument("--toc", action="store_true",
                        help="Generate Table of Contents")
    parser.add_argument("--acronyms", action="store_true",
                        help="Extract and reconcile acronyms")
    parser.add_argument("--compliance", action="store_true",
                        help="Check compliance matrix coverage")
    parser.add_argument("--page-budget", action="store_true",
                        help="Track page budget utilization")
    parser.add_argument("--status", action="store_true",
                        help="Overall assembly readiness status")
    parser.add_argument("--proposal-id", required=True,
                        help="Proposal ID")
    parser.add_argument("--volume",
                        help="Volume filter (for --toc)")
    parser.add_argument("--db-path",
                        help="Override database path")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")
    args = parser.parse_args()

    db = args.db_path or None
    result = {}

    if args.assemble:
        result = assemble_proposal(args.proposal_id, db)
    elif args.validate:
        result = validate_assembly(args.proposal_id, db)
    elif args.toc:
        result = generate_toc(args.proposal_id, volume=args.volume, db_path=db)
    elif args.acronyms:
        result = extract_acronyms(args.proposal_id, db)
    elif args.compliance:
        result = compliance_check(args.proposal_id, db)
    elif args.page_budget:
        result = page_budget(args.proposal_id, db)
    elif args.status:
        result = assembly_status(args.proposal_id, db)
    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)

        if args.assemble:
            print(f"Proposal assembled: {result.get('assembly_id', '')}")
            print(f"  Title:    {result.get('title', '')}")
            print(f"  Volumes:  {len(result.get('volumes', []))}")
            total_pc = result.get("page_counts", {})
            print(f"  Pages:    {total_pc.get('total_pages', 0)}")
            print(f"  Words:    {total_pc.get('total_words', 0)}")
            acr = result.get("acronyms", [])
            unk = result.get("unknown_acronyms", [])
            print(f"  Acronyms: {len(acr)} known, {len(unk)} unknown")
            cov = result.get("compliance_coverage", {})
            print(f"  Compliance: {cov.get('coverage_pct', 0)}%")
            warns = result.get("warnings", [])
            if warns:
                print(f"\n  Warnings ({len(warns)}):")
                for w in warns:
                    print(f"    - {w}")

        elif args.validate:
            valid = result.get("valid", False)
            print(f"Validation: {'PASS' if valid else 'FAIL'}")
            for e in result.get("errors", []):
                print(f"  [ERROR] {e}")
            for w in result.get("warnings", []):
                print(f"  [WARN]  {w}")
            ss = result.get("section_status_summary", {})
            if ss:
                print(f"\n  Section statuses:")
                for status, cnt in sorted(ss.items()):
                    print(f"    {status}: {cnt}")

        elif args.toc:
            entries = result.get("toc_entries", [])
            print(f"Table of Contents ({result.get('total_pages', 0)} pages):")
            cur_vol = None
            for e in entries:
                if e["volume"] != cur_vol:
                    cur_vol = e["volume"]
                    print(f"\n  Volume: {cur_vol.upper()}")
                sn = e.get("section_number", "")
                title = e.get("title", "")
                pg = e.get("page", "")
                print(f"    {sn}\t{title}\t{pg}")

        elif args.acronyms:
            found = result.get("acronyms", [])
            unknown = result.get("unknown", [])
            print(f"Acronyms ({len(found)} found, {len(unknown)} unknown):")
            for a in found:
                print(f"  {a['acronym']}\t{a['expansion']}\t(first: {a['first_use_section']})")
            if unknown:
                print(f"\n  Unknown: {', '.join(unknown)}")

        elif args.compliance:
            print(f"Compliance Coverage: {result.get('coverage_pct', 0)}%")
            print(f"  Total:              {result.get('total', 0)}")
            print(f"  Fully addressed:    {result.get('fully_addressed', 0)}")
            print(f"  Partially addressed:{result.get('partially_addressed', 0)}")
            print(f"  Not addressed:      {result.get('not_addressed', 0)}")
            print(f"  Not applicable:     {result.get('not_applicable', 0)}")
            gaps = result.get("gaps", [])
            if gaps:
                print(f"\n  Gaps ({len(gaps)}):")
                for g in gaps[:10]:
                    print(f"    [{g['source']}] {g['requirement_text'][:80]}")
                if len(gaps) > 10:
                    print(f"    ... and {len(gaps) - 10} more")

        elif args.page_budget:
            vols = result.get("volumes", [])
            summary = result.get("summary", {})
            print(f"Page Budget:")
            print(f"  Over limit:  {summary.get('over_limit', 0)}")
            print(f"  At limit:    {summary.get('at_limit', 0)}")
            print(f"  Under limit: {summary.get('under_limit', 0)}")
            for v in vols:
                print(f"\n  {v['volume'].upper()} ({v['total_pages']} pages):")
                for s in v["sections"]:
                    limit_str = f"/{s['limit']}" if s["limit"] else ""
                    flag = " !!!" if s["status"] == "over" else ""
                    print(f"    {s['section_number']}\t{s['pages']}{limit_str} pages{flag}")

        elif args.status:
            ready = result.get("ready", False)
            print(f"Assembly Status: {'READY' if ready else 'NOT READY'}")
            sc = result.get("section_completion", {})
            print(f"  Sections: {sc.get('final_or_locked', 0)}/{sc.get('total_sections', 0)} "
                  f"final ({sc.get('completion_pct', 0)}%)")
            cc = result.get("compliance_coverage", {})
            print(f"  Compliance: {cc.get('coverage_pct', 0)}% "
                  f"({cc.get('gaps', 0)} gaps)")
            pb = result.get("page_budget_status", {})
            print(f"  Page budget: {pb.get('over_limit', 0)} over limit")
            blockers = result.get("blockers", [])
            if blockers:
                print(f"\n  Blockers ({len(blockers)}):")
                for b in blockers:
                    print(f"    - {b}")
            warns = result.get("warnings", [])
            if warns:
                print(f"\n  Warnings ({len(warns)}):")
                for w in warns:
                    print(f"    - {w}")

        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
