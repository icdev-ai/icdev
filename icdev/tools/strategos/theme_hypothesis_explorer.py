# CUI // SP-CTI
"""Theme and Hypothesis Explorer — search data mesh, analyze patterns, formulate hypotheses.

Enables analysts to:
1. Search sg_mesh_signals by keyword/event-type/theater
2. Analyze emerging themes across the corpus using frequency analysis
3. Formulate and persist hypotheses (event, person, situation)
4. Generate a leadership briefing from the selected hypothesis + evidence

NIST 800-53: SI-12, RA-3, AU-9
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.strategos.theme_hypothesis_explorer")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_hypotheses (
    id              TEXT PRIMARY KEY,
    analyst_id      TEXT,
    hypothesis_type TEXT NOT NULL,
    subject         TEXT NOT NULL,
    statement       TEXT NOT NULL,
    evidence_json   TEXT DEFAULT '[]',
    confidence      REAL DEFAULT 0.5,
    status          TEXT DEFAULT 'open',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sg_themes (
    id              TEXT PRIMARY KEY,
    theme           TEXT NOT NULL UNIQUE,
    frequency       INTEGER DEFAULT 1,
    theater_id      TEXT,
    last_seen       TEXT NOT NULL
);
"""

HYPOTHESIS_TYPES = ("event", "person", "situation")


# ── Search ────────────────────────────────────────────────────────────────────

def search_mesh(
    query: str,
    event_type: str | None = None,
    theater_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Full-text search over sg_mesh_signals description field."""
    clauses: list[str] = []
    params: list[Any] = []

    if query.strip():
        clauses.append("LOWER(description) LIKE ?")
        params.append(f"%{query.lower()}%")
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if theater_id:
        clauses.append("theater_id = ?")
        params.append(theater_id)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT id, provider, event_type, event_date, theater_id, "
                f"description, fatalities FROM sg_mesh_signals {where} "
                f"ORDER BY event_date DESC LIMIT ?",
                params,
            ).fetchall()
    except Exception:
        return []

    return [
        {
            "id": r[0], "provider": r[1], "event_type": r[2],
            "event_date": r[3], "theater_id": r[4],
            "description": r[5] or "", "fatalities": r[6] or 0,
        }
        for r in rows
    ]


# ── Theme analysis ─────────────────────────────────────────────────────────────

_STOP_WORDS = frozenset([
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "through", "during",
    "is", "was", "are", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "that", "this", "these", "those", "it", "its", "as", "not", "no", "so",
])


def extract_themes(
    signals: list[dict[str, Any]],
    top_n: int = 20,
    theater_id: str | None = None,
) -> list[dict[str, Any]]:
    """Extract top N recurring themes from a list of mesh signals."""
    from collections import Counter
    token_counts: Counter = Counter()

    for sig in signals:
        tokens = re.findall(r"\b[a-z]{4,}\b", (sig.get("description") or "").lower())
        for tok in tokens:
            if tok not in _STOP_WORDS:
                token_counts[tok] += 1

    now = datetime.now(timezone.utc).isoformat()
    themes = []
    try:
        with get_connection() as conn:
            for stmt in _SCHEMA.strip().split(";"):
                if stmt.strip():
                    conn.execute(stmt.strip())
            conn.commit()

            for theme, freq in token_counts.most_common(top_n):
                tid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO sg_themes (id, theme, frequency, theater_id, last_seen) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(theme) DO UPDATE SET frequency=excluded.frequency, last_seen=excluded.last_seen",
                    (tid, theme, freq, theater_id, now),
                )
                themes.append({"theme": theme, "frequency": freq})
            conn.commit()
    except Exception:
        logger.exception("Failed to persist themes")

    return themes


# ── Hypothesis management ──────────────────────────────────────────────────────

def create_hypothesis(
    statement: str,
    subject: str,
    hypothesis_type: str = "situation",
    evidence_signal_ids: list[str] | None = None,
    analyst_id: str | None = None,
    confidence: float = 0.5,
) -> dict[str, Any]:
    """Persist a new analyst hypothesis with supporting evidence."""
    if hypothesis_type not in HYPOTHESIS_TYPES:
        raise ValueError(f"hypothesis_type must be one of {HYPOTHESIS_TYPES}")

    now = datetime.now(timezone.utc).isoformat()
    hyp_id = str(uuid.uuid4())
    evidence = json.dumps(evidence_signal_ids or [])

    with get_connection() as conn:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt.strip())
        conn.execute(
            "INSERT INTO sg_hypotheses "
            "(id, analyst_id, hypothesis_type, subject, statement, evidence_json, "
            "confidence, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (hyp_id, analyst_id, hypothesis_type, subject, statement,
             evidence, confidence, "open", now, now),
        )
        conn.commit()

    logger.info("Created hypothesis %s (%s)", hyp_id, hypothesis_type)
    return {
        "id": hyp_id, "analyst_id": analyst_id,
        "hypothesis_type": hypothesis_type, "subject": subject,
        "statement": statement, "evidence": evidence_signal_ids or [],
        "confidence": confidence, "status": "open", "created_at": now,
    }


def list_hypotheses(status: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE status = ?" if status else ""
    params = [status] if status else []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT id, hypothesis_type, subject, statement, confidence, status, created_at "
                f"FROM sg_hypotheses {where} ORDER BY created_at DESC",
                params,
            ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "type": r[1], "subject": r[2],
         "statement": r[3], "confidence": r[4], "status": r[5], "created_at": r[6]}
        for r in rows
    ]


def generate_brief_from_hypothesis(hypothesis_id: str) -> dict[str, Any]:
    """Generate a leadership brief from a specific hypothesis and its evidence."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT hypothesis_type, subject, statement, evidence_json, confidence "
                "FROM sg_hypotheses WHERE id = ?", (hypothesis_id,)
            ).fetchone()
    except Exception:
        return {"error": "hypothesis not found"}

    if not row:
        return {"error": "hypothesis not found"}

    h_type, subject, statement, ev_json, confidence = row
    evidence_ids: list[str] = json.loads(ev_json or "[]")

    # Fetch supporting signals
    supporting: list[dict] = []
    if evidence_ids:
        placeholders = ",".join("?" for _ in evidence_ids)
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    f"SELECT event_date, event_type, description FROM sg_mesh_signals "
                    f"WHERE id IN ({placeholders}) ORDER BY event_date DESC",
                    evidence_ids,
                ).fetchall()
            supporting = [{"date": r[0], "type": r[1], "desc": (r[2] or "")[:150]} for r in rows]
        except Exception:
            pass

    return {
        "classification": "CUI // SP-CTI",
        "type": "Theme & Hypothesis Brief",
        "hypothesis_id": hypothesis_id,
        "hypothesis_type": h_type,
        "subject": subject,
        "analyst_assessment": statement,
        "confidence": confidence,
        "supporting_evidence": supporting,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default=None)
    ap.add_argument("--theater", default=None)
    ap.add_argument("--list-hypotheses", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list_hypotheses:
        print(json.dumps(list_hypotheses(), indent=2))
    elif args.search:
        signals = search_mesh(args.search, theater_id=args.theater)
        themes = extract_themes(signals, theater_id=args.theater)
        print(json.dumps({"results": len(signals), "themes": themes[:10]}, indent=2))
    else:
        ap.print_help()
