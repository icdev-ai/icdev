#!/usr/bin/env python3
# CUI // SP-CTI
"""Domain-adapted embedding feasibility probe (rce-eval-02).

A dependency-free corpus-stats reporter that answers the *one* empirical
question behind the "should we fine-tune a domain-adapted embedding model?"
go/no-go: **is there enough in-domain (compliance/NIST) training text in the
vector store to fine-tune on, or is the low retrieval baseline a corpus gap?**

Fine-tuning a sentence-transformers model for the compliance corpus needs
(query -> positive-passage) pairs mined from in-domain chunks. If the vector
store holds ~zero compliance chunks, there is nothing to fine-tune on and the
low golden-set recall is a *corpus* problem, not an *embedding-quality* problem
— so the decision is DEFER until the compliance corpus is ingested.

This tool makes that decision **re-runnable**: run it again after compliance
ingestion; when eligible-chunk count crosses the threshold the signal flips.

Pure stdlib (sqlite3 + json). No torch, no sentence-transformers, air-gap safe.
Reads the committed RCE vector store (``data/rag/rag_vectors.db``) by default.

Usage:
    python tools/rag/embedding_feasibility.py --json
    python tools/rag/embedding_feasibility.py --db data/rag/rag_vectors.db --json
    python tools/rag/embedding_feasibility.py --min-eligible 2000 --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

# Source types whose chunk text is compliance/NIST-domain — i.e. eligible as
# in-domain positives for a domain-adapted compliance embedding model. Kept as a
# module constant (not hardcoded in logic) so it can be widened as new
# compliance ingesters land (crosswalk, OSCAL, STIG, ...).
COMPLIANCE_SOURCE_TYPES = frozenset(
    {
        "compliance_artifacts",
        "dic_document",
        "nist_controls",
        "nist_800_53",
        "oscal_catalog",
        "ssp",
        "poam",
        "stig",
        "crosswalk",
        "fedramp",
        "cmmc",
    }
)

# Minimum in-domain chunk count below which fine-tuning is not viable (too few
# positives to mine a usable pair set without overfitting). A soft heuristic,
# not a hard gate — the actual go decision also weighs deps/air-gap/treadmill.
DEFAULT_MIN_ELIGIBLE = 2000

DEFAULT_DB = "data/rag/rag_vectors.db"


def _resolve_conn(
    db: Union[str, Path, sqlite3.Connection, None],
) -> tuple[sqlite3.Connection, bool]:
    """Return (connection, owns_connection). Accepts a path or a live conn.

    Accepting an injected connection keeps the tool testable against an
    in-memory fixture with no DB file (see tests/test_embedding_feasibility.py).
    """
    if isinstance(db, sqlite3.Connection):
        return db, False
    path = Path(db) if db else Path(DEFAULT_DB)
    if not path.exists():
        raise FileNotFoundError(f"Vector store not found: {path}")
    return sqlite3.connect(str(path)), True


def corpus_stats(
    db: Union[str, Path, sqlite3.Connection, None] = None,
    *,
    table: str = "rag_chunks",
    compliance_source_types: Iterable[str] = COMPLIANCE_SOURCE_TYPES,
) -> Dict[str, Any]:
    """Count chunks per source_type and split eligible vs ineligible.

    Returns a dict with total, per-source-type counts, the eligible
    (compliance-domain) subtotal, and the eligible fraction.
    """
    compliance = frozenset(compliance_source_types)
    conn, owns = _resolve_conn(db)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT source_type, COUNT(*) FROM {table} GROUP BY source_type"  # noqa: S608 - table is a fixed literal/param, not user input
        )
        by_source_type: Dict[str, int] = {
            (row[0] or "(unknown)"): int(row[1]) for row in cur.fetchall()
        }
    finally:
        if owns:
            conn.close()

    total = sum(by_source_type.values())
    eligible = sum(n for st, n in by_source_type.items() if st in compliance)
    eligible_fraction = round(eligible / total, 4) if total else 0.0
    return {
        "total_chunks": total,
        "by_source_type": dict(
            sorted(by_source_type.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "compliance_source_types": sorted(compliance),
        "eligible_chunks": eligible,
        "ineligible_chunks": total - eligible,
        "eligible_fraction": eligible_fraction,
    }


def assess_feasibility(
    stats: Dict[str, Any], *, min_eligible: int = DEFAULT_MIN_ELIGIBLE
) -> Dict[str, Any]:
    """Turn corpus stats into a training-data-availability signal.

    NOTE: this is only the *training-data* dimension of the decision. The full
    go/no-go (deps, air-gap distribution, retrain treadmill, expected lift vs
    the rag_benchmark baseline) lives in the feature doc, not in code.
    """
    eligible = int(stats.get("eligible_chunks", 0))
    viable = eligible >= min_eligible
    return {
        "min_eligible": min_eligible,
        "eligible_chunks": eligible,
        "training_data_viable": viable,
        "signal": "TRAIN-DATA-SUFFICIENT" if viable else "TRAIN-DATA-INSUFFICIENT",
        "recommendation": (
            "In-domain corpus is large enough to mine fine-tuning pairs; "
            "proceed to A/B a candidate model via rag_benchmark --compare."
            if viable
            else "Too few compliance/NIST chunks to fine-tune on. DEFER: ingest "
            "the compliance corpus first, then re-run this probe and the "
            "rag_benchmark baseline before reconsidering fine-tuning."
        ),
    }


def build_report(
    db: Union[str, Path, sqlite3.Connection, None] = None,
    *,
    min_eligible: int = DEFAULT_MIN_ELIGIBLE,
    baseline_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Full report: corpus stats + feasibility signal (+ baseline metrics)."""
    stats = corpus_stats(db)
    report: Dict[str, Any] = {
        "classification": "CUI // SP-CTI",
        "card": "rce-eval-02",
        "corpus": stats,
        "assessment": assess_feasibility(stats, min_eligible=min_eligible),
    }
    if baseline_path:
        bp = Path(baseline_path)
        if bp.exists():
            with open(bp, encoding="utf-8") as fh:
                baseline = json.load(fh)
            report["baseline_metrics"] = baseline.get("aggregate", {})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Domain-adapted embedding feasibility probe (rce-eval-02)."
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB, help=f"Vector store SQLite path (default {DEFAULT_DB})."
    )
    parser.add_argument(
        "--min-eligible",
        type=int,
        default=DEFAULT_MIN_ELIGIBLE,
        help="Min in-domain chunks for fine-tuning to be viable.",
    )
    parser.add_argument(
        "--baseline",
        help="Optional rce_baseline.json to fold in current retrieval metrics.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output.")
    args = parser.parse_args()

    try:
        report = build_report(
            args.db, min_eligible=args.min_eligible, baseline_path=args.baseline
        )
    except FileNotFoundError as exc:
        payload = {"error": str(exc)}
        print(json.dumps(payload) if args.json_output else f"ERROR: {exc}")
        raise SystemExit(1)

    if args.json_output:
        print(json.dumps(report, indent=2))
        return

    corpus = report["corpus"]
    assessment = report["assessment"]
    print("CUI // SP-CTI — domain-embedding feasibility probe (rce-eval-02)")
    print(f"  total chunks         : {corpus['total_chunks']}")
    print(f"  eligible (compliance): {corpus['eligible_chunks']} "
          f"({corpus['eligible_fraction']:.1%})")
    print("  by source_type:")
    for st, n in corpus["by_source_type"].items():
        print(f"    {st:<24} {n}")
    print(f"  signal               : {assessment['signal']}")
    print(f"  recommendation       : {assessment['recommendation']}")


if __name__ == "__main__":
    main()
