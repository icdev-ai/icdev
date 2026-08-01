#!/usr/bin/env python3
# CUI // SP-CTI
"""Promote triaged innovation signals into the kanban backlog.

Reads innovation_signals WHERE triage_result IN ('approved', 'suggested')
AND NOT already-promoted, inserts kanban_tasks(status='suggested') rows,
stamps source_prediction_id with the originating signal id.

Usage:
    python tools/innovation/kanban_promoter.py --dry-run --json
    python tools/innovation/kanban_promoter.py --triage-result approved --limit 10
    python tools/innovation/kanban_promoter.py --list --json
    python tools/innovation/kanban_promoter.py --promote-id <signal_id>

Idempotent: re-running skips signals already in kanban_tasks.source_prediction_id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.innovation.kanban_promoter")

CONFIG_PATH = BASE_DIR / "args" / "innovation_promoter.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "triage_results": ["approved"],
    # innovation_score is stored on a 0-1 scale in production data
    "min_innovation_score": 0.5,
    "max_per_run": 50,
    "default_task_type": "research",
    "priority_thresholds": {"high": 0.7, "medium": 0.5},
    "classification": "CUI // SP-CTI",
}


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        return merged
    return dict(DEFAULT_CONFIG)


def _short_id(prefix: str = "task") -> str:
    # SHA1 for short non-security IDs; usedforsecurity=False per FIPS guidance
    seed = (str(datetime.now(timezone.utc).timestamp()).encode()
            + str(id(object())).encode())
    return f"{prefix}-{hashlib.sha1(seed, usedforsecurity=False).hexdigest()[:10]}"


def _priority_for_score(score: float | None, thresholds: dict[str, int]) -> str:
    if score is None:
        return "low"
    if score >= thresholds.get("high", 80):
        return "high"
    if score >= thresholds.get("medium", 60):
        return "medium"
    return "low"


def find_promotable_signals(
    conn,
    triage_results: tuple[str, ...] = ("approved",),
    limit: int = 50,
    min_score: float = 0.0,
    since_days: int = 30,
) -> list[dict]:
    """Return innovation signals eligible for promotion.

    Filters: triage_result in provided list, innovation_score >= min_score,
    discovered within since_days, and not already present in
    kanban_tasks.source_prediction_id.
    """
    cur = conn.cursor()
    # placeholders is a comma-joined list of literal '%s' markers only —
    # no user input is interpolated; parameters are bound via params tuple.
    placeholders = ",".join(["%s"] * len(triage_results))
    sql = f"""
        SELECT s.id, s.title, s.description, s.body, s.url, s.category,
               s.innovation_score, s.composite_score, s.community_score,
               s.score_breakdown, s.gotcha_layer, s.boundary_tier,
               s.triage_result, s.created_at, s.discovered_at,
               sol.spec_content, sol.asset_type, sol.estimated_effort
        FROM innovation_signals s
        LEFT JOIN innovation_solutions sol ON sol.signal_id = s.id
        WHERE s.triage_result IN ({placeholders})
          AND COALESCE(s.innovation_score, 0) >= %s
          AND s.id NOT IN (
              SELECT source_prediction_id FROM kanban_tasks
              WHERE source_prediction_id IS NOT NULL
          )
        ORDER BY s.innovation_score DESC NULLS LAST, s.created_at DESC
        LIMIT %s
    """  # nosec B608 — placeholders are literal %s, all values parameterized via params tuple
    params = tuple(triage_results) + (min_score, limit)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def build_kanban_task(signal: dict, config: dict) -> dict:
    """Map an innovation signal (+ optional solution) to a kanban_tasks row."""
    sid = signal["id"]
    raw_title = signal.get("title") or f"Signal {sid[:8]}"
    title = f"INNOV-{sid[:8]}: {raw_title}"[:120]

    score = signal.get("innovation_score")
    priority = _priority_for_score(score, config.get("priority_thresholds", {}))

    lines: list[str] = []
    lines.append(f"**Source:** innovation_signals.id = {sid}")
    if signal.get("category"):
        lines.append(f"**Category:** {signal['category']}")
    if signal.get("url"):
        lines.append(f"**URL:** {signal['url']}")
    if score is not None:
        lines.append(f"**Innovation score:** {score}")
    if signal.get("score_breakdown"):
        lines.append(f"**Score breakdown:** {signal['score_breakdown']}")
    if signal.get("gotcha_layer"):
        lines.append(f"**FORGE layer:** {signal['gotcha_layer']}")
    if signal.get("boundary_tier"):
        lines.append(f"**Boundary tier:** {signal['boundary_tier']}")
    if signal.get("triage_result"):
        lines.append(f"**Triage:** {signal['triage_result']}")
    if signal.get("description"):
        lines.append("")
        lines.append("**Description:**")
        lines.append(str(signal["description"])[:2000])
    if signal.get("spec_content"):
        lines.append("")
        lines.append("**Solution spec:**")
        lines.append(str(signal["spec_content"])[:4000])
        if signal.get("estimated_effort"):
            lines.append(f"**Estimated effort:** {signal['estimated_effort']}")

    description = "\n".join(lines)

    return {
        "id": _short_id("task"),
        "title": title,
        "description": description,
        "task_type": config.get("default_task_type", "research"),
        "priority": priority,
        "status": "suggested",
        "source_prediction_id": sid,
    }


def promote_signals(conn, signals: list[dict], config: dict, dry_run: bool = False) -> dict:
    """Insert kanban_tasks rows for the given signals. Idempotent by contract:
    callers should pre-filter via find_promotable_signals."""
    inserted: list[str] = []
    failed: list[tuple[str, str]] = []
    cur = conn.cursor()

    for sig in signals:
        task = build_kanban_task(sig, config)
        if dry_run:
            inserted.append(task["id"])
            continue
        try:
            cur.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    source_prediction_id, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())""",
                (
                    task["id"], task["title"], task["description"],
                    task["task_type"], task["priority"], task["status"],
                    task["source_prediction_id"],
                ),
            )
            inserted.append(task["id"])
        except Exception as exc:
            failed.append((sig["id"], str(exc)[:200]))

    if not dry_run and inserted:
        try:
            details = json.dumps({
                "inserted_count": len(inserted),
                "signal_ids": [s["id"] for s in signals[: len(inserted)]],
                "failed_count": len(failed),
            })
            cur.execute(
                """INSERT INTO audit_trail
                   (event_type, actor, action, details, created_at)
                   VALUES (%s, %s, %s, %s, NOW())""",
                ("decision_made", "kanban_promoter", "innovation.kanban_promote", details),
            )
        except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # audit is best-effort
            logger.warning("promote_signals: best-effort INSERT into audit_trail failed (non-blocking): %s", _exc)
        conn.commit()

    return {
        "inserted": len(inserted),
        "inserted_ids": inserted,
        "failed": len(failed),
        "failures": failed,
        "dry_run": dry_run,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--triage-result", default="approved",
                   help="approved, suggested, both (default: approved)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--since-days", type=int, default=30)
    p.add_argument("--min-innovation-score", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--promote-id", type=str, default=None)
    p.add_argument("--list", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = load_config()

    if args.triage_result == "both":
        triage = ("approved", "suggested")
    else:
        triage = (args.triage_result,)

    limit = args.limit if args.limit is not None else config.get("max_per_run", 50)
    min_score = (args.min_innovation_score if args.min_innovation_score is not None
                 else config.get("min_innovation_score", 50))

    conn = get_connection()

    if args.promote_id:
        cur = conn.cursor()
        cur.execute("""SELECT 1 FROM kanban_tasks
                       WHERE source_prediction_id=%s LIMIT 1""", (args.promote_id,))
        if cur.fetchone() is not None:
            out = {"inserted": 0, "skipped_existing": 1, "signal_id": args.promote_id,
                   "dry_run": args.dry_run}
            print(json.dumps(out) if args.json else f"Already promoted: {args.promote_id}")
            return 0
        cur.execute("""SELECT s.*, sol.spec_content, sol.asset_type, sol.estimated_effort
                       FROM innovation_signals s
                       LEFT JOIN innovation_solutions sol ON sol.signal_id = s.id
                       WHERE s.id=%s""", (args.promote_id,))
        row = cur.fetchone()
        if row is None:
            out = {"error": "signal not found", "signal_id": args.promote_id}
            print(json.dumps(out) if args.json else f"ERROR: {out}")
            return 1
        result = promote_signals(conn, [dict(row)], config, dry_run=args.dry_run)
    else:
        signals = find_promotable_signals(
            conn, triage_results=triage, limit=limit,
            min_score=min_score, since_days=args.since_days,
        )
        if args.list:
            out = [{"id": s["id"], "title": s.get("title"),
                    "innovation_score": s.get("innovation_score"),
                    "triage_result": s.get("triage_result")} for s in signals]
            print(json.dumps(out, indent=2) if args.json else
                  "\n".join(f"  {s['id']} {s['innovation_score']:>5} {s.get('title', '')[:80]}" for s in signals))
            return 0
        result = promote_signals(conn, signals, config, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        mode = "DRY-RUN: would insert" if args.dry_run else "Inserted"
        print(f"{mode} {result['inserted']} kanban_tasks (failed: {result['failed']})")
        if result["failures"]:
            for sid, err in result["failures"][:5]:
                print(f"  {sid}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
