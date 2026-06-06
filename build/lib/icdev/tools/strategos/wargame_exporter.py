#!/usr/bin/env python3
# CUI // SP-CTI
"""Wargame Exporter — produces intelligence briefs from completed wargame runs.

Usage:
    from tools.strategos.wargame_exporter import export_to_brief
    result = export_to_brief("wargame-uuid")
    # {"brief_id": "...", "title": "Wargame Export — ..."}

CLI:
    python tools/strategos/wargame_exporter.py --wargame-id <id> [--json]
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from tools.db.storage import get_connection, is_pg
from tools.strategos.ooda import compute_tempo

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_CLASSIFICATION = "CUI // SP-CTI"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _load_wargame(conn, wargame_id: str) -> dict[str, Any]:
    ph = "%s" if is_pg() else "?"
    row = conn.execute(
        f"SELECT id, name, scenario, state, blue_force, red_force, "  # nosec B608
        f"blue_strength, red_strength, outcome "
        f"FROM sg_wargames WHERE id = {ph}",
        (wargame_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Wargame not found: {wargame_id}")
    keys = ("id", "name", "scenario", "state", "blue_force", "red_force",
            "blue_strength", "red_strength", "outcome")
    return dict(zip(keys, row))


def _load_latest_turn(conn, wargame_id: str) -> dict[str, Any] | None:
    ph = "%s" if is_pg() else "?"
    row = conn.execute(
        f"SELECT turn_number, blue_losses, red_losses, blue_remaining, "  # nosec B608
        f"red_remaining, tempo_delta, notes "
        f"FROM sg_wargame_turns WHERE wargame_id = {ph} "
        f"ORDER BY turn_number DESC LIMIT 1",
        (wargame_id,),
    ).fetchone()
    if row is None:
        return None
    keys = ("turn_number", "blue_losses", "red_losses",
            "blue_remaining", "red_remaining", "tempo_delta", "notes")
    return dict(zip(keys, row))


def _load_top_signals(conn, limit: int = 10) -> list[dict[str, Any]]:
    ph = "%s" if is_pg() else "?"
    rows = conn.execute(
        f"SELECT id, composite_score, temporal_recency_score, run_at "  # nosec B608
        f"FROM sg_prioritized_signals "
        f"ORDER BY created_at DESC LIMIT {ph}",
        (limit,),
    ).fetchall()
    keys = ("id", "composite_score", "temporal_recency_score", "run_at")
    return [dict(zip(keys, r)) for r in rows]


def _insert_brief(conn, brief_id: str, wargame_id: str, title: str,
                  body: str, classification: str, now: str) -> None:
    ph = "%s" if is_pg() else "?"
    conn.execute(
        f"INSERT INTO sg_intelligence_briefs"  # nosec B608
        f"(id, brief_type, conflict_id, title, content_md, classification, created_at) "
        f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
        (brief_id, "assessment", wargame_id, title, body, classification, now),
    )


# ── Template renderer ──────────────────────────────────────────────────────────

def _render_brief(context: dict[str, Any]) -> str:
    env = Environment(  # nosec B701 — Markdown output, not HTML; autoescape would corrupt syntax
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=False,
    )
    tmpl = env.get_template("brief_export.md.j2")
    return tmpl.render(**context)


# ── Public API ─────────────────────────────────────────────────────────────────

def export_to_brief(wargame_id: str) -> dict[str, str]:
    """Export a wargame to an intelligence brief stored in sg_intelligence_briefs.

    Steps:
      1. Load wargame + latest turn + OODA scores + top signals (last 10)
      2. Render markdown via tools/strategos/templates/brief_export.md.j2
      3. INSERT into sg_intelligence_briefs
      4. Return {brief_id, title}
    """
    conn = get_connection()
    try:
        wargame = _load_wargame(conn, wargame_id)
        turn = _load_latest_turn(conn, wargame_id)
        signals = _load_top_signals(conn, limit=10)
    finally:
        conn.close()

    ooda = compute_tempo(wargame_id)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    turn_num = turn["turn_number"] if turn else 0
    title = f"Wargame Export — {wargame['name']} — Turn {turn_num} — {date_str}"
    brief_id = str(uuid.uuid4())
    generated_at = now.isoformat()

    body = _render_brief({
        "brief_id": brief_id,
        "title": title,
        "classification": _CLASSIFICATION,
        "generated_at": generated_at,
        "wargame": wargame,
        "turn": turn or {},
        "ooda": ooda,
        "signals": signals,
    })

    conn2 = get_connection()
    try:
        _insert_brief(conn2, brief_id, wargame_id, title, body, _CLASSIFICATION, generated_at)
        conn2.commit()
    finally:
        conn2.close()

    return {"brief_id": brief_id, "title": title}


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Export a wargame to an intelligence brief")
    parser.add_argument("--wargame-id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = export_to_brief(args.wargame_id)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Brief created: {result['brief_id']}")
        print(f"Title: {result['title']}")


if __name__ == "__main__":
    _cli()
