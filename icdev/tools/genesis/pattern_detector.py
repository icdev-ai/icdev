#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Pattern Detector — deterministic tool-chain mining from telemetry.

Scans hook_events and ai_telemetry for recurring multi-tool sequences,
scoring each by frequency, caller diversity, and failure avoidance.

Pure stdlib (collections, itertools) — air-gap safe, zero LLM cost.

Usage:
    python tools/genesis/pattern_detector.py --json
    python tools/genesis/pattern_detector.py --lookback-days 14 --min-frequency 5 --json
    python tools/genesis/pattern_detector.py --adaptive --sensitivity 1.0 --json
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Config defaults — used when YAML is unavailable or a key is absent
# ---------------------------------------------------------------------------

_DEFAULTS: Dict[str, Any] = {
    "min_pattern_frequency": 3,
    "min_chain_length": 3,
    "min_chain_flush_length": 2,
    "lookback_days": 7,
    "max_gap_seconds": 300,
    "top_k": 20,
    "session_ids_cap": 10,
    "anomaly_detection": {
        "enabled": True,
        "min_samples": 4,
        "sensitivity": 1.0,
        "fallback_to_static": True,
        "frequency_floor": 2,
    },
}


def _load_synthesize_config() -> Dict[str, Any]:
    """Load synthesize reflex config from args/genesis_config.yaml.

    Returns _DEFAULTS merged with the YAML values so callers always get
    a complete config even when the file is absent or malformed.
    """
    config_path = BASE_DIR / "args" / "genesis_config.yaml"
    try:
        import yaml  # type: ignore

        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        synth = (raw or {}).get("reflexes", {}).get("synthesize", {})
    except Exception:
        synth = {}

    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in synth.items() if v is not None})
    ad_override = synth.get("anomaly_detection", {})
    merged["anomaly_detection"] = {**_DEFAULTS["anomaly_detection"], **ad_override}
    return merged


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Tool chain extraction
# ---------------------------------------------------------------------------


def _extract_session_tool_chains(
    lookback_days: int = 7,
    max_gap_seconds: int = 300,
    min_flush_length: int = 2,
) -> Dict[str, List[List[str]]]:
    """Extract ordered tool-call chains per session from hook_events.

    Groups consecutive tool calls within a session, splitting chains when
    the gap between calls exceeds max_gap_seconds.

    min_flush_length: minimum chain size to keep when flushing; sourced from
      min_chain_flush_length in genesis_config.yaml (default 2).

    Returns:
        Dict mapping session_id → list of tool chains (each chain is a list
        of tool_name strings in execution order).
    """
    conn = get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute(
            """
            SELECT session_id, tool_name, created_at
            FROM hook_events
            WHERE created_at > %s
              AND session_id IS NOT NULL
              AND tool_name IS NOT NULL
            ORDER BY session_id, created_at
        """,
            (cutoff,),
        ).fetchall()

        sessions: Dict[str, List[List[str]]] = defaultdict(list)
        current_session = None
        current_chain: List[str] = []
        prev_ts: Optional[datetime] = None

        for row in rows:
            sid = row["session_id"] if isinstance(row, dict) else row[0]
            tool = row["tool_name"] if isinstance(row, dict) else row[1]
            ts_str = row["created_at"] if isinstance(row, dict) else row[2]

            if not sid or not tool:
                continue

            # Parse timestamp
            try:
                if isinstance(ts_str, str):
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                else:
                    ts = ts_str
            except (ValueError, TypeError):
                continue

            # New session → flush previous chain
            if sid != current_session:
                if current_chain and len(current_chain) >= min_flush_length:
                    sessions[current_session].append(current_chain)
                current_session = sid
                current_chain = [tool]
                prev_ts = ts
                continue

            # Same session — check time gap
            if prev_ts:
                gap = (ts - prev_ts).total_seconds()
                if gap > max_gap_seconds:
                    # Gap too large → start new chain
                    if len(current_chain) >= min_flush_length:
                        sessions[sid].append(current_chain)
                    current_chain = [tool]
                    prev_ts = ts
                    continue

            current_chain.append(tool)
            prev_ts = ts

        # Flush last chain
        if current_chain and len(current_chain) >= min_flush_length and current_session:
            sessions[current_session].append(current_chain)

        return dict(sessions)
    except Exception as exc:
        return {"_error": [[str(exc)]]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Adaptive frequency threshold (anomaly detection)
# ---------------------------------------------------------------------------


def _compute_adaptive_frequency_threshold(
    ngram_counts: Counter,
    sensitivity: float = 1.0,
    fallback: int = 2,
    min_samples: int = 4,
) -> Tuple[int, Dict[str, Any]]:
    """Compute a data-driven minimum frequency threshold via IQR anomaly detection.

    Replaces the fixed min_frequency constant with a threshold derived from
    the observed distribution of n-gram frequencies.

    Formula: threshold = max(fallback, int(Q1 + sensitivity * IQR))
      sensitivity=0.5 → between Q1 and Q3 (lenient, more patterns pass)
      sensitivity=1.0 → at Q3 / 75th percentile (balanced default)
      sensitivity=1.5 → Q3 + 0.5*IQR (strict, only clear outliers)
      sensitivity=2.0 → Q3 + 1.0*IQR (classical IQR upper fence)

    min_samples: minimum distinct n-grams required before adaptive mode activates;
      sourced from anomaly_detection.min_samples in genesis_config.yaml.

    Returns:
        (threshold, stats_dict) where stats_dict has distribution metadata.
    """
    if not ngram_counts:
        return fallback, {"method": "fallback", "reason": "empty_counts"}

    freqs = sorted(ngram_counts.values())
    n = len(freqs)

    if n < min_samples:
        return fallback, {"method": "fallback", "reason": "too_few_ngrams", "n": n, "min_samples": min_samples}

    q1_idx = n // 4
    q3_idx = (3 * n) // 4
    q1 = freqs[q1_idx]
    q3 = freqs[q3_idx]
    median = freqs[n // 2]
    iqr = q3 - q1

    raw = q1 + sensitivity * iqr
    threshold = max(fallback, int(raw))

    stats = {
        "method": "iqr",
        "n_ngrams": n,
        "q1": q1,
        "median": median,
        "q3": q3,
        "iqr": iqr,
        "sensitivity": sensitivity,
        "raw_threshold": round(raw, 3),
        "threshold": threshold,
    }
    return threshold, stats


def compute_adaptive_frequency_threshold(
    ngram_counts: Counter,
    sensitivity: float = 1.0,
    fallback: int = 2,
    min_samples: int = 4,
) -> Dict[str, Any]:
    """Public API: compute IQR-based adaptive frequency threshold.

    Returns a dict with 'threshold' (int) guaranteed, plus distribution metadata.
    See _compute_adaptive_frequency_threshold for the full formula.

    min_samples: minimum distinct n-grams required before adaptive mode activates;
      default matches anomaly_detection.min_samples in genesis_config.yaml.
    """
    threshold, stats = _compute_adaptive_frequency_threshold(
        ngram_counts, sensitivity=sensitivity, fallback=fallback, min_samples=min_samples
    )
    return {"threshold": threshold, **stats}


# ---------------------------------------------------------------------------
# Sub-chain (n-gram) extraction
# ---------------------------------------------------------------------------


def _extract_ngrams(chains: Dict[str, List[List[str]]], min_length: int = 3) -> Counter:
    """Extract all n-grams of length >= min_length from tool chains.

    Returns Counter mapping tuple(tool_names) → count across all sessions.
    """
    ngram_counts: Counter = Counter()
    for session_chains in chains.values():
        for chain in session_chains:
            for n in range(min_length, len(chain) + 1):
                for i in range(len(chain) - n + 1):
                    ngram = tuple(chain[i : i + n])
                    ngram_counts[ngram] += 1
    return ngram_counts


# ---------------------------------------------------------------------------
# Pattern scoring
# ---------------------------------------------------------------------------


def _score_pattern(
    ngram: tuple,
    count: int,
    chains: Dict[str, List[List[str]]],
    session_ids_cap: int = 10,
) -> Dict[str, Any]:
    """Score a tool-chain pattern on frequency, caller diversity, and length.

    Dimensions:
      - frequency: raw count (normalized later)
      - caller_diversity: number of distinct sessions containing this pattern
      - chain_length: longer chains = more complex workflows captured
      - uniqueness: 1 - overlap with shorter sub-patterns (penalize redundancy)

    session_ids_cap: max session IDs stored per pattern for readability;
      sourced from synthesize.session_ids_cap in genesis_config.yaml.

    Returns dict with scores and metadata.
    """
    # Caller diversity: count distinct sessions containing this ngram
    session_ids = set()
    for sid, session_chains in chains.items():
        for chain in session_chains:
            chain_tuple = tuple(chain)
            for i in range(len(chain_tuple) - len(ngram) + 1):
                if chain_tuple[i : i + len(ngram)] == ngram:
                    session_ids.add(sid)
                    break

    diversity = len(session_ids)

    # Composite score: frequency * diversity * log(length)
    import math

    length_bonus = math.log2(max(len(ngram), 2))
    composite = count * diversity * length_bonus

    return {
        "pattern": list(ngram),
        "frequency": count,
        "caller_diversity": diversity,
        "chain_length": len(ngram),
        "length_bonus": round(length_bonus, 3),
        "composite_score": round(composite, 3),
        "session_ids": sorted(session_ids)[:session_ids_cap],
    }


# ---------------------------------------------------------------------------
# Main detection pipeline
# ---------------------------------------------------------------------------


def detect_tool_patterns(
    min_frequency: Optional[int] = None,
    min_chain_length: Optional[int] = None,
    lookback_days: Optional[int] = None,
    max_gap_seconds: Optional[int] = None,
    top_k: Optional[int] = None,
    adaptive: Optional[bool] = None,
    sensitivity: Optional[float] = None,
) -> Dict[str, Any]:
    """Run the full pattern detection pipeline.

    All thresholds default to args/genesis_config.yaml (reflexes.synthesize).
    When adaptive=True (the config default), min_frequency is replaced by an
    IQR-based anomaly detection threshold computed from the n-gram distribution.

    1. Extract session tool chains from hook_events
    2. Mine n-grams of length >= min_chain_length
    3. Compute adaptive IQR threshold (or use static min_frequency)
    4. Filter, score, and rank
    5. Deduplicate (remove sub-patterns of higher-scoring super-patterns)

    Returns:
        Dict with patterns list, metadata, threshold_info, and statistics.
    """
    cfg = _load_synthesize_config()
    ad_cfg = cfg["anomaly_detection"]

    _min_frequency = min_frequency if min_frequency is not None else cfg["min_pattern_frequency"]
    _min_chain_length = min_chain_length if min_chain_length is not None else cfg["min_chain_length"]
    _min_chain_flush = cfg.get("min_chain_flush_length", _DEFAULTS["min_chain_flush_length"])
    _lookback_days = lookback_days if lookback_days is not None else cfg["lookback_days"]
    _max_gap_seconds = max_gap_seconds if max_gap_seconds is not None else cfg["max_gap_seconds"]
    _top_k = top_k if top_k is not None else cfg["top_k"]
    _adaptive = adaptive if adaptive is not None else ad_cfg.get("enabled", True)
    _sensitivity = sensitivity if sensitivity is not None else ad_cfg.get("sensitivity", 1.0)

    result: Dict[str, Any] = {
        "detected_at": _utcnow_iso(),
        "lookback_days": _lookback_days,
        "min_frequency": _min_frequency,
        "min_chain_length": _min_chain_length,
        "adaptive": _adaptive,
        "config_source": "args/genesis_config.yaml",
    }

    # Step 1: Extract chains
    chains = _extract_session_tool_chains(
        lookback_days=_lookback_days,
        max_gap_seconds=_max_gap_seconds,
        min_flush_length=_min_chain_flush,
    )
    if "_error" in chains:
        result["error"] = chains["_error"][0][0] if chains["_error"] else "unknown"
        result["patterns"] = []
        return result

    total_sessions = len(chains)
    total_chains = sum(len(c) for c in chains.values())
    result["sessions_analyzed"] = total_sessions
    result["chains_extracted"] = total_chains

    if total_chains == 0:
        result["patterns"] = []
        result["message"] = "No tool chains found in the lookback window"
        return result

    # Step 2: Extract n-grams
    ngram_counts = _extract_ngrams(chains, min_length=_min_chain_length)

    _min_samples = ad_cfg.get("min_samples", _DEFAULTS["anomaly_detection"]["min_samples"])
    _session_ids_cap = cfg.get("session_ids_cap", _DEFAULTS["session_ids_cap"])

    # Step 3: Determine frequency threshold (adaptive IQR or static)
    if _adaptive:
        effective_threshold, threshold_stats = _compute_adaptive_frequency_threshold(
            ngram_counts,
            sensitivity=_sensitivity,
            fallback=_min_frequency,
            min_samples=_min_samples,
        )
        result["threshold_info"] = threshold_stats
        result["effective_min_frequency"] = effective_threshold
    else:
        effective_threshold = _min_frequency
        result["threshold_info"] = {"method": "static", "threshold": _min_frequency}
        result["effective_min_frequency"] = _min_frequency

    # Step 4: Filter by threshold
    frequent = {ng: cnt for ng, cnt in ngram_counts.items() if cnt >= effective_threshold}

    # Step 5: Score
    scored = []
    for ngram, count in frequent.items():
        scored.append(_score_pattern(ngram, count, chains, session_ids_cap=_session_ids_cap))

    # Step 6: Sort by composite score descending
    scored.sort(key=lambda x: x["composite_score"], reverse=True)

    # Step 7: Deduplicate — remove sub-patterns if a super-pattern scores higher
    deduplicated = []
    seen_supersets = []
    for pattern in scored:
        p_tuple = tuple(pattern["pattern"])
        is_sub = False
        for sp in seen_supersets:
            # Check if p_tuple is a contiguous sub-sequence of sp
            sp_str = " ".join(sp)
            p_str = " ".join(p_tuple)
            if p_str in sp_str and len(p_tuple) < len(sp):
                is_sub = True
                break
        if not is_sub:
            deduplicated.append(pattern)
            seen_supersets.append(p_tuple)

    result["patterns"] = deduplicated[:_top_k]
    result["total_ngrams"] = len(ngram_counts)
    result["frequent_ngrams"] = len(frequent)
    result["deduplicated_patterns"] = len(deduplicated[:_top_k])

    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def store_patterns(patterns: List[Dict], db_path: Optional[str] = None) -> int:
    """Store detected patterns in genesis_tool_patterns table.

    Returns count of new patterns stored (skips duplicates by tool_chain hash).
    """
    import hashlib

    conn = get_connection(db_path)
    stored = 0
    try:
        for p in patterns:
            chain_json = json.dumps(p["pattern"], sort_keys=True)
            chain_hash = hashlib.sha256(chain_json.encode()).hexdigest()[:16]

            # Check for existing pattern with same hash
            existing = conn.execute(
                "SELECT id FROM genesis_tool_patterns WHERE chain_hash = %s", (chain_hash,)
            ).fetchone()

            if existing:
                # Update frequency and last_seen
                conn.execute(
                    """
                    UPDATE genesis_tool_patterns
                    SET frequency = %s, last_seen = %s,
                        sessions = %s, composite_score = %s
                    WHERE chain_hash = %s
                """,
                    (
                        p["frequency"],
                        _utcnow_iso(),
                        json.dumps(p.get("session_ids", [])),
                        p["composite_score"],
                        chain_hash,
                    ),
                )
            else:
                pattern_id = f"tpat-{chain_hash}"
                conn.execute(
                    """
                    INSERT INTO genesis_tool_patterns
                        (id, chain_hash, tool_chain, frequency, caller_diversity,
                         chain_length, composite_score, sessions, status,
                         first_seen, last_seen, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        pattern_id,
                        chain_hash,
                        chain_json,
                        p["frequency"],
                        p["caller_diversity"],
                        p["chain_length"],
                        p["composite_score"],
                        json.dumps(p.get("session_ids", [])),
                        "detected",
                        _utcnow_iso(),
                        _utcnow_iso(),
                        _utcnow_iso(),
                    ),
                )
                stored += 1

        conn.commit()
        return stored
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    cfg = _load_synthesize_config()
    ad_cfg = cfg["anomaly_detection"]
    parser = argparse.ArgumentParser(description="Genesis Pattern Detector — mine recurring tool chains")
    parser.add_argument("--lookback-days", type=int, default=cfg["lookback_days"])
    parser.add_argument("--min-frequency", type=int, default=cfg["min_pattern_frequency"])
    parser.add_argument("--min-chain-length", type=int, default=cfg["min_chain_length"])
    parser.add_argument("--top-k", type=int, default=cfg["top_k"])
    parser.add_argument("--adaptive", action="store_true", default=ad_cfg.get("enabled", True),
                        help="Use IQR-based adaptive frequency threshold (default: from config)")
    parser.add_argument("--no-adaptive", dest="adaptive", action="store_false",
                        help="Use static min-frequency threshold")
    parser.add_argument("--sensitivity", type=float, default=ad_cfg.get("sensitivity", 1.0),
                        help="IQR sensitivity for adaptive threshold (1.0 = Q3)")
    parser.add_argument("--store", action="store_true", help="Store detected patterns in DB")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = detect_tool_patterns(
        min_frequency=args.min_frequency,
        min_chain_length=args.min_chain_length,
        lookback_days=args.lookback_days,
        top_k=args.top_k,
        adaptive=args.adaptive,
        sensitivity=args.sensitivity,
    )

    if args.store and result.get("patterns"):
        stored = store_patterns(result["patterns"])
        result["patterns_stored"] = stored

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Pattern Detection Report ({result['detected_at']})")
        print(f"  Sessions analyzed: {result.get('sessions_analyzed', 0)}")
        print(f"  Chains extracted:  {result.get('chains_extracted', 0)}")
        print(f"  Patterns found:    {result.get('deduplicated_patterns', 0)}")
        for i, p in enumerate(result.get("patterns", []), 1):
            print(f"\n  {i}. {' → '.join(p['pattern'])}")
            print(f"     freq={p['frequency']} diversity={p['caller_diversity']} score={p['composite_score']}")


if __name__ == "__main__":
    main()
