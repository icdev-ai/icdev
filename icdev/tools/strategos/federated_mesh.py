# CUI // SP-CTI
"""Federated data mesh — multi-provider OSINT ETL + ML pattern detection.

Pulls from multiple data providers (ACLED, GDELT, OSINT inbox, DAT, orbital),
standardizes into a common conflict_signal schema, applies TF-IDF-based
keyword clustering to identify escalation patterns in unstructured data.

NIST 800-53: SI-10, SI-12, AU-9
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.strategos.federated_mesh")

# ── Common schema ─────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_mesh_signals (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    source_id       TEXT,
    event_date      TEXT,
    theater_id      TEXT,
    event_type      TEXT,
    description     TEXT,
    lat             REAL,
    lon             REAL,
    fatalities      INTEGER DEFAULT 0,
    raw_json        TEXT,
    ingested_at     TEXT NOT NULL,
    UNIQUE(provider, source_id)
);

CREATE TABLE IF NOT EXISTS sg_mesh_patterns (
    id              TEXT PRIMARY KEY,
    pattern_type    TEXT NOT NULL,
    keywords        TEXT NOT NULL,
    event_count     INTEGER DEFAULT 0,
    confidence      REAL DEFAULT 0.0,
    detected_at     TEXT NOT NULL
);
"""

# ── Escalation pattern vocabulary ─────────────────────────────────────────────
_ESCALATION_PATTERNS = {
    "force_buildup": ["mobilization", "deployment", "reinforcement", "buildup", "surge", "massing", "convoy"],
    "offensive_action": ["advance", "assault", "breakthrough", "offensive", "push", "attack", "raid", "incursion"],
    "diplomatic_breakdown": ["ceasefire violation", "talks collapsed", "withdrew", "expelled", "severed", "denounced"],
    "civilian_impact": ["evacuation", "displacement", "refugee", "humanitarian", "civilian", "hospital", "shelter"],
    "wmd_concern": ["chemical", "biological", "radiological", "nuclear", "wmd", "dirty bomb", "sarin", "nerve agent"],
}


# ── Provider adapters ─────────────────────────────────────────────────────────

def _pull_acled(conn, limit: int = 200) -> list[dict[str, Any]]:
    """Pull from sg_conflict_events (ACLED-sourced rows)."""
    try:
        rows = conn.execute(
            "SELECT id, source, event_type, event_date, theater_id, "
            "latitude, longitude, fatalities, description "
            "FROM sg_conflict_events WHERE source='acled' "
            "ORDER BY event_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "provider": "acled",
            "source_id": r[0],
            "event_type": r[2],
            "event_date": r[3],
            "theater_id": r[4],
            "lat": r[5],
            "lon": r[6],
            "fatalities": r[7] or 0,
            "description": r[8] or "",
        }
        for r in rows
    ]


def _pull_osint_signals(conn, limit: int = 200) -> list[dict[str, Any]]:
    """Pull from sg_raw_signals (OSINT harvester)."""
    try:
        rows = conn.execute(
            "SELECT id, source_domain, title, published_at, content_snippet "
            "FROM sg_raw_signals ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "provider": "osint_inbox",
            "source_id": r[0],
            "event_type": "news",
            "event_date": (r[3] or "")[:10],
            "theater_id": None,
            "lat": None,
            "lon": None,
            "fatalities": 0,
            "description": f"{r[2] or ''} {r[4] or ''}".strip(),
        }
        for r in rows
    ]


def _pull_dat(conn, limit: int = 50) -> list[dict[str, Any]]:
    """Pull from sg_dat_cables (Diplomatic Activity Tracker)."""
    try:
        rows = conn.execute(
            "SELECT id, theater_id, tension_score, content_snippet, created_at "
            "FROM sg_dat_cables ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "provider": "dat",
            "source_id": r[0],
            "event_type": "diplomatic",
            "event_date": (r[4] or "")[:10],
            "theater_id": r[1],
            "lat": None,
            "lon": None,
            "fatalities": 0,
            "description": r[3] or "",
        }
        for r in rows
    ]


# ── ETL normalization ──────────────────────────────────────────────────────────

def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Standardize a raw provider record to mesh schema."""
    return {
        "id": str(uuid.uuid4()),
        "provider": raw.get("provider", "unknown"),
        "source_id": str(raw.get("source_id") or uuid.uuid4()),
        "event_date": (raw.get("event_date") or "")[:10] or None,
        "theater_id": raw.get("theater_id"),
        "event_type": (raw.get("event_type") or "unknown").lower(),
        "description": (raw.get("description") or "")[:2000],
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "fatalities": int(raw.get("fatalities") or 0),
        "raw_json": json.dumps({k: raw.get(k) for k in ("provider", "source_id", "event_type")}),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def ingest(conn, limit_per_provider: int = 200) -> dict[str, int]:
    """Pull from all providers, normalize, and upsert into sg_mesh_signals."""
    for stmt in _SCHEMA.strip().split(";"):
        if stmt.strip():
            conn.execute(stmt.strip())
    conn.commit()

    all_records = (
        _pull_acled(conn, limit_per_provider)
        + _pull_osint_signals(conn, limit_per_provider)
        + _pull_dat(conn, limit_per_provider)
    )

    inserted = 0
    for raw in all_records:
        norm = _normalize(raw)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sg_mesh_signals "
                "(id, provider, source_id, event_date, theater_id, event_type, "
                "description, lat, lon, fatalities, raw_json, ingested_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    norm["id"], norm["provider"], norm["source_id"],
                    norm["event_date"], norm["theater_id"], norm["event_type"],
                    norm["description"], norm["lat"], norm["lon"],
                    norm["fatalities"], norm["raw_json"], norm["ingested_at"],
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception:
            logger.exception("Failed to insert mesh signal")
    conn.commit()
    return {"total_pulled": len(all_records), "inserted": inserted}


# ── ML pattern detection (deterministic TF-IDF keyword clustering) ─────────────

def _simple_tfidf(docs: list[str], vocab: list[str]) -> list[tuple[str, float]]:
    """Return (keyword, tf_idf_score) pairs for the corpus."""
    n = len(docs) or 1
    tf: Counter = Counter()
    df: Counter = Counter()
    for doc in docs:
        tokens = set(re.findall(r"\b\w+\b", doc.lower()))
        for kw in vocab:
            kw_lower = kw.lower()
            if kw_lower in doc.lower():
                tf[kw] += 1
                if kw_lower in tokens:
                    df[kw] += 1
    results = []
    for kw in vocab:
        idf = math.log((n + 1) / (df.get(kw, 0) + 1)) + 1.0
        score = tf.get(kw, 0) / n * idf
        results.append((kw, round(score, 4)))
    return sorted(results, key=lambda x: x[1], reverse=True)


def detect_patterns(conn, theater_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Detect escalation patterns in sg_mesh_signals using TF-IDF keyword scoring."""
    where = "WHERE theater_id = ?" if theater_id else ""
    params = [theater_id, limit] if theater_id else [limit]
    try:
        rows = conn.execute(
            f"SELECT description FROM sg_mesh_signals {where} ORDER BY ingested_at DESC LIMIT ?",
            params,
        ).fetchall()
    except Exception:
        return []

    docs = [r[0] or "" for r in rows]
    if not docs:
        return []

    now = datetime.now(timezone.utc).isoformat()
    patterns: list[dict[str, Any]] = []

    for pattern_type, keywords in _ESCALATION_PATTERNS.items():
        scores = _simple_tfidf(docs, keywords)
        top = [(kw, s) for kw, s in scores if s > 0][:5]
        if not top:
            continue
        confidence = round(min(1.0, sum(s for _, s in top) / len(top) * 5), 3)
        if confidence < 0.05:
            continue

        pat = {
            "id": str(uuid.uuid4()),
            "pattern_type": pattern_type,
            "keywords": json.dumps([kw for kw, _ in top]),
            "event_count": len(docs),
            "confidence": confidence,
            "detected_at": now,
        }
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sg_mesh_patterns "
                "(id, pattern_type, keywords, event_count, confidence, detected_at) "
                "VALUES (?,?,?,?,?,?)",
                (pat["id"], pat["pattern_type"], pat["keywords"],
                 pat["event_count"], pat["confidence"], pat["detected_at"]),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("detect_patterns: best-effort INSERT into sg_mesh_patterns failed (non-blocking): %s", exc)
        patterns.append(pat)

    conn.commit()
    return patterns


def run(theater_id: str | None = None) -> dict[str, Any]:
    """Full ETL + pattern detection pipeline."""
    with get_connection() as conn:
        ingest_stats = ingest(conn)
        detected = detect_patterns(conn, theater_id)
    return {"ingest": ingest_stats, "patterns": detected}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--theater", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.theater)
    print(json.dumps(result, indent=2))
