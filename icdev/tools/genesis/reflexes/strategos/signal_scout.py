# CUI // SP-CTI
"""Signal Scout Reflex — Signal Prioritization Engine for ICDEV™ Strategos.

Reads unprocessed rows from sg_raw_signals (processed=false), scores each
signal across four dimensions, inserts top-20 into sg_prioritized_signals,
and marks scored rows processed=true.

Scoring dimensions (composite = weighted sum):
  posterior_shift(0.35)         KL-proxy: rarity of PMESII-PT dimension vs. prior
  source_discriminability(0.25) STANAG A-F source reliability mapped to 1-6 / 6
  temporal_recency(0.20)        exp(-hours_old / 24)
  domain_coverage(0.20)         binary keyword match against active PIR topics

Designed to run every 4h after osint_harvester.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[4]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, is_pg  # noqa: E402

logger = get_logger(__name__)

_TOP_N = 20

# PMESII-PT dimension keyword map.
# Each dimension is a list of discriminating keywords (lower-case).
_PMESII_PT: Dict[str, List[str]] = {
    "political": [
        "election", "government", "parliament", "minister", "president", "coup",
        "sanctions", "diplomatic", "treaty", "bilateral", "nato", "un ", "g7", "g20",
        "regime", "opposition", "protest", "legislation", "policy",
    ],
    "military": [
        "troops", "military", "army", "navy", "air force", "missile", "drone",
        "airstrike", "offensive", "defense", "weapon", "combat", "battalion",
        "artillery", "soldier", "brigadier", "general", "tank", "naval",
        "war", "attack", "invasion", "cease-fire", "ceasefire",
    ],
    "economic": [
        "gdp", "inflation", "trade", "sanction", "tariff", "oil price", "gas price",
        "currency", "bank", "financial", "market", "economy", "recession",
        "export", "import", "investment", "supply chain", "commodity", "debt",
    ],
    "social": [
        "civilian", "population", "refugee", "displacement", "humanitarian",
        "protest", "riot", "demonstration", "social", "community", "religion",
        "ethnic", "tribal", "poverty", "healthcare", "education",
    ],
    "information": [
        "cyber", "disinformation", "propaganda", "hack", "breach", "leak",
        "information", "media", "social media", "fake news", "psyop", "signal",
        "intelligence", "surveillance", "wiretap", "intercept", "darkweb",
    ],
    "infrastructure": [
        "pipeline", "power grid", "electricity", "bridge", "road", "railway",
        "airport", "port", "water", "dam", "infrastructure", "factory",
        "hospital", "communication", "internet outage", "blackout",
    ],
    "physical_environment": [
        "terrain", "flood", "earthquake", "storm", "weather", "geography",
        "climate", "drought", "volcano", "wildfire", "river", "mountain",
        "desert", "coast", "forest", "environment",
    ],
    "time": [
        "deadline", "timeline", "schedule", "imminent", "24 hours", "48 hours",
        "hours", "days", "weeks", "months", "anniversary", "window", "phase",
    ],
}

# STANAG 2022 source reliability: letter → normalised score (1-6 scale → 0-1).
# A=6/6, B=5/6, C=4/6, D=3/6, E=2/6, F=1/6
_STANAG_SCORE: Dict[str, float] = {
    "A": 6 / 6,
    "B": 5 / 6,
    "C": 4 / 6,
    "D": 3 / 6,
    "E": 2 / 6,
    "F": 1 / 6,
}

# Known source-name → STANAG grade mappings (extend as needed).
_SOURCE_GRADE: Dict[str, str] = {
    # Tier-1 vetted sources
    "reuters world news": "A",
    "reuters": "A",
    "bbc world news": "A",
    "bbc": "A",
    "ap news": "A",
    "associated press": "A",
    # Tier-2 reliable
    "defense one": "B",
    "bellingcat": "B",
    "al jazeera": "B",
    "cisa alerts": "B",
    "cisa": "B",
    # Social / unverified
    "twitter": "E",
    "telegram": "D",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_tables() -> None:
    try:
        from tools.db.migrations.run_migration import run_migration  # type: ignore
        run_migration("056_sg_prioritized_signals")
    except Exception:
        try:
            from tools.db.migrations._056_sg_prioritized_signals.up import up  # noqa: F401
        except Exception:
            pass
        # Last-resort inline DDL
        try:
            conn = get_connection()
            _pg = is_pg()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sg_prioritized_signals ("
                "id SERIAL PRIMARY KEY,"
                "raw_signal_id INTEGER NOT NULL,"
                "composite_score REAL NOT NULL,"
                "posterior_shift_score REAL NOT NULL DEFAULT 0.0,"
                "source_discriminability_score REAL NOT NULL DEFAULT 0.0,"
                "temporal_recency_score REAL NOT NULL DEFAULT 0.0,"
                "domain_coverage_score REAL NOT NULL DEFAULT 0.0,"
                "rationale TEXT,"
                "run_at TEXT NOT NULL,"
                "created_at TEXT NOT NULL"
                ")"
                if _pg else
                "CREATE TABLE IF NOT EXISTS sg_prioritized_signals ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "raw_signal_id INTEGER NOT NULL,"
                "composite_score REAL NOT NULL,"
                "posterior_shift_score REAL NOT NULL DEFAULT 0.0,"
                "source_discriminability_score REAL NOT NULL DEFAULT 0.0,"
                "temporal_recency_score REAL NOT NULL DEFAULT 0.0,"
                "domain_coverage_score REAL NOT NULL DEFAULT 0.0,"
                "rationale TEXT,"
                "run_at TEXT NOT NULL,"
                "created_at TEXT NOT NULL"
                ")"
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Could not ensure sg_prioritized_signals table: %s", exc)


def _classify_pmesii(text: str) -> Dict[str, int]:
    """Count keyword hits per PMESII-PT dimension in text."""
    lower = text.lower()
    hits = {dim: 0 for dim in _PMESII_PT}
    for dim, keywords in _PMESII_PT.items():
        for kw in keywords:
            if kw in lower:
                hits[dim] += 1
    return hits


def _fetch_unprocessed() -> List[Dict[str, Any]]:
    """Fetch all sg_raw_signals rows where processed is false/null."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, body, source, signal_date, created_at "
            "FROM sg_raw_signals WHERE processed != 1 OR processed IS NULL ORDER BY id"
        ).fetchall()

        if not rows:
            return []

        def _row_to_dict(r: Any) -> Dict[str, Any]:
            if isinstance(r, dict):
                return dict(r)
            keys = ["id", "title", "body", "source", "signal_date", "created_at"]
            return dict(zip(keys, r))

        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _fetch_prior_pmesii_counts() -> Dict[str, int]:
    """Count already-processed signals per PMESII-PT dimension from recent 7d."""
    conn = get_connection()
    try:
        if is_pg():
            rows = conn.execute(
                "SELECT title, body FROM sg_raw_signals "
                "WHERE processed = 1 "
                "AND created_at >= NOW() - INTERVAL '7 days'"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT title, body FROM sg_raw_signals "
                "WHERE processed = 1 "
                "AND created_at >= datetime('now', '-7 days')"
            ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    counts = {dim: 0 for dim in _PMESII_PT}
    for r in rows:
        title = (r["title"] if isinstance(r, dict) else r[0]) or ""
        body = (r["body"] if isinstance(r, dict) else r[1]) or ""
        hits = _classify_pmesii(title + " " + body)
        for dim, h in hits.items():
            if h > 0:
                counts[dim] += 1

    return counts


def _posterior_shift_score(
    signal_text: str, prior_counts: Dict[str, int]
) -> Tuple[float, str]:
    """KL-divergence proxy: reward signals covering underrepresented PMESII-PT dimensions.

    Algorithm:
    1. Classify the new signal into PMESII-PT dimensions (keyword hits → binary present/absent).
    2. Build a prior distribution P from prior_counts (add Laplace smoothing ε=1).
    3. For each dimension the signal covers, compute information gain ≈ -log(P[dim]).
    4. Average over covered dimensions. Normalise to [0, 1] via sigmoid-like stretch.
    """
    hits = _classify_pmesii(signal_text)
    covered = [dim for dim, h in hits.items() if h > 0]

    if not covered:
        return 0.0, "no_pmesii_match"

    total_prior = sum(prior_counts.values()) + len(_PMESII_PT)  # Laplace-smoothed total
    scores = []
    top_dim = max(hits, key=lambda d: hits[d])

    for dim in covered:
        p = (prior_counts.get(dim, 0) + 1) / total_prior
        # Information content: rarer dimension = more surprising = higher shift
        scores.append(-math.log(p))

    raw = sum(scores) / len(scores)
    # Normalise: max raw ≈ -log(1/N) with N=8, log(8)≈2.08; cap at 2.5 for safety
    normalised = min(raw / 2.5, 1.0)
    return normalised, f"pmesii={top_dim};covered={len(covered)}"


def _source_discriminability_score(source: str) -> Tuple[float, str]:
    """Map source string to STANAG A-F grade → normalised 0-1 score."""
    if not source:
        return _STANAG_SCORE["F"], "stanag=F(unknown)"

    lower = source.lower().strip()
    # Prefix match against known sources
    for name, grade in _SOURCE_GRADE.items():
        if lower.startswith(name) or name in lower:
            return _STANAG_SCORE[grade], f"stanag={grade}({name})"

    # Heuristic: HTTPS-published government domains → B
    for gov_token in ("gov", "mil", ".un.", "nato", "interpol"):
        if gov_token in lower:
            return _STANAG_SCORE["B"], "stanag=B(gov_domain)"

    return _STANAG_SCORE["F"], "stanag=F(unknown_source)"


def _temporal_recency_score(signal_date: Optional[str], created_at: str) -> Tuple[float, str]:
    """exp(-hours_old / 24). Uses signal_date if parseable, else created_at."""
    now = datetime.now(timezone.utc)

    def _parse(s: str) -> Optional[datetime]:
        if not s:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None

    dt = _parse(signal_date or "") or _parse(created_at or "")
    if dt is None:
        # Unknown age → neutral score
        return 0.5, "age=unknown"

    hours_old = max((now - dt).total_seconds() / 3600, 0.0)
    score = math.exp(-hours_old / 24.0)
    age_label = f"{hours_old:.1f}h"
    return score, f"age={age_label}"


def _domain_coverage_score(
    signal_text: str, pir_topics: List[str]
) -> Tuple[float, str]:
    """Binary: 1.0 if signal text overlaps with any active PIR topic keyword, else 0.0."""
    if not pir_topics:
        return 0.0, "pir=no_requirements"

    lower = signal_text.lower()
    matched: List[str] = []
    for topic in pir_topics:
        topic_lower = topic.lower()
        topic_words = [w for w in topic_lower.split() if len(w) >= 4]
        if not topic_words:
            continue
        if any(w in lower for w in topic_words):
            matched.append(topic[:40])

    if matched:
        return 1.0, f"pir_match={matched[0]!r}"
    return 0.0, "pir=no_match"


def _fetch_pir_topics() -> List[str]:
    """Fetch active PIR/CCIR/EEI topic strings from sg_pir_requirements."""
    conn = get_connection()
    try:
        if is_pg():
            rows = conn.execute(
                "SELECT topic FROM sg_pir_requirements WHERE status = 'active'"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT topic FROM sg_pir_requirements WHERE status = 'active'"
            ).fetchall()
        return [(r["topic"] if isinstance(r, dict) else r[0]) or "" for r in rows]
    except Exception as exc:
        logger.warning("Could not fetch PIR topics: %s", exc)
        return []
    finally:
        conn.close()


def _score_signal(
    signal: Dict[str, Any],
    prior_counts: Dict[str, int],
    pir_topics: List[str],
) -> Dict[str, Any]:
    """Compute composite score and sub-scores for a single raw signal."""
    text = (signal.get("title") or "") + " " + (signal.get("body") or "")

    ps, ps_label = _posterior_shift_score(text, prior_counts)
    sd, sd_label = _source_discriminability_score(signal.get("source") or "")
    tr, tr_label = _temporal_recency_score(
        signal.get("signal_date"), signal.get("created_at") or ""
    )
    dc, dc_label = _domain_coverage_score(text, pir_topics)

    composite = 0.35 * ps + 0.25 * sd + 0.20 * tr + 0.20 * dc

    rationale = {
        "posterior_shift": {"score": round(ps, 4), "note": ps_label},
        "source_discriminability": {"score": round(sd, 4), "note": sd_label},
        "temporal_recency": {"score": round(tr, 4), "note": tr_label},
        "domain_coverage": {"score": round(dc, 4), "note": dc_label},
        "composite": round(composite, 4),
    }

    return {
        "raw_signal_id": signal["id"],
        "composite_score": composite,
        "posterior_shift_score": ps,
        "source_discriminability_score": sd,
        "temporal_recency_score": tr,
        "domain_coverage_score": dc,
        "rationale": json.dumps(rationale),
    }


def _insert_prioritized(scored: List[Dict[str, Any]], run_at: str) -> int:
    """Bulk-insert top-N scored signals; returns count inserted."""
    if not scored:
        return 0

    conn = get_connection()
    inserted = 0
    now = _utcnow_iso()
    try:
        for row in scored:
            if is_pg():
                conn.execute(
                    "INSERT INTO sg_prioritized_signals "
                    "(raw_signal_id, composite_score, posterior_shift_score, "
                    "source_discriminability_score, temporal_recency_score, "
                    "domain_coverage_score, rationale, run_at, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        row["raw_signal_id"],
                        row["composite_score"],
                        row["posterior_shift_score"],
                        row["source_discriminability_score"],
                        row["temporal_recency_score"],
                        row["domain_coverage_score"],
                        row["rationale"],
                        run_at,
                        now,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO sg_prioritized_signals "
                    "(raw_signal_id, composite_score, posterior_shift_score, "
                    "source_discriminability_score, temporal_recency_score, "
                    "domain_coverage_score, rationale, run_at, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        row["raw_signal_id"],
                        row["composite_score"],
                        row["posterior_shift_score"],
                        row["source_discriminability_score"],
                        row["temporal_recency_score"],
                        row["domain_coverage_score"],
                        row["rationale"],
                        run_at,
                        now,
                    ),
                )
            inserted += 1
        conn.commit()
    finally:
        conn.close()

    return inserted


def _mark_processed(signal_ids: List[int]) -> None:
    """Mark sg_raw_signals rows as processed=true."""
    if not signal_ids:
        return
    conn = get_connection()
    try:
        placeholder = "%s" if is_pg() else "?"  # nosec B608
        sql = "UPDATE sg_raw_signals SET processed = 1 WHERE id = " + placeholder  # nosec B608
        for sig_id in signal_ids:
            conn.execute(sql, (sig_id,))
        conn.commit()
    finally:
        conn.close()


# ── reflex entry point ────────────────────────────────────────────────────────

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Genesis reflex entry point. Returns {success, metric_value, details}."""
    _ensure_tables()

    run_at = _utcnow_iso()

    signals = _fetch_unprocessed()
    if not signals:
        msg = "No unprocessed signals found — nothing to score."
        print(f"  Strategos SignalScout: {msg}")
        logger.info(msg)
        return {"success": True, "metric_value": 0, "details": {"scored": 0, "inserted": 0}}

    prior_counts = _fetch_prior_pmesii_counts()
    pir_topics = _fetch_pir_topics()

    scored = [_score_signal(s, prior_counts, pir_topics) for s in signals]
    scored.sort(key=lambda r: r["composite_score"], reverse=True)

    top = scored[:_TOP_N]
    inserted = _insert_prioritized(top, run_at)

    all_ids = [s["id"] for s in signals]
    _mark_processed(all_ids)

    summary = (
        f"signals_evaluated={len(signals)} top_n={len(top)} "
        f"inserted={inserted} pir_topics={len(pir_topics)}"
    )
    print(f"  Strategos SignalScout: {summary}")
    logger.info("SignalScout complete: %s", summary)

    return {
        "success": True,
        "metric_value": inserted,
        "details": {
            "signals_evaluated": len(signals),
            "scored": len(scored),
            "inserted": inserted,
            "pir_topics_active": len(pir_topics),
            "top_score": round(top[0]["composite_score"], 4) if top else 0.0,
        },
    }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="ICDEV™ Strategos Signal Scout")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    result = run({}, None)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        d = result.get("details", {})
        print(f"Evaluated : {d.get('signals_evaluated', 0)}")
        print(f"Inserted  : {d.get('inserted', 0)}")
        print(f"Top score : {d.get('top_score', 0.0)}")
