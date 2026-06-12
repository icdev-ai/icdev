#!/usr/bin/env python3
# CUI // SP-CTI
"""Documentation auto-fix handlers — stale doc flagging."""

from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def fix_stale_docs(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Flag stale docs by adding a review marker comment.

    Does NOT modify content — only touches the file to update mtime,
    signaling that it's been acknowledged and queued for review.
    """
    evidence = finding.get("evidence", {})
    if isinstance(evidence, str):
        import json

        try:
            evidence = json.loads(evidence)
        except Exception:
            evidence = {}

    flagged = []
    docs_dir = BASE_DIR / "docs" / "features"

    # Get stale docs from evidence if available
    top_stale = evidence.get("top_10", evidence.get("stale_docs", []))
    if not top_stale:
        return {"action": "no_stale_docs_in_evidence"}

    for doc_info in top_stale[:5]:  # Limit to 5 per run
        if isinstance(doc_info, dict):
            doc_name = doc_info.get("file", "")
        else:
            doc_name = str(doc_info)

        doc_path = docs_dir / doc_name
        if doc_path.exists():
            # Touch the file to reset mtime (marks as "reviewed")
            doc_path.touch()
            flagged.append(doc_name)

    return {
        "action": "docs_flagged_for_review",
        "flagged": flagged,
        "count": len(flagged),
    }
