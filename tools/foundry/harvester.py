# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Foundry Harvester — pull signals from every source into foundry_signals.

Bridges signal-discovery into the ACF pipeline. Pulls from five sources, each
normalized into a common ``foundry_signals`` row:

    1. innovation  — tools/innovation web/competitive signal store (innovation_signals)
    2. creative    — creative engine outputs (optional store)
    3. research    — industry research engine outputs (optional store)
    4. genesis     — Oracle predictions + KG self-awareness gap nodes (oracle_predictions)
    5. telemetry   — introspective_analyzer internal telemetry (gate failures,
                     unused tools, slow pipelines, knowledge gaps)

Cross-source dedup: every signal carries a SHA-256 ``dedup_hash`` over its
normalized (theme + keywords). When the same signal is surfaced by two engines
it collapses to a single stored row — the higher-precedence engine becomes the
primary ``source_engine`` and both engines are recorded in
``contributing_engines``. DB-level dedup is also enforced by a UNIQUE constraint
on ``dedup_hash`` (INSERT-OR-IGNORE), so re-runs never duplicate.

Usage:
    python tools/foundry/harvester.py --harvest --json
    python tools/foundry/harvester.py --harvest --source genesis --json
    python tools/foundry/harvester.py --harvest --source telemetry --json
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.foundry.constants import (  # noqa: E402
    DEFAULT_CLASSIFICATION,
    DEFAULT_SEVERITY,
    SEVERITY_WEIGHTS,
    SOURCE_PRECEDENCE,
)

# Source engines harvested by harvest_all(). "creative"/"research" are optional
# stores that acf-harvest-01 may not have populated yet — harvested gracefully.
ALL_SOURCES = ("innovation", "creative", "research", "genesis", "telemetry")

# introspective_analyzer telemetry analyses this harvester consumes.
TELEMETRY_ANALYSES = ("gate_failures", "unused_tools", "slow_pipelines", "knowledge_gaps")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sig_id():
    return f"fsig-{uuid.uuid4().hex[:12]}"


def _get_conn(db_path=None):
    """Connection respecting the configured backend; db_path forces SQLite (tests)."""
    return get_connection(db_path=db_path) if db_path else get_connection()


def _table_exists(conn, name):
    backend = getattr(conn, "_backend", "sqlite")
    if backend == "postgresql":
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", (name,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    return (row[0] if row else 0) > 0


def _norm_token(text):
    """Lower-case, collapse non-alphanumerics to single spaces, strip."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _norm_keywords(keywords):
    """Canonicalize a keyword list: normalize, drop empties, dedup, sort."""
    seen = []
    for kw in keywords or []:
        n = _norm_token(str(kw))
        n = re.sub(r"\s+", "_", n)
        if n and n not in seen:
            seen.append(n)
    return sorted(seen)


def dedup_hash(theme, keywords):
    """SHA-256 over normalized theme + keywords. Stable across sources."""
    norm_theme = re.sub(r"\s+", " ", _norm_token(theme))
    norm_kw = ",".join(_norm_keywords(keywords))
    return hashlib.sha256(f"{norm_theme}|{norm_kw}".encode("utf-8")).hexdigest()


def _make_signal(source_engine, theme, keywords, summary="", severity=None,
                 source_type=None, source_ref=None, metadata=None):
    """Build a normalized in-memory signal dict (pre-persist)."""
    sev = (severity or DEFAULT_SEVERITY).lower()
    if sev not in SEVERITY_WEIGHTS:
        sev = DEFAULT_SEVERITY
    return {
        "source_engine": source_engine,
        "source_type": source_type,
        "source_ref": source_ref,
        "theme": theme.strip(),
        "keywords": _norm_keywords(keywords),
        "summary": summary or "",
        "severity": sev,
        "weight": SEVERITY_WEIGHTS[sev],
        "metadata": metadata or {},
        "dedup_hash": dedup_hash(theme, keywords),
    }


# --------------------------------------------------------------------------- #
# Source: innovation (acf-harvest-01 anchor — innovation_signals store)
# --------------------------------------------------------------------------- #
def harvest_innovation(conn, limit=200):
    signals = []
    if not _table_exists(conn, "innovation_signals"):
        return signals
    rows = conn.execute(
        """SELECT id, title, description, category, source_type, status
           FROM innovation_signals
           WHERE status IN ('new', 'scored', 'triaged', 'approved')
           ORDER BY COALESCE(composite_score, score, 0) DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    for row in rows:
        title = row["title"]
        if not title:
            continue
        kws = [w for w in re.split(r"\s+", title) if len(w) > 3][:6]
        if row["category"]:
            kws.append(row["category"])
        signals.append(_make_signal(
            "innovation", theme=title, keywords=kws,
            summary=(row["description"] or "")[:500],
            source_type=row["source_type"], source_ref=row["id"],
            metadata={"category": row["category"], "status": row["status"]},
        ))
    return signals


# --------------------------------------------------------------------------- #
# Source: creative / research (optional stores — graceful if absent)
# --------------------------------------------------------------------------- #
def _harvest_optional_store(conn, engine, table, limit=200):
    """Read an optional <engine> store that mirrors innovation_signals columns."""
    signals = []
    if not _table_exists(conn, table):
        return signals
    rows = conn.execute(
        f"SELECT * FROM {table} LIMIT ?",  # nosec B608 -- table name is an internal constant
        (limit,),
    ).fetchall()
    for row in rows:
        d = dict(row)
        theme = d.get("title") or d.get("theme") or d.get("name")
        if not theme:
            continue
        kws = [w for w in re.split(r"\s+", theme) if len(w) > 3][:6]
        if d.get("category"):
            kws.append(d["category"])
        signals.append(_make_signal(
            engine, theme=theme, keywords=kws,
            summary=(d.get("description") or d.get("summary") or "")[:500],
            source_type=d.get("source_type"), source_ref=str(d.get("id", "")),
        ))
    return signals


def harvest_creative(conn, limit=200):
    return _harvest_optional_store(conn, "creative", "creative_signals", limit)


def harvest_research(conn, limit=200):
    return _harvest_optional_store(conn, "research", "research_signals", limit)


# --------------------------------------------------------------------------- #
# Source: genesis (Oracle predictions + KG self-awareness gap nodes)
# --------------------------------------------------------------------------- #
# gap_detector writes self-awareness gap nodes into oracle_predictions, so this
# one table covers both "oracle_prediction" and "kg self-awareness gap nodes".
def harvest_genesis(conn, limit=200):
    signals = []
    if not _table_exists(conn, "oracle_predictions"):
        return signals
    rows = conn.execute(
        """SELECT id, lens_name, subject_type, subject_id, prediction_type,
                  prediction_text, severity, confidence
           FROM oracle_predictions
           WHERE outcome IS NULL OR outcome = 'pending'
           ORDER BY confidence DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    for row in rows:
        text = row["prediction_text"]
        if not text:
            continue
        ptype = row["prediction_type"] or "prediction"
        # Theme keeps the prediction type + subject so distinct predictions on
        # the same subject don't collide, but normalized so a telemetry signal
        # describing the same condition can dedup against it.
        theme = text.strip()
        kws = [ptype, row["subject_type"], row["subject_id"]]
        kws += [w for w in re.split(r"\s+", text) if len(w) > 3][:5]
        signals.append(_make_signal(
            "genesis", theme=theme, keywords=kws,
            summary=text[:500], severity=row["severity"],
            source_type=ptype, source_ref=row["id"],
            metadata={
                "lens": row["lens_name"],
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "confidence": row["confidence"],
            },
        ))
    return signals


# --------------------------------------------------------------------------- #
# Source: telemetry (introspective_analyzer internal mining)
# --------------------------------------------------------------------------- #
def _telemetry_signal_from_finding(analysis_type, finding, recommendations):
    """Map one introspective_analyzer finding to a normalized signal dict."""
    rec = recommendations[0] if recommendations else ""
    if analysis_type == "gate_failures":
        gate = finding.get("gate_event_type", "gate")
        proj = finding.get("project_id", "")
        return _make_signal(
            "telemetry",
            theme=f"Recurring gate failure: {gate}",
            keywords=["gate_failure", gate, proj],
            summary=rec or f"{gate} failed {finding.get('failure_count')}x in {proj}",
            severity="high", source_type=analysis_type,
            source_ref=f"{analysis_type}:{gate}:{proj}",
            metadata=finding,
        )
    if analysis_type == "unused_tools":
        evt = finding.get("event_type", "tool")
        return _make_signal(
            "telemetry",
            theme=f"Unused or dormant tool: {evt}",
            keywords=["unused_tool", evt],
            summary=rec or f"{evt} has no recent invocations",
            severity="low", source_type=analysis_type,
            source_ref=f"{analysis_type}:{evt}",
            metadata=finding,
        )
    if analysis_type == "slow_pipelines":
        stage = finding.get("stage", "stage")
        proj = finding.get("project_id", "")
        return _make_signal(
            "telemetry",
            theme=f"Slow pipeline stage: {stage}",
            keywords=["slow_pipeline", stage, proj],
            summary=rec or f"{stage} took {finding.get('duration_seconds')}s in {proj}",
            severity="medium", source_type=analysis_type,
            source_ref=f"{analysis_type}:{stage}:{proj}",
            metadata=finding,
        )
    if analysis_type == "knowledge_gaps":
        desc = finding.get("description") or finding.get("pattern_type") or "knowledge gap"
        return _make_signal(
            "telemetry",
            theme=f"Knowledge gap: {desc}",
            keywords=["knowledge_gap", finding.get("pattern_type", ""), finding.get("gap_type", "")],
            summary=rec or desc,
            severity="medium", source_type=analysis_type,
            source_ref=f"{analysis_type}:{finding.get('pattern_id', '')}",
            metadata=finding,
        )
    return None


def harvest_telemetry(db_path=None):
    """Run introspective_analyzer telemetry analyses → normalized signals.

    Calls the analyzer functions directly (each takes its own db_path and opens
    its own connection) so we reuse the real telemetry-mining logic rather than
    re-querying audit_trail here.
    """
    signals = []
    try:
        from tools.innovation import introspective_analyzer as ia
    except ImportError:
        return signals

    analyses = {
        "gate_failures": ia.analyze_gate_failures,
        "unused_tools": ia.analyze_unused_tools,
        "slow_pipelines": ia.analyze_slow_pipelines,
        "knowledge_gaps": ia.analyze_knowledge_gaps,
    }
    for atype in TELEMETRY_ANALYSES:
        fn = analyses.get(atype)
        if fn is None:
            continue
        try:
            result = fn(db_path=db_path)
        except Exception:
            continue
        if result.get("skipped"):
            continue
        recs = result.get("recommendations", [])
        for finding in result.get("findings", []):
            sig = _telemetry_signal_from_finding(atype, finding, recs)
            if sig:
                signals.append(sig)
    return signals


# --------------------------------------------------------------------------- #
# Cross-source dedup + persist
# --------------------------------------------------------------------------- #
def _collapse(signals):
    """Collapse signals sharing a dedup_hash into one, merging contributors.

    The primary source_engine is chosen by SOURCE_PRECEDENCE (then weight).
    contributing_engines records every engine that surfaced the signal.
    """
    by_hash = {}
    for sig in signals:
        h = sig["dedup_hash"]
        if h not in by_hash:
            merged = dict(sig)
            merged["contributing_engines"] = [sig["source_engine"]]
            by_hash[h] = merged
            continue
        merged = by_hash[h]
        if sig["source_engine"] not in merged["contributing_engines"]:
            merged["contributing_engines"].append(sig["source_engine"])
        # Pick the higher-precedence (then higher-weight) engine as primary.
        cur = (SOURCE_PRECEDENCE.get(merged["source_engine"], 0), merged["weight"])
        new = (SOURCE_PRECEDENCE.get(sig["source_engine"], 0), sig["weight"])
        if new > cur:
            for k in ("source_engine", "source_type", "source_ref", "theme",
                      "summary", "severity", "weight", "metadata"):
                merged[k] = sig[k]
    for merged in by_hash.values():
        merged["contributing_engines"].sort()
    return list(by_hash.values())


def persist_signals(conn, signals):
    """Insert collapsed signals into foundry_signals (append-only, dedup-safe).

    UNIQUE(dedup_hash) + INSERT-OR-IGNORE means a signal already stored from a
    prior run (or already present from another engine in this batch) is stored
    once. Returns the count actually inserted.
    """
    inserted = 0
    now = _now()
    for sig in signals:
        # Skip if this hash already exists (cross-run dedup; explicit so the
        # count is accurate regardless of OR IGNORE rowcount semantics).
        existing = conn.execute(
            "SELECT 1 FROM foundry_signals WHERE dedup_hash = ?", (sig["dedup_hash"],)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """INSERT INTO foundry_signals
               (id, source_engine, source_type, source_ref, theme, keywords,
                summary, severity, weight, dedup_hash, contributing_engines,
                metadata, status, tenant_id, classification, discovered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 'default', ?, ?)""",
            (
                _sig_id(), sig["source_engine"], sig.get("source_type"),
                sig.get("source_ref"), sig["theme"],
                json.dumps(sig["keywords"]), sig.get("summary", ""),
                sig.get("severity"), sig.get("weight", 0.5), sig["dedup_hash"],
                json.dumps(sig.get("contributing_engines", [sig["source_engine"]])),
                json.dumps(sig.get("metadata", {})), DEFAULT_CLASSIFICATION, now,
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


def harvest_all(db_path=None, sources=None):
    """Harvest every (or selected) source, dedup cross-source, persist.

    Returns a summary dict: raw counts per source, collapsed total, inserted.
    """
    sources = tuple(sources) if sources else ALL_SOURCES
    conn = _get_conn(db_path)
    raw = []
    per_source = {}
    try:
        if "innovation" in sources:
            s = harvest_innovation(conn)
            per_source["innovation"] = len(s)
            raw += s
        if "creative" in sources:
            s = harvest_creative(conn)
            per_source["creative"] = len(s)
            raw += s
        if "research" in sources:
            s = harvest_research(conn)
            per_source["research"] = len(s)
            raw += s
        if "genesis" in sources:
            s = harvest_genesis(conn)
            per_source["genesis"] = len(s)
            raw += s
        if "telemetry" in sources:
            # telemetry analyses open their own connections via db_path
            s = harvest_telemetry(db_path=db_path)
            per_source["telemetry"] = len(s)
            raw += s

        collapsed = _collapse(raw)
        inserted = persist_signals(conn, collapsed)
    finally:
        conn.close()

    return {
        "raw_signals": len(raw),
        "per_source": per_source,
        "collapsed_signals": len(collapsed),
        "inserted": inserted,
        "deduped": len(raw) - len(collapsed),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(description="Foundry signal harvester")
    p.add_argument("--harvest", action="store_true", help="Run a harvest cycle")
    p.add_argument("--source", action="append", choices=ALL_SOURCES,
                   help="Limit to specific source(s); repeatable")
    p.add_argument("--db-path", help="SQLite DB path (defaults to configured backend)")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args(argv)

    if not args.harvest:
        p.print_help()
        return 1

    summary = harvest_all(db_path=args.db_path, sources=args.source)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Harvested {summary['raw_signals']} raw → "
              f"{summary['collapsed_signals']} unique "
              f"({summary['deduped']} deduped), {summary['inserted']} new inserted")
        for src, n in summary["per_source"].items():
            print(f"  {src}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
