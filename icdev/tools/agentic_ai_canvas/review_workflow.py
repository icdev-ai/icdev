# CUI // SP-CTI
"""Agentic AI Design Canvas — Design Review Workflow (Phase 10).

Structured multi-reviewer approval process for AI designs.
Comment types: COMMENT / APPROVAL / CHANGE_REQUEST / REJECTION
Review status derived from comments: PENDING / APPROVED / CHANGES_REQUESTED / REJECTED.

Reviewers are free-text identifiers (email or username).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

_COMMENT_TYPES = {"COMMENT", "APPROVAL", "CHANGE_REQUEST", "REJECTION"}

_TYPE_COLORS = {
    "COMMENT": "secondary",
    "APPROVAL": "success",
    "CHANGE_REQUEST": "warning",
    "REJECTION": "danger",
}

_TYPE_ICONS = {
    "COMMENT": "💬",
    "APPROVAL": "✅",
    "CHANGE_REQUEST": "↩",
    "REJECTION": "❌",
}


def get_review(design_id: str, conn) -> dict:
    """
    Return all review comments for a design, plus derived review status.
    """
    rows = conn.execute(
        "SELECT * FROM aadc_review_comments WHERE design_id=%s ORDER BY created_at ASC",
        (design_id,),
    ).fetchall()
    comments = [dict(r) for r in rows]
    status = _derive_status(comments)

    reviewer_summary: dict[str, str] = {}
    for c in comments:
        rev = c.get("reviewer", "")
        ctype = c.get("comment_type", "COMMENT")
        if ctype in ("APPROVAL", "CHANGE_REQUEST", "REJECTION"):
            reviewer_summary[rev] = ctype

    return {
        "design_id": design_id,
        "comments": comments,
        "status": status,
        "reviewer_summary": reviewer_summary,
        "type_colors": _TYPE_COLORS,
        "type_icons": _TYPE_ICONS,
    }


def add_comment(
    design_id: str,
    reviewer: str,
    comment_type: str,
    body: str,
    node_id: str | None,
    conn,
) -> dict:
    """
    Add a review comment/decision.

    Returns {"ok": True, "comment_id": str} or {"ok": False, "error": str}
    """
    if comment_type not in _COMMENT_TYPES:
        return {"ok": False, "error": f"Invalid comment type: {comment_type}"}
    if not reviewer or not reviewer.strip():
        return {"ok": False, "error": "Reviewer is required"}

    cid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO aadc_review_comments
           (id, design_id, reviewer, comment_type, body, node_id, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (cid, design_id, reviewer.strip(), comment_type, body or "", node_id or "", now),
    )
    conn.commit()
    return {"ok": True, "comment_id": cid}


def _derive_status(comments: list[dict]) -> str:
    """
    Derive overall review status from comment types.
    Priority: REJECTION > CHANGE_REQUEST > APPROVAL > PENDING.
    """
    types = {c.get("comment_type") for c in comments}
    if "REJECTION" in types:
        return "REJECTED"
    if "CHANGE_REQUEST" in types:
        return "CHANGES_REQUESTED"
    if "APPROVAL" in types:
        return "APPROVED"
    return "PENDING"
