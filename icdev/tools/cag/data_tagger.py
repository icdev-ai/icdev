#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""CAG Layer 1 -- Content element tagging with security-relevant categories.

Analyzes text content and tags it with one of 10 security-relevant categories
defined by EO 13526 Section 1.7(e) to support classification-by-compilation
detection.

Categories:
    PERSONNEL, CAPABILITY, LOCATION, TIMING, PROGRAM,
    VULNERABILITY, METHOD, SCALE, SOURCE, RELATIONSHIP

The tagging algorithm:
    1. Load category indicators from args/cag_rules.yaml
    2. For each category, scan text for strong then moderate indicators
    3. Record position (character offset) and paragraph index
    4. Calculate confidence based on indicator strength
    5. Store in cag_data_tags table

Usage:
    python tools/cag/data_tagger.py --tag --content "text" --source-type free_text --source-id "src-1" [--json]
    python tools/cag/data_tagger.py --tag-kb --entry-id "kb-1" [--json]
    python tools/cag/data_tagger.py --tag-section --section-id "sec-1" [--json]
    python tools/cag/data_tagger.py --tag-document --proposal-id "prop-1" [--json]
    python tools/cag/data_tagger.py --get-tags --source-type kb_entry --source-id "kb-1" [--json]
    python tools/cag/data_tagger.py --verify --tag-id "tag-1" --verified-by "officer@mil" [--json]
    python tools/cag/data_tagger.py --add-manual --source-type kb_entry --source-id "kb-1" --category PERSONNEL --indicator-text "key personnel" [--json]
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

# --- Valid categories ---
VALID_CATEGORIES = [
    "PERSONNEL", "CAPABILITY", "LOCATION", "TIMING", "PROGRAM",
    "VULNERABILITY", "METHOD", "SCALE", "SOURCE", "RELATIONSHIP",
]

VALID_SOURCE_TYPES = [
    "kb_entry", "proposal_section", "past_performance",
    "resume", "opportunity", "free_text",
]

# --- Confidence scores by indicator strength ---
STRONG_CONFIDENCE = 0.9
MODERATE_CONFIDENCE = 0.6


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


def _gen_id():
    """Generate a unique ID for a tag record."""
    return f"tag-{uuid.uuid4().hex[:12]}"


def _load_rules_yaml():
    """Load category indicators from cag_rules.yaml.

    Returns:
        dict mapping category_id -> {strong: [...], moderate: [...]}
    """
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required for CAG tagging. Install with: pip install pyyaml"
        )

    if not CAG_RULES_PATH.exists():
        raise FileNotFoundError(f"CAG rules not found: {CAG_RULES_PATH}")

    with open(CAG_RULES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    indicators = {}
    for cat_def in data.get("categories", []):
        cat_id = cat_def.get("id", "")
        if cat_id in VALID_CATEGORIES:
            ind = cat_def.get("indicators", {})
            indicators[cat_id] = {
                "strong": ind.get("strong", []),
                "moderate": ind.get("moderate", []),
            }
    return indicators


def _compute_paragraph_index(content, position):
    """Compute the paragraph index for a character position.

    Paragraphs are delineated by double newlines (blank lines).
    """
    if not content or position < 0:
        return 0
    paragraphs = re.split(r"\n\s*\n", content[:position + 1])
    return max(0, len(paragraphs) - 1)


def _find_indicators(content, indicators_list, case_sensitive=False):
    """Find all occurrences of indicator strings in content.

    Returns:
        list of (start_pos, end_pos, matched_text)
    """
    results = []
    if not content or not indicators_list:
        return results

    for indicator in indicators_list:
        if not indicator:
            continue
        # Escape for regex and allow flexible whitespace
        pattern = re.escape(indicator)
        flags = 0 if case_sensitive else re.IGNORECASE
        for match in re.finditer(pattern, content, flags):
            results.append((match.start(), match.end(), match.group()))

    return results


# ---------------------------------------------------------------------------
# Core tagging function
# ---------------------------------------------------------------------------

def tag_content(content, source_type, source_id, db_path=None):
    """Analyze text and tag with security-relevant categories.

    Scans content for indicator keywords defined in args/cag_rules.yaml.
    Strong indicators receive 0.9 confidence; moderate get 0.6.
    Tags are stored in the cag_data_tags table.

    Args:
        content: Text to analyze.
        source_type: One of VALID_SOURCE_TYPES.
        source_id: ID of the source entity.
        db_path: Optional database path override.

    Returns:
        list of dicts, each with: id, category, confidence, indicator_text,
        indicator_type, position_start, position_end, paragraph_index.
    """
    if not content or not content.strip():
        return []

    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"Invalid source_type '{source_type}'. "
            f"Must be one of: {VALID_SOURCE_TYPES}"
        )

    indicators = _load_rules_yaml()
    detected_tags = []

    for category, cat_indicators in indicators.items():
        # 1. Scan for strong indicators first
        strong_hits = _find_indicators(content, cat_indicators.get("strong", []))
        for start, end, matched in strong_hits:
            para_idx = _compute_paragraph_index(content, start)
            detected_tags.append({
                "id": _gen_id(),
                "source_type": source_type,
                "source_id": source_id,
                "category": category,
                "confidence": STRONG_CONFIDENCE,
                "indicator_text": matched,
                "indicator_type": "strong",
                "position_start": start,
                "position_end": end,
                "paragraph_index": para_idx,
                "section_context": content[max(0, start - 40):end + 40].strip(),
            })

        # 2. Scan for moderate indicators
        moderate_hits = _find_indicators(
            content, cat_indicators.get("moderate", [])
        )
        for start, end, matched in moderate_hits:
            para_idx = _compute_paragraph_index(content, start)
            detected_tags.append({
                "id": _gen_id(),
                "source_type": source_type,
                "source_id": source_id,
                "category": category,
                "confidence": MODERATE_CONFIDENCE,
                "indicator_text": matched,
                "indicator_type": "moderate",
                "position_start": start,
                "position_end": end,
                "paragraph_index": para_idx,
                "section_context": content[max(0, start - 40):end + 40].strip(),
            })

    # 3. Store in database
    if detected_tags:
        conn = _get_db(db_path)
        try:
            for tag in detected_tags:
                conn.execute(
                    "INSERT INTO cag_data_tags "
                    "(id, source_type, source_id, category, confidence, "
                    "indicator_text, indicator_type, position_start, position_end, "
                    "paragraph_index, section_context, tagged_by, "
                    "classification_at_tag, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tag["id"], tag["source_type"], tag["source_id"],
                        tag["category"], tag["confidence"],
                        tag["indicator_text"], tag["indicator_type"],
                        tag["position_start"], tag["position_end"],
                        tag["paragraph_index"], tag["section_context"],
                        "auto", "UNCLASSIFIED", _now(),
                    ),
                )
            _audit(
                conn, "cag.tag", "auto",
                f"Tagged {len(detected_tags)} elements in {source_type}/{source_id}",
                entity_type=source_type, entity_id=source_id,
                details=json.dumps({
                    "tag_count": len(detected_tags),
                    "categories": list(set(t["category"] for t in detected_tags)),
                }),
            )
            conn.commit()
        finally:
            conn.close()

    return detected_tags


# ---------------------------------------------------------------------------
# Convenience tagging functions
# ---------------------------------------------------------------------------

def tag_kb_entry(entry_id, db_path=None):
    """Tag a knowledge base entry by reading its content from the database.

    Args:
        entry_id: ID of the kb_entries record.
        db_path: Optional database path override.

    Returns:
        list of detected tags.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, content FROM kb_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"KB entry not found: {entry_id}")
        content = row["content"] or ""
    finally:
        conn.close()

    tags = tag_content(content, "kb_entry", entry_id, db_path=db_path)

    # Update the kb_entries cag_categories field
    if tags:
        categories = json.dumps(sorted(set(t["category"] for t in tags)))
        conn = _get_db(db_path)
        try:
            conn.execute(
                "UPDATE kb_entries SET cag_categories = ?, updated_at = ? "
                "WHERE id = ?",
                (categories, _now(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

    return tags


def tag_proposal_section(section_id, db_path=None):
    """Tag a proposal section by reading its content from the database.

    Args:
        section_id: ID of the proposal_sections record.
        db_path: Optional database path override.

    Returns:
        list of detected tags.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, content FROM proposal_sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Proposal section not found: {section_id}")
        content = row["content"] or ""
    finally:
        conn.close()

    tags = tag_content(
        content, "proposal_section", section_id, db_path=db_path
    )

    # Update the section cag_categories field
    if tags:
        categories = json.dumps(sorted(set(t["category"] for t in tags)))
        conn = _get_db(db_path)
        try:
            conn.execute(
                "UPDATE proposal_sections SET cag_categories = ?, updated_at = ? "
                "WHERE id = ?",
                (categories, _now(), section_id),
            )
            conn.commit()
        finally:
            conn.close()

    return tags


def tag_document(proposal_id, db_path=None):
    """Tag all sections of a proposal.

    Args:
        proposal_id: ID of the proposals record.
        db_path: Optional database path override.

    Returns:
        dict with proposal_id, total_tags, sections_tagged, categories_found,
        and per_section breakdown.
    """
    conn = _get_db(db_path)
    try:
        # Verify proposal exists
        prop = conn.execute(
            "SELECT id FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if not prop:
            raise ValueError(f"Proposal not found: {proposal_id}")

        sections = conn.execute(
            "SELECT id FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()
    finally:
        conn.close()

    all_tags = []
    per_section = []

    for section_row in sections:
        section_id = section_row["id"]
        try:
            section_tags = tag_proposal_section(section_id, db_path=db_path)
        except Exception as exc:
            section_tags = []
            per_section.append({
                "section_id": section_id,
                "tags": 0,
                "categories": [],
                "error": str(exc),
            })
            continue

        all_tags.extend(section_tags)
        section_cats = sorted(set(t["category"] for t in section_tags))
        per_section.append({
            "section_id": section_id,
            "tags": len(section_tags),
            "categories": section_cats,
        })

    # Update proposal cag_last_scan timestamp
    conn = _get_db(db_path)
    try:
        conn.execute(
            "UPDATE proposals SET cag_last_scan = ?, updated_at = ? "
            "WHERE id = ?",
            (_now(), _now(), proposal_id),
        )
        _audit(
            conn, "cag.tag_document", "auto",
            f"Tagged entire proposal {proposal_id}: "
            f"{len(all_tags)} tags across {len(sections)} sections",
            entity_type="proposal", entity_id=proposal_id,
            details=json.dumps({
                "total_tags": len(all_tags),
                "sections_tagged": len(sections),
            }),
        )
        conn.commit()
    finally:
        conn.close()

    all_categories = sorted(set(t["category"] for t in all_tags))

    return {
        "proposal_id": proposal_id,
        "total_tags": len(all_tags),
        "sections_tagged": len(sections),
        "categories_found": all_categories,
        "per_section": per_section,
        "scanned_at": _now(),
    }


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def get_tags(source_type, source_id, db_path=None):
    """Retrieve existing tags for a source entity.

    Args:
        source_type: One of VALID_SOURCE_TYPES.
        source_id: ID of the source entity.
        db_path: Optional database path override.

    Returns:
        list of tag dicts.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"Invalid source_type '{source_type}'. "
            f"Must be one of: {VALID_SOURCE_TYPES}"
        )

    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, source_type, source_id, category, confidence, "
            "indicator_text, indicator_type, position_start, position_end, "
            "paragraph_index, section_context, tagged_by, verified_by, "
            "classification_at_tag, created_at "
            "FROM cag_data_tags "
            "WHERE source_type = ? AND source_id = ? "
            "ORDER BY position_start",
            (source_type, source_id),
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_tag(tag_id, verified_by, db_path=None):
    """Record human verification of an auto-generated tag.

    Args:
        tag_id: ID of the cag_data_tags record.
        verified_by: Identity of the verifier (e.g., email or badge ID).
        db_path: Optional database path override.

    Returns:
        dict with verification result.
    """
    if not verified_by:
        raise ValueError("verified_by is required")

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, category, source_type, source_id FROM cag_data_tags "
            "WHERE id = ?",
            (tag_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Tag not found: {tag_id}")

        conn.execute(
            "UPDATE cag_data_tags SET verified_by = ? WHERE id = ?",
            (verified_by, tag_id),
        )
        _audit(
            conn, "cag.verify_tag", verified_by,
            f"Verified tag {tag_id} (category={row['category']})",
            entity_type="cag_data_tags", entity_id=tag_id,
            details=json.dumps({
                "category": row["category"],
                "source_type": row["source_type"],
                "source_id": row["source_id"],
            }),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "tag_id": tag_id,
        "verified_by": verified_by,
        "category": row["category"],
        "verified_at": _now(),
    }


# ---------------------------------------------------------------------------
# Manual tagging
# ---------------------------------------------------------------------------

def add_manual_tag(source_type, source_id, category, indicator_text,
                   tagged_by="manual", db_path=None):
    """Add a manual tag by a security officer.

    Args:
        source_type: One of VALID_SOURCE_TYPES.
        source_id: ID of the source entity.
        category: One of VALID_CATEGORIES.
        indicator_text: Descriptive text for the tag.
        tagged_by: Identity of the tagger.
        db_path: Optional database path override.

    Returns:
        dict with new tag details.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"Invalid source_type '{source_type}'. "
            f"Must be one of: {VALID_SOURCE_TYPES}"
        )
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category '{category}'. "
            f"Must be one of: {VALID_CATEGORIES}"
        )
    if not indicator_text:
        raise ValueError("indicator_text is required")

    tag_id = _gen_id()
    now = _now()

    conn = _get_db(db_path)
    try:
        conn.execute(
            "INSERT INTO cag_data_tags "
            "(id, source_type, source_id, category, confidence, "
            "indicator_text, indicator_type, tagged_by, "
            "classification_at_tag, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tag_id, source_type, source_id, category,
                1.0,  # Manual tags always have full confidence
                indicator_text, "manual", tagged_by,
                "UNCLASSIFIED", now,
            ),
        )
        _audit(
            conn, "cag.manual_tag", tagged_by,
            f"Manual tag added: {category} on {source_type}/{source_id}",
            entity_type="cag_data_tags", entity_id=tag_id,
            details=json.dumps({
                "category": category,
                "indicator_text": indicator_text,
            }),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "tag_id": tag_id,
        "source_type": source_type,
        "source_id": source_id,
        "category": category,
        "confidence": 1.0,
        "indicator_text": indicator_text,
        "indicator_type": "manual",
        "tagged_by": tagged_by,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for CAG data tagger."""
    parser = argparse.ArgumentParser(
        description="CAG Layer 1: Content element tagging"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--tag", action="store_true",
        help="Tag free-text content",
    )
    group.add_argument(
        "--tag-kb", action="store_true",
        help="Tag a KB entry by its ID",
    )
    group.add_argument(
        "--tag-section", action="store_true",
        help="Tag a proposal section by its ID",
    )
    group.add_argument(
        "--tag-document", action="store_true",
        help="Tag all sections of a proposal",
    )
    group.add_argument(
        "--get-tags", action="store_true",
        help="Retrieve existing tags for a source",
    )
    group.add_argument(
        "--verify", action="store_true",
        help="Human-verify an auto-generated tag",
    )
    group.add_argument(
        "--add-manual", action="store_true",
        help="Add a manual tag by a security officer",
    )

    parser.add_argument("--content", help="Text content to tag")
    parser.add_argument("--source-type", help="Source type (kb_entry, proposal_section, etc.)")
    parser.add_argument("--source-id", help="Source entity ID")
    parser.add_argument("--entry-id", help="KB entry ID (for --tag-kb)")
    parser.add_argument("--section-id", help="Section ID (for --tag-section)")
    parser.add_argument("--proposal-id", help="Proposal ID (for --tag-document)")
    parser.add_argument("--tag-id", help="Tag ID (for --verify)")
    parser.add_argument("--verified-by", help="Verifier identity (for --verify)")
    parser.add_argument("--category", help="Category (for --add-manual)")
    parser.add_argument("--indicator-text", help="Indicator text (for --add-manual)")
    parser.add_argument("--tagged-by", default="manual", help="Tagger identity (for --add-manual)")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    db = args.db_path

    try:
        if args.tag:
            if not args.content:
                parser.error("--tag requires --content")
            if not args.source_type:
                parser.error("--tag requires --source-type")
            if not args.source_id:
                parser.error("--tag requires --source-id")
            result = tag_content(
                args.content, args.source_type, args.source_id, db_path=db
            )
            output = {
                "status": "tagged",
                "source_type": args.source_type,
                "source_id": args.source_id,
                "tags_detected": len(result),
                "categories": sorted(set(t["category"] for t in result)),
                "tags": result,
            }

        elif args.tag_kb:
            if not args.entry_id:
                parser.error("--tag-kb requires --entry-id")
            result = tag_kb_entry(args.entry_id, db_path=db)
            output = {
                "status": "tagged",
                "source_type": "kb_entry",
                "source_id": args.entry_id,
                "tags_detected": len(result),
                "categories": sorted(set(t["category"] for t in result)),
                "tags": result,
            }

        elif args.tag_section:
            if not args.section_id:
                parser.error("--tag-section requires --section-id")
            result = tag_proposal_section(args.section_id, db_path=db)
            output = {
                "status": "tagged",
                "source_type": "proposal_section",
                "source_id": args.section_id,
                "tags_detected": len(result),
                "categories": sorted(set(t["category"] for t in result)),
                "tags": result,
            }

        elif args.tag_document:
            if not args.proposal_id:
                parser.error("--tag-document requires --proposal-id")
            output = tag_document(args.proposal_id, db_path=db)
            output["status"] = "tagged"

        elif args.get_tags:
            if not args.source_type:
                parser.error("--get-tags requires --source-type")
            if not args.source_id:
                parser.error("--get-tags requires --source-id")
            tags = get_tags(args.source_type, args.source_id, db_path=db)
            output = {
                "status": "retrieved",
                "source_type": args.source_type,
                "source_id": args.source_id,
                "tag_count": len(tags),
                "categories": sorted(set(t["category"] for t in tags)),
                "tags": tags,
            }

        elif args.verify:
            if not args.tag_id:
                parser.error("--verify requires --tag-id")
            if not args.verified_by:
                parser.error("--verify requires --verified-by")
            output = verify_tag(args.tag_id, args.verified_by, db_path=db)
            output["status"] = "verified"

        elif args.add_manual:
            if not args.source_type:
                parser.error("--add-manual requires --source-type")
            if not args.source_id:
                parser.error("--add-manual requires --source-id")
            if not args.category:
                parser.error("--add-manual requires --category")
            if not args.indicator_text:
                parser.error("--add-manual requires --indicator-text")
            output = add_manual_tag(
                args.source_type, args.source_id, args.category,
                args.indicator_text, tagged_by=args.tagged_by, db_path=db,
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
    print(f"CAG Data Tagger -- {status.upper()}")
    print("-" * 50)

    if "tags_detected" in output:
        print(f"  Source:     {output.get('source_type', '?')}/{output.get('source_id', '?')}")
        print(f"  Tags found: {output['tags_detected']}")
        cats = output.get("categories", [])
        if cats:
            print(f"  Categories: {', '.join(cats)}")
        for tag in output.get("tags", [])[:20]:  # Limit display
            conf = tag.get("confidence", 0)
            ind = tag.get("indicator_text", "?")
            cat = tag.get("category", "?")
            itype = tag.get("indicator_type", "?")
            print(f"    [{cat}] ({itype}, {conf:.1f}) \"{ind}\"")
        remaining = output.get("tags_detected", 0) - 20
        if remaining > 0:
            print(f"    ... and {remaining} more tags")

    elif "total_tags" in output:
        print(f"  Proposal:        {output.get('proposal_id', '?')}")
        print(f"  Total tags:      {output['total_tags']}")
        print(f"  Sections tagged: {output.get('sections_tagged', 0)}")
        cats = output.get("categories_found", [])
        if cats:
            print(f"  Categories:      {', '.join(cats)}")
        for sec in output.get("per_section", []):
            sid = sec.get("section_id", "?")
            cnt = sec.get("tags", 0)
            scats = sec.get("categories", [])
            print(f"    {sid}: {cnt} tags ({', '.join(scats) if scats else 'none'})")

    elif "tag_id" in output:
        print(f"  Tag ID:     {output['tag_id']}")
        for key in ["category", "verified_by", "confidence", "indicator_text",
                     "tagged_by", "created_at", "verified_at"]:
            if key in output:
                print(f"  {key}: {output[key]}")

    elif "tag_count" in output:
        print(f"  Source:   {output.get('source_type', '?')}/{output.get('source_id', '?')}")
        print(f"  Tags:    {output['tag_count']}")
        cats = output.get("categories", [])
        if cats:
            print(f"  Categories: {', '.join(cats)}")

    else:
        for key, val in output.items():
            if key != "status":
                print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
