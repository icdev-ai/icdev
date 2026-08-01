#!/usr/bin/env python3
# CUI // SP-CTI
"""Measure whether the memory tier earns its keep (oss2-meas-01).

The oss-02 spike (§3.4) says: fix the consolidation defect (oss2-fix-04, done),
THEN measure before building anything mem0-shaped. This is that measurement.

Correction to the spike's pointer: ``tools/rag/rag_benchmark.py`` measures RAG
*document* retrieval against a compliance golden set — a DIFFERENT system from the
memory tier (``tools/memory/`` over ``memory_entries``, recalled into the agent
loop). So the memory tier needs its own instrument; this is it.

The decisive question (spike §3.3) is CONSOLIDATION IMPACT: exact-hash dedup already
works, so the only thing the now-repaired ``MemoryConsolidator`` adds is merging
*semantically* redundant memories that differ byte-for-byte. If the tier accumulates
meaningful near-duplication, consolidation earns its keep and mem0-shaped
consolidation features have headroom; if it does not, they buy little.

**Honest-measurement guardrail (from the oss-adaptation golden-set lesson):** a
measurement over too little data launders a verdict. Below ``MIN_SAMPLE`` entries
this reports ``insufficient_data`` and makes no keep/drop claim — it does not round
a tiny table up to "no redundancy, don't build."

Uses the CONSOLIDATOR'S OWN similarity (``_extract_keywords`` / ``_jaccard_similarity``)
so the redundancy it counts is exactly what consolidation would act on.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from tools.db.storage import get_connection
from tools.memory.memory_consolidation import MemoryConsolidator

# Below this many entries, refuse to render a verdict — too little to be honest.
MIN_SAMPLE = 30
# Redundancy-rate bands for the keep/drop reading (fraction of entries that have at
# least one semantic near-duplicate the exact-hash dedup missed).
_EARNS_KEEP = 0.15
_LOW_VALUE = 0.05


def _load_entries(conn, limit: int) -> List[Dict[str, Any]]:
    # LIMIT takes an internal int (not user input); inline it so the query needs no
    # placeholder and runs identically on PostgreSQL (via get_connection) and a raw
    # SQLite connection in tests.
    rows = conn.execute(
        f"SELECT id, content, type, content_hash FROM memory_entries "
        f"ORDER BY created_at DESC LIMIT {int(limit)}"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {"id": r[0], "content": r[1], "type": r[2], "content_hash": r[3]}
        out.append(d)
    return out


def measure_consolidation(limit: int = 2000, threshold: float = 0.75) -> Dict[str, Any]:
    """Pairwise semantic-redundancy scan over memory_entries using the
    consolidator's own keyword/Jaccard similarity."""
    conn = get_connection()
    try:
        entries = _load_entries(conn, limit)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    total = len(entries)
    result: Dict[str, Any] = {
        "total_entries": total,
        "threshold": threshold,
        "min_sample": MIN_SAMPLE,
    }
    if total < MIN_SAMPLE:
        result["verdict"] = "insufficient_data"
        result["reason"] = (
            f"only {total} memory entries (< {MIN_SAMPLE}); too few to measure the "
            "tier's redundancy honestly. Re-run against a populated memory_entries."
        )
        return result

    c = MemoryConsolidator(use_llm=False)
    kws = [c._extract_keywords(e["content"] or "") for e in entries]

    # exact-hash duplicates (the baseline dedup already handles these)
    hashes = [e.get("content_hash") for e in entries if e.get("content_hash")]
    exact_dups = len(hashes) - len(set(hashes))

    near_dup_pairs = 0
    entries_with_near_dup = set()
    for i in range(total):
        if not kws[i]:
            continue
        for j in range(i + 1, total):
            if not kws[j]:
                continue
            sim = c._jaccard_similarity(kws[i], kws[j])
            if sim >= threshold:
                near_dup_pairs += 1
                entries_with_near_dup.add(i)
                entries_with_near_dup.add(j)

    redundancy_rate = round(len(entries_with_near_dup) / total, 4) if total else 0.0
    result.update(
        {
            "exact_hash_duplicates": exact_dups,
            "semantic_near_dup_pairs": near_dup_pairs,
            "entries_with_a_near_dup": len(entries_with_near_dup),
            "redundancy_rate": redundancy_rate,
        }
    )
    if redundancy_rate >= _EARNS_KEEP:
        result["verdict"] = "consolidation_earns_its_keep"
        result["reason"] = (
            f"{redundancy_rate:.1%} of entries have a semantic near-duplicate that "
            "byte-hash dedup missed; the repaired consolidation would merge these."
        )
    elif redundancy_rate < _LOW_VALUE:
        result["verdict"] = "low_value"
        result["reason"] = (
            f"only {redundancy_rate:.1%} of entries are near-duplicates; the tier "
            "accumulates little semantic redundancy, so mem0-shaped consolidation "
            "features buy little on this corpus."
        )
    else:
        result["verdict"] = "marginal"
        result["reason"] = f"{redundancy_rate:.1%} near-duplication — measure again as the corpus grows."
    return result


def measure_baseline() -> Dict[str, Any]:
    """Cheap descriptive stats about the tier's current state."""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
        by_type = [
            tuple(r) if not hasattr(r, "keys") else (r[0], r[1])
            for r in conn.execute("SELECT type, COUNT(*) FROM memory_entries GROUP BY type").fetchall()
        ]
        embedded = conn.execute(
            "SELECT COUNT(*) FROM memory_entries WHERE embedding IS NOT NULL"
        ).fetchone()[0]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return {
        "total_entries": total,
        "by_type": {t: n for t, n in by_type},
        "embedding_coverage": round(embedded / total, 4) if total else 0.0,
    }


def run(limit: int = 2000, threshold: float = 0.75) -> Dict[str, Any]:
    return {
        "baseline": measure_baseline(),
        "consolidation": measure_consolidation(limit=limit, threshold=threshold),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure whether the memory tier earns its keep (oss2-meas-01)")
    parser.add_argument("--limit", type=int, default=2000, help="Max entries to scan")
    parser.add_argument("--threshold", type=float, default=0.75, help="Jaccard near-dup threshold")
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args()
    report = run(limit=ns.limit, threshold=ns.threshold)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
