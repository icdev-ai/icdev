#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — FORGE Academy Auto-Currency (academy_reflex).

Two-phase reflex that keeps the Academy curriculum current autonomously:

Phase 1 — Pattern → Draft:
  Query genesis_gkp for proven_pattern artifacts (confidence >= 0.7, status promoted).
  For each pattern not yet mapped to a mission, insert a draft scaffold into fa_missions
  (status='draft') for human review and activation.

Phase 2 — Staleness → Kanban:
  Run the LensStalenesssDetector Oracle lens. For every prediction with
  confidence >= CONFIDENCE_THRESHOLD, create a kanban_tasks row with
  status='suggested' and source='academy_reflex'. The Kanban scheduler
  surfaces these to reviewers.

COOLDOWN_HOURS = 6 (enforced by Genesis daemon).

Usage:
    python tools/genesis/reflexes/academy_reflex.py [--dry-run] [--json]
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

COOLDOWN_HOURS = 6
CONFIDENCE_THRESHOLD = 0.70

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    """Turn a pattern name into a mission-slug-friendly string."""
    import re
    s = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    s = re.sub(r"[\s_]+", "-", s.strip())
    return f"draft-{s[:40]}"


# ---------------------------------------------------------------------------
# Phase 1 — proven_pattern GKPs → fa_missions draft scaffolds
# ---------------------------------------------------------------------------

def _fetch_unmatched_patterns(conn: Any) -> List[Dict]:
    """Return genesis_gkp rows that are proven_pattern and not yet a mission."""
    try:
        rows = conn.execute(
            """
            SELECT id, payload, confidence, created_at
            FROM genesis_gkp
            WHERE artifact_type = 'proven_pattern'
              AND confidence >= ?
              AND promotion_status = 'promoted'
            ORDER BY confidence DESC
            LIMIT 20
            """,
            (CONFIDENCE_THRESHOLD,),
        ).fetchall()
    except Exception:
        return []

    results = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except Exception:
            payload = {}
        pattern_name = (
            payload.get("pattern_name")
            or payload.get("name")
            or payload.get("title")
            or f"Pattern {row['id'][:6]}"
        )
        slug = _slugify(pattern_name)
        # Skip if a mission already uses this slug prefix
        existing = conn.execute(
            "SELECT id FROM fa_missions WHERE slug LIKE ?", (f"%{slug[:20]}%",)
        ).fetchone()
        if existing:
            continue
        results.append({
            "gkp_id": row["id"],
            "pattern_name": pattern_name,
            "slug": slug,
            "confidence": row["confidence"],
            "payload": payload,
        })
    return results


def _insert_draft_mission(conn: Any, pattern: Dict, dry_run: bool = False) -> bool:
    """Insert a draft mission scaffold from a pattern dict. Returns True if inserted."""
    payload = pattern.get("payload", {})
    topic   = payload.get("topic") or payload.get("category") or "ai_patterns"
    desc    = payload.get("description") or payload.get("summary") or ""
    tagline = desc[:120] if desc else f"Auto-generated from Genesis pattern: {pattern['pattern_name']}"
    now     = _utcnow_iso()
    slug    = pattern["slug"]

    if dry_run:
        return True

    try:
        conn.execute(
            """INSERT OR IGNORE INTO fa_missions
               (slug, title, tagline, tier, topic, role_filter, mission_type,
                xp_reward, order_idx, difficulty, estimated_minutes,
                is_active, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                slug,
                f"[DRAFT] {pattern['pattern_name'][:80]}",
                tagline,
                2,           # Tier 2 — advanced pattern
                topic,
                "all",
                "guided",
                300,
                999,         # High order_idx so it sorts to the bottom
                "intermediate",
                45,
                0,           # is_active=0 until human promotes
                "draft",
                now,
                now,
            ),
        )
        conn.commit()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Phase 2 — Staleness Oracle lens → Kanban suggested tasks
# ---------------------------------------------------------------------------

def _run_staleness_lens() -> List[Dict]:
    """Run the LensStalenesssDetector and return predictions as dicts."""
    try:
        from apps.forge_academy.oracle.lens_staleness_detector import LensStalenesssDetector
        lens = LensStalenesssDetector()
        preds = lens.run()
        return [p.to_dict() for p in preds]
    except Exception:
        return []


def _promote_to_kanban(conn: Any, predictions: List[Dict], dry_run: bool = False) -> int:
    """Create kanban_tasks rows (status='suggested') for qualifying predictions."""
    now = _utcnow_iso()
    count = 0
    for pred in predictions:
        if pred.get("confidence", 0) < CONFIDENCE_THRESHOLD:
            continue
        task_id   = str(uuid.uuid4())
        title     = f"[Academy] {pred.get('title', 'Curriculum review')}"
        desc      = (
            f"**Oracle Prediction (confidence {pred.get('confidence', 0):.0%})**\n\n"
            f"{pred.get('description', '')}\n\n"
            f"**Recommendations:**\n"
            + "\n".join(f"- {r}" for r in (pred.get("recommendations") or []))
            + f"\n\n**Source:** academy_reflex staleness detector\n"
            f"**Severity:** {pred.get('severity', 'info')}"
        )
        if dry_run:
            count += 1
            continue
        try:
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, status, priority,
                    created_at, updated_at, dispatch_source)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    task_id, title, desc,
                    "suggested",
                    "high" if pred.get("severity") == "critical" else "medium",
                    now, now,
                    "academy_reflex",
                ),
            )
            count += 1
        except Exception:
            pass
    if not dry_run:
        try:
            conn.commit()
        except Exception:
            pass
    return count


# ---------------------------------------------------------------------------
# Main reflex entry point
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Genesis reflex entry point.

    config may contain:
      dry_run (bool)  — log actions without writing to DB
      max_drafts (int) — cap on new draft missions per run (default 5)
    """
    dry_run   = bool(config.get("dry_run", False))
    max_drafts = int(config.get("max_drafts", 5))

    if get_connection is None:
        return {"success": False, "error": "DB unavailable"}

    conn = get_connection()

    # ── Phase 1: Pattern → Draft ─────────────────────────────────────────────
    patterns     = _fetch_unmatched_patterns(conn)[:max_drafts]
    drafts_created = 0
    draft_slugs: List[str] = []
    for pat in patterns:
        if _insert_draft_mission(conn, pat, dry_run=dry_run):
            drafts_created += 1
            draft_slugs.append(pat["slug"])

    # ── Phase 2: Staleness → Kanban ──────────────────────────────────────────
    stale_preds       = _run_staleness_lens()
    kanban_created    = _promote_to_kanban(conn, stale_preds, dry_run=dry_run)

    total_metric = drafts_created + kanban_created
    return {
        "success": True,
        "metric_value": float(total_metric),
        "details": {
            "dry_run":          dry_run,
            "patterns_found":   len(patterns),
            "drafts_created":   drafts_created,
            "draft_slugs":      draft_slugs,
            "stale_predictions": len(stale_preds),
            "kanban_tasks_created": kanban_created,
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="FORGE Academy auto-currency reflex")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json",    action="store_true")
    p.add_argument("--max-drafts", type=int, default=5)
    args = p.parse_args()

    result = run(
        {"dry_run": args.dry_run, "max_drafts": args.max_drafts},
        None,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        d = result.get("details", {})
        print(f"Drafts created      : {d.get('drafts_created', 0)}")
        print(f"Stale predictions   : {d.get('stale_predictions', 0)}")
        print(f"Kanban tasks created: {d.get('kanban_tasks_created', 0)}")
        if d.get("draft_slugs"):
            print("Draft slugs:", ", ".join(d["draft_slugs"]))