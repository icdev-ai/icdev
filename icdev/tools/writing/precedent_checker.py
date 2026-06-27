# CUI // SP-CTI
"""WriteGuard precedent checker (WG 23).

Search prior ARB/ERB board decisions for precedent conflicts with current text.
Deterministic keyword / Jaccard overlap — no LLM.

CLI:
    python -m tools.writing.precedent_checker --check --text "..." --board erb --json
    python -m tools.writing.precedent_checker --record --board arb --verb approved --rationale "..." --json
    python -m tools.writing.precedent_checker --list --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from typing import Any, Dict, List, Optional, Set


_STOPWORDS = {
    "this", "that", "these", "those", "there", "their", "they", "them",
    "with", "from", "have", "been", "were", "will", "would", "could",
    "should", "shall", "into", "upon", "such", "than", "then", "when",
    "where", "which", "what", "while", "about", "above", "below", "other",
    "some", "most", "also", "because", "against", "between", "during",
    "after", "before", "under", "over", "being", "does", "done", "each",
    "more", "much", "many", "only", "same", "very", "just", "onto",
}


def _ensure_table() -> None:
    """Create wg_board_decisions if not present."""
    from tools.db.storage import get_connection
    ddl = """
    CREATE TABLE IF NOT EXISTS wg_board_decisions (
        id TEXT PRIMARY KEY,
        board_type TEXT NOT NULL CHECK(board_type IN ('arb','erb')),
        topic_keywords TEXT,
        decision_verb TEXT NOT NULL,
        rationale TEXT,
        decided_at TEXT DEFAULT CURRENT_TIMESTAMP,
        decided_by TEXT,
        source_document TEXT,
        classification TEXT DEFAULT 'CUI'
    )
    """
    with get_connection() as conn:
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            pass
        # Additive schema repair: ensure every expected column exists.
        expected = {
            "board_type": "TEXT",
            "topic_keywords": "TEXT",
            "decision_verb": "TEXT",
            "rationale": "TEXT",
            "decided_at": "TEXT",
            "decided_by": "TEXT",
            "source_document": "TEXT",
            "classification": "TEXT",
        }
        for col, coltype in expected.items():
            try:
                conn.execute(
                    f"ALTER TABLE wg_board_decisions ADD COLUMN IF NOT EXISTS {col} {coltype}"
                )
                conn.commit()
            except Exception:
                try:
                    conn.execute(
                        f"ALTER TABLE wg_board_decisions ADD COLUMN {col} {coltype}"
                    )
                    conn.commit()
                except Exception:
                    pass


def _extract_keywords(text: str) -> Set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", text.lower())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


_REJECT_VERBS = {"reject", "rejected", "deny", "denied", "decline", "declined"}
_APPROVE_VERBS = {"approve", "approved", "accept", "accepted", "endorse", "endorsed"}


def _current_intent(text: str) -> str:
    low = text.lower()
    if any(v in low for v in _REJECT_VERBS):
        return "reject"
    if any(v in low for v in _APPROVE_VERBS):
        return "approve"
    return "neutral"


def record_decision(
    board_type: str,
    decision_verb: str,
    rationale: str,
    topic_keywords: Optional[List[str]] = None,
    decided_by: str = "",
    source_document: str = "",
) -> str:
    """Persist a board decision. Returns the new decision id."""
    _ensure_table()
    if board_type not in ("arb", "erb"):
        raise ValueError("board_type must be 'arb' or 'erb'")
    decision_id = str(uuid.uuid4())
    kw_str = ",".join(sorted({k.lower().strip() for k in (topic_keywords or []) if k.strip()}))

    from tools.db.storage import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO wg_board_decisions "
            "(id, board_type, topic_keywords, decision_verb, rationale, "
            "decided_by, source_document, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (decision_id, board_type, kw_str, decision_verb, rationale,
             decided_by, source_document, "CUI"),
        )
        conn.commit()
    return decision_id


def _row_to_dict(row: Any) -> Dict[str, Any]:
    keys = [
        "id", "board_type", "topic_keywords", "decision_verb", "rationale",
        "decided_at", "decided_by", "source_document", "classification",
    ]
    try:
        return dict(row)
    except Exception:
        return {k: row[i] for i, k in enumerate(keys)}


def check_precedents(text: str, board_type: Optional[str] = None) -> Dict[str, Any]:
    """Find prior decisions similar to ``text`` by keyword Jaccard overlap."""
    _ensure_table()
    current_kw = _extract_keywords(text)
    intent = _current_intent(text)

    from tools.db.storage import get_connection
    with get_connection() as conn:
        if board_type:
            rows = conn.execute(
                "SELECT id, board_type, topic_keywords, decision_verb, rationale, "
                "decided_at, decided_by, source_document, classification "
                "FROM wg_board_decisions WHERE board_type = %s "
                "ORDER BY decided_at DESC",
                (board_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, board_type, topic_keywords, decision_verb, rationale, "
                "decided_at, decided_by, source_document, classification "
                "FROM wg_board_decisions ORDER BY decided_at DESC"
            ).fetchall()

    matches: List[Dict[str, Any]] = []
    prior_count = len(rows)
    for row in rows:
        d = _row_to_dict(row)
        stored_kw = {k.strip() for k in (d.get("topic_keywords") or "").split(",") if k.strip()}
        sim = _jaccard(current_kw, stored_kw)
        if sim >= 0.3:
            verb = (d.get("decision_verb") or "").lower()
            contradicts = (
                intent == "reject"
                and any(av in verb for av in _APPROVE_VERBS)
            )
            matches.append({
                "decision_id": d["id"],
                "similarity": round(sim, 3),
                "decision_verb": d.get("decision_verb"),
                "board_type": d.get("board_type"),
                "decided_at": d.get("decided_at"),
                "contradicts_current": contradicts,
            })

    matches.sort(key=lambda m: m["similarity"], reverse=True)

    if not matches:
        score = 100
    else:
        contradictions = sum(1 for m in matches if m["contradicts_current"])
        score = max(0, 100 - 25 * contradictions - 5 * (len(matches) - contradictions))

    return {
        "matches": matches,
        "score": score,
        "prior_count": prior_count,
    }


def list_decisions(board_type: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List recent decisions, most-recent first."""
    _ensure_table()
    from tools.db.storage import get_connection
    with get_connection() as conn:
        if board_type:
            rows = conn.execute(
                "SELECT id, board_type, topic_keywords, decision_verb, rationale, "
                "decided_at, decided_by, source_document, classification "
                "FROM wg_board_decisions WHERE board_type = %s "
                "ORDER BY decided_at DESC LIMIT %s",
                (board_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, board_type, topic_keywords, decision_verb, rationale, "
                "decided_at, decided_by, source_document, classification "
                "FROM wg_board_decisions ORDER BY decided_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="WriteGuard precedent checker (WG 23)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--text", default="")
    ap.add_argument("--board", choices=["arb", "erb"])
    ap.add_argument("--verb", default="")
    ap.add_argument("--rationale", default="")
    ap.add_argument("--keywords", default="", help="comma-separated")
    ap.add_argument("--decided-by", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.record:
        if not args.board or not args.verb:
            ap.error("--record requires --board and --verb")
            return 2
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        did = record_decision(
            board_type=args.board, decision_verb=args.verb,
            rationale=args.rationale, topic_keywords=kws,
            decided_by=args.decided_by, source_document=args.source,
        )
        result = {"decision_id": did}
    elif args.check:
        if not args.text:
            ap.error("--check requires --text")
            return 2
        result = check_precedents(args.text, board_type=args.board)
    elif args.list:
        result = {"decisions": list_decisions(board_type=args.board, limit=args.limit)}
    else:
        ap.error("Specify --check, --record, or --list")
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
