#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed: wne-core-06 — WriteGuard integration in narrative_generator.py."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

INSERT_SQL = """
INSERT INTO kanban_tasks
    (id, title, description, task_type, priority, status,
     scheduled_at, executor_type, dispatch_source,
     depends_on_task_id, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO NOTHING
"""

DESC = (
    "In tools/studio/wne/narrative_generator.py, after the narrative text is generated, "
    "run it through WriteGuard for quality scoring and optional auto-improvement. "
    "Reuse tools/pulse/writeguard.py: run_full_quality_check(text) -> dict. "
    "Gate logic based on overall_score and badge field (green/yellow/red): "
    "  Green (>=80): accept, return immediately. "
    "  Yellow (60-79): if LLM available via LLMRouter, rewrite once with top findings "
    "    as guidance then re-score; if no LLM, return with quality_warning. "
    "  Red (<60): if LLM available, rewrite up to 2 passes until Green or Yellow; "
    "    if no LLM, return with quality_warning='critical'. "
    "Extend NarrativeResult dataclass with 4 new fields: "
    "  writeguard_score: float | None "
    "  writeguard_badge: str | None  (green/yellow/red) "
    "  writeguard_findings: list[str]  (top 3 actionable findings from WriteGuard) "
    "  quality_warning: str | None  (non-None when score<80 and no LLM to fix it) "
    "WNE chat_engine.py surfaces the score after generation: "
    "  Green: 'Executive brief scored 87 (Green) -- ready to export.' "
    "  Yellow improved: 'Brief scored 64 (Yellow), improved to 81 (Green) after rewrite.' "
    "  Red no-LLM: 'Brief scored 52 (Red, no LLM available) -- quality_warning included.' "
    "Degrade gracefully if run_full_quality_check import fails (wg_score=None, no raise). "
    "Only touch narrative_generator.py."
)

def main():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(INSERT_SQL, (
        "wne-core-06",
        "Integrate WriteGuard into narrative_generator.py -- score + auto-improve narrative output",
        DESC,
        "build", "high", "scheduled", NOW,
        "claude_cli", "wne_plan",
        "wne-core-02",
        NOW, NOW,
    ))
    conn.commit()
    inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.close()
    print(f"[seed_wne_writeguard] wne-core-06 -- {inserted} inserted (0 = already exists)")
    print("  WriteGuard: run_full_quality_check() from tools/pulse/writeguard.py")
    print("  Gate: Green>=80 accept | Yellow rewrite 1x | Red rewrite 2x | no-LLM warn")
    print("  Depends on: wne-core-02 (narrative_generator.py created)")

if __name__ == "__main__":
    main()
