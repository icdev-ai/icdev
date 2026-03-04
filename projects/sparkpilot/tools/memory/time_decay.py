#!/usr/bin/env python3
# CUI // SP-CTI
"""Time-decay scoring for memory entries (D147).

Exponential decay: decay_factor = 2^(-(age_days / half_life_days))
Air-gap safe, stdlib only.

CLI:
    python tools/memory/time_decay.py --score --entry-id 42 --json
    python tools/memory/time_decay.py --rank --query "keyword" --top-k 10 --json
"""

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "half_lives": {
        "fact": 90,
        "preference": 180,
        "event": 7,
        "insight": 30,
        "task": 14,
        "relationship": 120,
        "thinking": 3,  # D182 — reasoning traces decay rapidly
    },
    "default_half_life": 30,
    "min_decay_factor": 0.01,
    "weights": {
        "relevance": 0.60,
        "recency": 0.25,
        "importance": 0.15,
    },
    "importance_decay_resistance_threshold": 8,
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_decay_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load time-decay configuration from YAML.

    Falls back to DEFAULT_CONFIG if file is missing or yaml unavailable.
    """
    path = config_path or (BASE_DIR / "args" / "memory_config.yaml")
    try:
        import yaml  # type: ignore
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            td = data.get("time_decay", {})
            config = dict(DEFAULT_CONFIG)
            if "half_lives" in td:
                config["half_lives"] = {**config["half_lives"], **td["half_lives"]}
            for key in ("default_half_life", "min_decay_factor",
                        "importance_decay_resistance_threshold"):
                if key in td:
                    config[key] = td[key]
            if "weights" in td:
                config["weights"] = {**config["weights"], **td["weights"]}
            return config
    except (ImportError, Exception):
        pass
    return dict(DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def _parse_timestamp(ts_str: str) -> datetime:
    """Parse an ISO-ish timestamp string into a timezone-aware datetime.

    Handles multiple SQLite timestamp formats and timezone-aware ISO strings.
    """
    if ts_str is None:
        return datetime.now(timezone.utc)
    # Try fromisoformat first (handles +00:00 timezone suffix)
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core decay computation
# ---------------------------------------------------------------------------

def compute_decay_factor(
    created_at: str,
    memory_type: str = "event",
    half_lives: Optional[Dict[str, float]] = None,
    default_half_life: float = 30.0,
    min_decay_factor: float = 0.01,
    importance: int = 5,
    importance_threshold: int = 8,
    reference_time: Optional[datetime] = None,
) -> float:
    """Compute exponential time-decay factor for a single memory entry.

    Formula: decay = max(min_decay_factor, 2^(-(age_days / half_life)))
    If importance >= threshold, half_life is doubled (resistance to decay).
    """
    ref = reference_time or datetime.now(timezone.utc)
    ts = _parse_timestamp(created_at)
    age_days = max(0.0, (ref - ts).total_seconds() / 86400.0)

    hl_map = half_lives or DEFAULT_CONFIG["half_lives"]
    half_life = hl_map.get(memory_type, default_half_life)
    half_life = max(half_life, 1.0)  # prevent division by zero

    # High-importance entries resist decay
    if importance >= importance_threshold:
        half_life *= 2.0

    decay = math.pow(2.0, -(age_days / half_life))
    return max(min_decay_factor, decay)


def compute_time_aware_score(
    base_score: float,
    created_at: str,
    memory_type: str = "event",
    importance: int = 5,
    config: Optional[Dict[str, Any]] = None,
    reference_time: Optional[datetime] = None,
) -> float:
    """Compute final time-aware score combining relevance, recency, importance.

    Formula:
        final = w_relevance * base_score
              + w_recency * decay_factor
              + w_importance * (importance / 10.0)
    """
    cfg = config or DEFAULT_CONFIG
    weights = cfg.get("weights", DEFAULT_CONFIG["weights"])

    decay = compute_decay_factor(
        created_at=created_at,
        memory_type=memory_type,
        half_lives=cfg.get("half_lives"),
        default_half_life=cfg.get("default_half_life", 30.0),
        min_decay_factor=cfg.get("min_decay_factor", 0.01),
        importance=importance,
        importance_threshold=cfg.get("importance_decay_resistance_threshold", 8),
        reference_time=reference_time,
    )

    importance_norm = min(importance, 10) / 10.0

    return (
        weights.get("relevance", 0.6) * base_score
        + weights.get("recency", 0.25) * decay
        + weights.get("importance", 0.15) * importance_norm
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a DB connection with Row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Entry-level scoring
# ---------------------------------------------------------------------------

def score_entry(
    entry_id: int,
    db_path: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score a single memory entry by ID."""
    cfg = config or load_decay_config()
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT id, content, type, importance, created_at FROM memory_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()

    if row is None:
        raise ValueError(f"Memory entry {entry_id} not found")

    ref = datetime.now(timezone.utc)
    ts = _parse_timestamp(row["created_at"])
    age_days = max(0.0, (ref - ts).total_seconds() / 86400.0)

    hl_map = cfg.get("half_lives", DEFAULT_CONFIG["half_lives"])
    half_life = hl_map.get(row["type"], cfg.get("default_half_life", 30.0))

    decay = compute_decay_factor(
        created_at=row["created_at"],
        memory_type=row["type"],
        half_lives=hl_map,
        default_half_life=cfg.get("default_half_life", 30.0),
        min_decay_factor=cfg.get("min_decay_factor", 0.01),
        importance=row["importance"] or 5,
        importance_threshold=cfg.get("importance_decay_resistance_threshold", 8),
        reference_time=ref,
    )

    return {
        "classification": "CUI // SP-CTI",
        "entry_id": row["id"],
        "content": row["content"][:200],
        "type": row["type"],
        "importance": row["importance"],
        "created_at": row["created_at"],
        "age_days": round(age_days, 2),
        "half_life": half_life,
        "decay_factor": round(decay, 6),
        "importance_normalized": round((row["importance"] or 5) / 10.0, 2),
    }


# ---------------------------------------------------------------------------
# Time-decay ranked search
# ---------------------------------------------------------------------------

def rank_with_decay(
    query: str,
    top_k: int = 10,
    db_path: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run time-decay-aware search: BM25 base + time-decay reranking."""
    cfg = config or load_decay_config()
    conn = _get_connection(db_path)

    try:
        sql = ("SELECT id, content, type, importance, embedding, created_at "
               "FROM memory_entries WHERE 1=1")
        params: list = []
        if user_id:
            sql += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        if tenant_id:
            sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
            params.append(tenant_id)
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    conn.close()

    if not rows:
        return []

    # Build entries list matching hybrid_search format
    entries = [(r["id"], r["content"], r["type"], r["importance"],
                r["embedding"], r["created_at"]) for r in rows]

    # Compute BM25 scores
    try:
        sys.path.insert(0, str(BASE_DIR))
        from tools.memory.hybrid_search import bm25_search
        bm25_scores = bm25_search(query, entries)
    except (ImportError, Exception):
        # Fallback: simple term frequency
        query_terms = query.lower().split()
        bm25_scores = []
        for e in entries:
            doc_lower = (e[1] or "").lower()
            score = sum(doc_lower.count(t) for t in query_terms)
            bm25_scores.append(float(score))
        max_s = max(bm25_scores) if bm25_scores and max(bm25_scores) > 0 else 1.0
        bm25_scores = [s / max_s for s in bm25_scores]

    ref = datetime.now(timezone.utc)
    results = []
    for i, entry in enumerate(entries):
        id_, content, type_, importance, _, created_at = entry
        base_score = bm25_scores[i] if i < len(bm25_scores) else 0.0
        imp = importance or 5

        time_score = compute_time_aware_score(
            base_score=base_score,
            created_at=created_at or "",
            memory_type=type_ or "event",
            importance=imp,
            config=cfg,
            reference_time=ref,
        )

        ts = _parse_timestamp(created_at)
        age_days = max(0.0, (ref - ts).total_seconds() / 86400.0)

        decay = compute_decay_factor(
            created_at=created_at or "",
            memory_type=type_ or "event",
            half_lives=cfg.get("half_lives"),
            default_half_life=cfg.get("default_half_life", 30.0),
            min_decay_factor=cfg.get("min_decay_factor", 0.01),
            importance=imp,
            importance_threshold=cfg.get("importance_decay_resistance_threshold", 8),
            reference_time=ref,
        )

        results.append({
            "entry_id": id_,
            "content": content[:200] if content else "",
            "type": type_,
            "importance": imp,
            "created_at": created_at,
            "base_score": round(base_score, 4),
            "decay_factor": round(decay, 4),
            "time_aware_score": round(time_score, 4),
            "age_days": round(age_days, 2),
        })

    results.sort(key=lambda x: x["time_aware_score"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Time-decay scoring for memory entries (D147)"
    )
    parser.add_argument("--score", action="store_true",
                        help="Score a single entry")
    parser.add_argument("--entry-id", type=int,
                        help="Memory entry ID (for --score)")
    parser.add_argument("--rank", action="store_true",
                        help="Time-decay ranked search")
    parser.add_argument("--query", type=str,
                        help="Search query (for --rank)")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Max results (default 10)")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")
    parser.add_argument("--user-id", type=str,
                        help="Filter by user ID (D180)")
    parser.add_argument("--tenant-id", type=str,
                        help="Filter by tenant ID (D180)")
    parser.add_argument("--db-path", type=Path,
                        help="Override database path")
    parser.add_argument("--config", type=Path,
                        help="Override config path")
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    cfg = load_decay_config(args.config)

    if args.score:
        if not args.entry_id:
            print("Error: --entry-id required with --score", file=sys.stderr)
            sys.exit(1)
        result = score_entry(args.entry_id, db_path=db, config=cfg)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Entry #{result['entry_id']} ({result['type']}, "
                  f"importance={result['importance']})")
            print(f"  Age: {result['age_days']} days | "
                  f"Half-life: {result['half_life']}d | "
                  f"Decay: {result['decay_factor']:.4f}")
            print(f"  Content: {result['content']}")

    elif args.rank:
        if not args.query:
            print("Error: --query required with --rank", file=sys.stderr)
            sys.exit(1)
        results = rank_with_decay(args.query, top_k=args.top_k,
                                  db_path=db, config=cfg,
                                  user_id=args.user_id, tenant_id=args.tenant_id)
        if args.json:
            print(json.dumps({
                "classification": "CUI // SP-CTI",
                "query": args.query,
                "top_k": args.top_k,
                "results": results,
            }, indent=2))
        else:
            if not results:
                print("No memory entries found.")
                return
            for r in results:
                print(f"[#{r['entry_id']}] score={r['time_aware_score']:.3f} "
                      f"(base={r['base_score']:.3f}, decay={r['decay_factor']:.3f}) "
                      f"| {r['type']}, imp={r['importance']} "
                      f"| {r['age_days']}d ago")
                print(f"  {r['content']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
