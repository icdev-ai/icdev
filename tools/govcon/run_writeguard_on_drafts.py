#!/usr/bin/env python3
"""Run WriteGuard red-team review on all ICDEV proposal section drafts.

FORGE-compliant deterministic tool. No LLM calls inside this script.
Produces:
  - proposal_reviews rows (red_team, completed)
  - proposal_review_findings rows mapped from WriteGuard dimensions
  - proposal_section_drafts rows (append-only status=reviewed)
  - proposal_sections status updates based on score thresholds
  - proposal_status_history audit entries

Usage:
    python tools/govcon/run_writeguard_on_drafts.py --json
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# The 3 solicitations seeded in Phase 1–4
_TARGET_OPP_IDS = [
    "47294739-614f-f3d7-19db-3ad0ddd1dfb2",  # DHS-FY26-541511-0002 Cloud Migration
    "dd56cc94-3c9a-d14c-ee0c-aeb5ecfedb99",  # DHS-FY26-541511-0008 Cloud ZTA
    "bff9507d-cd14-a03e-8359-9af65d01f55f",  # DHS-FY26-541511-0304 AI/ML
]

# Map WriteGuard dimension → proposal_review_findings.finding_type.
# 'invalid_citation' is not a WriteGuard dimension — it is raised separately
# from the deterministic citation check recorded in draft metadata by
# response_drafter.py (ground-prop-02).
_DIMENSION_TO_FINDING_TYPE: dict[str, str] = {
    "grammar": "content_weakness",
    "readability": "content_weakness",
    "tone": "content_weakness",
    "plagiarism": "compliance_gap",
    "ai_detection": "other",
    "portion_marking": "formatting",
    "passive_voice": "content_weakness",
    "overused_words": "content_weakness",
    "cliches": "content_weakness",
    "consistency": "content_weakness",
    "style_guide": "formatting",
    "pacing": "content_weakness",
    "structure": "formatting",
    "evidence": "missing_content",
    "numeric_integrity": "formatting",
    "bias": "competitive_risk",
    "standards": "compliance_gap",
    "alternatives": "content_weakness",
    "reproducibility": "technical_error",
    "neutrality": "content_weakness",
}

# Dimensions considered structural / must-pass for GovCon proposals
_CRITICAL_DIMENSIONS = {
    "plagiarism",
    "portion_marking",
    "standards",
    "structure",
    "numeric_integrity",
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _score_to_severity(score: float, dimension: str) -> str:
    if score < 50:
        return "critical" if dimension in _CRITICAL_DIMENSIONS else "major"
    if score < 60:
        return "major" if dimension in _CRITICAL_DIMENSIONS else "minor"
    if score < 75:
        return "minor"
    return "observation"


def _overall_rating(overall_score: float, passed: bool, has_major: bool) -> str:
    if overall_score >= 80 and not has_major:
        return "pass"
    if overall_score >= 60 and passed:
        return "pass_with_findings"
    if overall_score >= 40:
        return "major_rework"
    return "fail"


def _new_section_status(overall_score: float, has_major: bool, has_critical: bool) -> str:
    if overall_score >= 80 and not has_major and not has_critical:
        return "approved"
    if overall_score >= 60:
        return "red_team_review"
    return "rework_red"


def _run_writeguard_on_text(text: str) -> dict:
    from tools.pulse.writeguard import run_full_quality_check  # noqa: E402

    logger.info("Running WriteGuard on %d characters...", len(text))
    result = run_full_quality_check(text)
    logger.info("WriteGuard result: passed=%s score=%s", result.get("passed"), result.get("overall_score"))
    return result


def _get_latest_drafts(conn, opp_id: str | None = None) -> list[dict]:
    """Fetch latest draft per section for target opportunities.

    If ``opp_id`` is provided, scopes to that single opportunity.
    """
    if opp_id:
        rows = conn.execute(
            """SELECT d.*, s.section_number, s.title as section_title, s.status as section_status
               FROM proposal_section_drafts d
               JOIN (
                   SELECT section_id, MAX(created_at) as max_created
                   FROM proposal_section_drafts
                   WHERE opportunity_id = %s
                     AND status IN ('draft', 'reviewed', 'approved')
                   GROUP BY section_id
               ) latest ON d.section_id = latest.section_id AND d.created_at = latest.max_created
               LEFT JOIN proposal_sections s ON d.section_id = s.id
               WHERE d.opportunity_id = %s
                 AND d.status IN ('draft', 'reviewed', 'approved')
               ORDER BY s.section_number""",
            (opp_id, opp_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT d.*, s.section_number, s.title as section_title, s.status as section_status
               FROM proposal_section_drafts d
               JOIN (
                   SELECT section_id, MAX(created_at) as max_created
                   FROM proposal_section_drafts
                   WHERE opportunity_id IN (%s, %s, %s)
                     AND status IN ('draft', 'reviewed', 'approved')
                   GROUP BY section_id
               ) latest ON d.section_id = latest.section_id AND d.created_at = latest.max_created
               LEFT JOIN proposal_sections s ON d.section_id = s.id
               WHERE d.opportunity_id IN (%s, %s, %s)
                 AND d.status IN ('draft', 'reviewed', 'approved')
               ORDER BY d.opportunity_id, s.section_number""",
            (*_TARGET_OPP_IDS, *_TARGET_OPP_IDS),
        ).fetchall()
    return [dict(r) for r in rows]


def _insert_review(conn, opp_id: str, overall_rating: str, summary: str) -> str:
    rev_id = _uuid()
    now = _utcnow_iso()
    conn.execute(
        """INSERT INTO proposal_reviews
           (id, opportunity_id, review_type, status, scheduled_date,
            started_at, completed_at, lead_reviewer, participants, summary, overall_rating, created_at)
           VALUES (%s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s, %s)""",
        (rev_id, opp_id, "red_team", now, now, now, "ICDEV WriteGuard", "", summary, overall_rating, now),
    )
    return rev_id


def _insert_finding(
    conn,
    rev_id: str,
    sec_id: str | None,
    finding_type: str,
    severity: str,
    description: str,
    recommendation: str,
) -> None:
    now = _utcnow_iso()
    conn.execute(
        """INSERT INTO proposal_review_findings
           (id, review_id, section_id, finding_type, severity, description,
            recommendation, status, assigned_to, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'open', %s, %s)""",
        (_uuid(), rev_id, sec_id, finding_type, severity, description, recommendation, "ICDEV WriteGuard", now),
    )


def _insert_reviewed_draft(conn, draft: dict, review_notes: str) -> str:
    now = _utcnow_iso()
    new_id = _uuid()
    conn.execute(
        """INSERT INTO proposal_section_drafts
           (id, section_id, opportunity_id, shall_statement_id, capability_ids,
            knowledge_block_ids, draft_content, draft_method, confidence_score,
            confidence, domain_category, generation_model, status, reviewed_by,
            reviewed_at, review_notes, reviewer_notes, metadata, created_at,
            updated_at, classification)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            new_id,
            draft.get("section_id"),
            draft.get("opportunity_id"),
            draft.get("shall_statement_id"),
            draft.get("capability_ids"),
            draft.get("knowledge_block_ids"),
            draft.get("draft_content"),
            draft.get("draft_method"),
            draft.get("confidence_score"),
            draft.get("confidence"),
            draft.get("domain_category"),
            draft.get("generation_model"),
            "reviewed",
            "ICDEV WriteGuard",
            now,
            review_notes,
            draft.get("reviewer_notes"),
            draft.get("metadata"),
            now,
            now,
            draft.get("classification", "CUI"),
        ),
    )
    return new_id


def _update_section_status(conn, sec_id: str, new_status: str, old_status: str, reason: str) -> None:
    now = _utcnow_iso()
    conn.execute(
        "UPDATE proposal_sections SET status = %s, updated_at = %s WHERE id = %s",
        (new_status, now, sec_id),
    )
    conn.execute(
        """INSERT INTO proposal_status_history
           (entity_type, entity_id, old_status, new_status, changed_by, reason, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        ("section", sec_id, old_status, new_status, "ICDEV WriteGuard", reason, now),
    )


def _build_finding_description(dimension: str, details: dict) -> str:
    findings = details.get("findings", [])
    lines = [f"{dimension} score: {details.get('score', 'N/A')}"]
    for f in findings[:3]:
        if isinstance(f, dict):
            msg = f.get("message", str(f))
        else:
            msg = str(f)
        lines.append(f"  • {msg}")
    return "\n".join(lines)


def _citation_finding(meta: dict) -> tuple[str, str, str] | None:
    """Build an invalid_citation finding from draft metadata (ground-prop-02).

    response_drafter.py validates every Section/Factor/Volume/CLIN reference
    against the parsed solicitation and records failures in
    metadata.invalid_references. Returns (severity, description,
    recommendation), or None when the draft has no invalid citations.
    """
    invalid_refs = meta.get("invalid_references") or []
    if not invalid_refs:
        return None
    severity = "major" if len(invalid_refs) >= 3 else "minor"
    lines = [
        f"invalid_citation: {len(invalid_refs)} reference(s) cite solicitation "
        "structure that does not exist in the parsed document"
    ]
    for r in invalid_refs[:5]:
        if isinstance(r, dict):
            lines.append(f"  • {r.get('ref', '?')} — {r.get('reason', '')}")
        else:
            lines.append(f"  • {r}")
    recommendation = (
        "Correct or remove each citation so it references only sections, "
        "instructions, volumes, evaluation factors, and CLINs that exist in "
        "the solicitation (see the parsed structure for this opportunity)."
    )
    return severity, "\n".join(lines), recommendation


def _build_recommendation(dimension: str, details: dict, wg_recs: list[str]) -> str:
    # Try to match a WriteGuard recommendation to this dimension
    dim_lower = dimension.lower()
    for rec in wg_recs:
        if dim_lower in rec.lower():
            return rec
    # Fallback: use top finding suggestion
    findings = details.get("findings", [])
    for f in findings[:1]:
        if isinstance(f, dict):
            sug = f.get("suggestion", "")
            if sug:
                return sug
        else:
            return str(f)
    return f"Review and improve {dimension} before submission."


def _process_draft(conn, draft: dict) -> dict:
    text = draft.get("draft_content", "")
    if not text or not text.strip():
        logger.warning("Draft %s has no content; skipping.", draft.get("id"))
        return {"draft_id": draft.get("id"), "skipped": True, "reason": "empty_content"}

    # Draft metadata carries the deterministic citation-check result written
    # by response_drafter.py (ground-prop-02).
    meta_str = draft.get("metadata", "{}")
    try:
        meta = json.loads(meta_str) if isinstance(meta_str, str) and meta_str.strip() else {}
    except Exception:
        meta = {}

    # 1. Run WriteGuard
    wg = _run_writeguard_on_text(text)
    details = wg.get("details", {})
    overall_score = wg.get("overall_score", 0.0)
    passed = wg.get("passed", False)
    wg_recs = wg.get("recommendations", [])

    # 2. Determine severity profile
    has_major = False
    has_critical = False
    for dim, dinfo in details.items():
        if not isinstance(dinfo, dict):
            continue
        score = dinfo.get("score", 100)
        if not isinstance(score, (int, float)):
            continue
        sev = _score_to_severity(score, dim)
        if sev == "major":
            has_major = True
        if sev == "critical":
            has_critical = True

    citation = _citation_finding(meta)
    if citation and citation[0] == "major":
        has_major = True

    overall_rating = _overall_rating(overall_score, passed, has_major)
    new_sec_status = _new_section_status(overall_score, has_major, has_critical)

    # 3. Create review
    summary = (
        f"WriteGuard Red-Team Review: overall_score={overall_score}, "
        f"passed={passed}, rating={overall_rating}. "
        f"Dimensions checked: {len(details)}."
    )
    rev_id = _insert_review(conn, draft["opportunity_id"], overall_rating, summary)

    # 4. Create findings
    findings_created = 0
    for dim, dinfo in details.items():
        if not isinstance(dinfo, dict):
            continue
        score = dinfo.get("score", 100)
        if not isinstance(score, (int, float)):
            continue
        # Only create findings for dimensions below threshold
        if score >= 85:
            continue
        sev = _score_to_severity(score, dim)
        ftype = _DIMENSION_TO_FINDING_TYPE.get(dim, "other")
        desc = _build_finding_description(dim, dinfo)
        rec = _build_recommendation(dim, dinfo, wg_recs)
        _insert_finding(conn, rev_id, draft.get("section_id"), ftype, sev, desc, rec)
        findings_created += 1

    # 4b. Invalid-citation finding from the deterministic solicitation
    # reference check (ground-prop-02)
    if citation:
        sev, desc, rec = citation
        _insert_finding(conn, rev_id, draft.get("section_id"), "invalid_citation", sev, desc, rec)
        findings_created += 1

    # 5. Append-only reviewed draft row
    review_notes_json = json.dumps(
        {
            "writeguard": {
                "passed": passed,
                "overall_score": overall_score,
                "overall_rating": overall_rating,
                "composites": wg.get("composites"),
                "findings_created": findings_created,
                "review_id": rev_id,
            }
        },
        default=str,
    )
    # Merge writeguard summary into metadata for downstream template visibility
    meta["writeguard"] = {
        "passed": passed,
        "overall_score": overall_score,
        "overall_rating": overall_rating,
        "findings_created": findings_created,
        "review_id": rev_id,
    }
    merged_metadata = json.dumps(meta, default=str)
    draft["metadata"] = merged_metadata
    _insert_reviewed_draft(conn, draft, review_notes_json)
    draft["metadata_parsed"] = meta  # for any downstream use in this run

    # 6. Update section status if warranted
    old_status = draft.get("section_status", "not_started")
    if new_sec_status != old_status:
        reason = f"WriteGuard score={overall_score}, rating={overall_rating}, findings={findings_created}"
        _update_section_status(conn, draft["section_id"], new_sec_status, old_status, reason)

    return {
        "draft_id": draft.get("id"),
        "section_id": draft.get("section_id"),
        "review_id": rev_id,
        "overall_score": overall_score,
        "overall_rating": overall_rating,
        "passed": passed,
        "findings_created": findings_created,
        "new_section_status": new_sec_status if new_sec_status != old_status else None,
        "old_section_status": old_status,
    }


def run(reporter=None, opp_id: str | None = None) -> dict:
    conn = get_connection()
    try:
        drafts = _get_latest_drafts(conn, opp_id=opp_id)
        logger.info("Found %d drafts to review.", len(drafts))

        results: list[dict] = []
        for draft in drafts:
            try:
                r = _process_draft(conn, draft)
                results.append(r)
            except Exception as exc:
                logger.error("Failed to process draft %s: %s", draft.get("id"), exc)
                results.append(
                    {"draft_id": draft.get("id"), "skipped": True, "reason": str(exc)}
                )

        conn.commit()

        summary = {
            "total_drafts": len(drafts),
            "processed": len([r for r in results if not r.get("skipped")]),
            "skipped": len([r for r in results if r.get("skipped")]),
            "findings_total": sum(r.get("findings_created", 0) for r in results),
            "sections_updated": len([r for r in results if r.get("new_section_status")]),
            "results": results,
        }
        logger.info("WriteGuard review batch complete: %s", summary)
        return summary
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run WriteGuard red-team review on proposal drafts")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    summary = run()
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"Processed {summary['processed']}/{summary['total_drafts']} drafts.")
        print(f"Findings created: {summary['findings_total']}")
        print(f"Sections updated: {summary['sections_updated']}")
