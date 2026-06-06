# CUI // SP-CTI
"""Idempotent enqueue for FathomDesk Epic E — Light Theme + Per-Page Voice Override.

Project: args/projects.yaml → fde, prefix=fde-
No external predecessor — these are independent UX tasks.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "fde-"

GATE = (
    "EPIC EXIT GATE (all 5 must pass before marking epic done): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/fathomdesk_smoke.py --json; "
    "(4) pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json."
)

SUBTASKS: list[tuple[str, str, str, str, str]] = [

    # ═══════════ EPIC E1 — LIGHT THEME ═══════════

    ("e1-01", "e1-01: CSS light theme variables in trading.css",
     "build", "high",
     "Add a [data-theme='light'] block to tools/dashboard/static/trading.css. "
     "Override every --bg-*, --text-*, --card-*, --border-*, --accent CSS variable "
     "currently defined at :root. Reference values: --bg-primary:#f6f8fa, "
     "--bg-secondary:#ffffff, --bg-card:#ffffff, --text-primary:#1f2328, "
     "--text-secondary:#656d76, --border-color:#d0d7de, --accent:#0969da. "
     "Also invert any hardcoded rgba(255,255,255,0.04) card backgrounds and "
     "#0d1117 input backgrounds in the scoped selector. Do NOT touch dark theme "
     "defaults. Only this one file changes."),

    ("e1-02", "e1-02: base.html — data-theme attribute + toggle JS",
     "build", "high",
     "In tools/dashboard/templates/base.html: (1) Set data-theme on <html> from "
     "the profile preference injected by the server: "
     "<html data-theme='{{ profile_theme|default(\"dark\") }}'>. "
     "(2) Add a theme toggle button in the top-right nav bar (sun/moon icon, "
     "visible to all users). On click: POST /api/profile/theme with "
     "{theme: 'light'|'dark'}, then set document.documentElement.dataset.theme "
     "= newTheme. Store the toggle button near the existing notification bell. "
     "No page reload required — CSS variables update instantly via data-theme."),

    ("e1-03", "e1-03: profile route — save and serve theme preference",
     "build", "high",
     "Two changes to tools/trading/dashboard/app.py: "
     "(1) Add POST /api/profile/theme endpoint that accepts {theme: 'light'|'dark'}, "
     "validates the value, and writes it to ad_profiles.theme for the current user "
     "via the existing profile update path (or direct DB update if simpler). "
     "Returns {ok: true, theme: <value>}. "
     "(2) In page_settings() (and any other route that renders base.html with "
     "profile context), pass profile_theme=profile.get('theme','dark') to the "
     "template context so the server-side data-theme renders correctly on first load. "
     "No migration needed — the theme column already exists on ad_profiles."),

    ("e1-vv", "e1-vv: E1 light theme V&V",
     "test", "high",
     "Verification steps: "
     "(1) Start the dev server and navigate to /settings. "
     "(2) Click the theme toggle → page should switch to light mode without reload; "
     "background becomes near-white, text becomes dark. "
     "(3) Refresh — light theme should persist (served from profile). "
     "(4) Toggle back to dark — should revert. "
     "(5) Run: python tools/testing/fathomdesk_smoke.py --fast --json → all checks pass. "
     "Screenshot light mode at /signals and /portfolio: "
     "playwright/screenshots/light-theme-signals.png and light-theme-portfolio.png."),

    # ═══════════ EPIC E2 — PER-PAGE VOICE OVERRIDE ═══════════

    ("e2-01", "e2-01: migration — voice_overrides column on ad_profiles",
     "build", "high",
     "Add an idempotent ALTER TABLE migration to the ensure_tables() or migration "
     "runner for ad_profiles: "
     "ALTER TABLE ad_profiles ADD COLUMN voice_overrides TEXT DEFAULT '{}'. "
     "The column stores a JSON object mapping page_key → voice_name, e.g. "
     "{\"signals\": \"quant\", \"portfolio\": \"pm\"}. "
     "Use the existing try/except pattern from progression/db.py ensure_tables() "
     "to make it idempotent. Update tests/conftest.py MINIMAL_ICDEV_SCHEMA to "
     "include voice_overrides in the ad_profiles table definition."),

    ("e2-02", "e2-02: reading_voice.py — get_voice_for_page() helper",
     "build", "high",
     "Add get_voice_for_page(page_key: str, profile: dict) -> str to "
     "tools/trading/analytics/reading_voice.py. "
     "Logic: parse profile.get('voice_overrides') as JSON (default {}); "
     "if page_key is in the dict and the value is a valid voice name (one of the "
     "keys in the voice_presets section of args/persona_presets.yaml), return it. "
     "Otherwise fall back to profile.get('voice') or 'pm'. "
     "This function is purely functional — no DB access, no side effects. "
     "Valid page keys: signals, portfolio, rebalance, options, news, alerts, "
     "progression, settings, dashboard, analysis."),

    ("e2-03", "e2-03: settings.html — per-page voice override UI",
     "build", "medium",
     "Add a 'Voice overrides per page' collapsible section to "
     "tools/dashboard/templates/settings.html inside the existing Voice card "
     "(near the global voice dropdown). "
     "Render a compact table with one row per page key "
     "(signals, portfolio, rebalance, options, news, alerts). "
     "Each row: page label, <select> with the same voice options as the global "
     "dropdown plus an 'Inherit global' (empty) option. "
     "On change, PATCH /api/profile with {voice_overrides: {<page>: <voice>}} "
     "merged into existing overrides. On load, populate from the profile's "
     "voice_overrides JSON. JS: loadVoiceOverrides() + saveVoiceOverride(page, voice)."),

    ("e2-04", "e2-04: app.py routes — load voice_overrides into page context",
     "build", "medium",
     "In tools/trading/dashboard/app.py: "
     "(1) In the existing PATCH /api/profile handler, ensure voice_overrides is "
     "an allowed field to update (merge-patch semantics: merge incoming keys into "
     "the stored JSON, don't replace the whole object). "
     "(2) In page routes that call reading_voice.apply_voice() for a page-specific "
     "reading (e.g., signals page reading, portfolio brief), replace the global "
     "voice lookup with get_voice_for_page(page_key, profile). Pass "
     "profile_voice_overrides=profile.get('voice_overrides','{}') to template "
     "context so the settings page JS can pre-populate dropdowns."),

    ("e2-vv", "e2-vv: E2 voice override V&V",
     "test", "high",
     "Verification steps: "
     "(1) Navigate to /settings → Voice section. "
     "(2) Set Signals page to 'quant' voice; verify the dropdown saves without "
     "error and the profile API returns voice_overrides with signals:quant. "
     "(3) Navigate to /signals → open the reading panel; verify the reading text "
     "uses quant-voice framing (metric-heavy, no narrative softening). "
     "(4) Set Signals override back to 'Inherit global' → reading reverts to "
     "global voice. "
     "(5) python tools/testing/fathomdesk_smoke.py --fast --json → all pass."),

    # ═══════════ WRAP ═══════════

    ("wrap-01", "wrap-01: manifest + coherence + companion sync",
     "chore", "medium",
     "Post-Epic E cleanup: "
     "(1) Add tools/trading/analytics/reading_voice.py get_voice_for_page() to "
     "tools/manifest/ topic shard (analytics or trading). "
     "(2) Run python tools/workflow/coherence_checker.py --all --fix --gate → pass. "
     "(3) Run python tools/dx/companion.py --sync --write --json → pass. "
     "No new files to register in manifest beyond reading_voice (already there — "
     "just note the new public function)."),

    ("vv-01", "vv-01: feature docs + final smoke test",
     "chore", "medium",
     "Write docs/features/phase-e1-light-theme.md and "
     "docs/features/phase-e2-voice-override.md (one page each: what shipped, "
     "how to use, key files). "
     "Then run the full FathomDesk smoke test: "
     "python tools/testing/fathomdesk_smoke.py --json → all checks pass. "
     "This task closes Epic E and the FathomDesk Phases 2-6 backlog."),
]


def enqueue() -> dict:
    conn = get_connection(db_path=str(DB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []
    skipped: list[str] = []
    parent: str | None = None   # no external predecessor — Epic E is independent
    try:
        cur = conn.cursor()
        for suffix, title, task_type, priority, description in SUBTASKS:
            tid = PREFIX + suffix
            existing = cur.execute(
                "SELECT id, status FROM kanban_tasks WHERE id = ?", (tid,),
            ).fetchone()
            if existing:
                skipped.append(tid)
                parent = tid
                continue
            cur.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "depends_on_task_id, dispatch_source, created_at, updated_at, scheduled_at) "
                "VALUES (?, ?, ?, ?, ?, 'scheduled', ?, 'manual_plan', ?, ?, ?)",
                (tid, title, description, task_type, priority, parent, now, now, now),
            )
            inserted.append(tid)
            parent = tid
        conn.commit()
    finally:
        conn.close()
    return {
        "project": "fde",
        "prefix": PREFIX,
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_subtasks": len(SUBTASKS),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
