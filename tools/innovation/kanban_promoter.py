#!/usr/bin/env python3
# CUI // SP-CTI
"""Promote benchmark findings into the kanban board as suggested cards.

Reads ``innovation_signals`` produced by the external-benchmark scouting
path, keeps only findings that benchmark a subsystem the benchmark map
judged deficient, rate-limits them, and materializes the survivors as
``kanban_tasks`` rows with ``status='suggested'``.

Three properties are load-bearing and are what the tests pin:

* **Gap-gated.** A finding whose subsystem verdict is ``ahead`` or
  ``parity`` is intelligence, not work. It is counted and skipped.
  Verdicts come from ``args/innovation_promoter.yaml``, transcribed from
  ``docs/research/external-benchmark-map.md``.
* **Rate-limited.** ``max_per_run`` and ``max_per_subsystem`` bound every
  invocation. When a cap truncates, that is logged at WARNING and reported
  in ``truncated`` — a cap that silently drops findings is indistinguishable
  from a promoter that found nothing.
* **Human-confirmed.** Cards are always written ``status='suggested'``.
  Nothing here can write ``backlog``; moving a card to backlog is an
  operator action on the board. This mirrors
  ``tools/awareness/suggested_card_writer.py`` rather than inventing a
  second promotion path.

Writes go through ``tools.kanban.task_factory.create_tasks`` with a stable
``idempotency_key`` derived from the signal id, so a second run over the
same findings creates nothing.

Usage:
    python tools/innovation/kanban_promoter.py --dry-run --json
    python tools/innovation/kanban_promoter.py --promote --json
    python tools/innovation/kanban_promoter.py --list --json
    python tools/innovation/kanban_promoter.py --promote-id <signal_id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.innovation.kanban_promoter")

CONFIG_PATH = BASE_DIR / "args" / "innovation_promoter.yaml"

# The only status this module may write. A promoter that can reach 'backlog'
# is a promoter that can dispatch an agent without a human ever looking.
SUGGESTED_STATUS = "suggested"

DEFAULT_CONFIG: dict[str, Any] = {
    "source_types": ["external_repo_scouting", "external_framework_analysis"],
    "triage_results": ["approved"],
    # innovation_score is stored on a 0-1 scale in production data
    "min_innovation_score": 0.5,
    "max_per_run": 5,
    "max_per_subsystem": 2,
    "default_task_type": "research",
    "gap_verdicts": ["gap", "parity_with_named_gaps"],
    "benchmark_subsystems": {},
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


# ---------------------------------------------------------------------------
# Stable identity — the whole idempotency story rests on these two
# ---------------------------------------------------------------------------


def stable_task_id(signal_id: str) -> str:
    """Deterministic task id for a signal.

    Derived from the signal id, never from the clock: a timestamp-seeded id
    makes every re-run look like a brand-new task and defeats the dedup in
    ``task_factory.create_tasks``.
    """
    # SHA256 for a short non-security id; usedforsecurity=False per FIPS guidance.
    digest = hashlib.sha256(signal_id.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"task-innov-{digest[:10]}"


def idempotency_key(signal_id: str) -> str:
    """Stable key so a retried or re-scheduled run is a no-op."""
    return f"innovation-promoter:{signal_id}"


def _priority_for_score(score: float | None, thresholds: dict[str, float]) -> str:
    if score is None:
        return "low"
    if score >= thresholds.get("high", 0.7):
        return "high"
    if score >= thresholds.get("medium", 0.5):
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Benchmark verdict gate
# ---------------------------------------------------------------------------


def _category_index(config: dict) -> dict[str, str]:
    """Build {category -> subsystem_key} from the configured subsystems."""
    index: dict[str, str] = {}
    subsystems = config.get("benchmark_subsystems") or {}
    for key, spec in subsystems.items():
        if not isinstance(spec, dict):
            continue
        for cat in spec.get("categories") or []:
            index[str(cat).strip().lower()] = key
    return index


def resolve_subsystem(signal: dict, config: dict) -> str | None:
    """Map a signal onto a benchmark subsystem, or None if it maps to none.

    Precedence:
      1. ``metadata.subsystem`` — the routing tag the scout watchlist
         attaches to a target (``context/genesis/competitors.yaml``).
         Authoritative when present.
      2. the signal ``category``, via the ``categories`` list on each
         configured subsystem.

    An unmapped signal is not promoted. Guessing a subsystem for an untagged
    finding is how a rate-limited promoter turns back into a flood.
    """
    raw_meta = signal.get("metadata")
    if isinstance(raw_meta, str) and raw_meta.strip():
        try:
            raw_meta = json.loads(raw_meta)
        except (ValueError, TypeError):
            raw_meta = None
    if isinstance(raw_meta, dict):
        tagged = raw_meta.get("subsystem")
        if tagged and str(tagged).strip():
            key = str(tagged).strip()
            # Only honour a tag we actually have a verdict for.
            if key in (config.get("benchmark_subsystems") or {}):
                return key
            logger.warning(
                "resolve_subsystem: signal %s carries unknown subsystem tag %r "
                "— falling back to category",
                signal.get("id"), key,
            )

    category = (signal.get("category") or "").strip().lower()
    if not category:
        return None
    return _category_index(config).get(category)


def verdict_for_subsystem(subsystem: str | None, config: dict) -> str | None:
    if not subsystem:
        return None
    spec = (config.get("benchmark_subsystems") or {}).get(subsystem)
    if not isinstance(spec, dict):
        return None
    verdict = spec.get("verdict")
    return str(verdict).strip().lower() if verdict else None


def is_gap_verdict(verdict: str | None, config: dict) -> bool:
    """True when this verdict means ICDEV is behind and work is warranted."""
    if not verdict:
        return False
    gap_verdicts = {
        str(v).strip().lower() for v in (config.get("gap_verdicts") or [])
    }
    return verdict in gap_verdicts


def classify_signals(signals: list[dict], config: dict) -> dict[str, Any]:
    """Split signals into gap-verdict candidates and explained skips."""
    eligible: list[dict] = []
    skipped_unmapped: list[str] = []
    skipped_not_a_gap: list[dict] = []

    for sig in signals:
        subsystem = resolve_subsystem(sig, config)
        if subsystem is None:
            skipped_unmapped.append(sig.get("id", "?"))
            continue
        verdict = verdict_for_subsystem(subsystem, config)
        if not is_gap_verdict(verdict, config):
            skipped_not_a_gap.append(
                {"id": sig.get("id"), "subsystem": subsystem, "verdict": verdict}
            )
            continue
        enriched = dict(sig)
        enriched["_subsystem"] = subsystem
        enriched["_verdict"] = verdict
        eligible.append(enriched)

    if skipped_unmapped:
        logger.info(
            "gap gate: %d signal(s) map to no benchmark subsystem — not promoted",
            len(skipped_unmapped),
        )
    if skipped_not_a_gap:
        logger.info(
            "gap gate: %d signal(s) benchmark a subsystem ICDEV is not behind on "
            "— recorded, not promoted",
            len(skipped_not_a_gap),
        )

    return {
        "eligible": eligible,
        "skipped_unmapped": skipped_unmapped,
        "skipped_not_a_gap": skipped_not_a_gap,
    }


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def apply_caps(signals: list[dict], config: dict) -> dict[str, Any]:
    """Enforce max_per_subsystem then max_per_run.

    Returns the kept signals plus a ``truncated`` report. Every dropped
    signal is accounted for by id — the cap must be auditable, not a
    silent ``[:n]`` slice.
    """
    max_per_run = int(config.get("max_per_run", 5))
    max_per_subsystem = int(config.get("max_per_subsystem", 2))

    # Highest-scoring findings survive a cap.
    ordered = sorted(
        signals,
        key=lambda s: (s.get("innovation_score") or 0.0),
        reverse=True,
    )

    kept: list[dict] = []
    per_subsystem: dict[str, int] = defaultdict(int)
    dropped_by_subsystem_cap: list[dict] = []
    dropped_by_run_cap: list[dict] = []

    for sig in ordered:
        subsystem = sig.get("_subsystem") or "unmapped"
        if per_subsystem[subsystem] >= max_per_subsystem:
            dropped_by_subsystem_cap.append(
                {"id": sig.get("id"), "subsystem": subsystem}
            )
            continue
        if len(kept) >= max_per_run:
            dropped_by_run_cap.append({"id": sig.get("id"), "subsystem": subsystem})
            continue
        per_subsystem[subsystem] += 1
        kept.append(sig)

    truncated = bool(dropped_by_subsystem_cap or dropped_by_run_cap)

    if dropped_by_subsystem_cap:
        by_sub: dict[str, int] = defaultdict(int)
        for d in dropped_by_subsystem_cap:
            by_sub[d["subsystem"]] += 1
        logger.warning(
            "CAP TRUNCATED: per-subsystem cap (%d) held back %d finding(s): %s. "
            "They remain eligible on the next run.",
            max_per_subsystem, len(dropped_by_subsystem_cap), dict(by_sub),
        )
    if dropped_by_run_cap:
        logger.warning(
            "CAP TRUNCATED: per-run cap (%d) held back %d finding(s): %s. "
            "They remain eligible on the next run.",
            max_per_run, len(dropped_by_run_cap),
            [d["id"] for d in dropped_by_run_cap],
        )

    return {
        "kept": kept,
        "truncated": truncated,
        "max_per_run": max_per_run,
        "max_per_subsystem": max_per_subsystem,
        "dropped_by_subsystem_cap": dropped_by_subsystem_cap,
        "dropped_by_run_cap": dropped_by_run_cap,
        "per_subsystem_counts": dict(per_subsystem),
    }


# ---------------------------------------------------------------------------
# Task construction
# ---------------------------------------------------------------------------


def find_promotable_signals(
    conn,
    triage_results: tuple[str, ...] = ("approved",),
    limit: int = 200,
    min_score: float = 0.0,
    source_types: tuple[str, ...] = ("external_repo_scouting", "external_framework_analysis"),
) -> list[dict]:
    """Return benchmark signals that clear the coarse SQL filters.

    Deliberately does NOT apply the run cap: the gap gate runs first, so
    capping here would let non-gap findings consume the budget. ``limit``
    is a query-size guard, not the rate limit.
    """
    cur = conn.cursor()
    # Both placeholder lists are literal '%s' markers sized to the tuples;
    # no user input is interpolated — every value is bound via params.
    triage_ph = ",".join(["%s"] * len(triage_results))
    source_ph = ",".join(["%s"] * len(source_types))
    sql = f"""
        SELECT s.id, s.title, s.description, s.body, s.url, s.category,
               s.source, s.source_type, s.metadata,
               s.innovation_score, s.composite_score, s.community_score,
               s.score_breakdown, s.gotcha_layer, s.boundary_tier,
               s.triage_result, s.created_at, s.discovered_at,
               sol.spec_content, sol.asset_type, sol.estimated_effort
        FROM innovation_signals s
        LEFT JOIN innovation_solutions sol ON sol.signal_id = s.id
        WHERE s.triage_result IN ({triage_ph})
          AND s.source_type IN ({source_ph})
          AND COALESCE(s.innovation_score, 0) >= %s
          AND s.id NOT IN (
              SELECT source_prediction_id FROM kanban_tasks
              WHERE source_prediction_id IS NOT NULL
          )
        ORDER BY s.innovation_score DESC NULLS LAST, s.created_at DESC
        LIMIT %s
    """  # nosec B608 — placeholders are literal %s, all values parameterized via params
    params = tuple(triage_results) + tuple(source_types) + (min_score, limit)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def build_kanban_task(signal: dict, config: dict) -> dict:
    """Map a benchmark finding to a kanban_tasks spec for task_factory."""
    sid = signal["id"]
    raw_title = signal.get("title") or f"Signal {sid[:8]}"
    subsystem = signal.get("_subsystem")
    verdict = signal.get("_verdict")

    title = f"INNOV-{sid[:8]}: {raw_title}"[:120]

    score = signal.get("innovation_score")
    priority = _priority_for_score(score, config.get("priority_thresholds", {}))

    spec = (config.get("benchmark_subsystems") or {}).get(subsystem) or {}

    lines: list[str] = []
    lines.append(f"**Source:** innovation_signals.id = {sid}")
    if subsystem:
        lines.append(
            f"**Benchmark subsystem:** {subsystem} "
            f"(map section {spec.get('section', '?')}) — verdict **{verdict}**"
        )
    if spec.get("benchmarked_against"):
        lines.append(f"**Benchmarked against:** {spec['benchmarked_against']}")
    if spec.get("icdev_surface"):
        lines.append(f"**ICDEV surface:** {spec['icdev_surface']}")
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

    lines.extend([
        "",
        "**This card is a suggestion, not scheduled work.** It was created by",
        "tools/innovation/kanban_promoter.py from an external benchmark finding.",
        "Review it and move it to backlog if it is worth building; dismiss it",
        "otherwise. Reference: docs/research/external-benchmark-map.md.",
    ])

    return {
        "id": stable_task_id(sid),
        "title": title,
        "description": "\n".join(lines),
        "task_type": config.get("default_task_type", "research"),
        "priority": priority,
        "status": SUGGESTED_STATUS,
        "source_prediction_id": sid,
        "idempotency_key": idempotency_key(sid),
        "dispatch_source": "innovation_promoter",
        "acceptance_criteria": (
            f"Adapt or explicitly reject the benchmark finding for subsystem "
            f"{subsystem or 'unknown'}; record the decision."
        ),
    }


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def _write_audit(signal_ids: list[str], created: list[str], truncation: dict) -> None:
    """Best-effort append-only audit of a promotion run."""
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001 - audit must never block promotion
        logger.warning("promote: audit connection failed (non-blocking): %s", exc)
        return
    try:
        details = json.dumps({
            "created_count": len(created),
            "created_task_ids": created,
            "signal_ids": signal_ids,
            "truncated": truncation.get("truncated", False),
            "dropped_by_run_cap": len(truncation.get("dropped_by_run_cap", [])),
            "dropped_by_subsystem_cap": len(
                truncation.get("dropped_by_subsystem_cap", [])
            ),
            "status": SUGGESTED_STATUS,
            "promoted_at": datetime.now(timezone.utc).isoformat(),
        })
        conn.execute(
            """INSERT INTO audit_trail
               (event_type, actor, action, details, created_at)
               VALUES (%s, %s, %s, %s, NOW())""",
            ("decision_made", "kanban_promoter", "innovation.kanban_promote", details),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("promote: audit_trail INSERT failed (non-blocking): %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def promote_signals(signals: list[dict], config: dict, dry_run: bool = False) -> dict:
    """Create suggested kanban cards for already-gated, already-capped signals.

    Writes via ``task_factory.create_tasks`` — never a raw INSERT — so the
    id and idempotency_key dedup applies and a re-run is a no-op.
    """
    specs = [build_kanban_task(sig, config) for sig in signals]

    # Belt and braces: task_factory honours whatever status it is handed, so
    # assert here rather than trusting every future caller and config edit.
    for spec in specs:
        if spec["status"] != SUGGESTED_STATUS:
            raise ValueError(
                f"kanban_promoter may only create '{SUGGESTED_STATUS}' cards, "
                f"got {spec['status']!r} — promotion to backlog is a human action"
            )

    if dry_run:
        return {
            "created": 0,
            "would_create": len(specs),
            "task_ids": [s["id"] for s in specs],
            "titles": [s["title"] for s in specs],
            "status": SUGGESTED_STATUS,
            "dry_run": True,
        }

    if not specs:
        return {
            "created": 0,
            "task_ids": [],
            "skipped_existing": 0,
            "status": SUGGESTED_STATUS,
            "dry_run": False,
        }

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(specs)
    skipped = len(specs) - len(created)
    if skipped:
        logger.info(
            "promote: %d finding(s) already had a card (idempotency_key hit) — skipped",
            skipped,
        )

    return {
        "created": len(created),
        "task_ids": created,
        "skipped_existing": skipped,
        "status": SUGGESTED_STATUS,
        "dry_run": False,
    }


def run_promotion(
    config: dict | None = None,
    dry_run: bool = True,
    query_limit: int = 200,
) -> dict:
    """Full pipeline: query -> gap gate -> caps -> suggested cards.

    ``dry_run`` defaults to True. A promoter whose default is to write is a
    promoter that writes by accident.
    """
    config = config or load_config()

    triage = tuple(config.get("triage_results") or ("approved",))
    sources = tuple(config.get("source_types") or ("external_repo_scouting",))
    min_score = float(config.get("min_innovation_score", 0.5))

    conn = get_connection()
    try:
        candidates = find_promotable_signals(
            conn,
            triage_results=triage,
            limit=query_limit,
            min_score=min_score,
            source_types=sources,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    gated = classify_signals(candidates, config)
    capped = apply_caps(gated["eligible"], config)
    written = promote_signals(capped["kept"], config, dry_run=dry_run)

    if not dry_run and written.get("created"):
        _write_audit(
            [s["id"] for s in capped["kept"]],
            written.get("task_ids", []),
            capped,
        )

    logger.info(
        "promote: %d candidate(s) -> %d gap-verdict -> %d after caps -> %d card(s) "
        "(dry_run=%s, truncated=%s)",
        len(candidates), len(gated["eligible"]), len(capped["kept"]),
        written.get("created", written.get("would_create", 0)),
        dry_run, capped["truncated"],
    )

    return {
        "candidates": len(candidates),
        "gap_verdict_eligible": len(gated["eligible"]),
        "skipped_unmapped": len(gated["skipped_unmapped"]),
        "skipped_not_a_gap": gated["skipped_not_a_gap"],
        "after_caps": len(capped["kept"]),
        "truncated": capped["truncated"],
        "max_per_run": capped["max_per_run"],
        "max_per_subsystem": capped["max_per_subsystem"],
        "dropped_by_run_cap": capped["dropped_by_run_cap"],
        "dropped_by_subsystem_cap": capped["dropped_by_subsystem_cap"],
        "per_subsystem_counts": capped["per_subsystem_counts"],
        **written,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--promote", action="store_true",
                   help="Write suggested cards (default is a dry run)")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview without writing (default)")
    p.add_argument("--limit", type=int, default=200,
                   help="Query-size guard, NOT the rate limit (see max_per_run)")
    p.add_argument("--min-innovation-score", type=float, default=None)
    p.add_argument("--max-per-run", type=int, default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--promote-id", type=str, default=None,
                   help="Promote one signal by id (still gap-gated)")
    p.add_argument("--list", action="store_true",
                   help="List candidates with their subsystem and verdict")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = load_config()

    if args.min_innovation_score is not None:
        config["min_innovation_score"] = args.min_innovation_score
    if args.max_per_run is not None:
        config["max_per_run"] = args.max_per_run

    # Writing requires --promote. Absent it, this is a preview.
    dry_run = not args.promote

    if args.promote_id:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM kanban_tasks WHERE source_prediction_id=%s LIMIT 1",
                (args.promote_id,),
            )
            if cur.fetchone() is not None:
                out = {"created": 0, "skipped_existing": 1,
                       "signal_id": args.promote_id, "dry_run": dry_run}
                print(json.dumps(out) if args.json
                      else f"Already promoted: {args.promote_id}")
                return 0
            cur.execute(
                """SELECT s.*, sol.spec_content, sol.asset_type, sol.estimated_effort
                   FROM innovation_signals s
                   LEFT JOIN innovation_solutions sol ON sol.signal_id = s.id
                   WHERE s.id=%s""",
                (args.promote_id,),
            )
            row = cur.fetchone()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if row is None:
            out = {"error": "signal not found", "signal_id": args.promote_id}
            print(json.dumps(out) if args.json else f"ERROR: {out}")
            return 1

        gated = classify_signals([dict(row)], config)
        if not gated["eligible"]:
            out = {
                "created": 0,
                "signal_id": args.promote_id,
                "reason": "no gap verdict — subsystem unmapped or not a gap",
                "skipped_not_a_gap": gated["skipped_not_a_gap"],
                "skipped_unmapped": gated["skipped_unmapped"],
            }
            print(json.dumps(out, indent=2) if args.json
                  else f"Not promoted (no gap verdict): {args.promote_id}")
            return 0
        result = promote_signals(gated["eligible"], config, dry_run=dry_run)
        if not dry_run and result.get("created"):
            _write_audit([args.promote_id], result.get("task_ids", []), {})
    elif args.list:
        conn = get_connection()
        try:
            candidates = find_promotable_signals(
                conn,
                triage_results=tuple(config.get("triage_results") or ("approved",)),
                limit=args.limit,
                min_score=float(config.get("min_innovation_score", 0.5)),
                source_types=tuple(config.get("source_types") or ()),
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass
        listing = []
        for sig in candidates:
            subsystem = resolve_subsystem(sig, config)
            verdict = verdict_for_subsystem(subsystem, config)
            listing.append({
                "id": sig["id"],
                "title": sig.get("title"),
                "innovation_score": sig.get("innovation_score"),
                "category": sig.get("category"),
                "subsystem": subsystem,
                "verdict": verdict,
                "gap": is_gap_verdict(verdict, config),
            })
        if args.json:
            print(json.dumps(listing, indent=2))
        else:
            for item in listing:
                flag = "GAP " if item["gap"] else "    "
                print(f"  {flag}{item['id']}  {item['subsystem'] or '-':<22}"
                      f"{str(item['verdict'] or '-'):<24}{str(item['title'])[:60]}")
        return 0
    else:
        result = run_promotion(config=config, dry_run=dry_run, query_limit=args.limit)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("dry_run"):
            print(f"DRY-RUN: would create {result.get('would_create', 0)} "
                  f"suggested card(s)")
        else:
            print(f"Created {result.get('created', 0)} suggested card(s) "
                  f"(skipped existing: {result.get('skipped_existing', 0)})")
        if result.get("truncated"):
            print(f"  CAP TRUNCATED — per-run={result.get('max_per_run')}, "
                  f"per-subsystem={result.get('max_per_subsystem')}; "
                  f"{len(result.get('dropped_by_run_cap', []))} + "
                  f"{len(result.get('dropped_by_subsystem_cap', []))} held for next run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
