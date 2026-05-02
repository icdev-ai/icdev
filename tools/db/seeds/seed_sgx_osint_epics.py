#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed sgx-sigint, sgx-geoint, sgx-socmint Kanban tasks.

22 tasks across 3 epics covering research-signal mining:
  sigint  — ARCANE-inspired passive RF/beacon attribution
  geoint  — GDELT wiring + Copernicus Sentinel-2 EO imagery
  socmint — Telegram milblog harvester → sg_raw_signals feed

All tasks status='scheduled' with scheduled_at=NOW.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


TASKS = [
    # ── sgx-sigint ────────────────────────────────────────��───────────────────
    {
        "id": "sgx-sigint-01",
        "title": "Research RTL-SDR + PySDR air-gap compatibility (Python 3.14)",
        "description": (
            "Verify rtlsdr / PySDR wheel availability for Python 3.14 on Windows/Linux. "
            "Check VRAM/CPU requirements for GNU Radio headless. "
            "Document fallback if RTL-SDR unavailable (file-replay mode). "
            "Output: compatibility_notes.md in docs/research/."
        ),
        "task_type": "research",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "sgx-sigint-02",
        "title": "DB migration — sg_sigint_events table",
        "description": (
            "Create migration file under tools/db/migrations/. "
            "Table sg_sigint_events: id (text PK), beacon_hash (text), freq_mhz (real), "
            "signal_type (text), attribution_score (real), lat (real), lon (real), "
            "raw_iq_ref (text), detected_at (timestamptz), created_at (timestamptz). "
            "Add to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "sgx-sigint-03",
        "title": "Extend tools/strategos/ais_sdr_receiver.py to emit beacon telemetry",
        "description": (
            "Add beacon_scan() function to existing ais_sdr_receiver.py. "
            "Collect Wi-Fi probe requests and BLE advertisements as passive beacon signals. "
            "Output: list of dicts with freq_mhz, signal_type, raw_bytes_b64, detected_at. "
            "Fallback to file-replay if no hardware present."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sgx-sigint-01",
    },
    {
        "id": "sgx-sigint-04",
        "title": "Create tools/strategos/rf_attribution.py — ARCANE passive beacon attribution",
        "description": (
            "Implement cross-campaign attacker attribution via passive beacon fingerprinting. "
            "hash_beacon(signal) → SHA256 of (freq_mhz + signal_type + mac_oui). "
            "match_campaigns(hash) → query sg_sigint_events for recurring hash across dates. "
            "attribution_score = log(occurrence_count) / log(total_campaigns). "
            "Write new events to sg_sigint_events via get_connection()."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sgx-sigint-03",
    },
    {
        "id": "sgx-sigint-05",
        "title": "Wire rf_attribution output to sg_iw_cascade_events",
        "description": (
            "After rf_attribution.run(), for each high-confidence attribution (score >= 0.6) "
            "insert a row into sg_iw_cascade_events: "
            "run_id, unit_id=beacon_hash, event_type='rf_attribution', "
            "probability=attribution_score, combat_degradation_score=0.0. "
            "Populate sg_iw_cascade_events which is currently empty."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sgx-sigint-04",
    },
    {
        "id": "sgx-sigint-06",
        "title": "Add /api/strategos/sigint/events endpoint",
        "description": (
            "In apps/strategos/blueprint.py add @_api.route('/sigint/events', methods=['GET']). "
            "Query sg_sigint_events ORDER BY detected_at DESC LIMIT 200. "
            "Return JSON array. Add nav link to EW Monitor page."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sgx-sigint-05",
    },
    {
        "id": "sgx-sigint-07",
        "title": "Manifest + companion sync — sgx-sigint epic",
        "description": (
            "Add entry to tools/manifest/strategos.md for rf_attribution.py and sigint_events. "
            "Run: python tools/dx/companion.py --sync --write --json"
        ),
        "task_type": "chore",
        "priority": "low",
        "depends_on_task_id": "sgx-sigint-06",
    },
    {
        "id": "sgx-sigint-08",
        "title": "V&V — SIGINT pipeline end-to-end",
        "description": (
            "Run rf_attribution.py with file-replay fixture. "
            "Assert sg_sigint_events has >= 1 row. "
            "Assert sg_iw_cascade_events has >= 1 rf_attribution event. "
            "Assert /api/strategos/sigint/events returns 200 with data array. "
            "Run: python tools/workflow/coherence_checker.py --all --gate"
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "sgx-sigint-07",
    },
    # ── sgx-geoint ────────────────────────────────────────────────────────────
    {
        "id": "sgx-geoint-01",
        "title": "Wire tools/strategos/gdelt_importer.py into OSINT scan pipeline",
        "description": (
            "gdelt_importer.run() already exists and the /osint/run-gdelt button calls it. "
            "Add auto-call to gdelt_importer.run() inside api_osint_scan() after prestage+harvest. "
            "This populates sg_raw_signals from GDELT geo-tagged events on every scan."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "sgx-geoint-02",
        "title": "DB migration — sg_eo_signals table",
        "description": (
            "Create migration under tools/db/migrations/. "
            "Table sg_eo_signals: id (text PK), scene_id (text), satellite (text), "
            "bbox_wkt (text), cloud_pct (real), sensing_date (date), "
            "thumbnail_url (text), relevance_score (real), aoi_tag (text), "
            "status (text default 'new'), created_at (timestamptz)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "sgx-geoint-03",
        "title": "Create tools/strategos/eo_importer.py — Copernicus Sentinel-2 open EO",
        "description": (
            "Query Copernicus Open Access Hub REST API (no API key for public data). "
            "Search by AOI bounding box derived from sg_theaters area_wkt. "
            "Filter: cloud_pct < 20, sensing_date within last 30 days. "
            "Write results to sg_eo_signals via get_connection(). "
            "Fallback: return empty list with status='api_unavailable' if offline."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sgx-geoint-02",
    },
    {
        "id": "sgx-geoint-04",
        "title": "Write EO findings to sg_osint_results with finding_type='geo_intelligence'",
        "description": (
            "After eo_importer.run(), for each sg_eo_signals row with relevance_score >= 0.5 "
            "insert into sg_osint_results: "
            "scan_id (from current scan), target=aoi_tag, source='copernicus', "
            "finding_type='geo_intelligence', title=scene_id, "
            "data_json=JSON of bbox_wkt+sensing_date+thumbnail_url, status='new'."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sgx-geoint-03",
    },
    {
        "id": "sgx-geoint-05",
        "title": "Add GEOINT satellite imagery overlay to Map Dashboard",
        "description": (
            "Add /api/strategos/geoint/scenes endpoint returning sg_eo_signals rows. "
            "In tools/dashboard/templates/strategos/map.html add a 'Satellite' toggle layer. "
            "Render bbox_wkt as a rectangle overlay with scene_id tooltip. "
            "Toggle off by default (performance)."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sgx-geoint-04",
    },
    {
        "id": "sgx-geoint-06",
        "title": "Manifest + companion sync — sgx-geoint epic",
        "description": (
            "Add entry to tools/manifest/strategos.md for eo_importer.py and sg_eo_signals. "
            "Run: python tools/dx/companion.py --sync --write --json"
        ),
        "task_type": "chore",
        "priority": "low",
        "depends_on_task_id": "sgx-geoint-05",
    },
    {
        "id": "sgx-geoint-07",
        "title": "V&V — GEOINT pipeline end-to-end",
        "description": (
            "Run eo_importer.py --dry-run. Assert returns valid JSON with status key. "
            "Assert sg_eo_signals table exists (migration ran). "
            "Assert /api/strategos/geoint/scenes returns 200. "
            "Assert GDELT importer wired into /osint/scan response. "
            "Run: python tools/workflow/coherence_checker.py --all --gate"
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "sgx-geoint-06",
    },
    # ── sgx-socmint ───────────────────────────────────────────────────────────
    {
        "id": "sgx-socmint-01",
        "title": "Research pyrogram/telethon air-gap + Python 3.14 compatibility",
        "description": (
            "Check pyrogram and telethon wheel availability for Python 3.14 on the air-gap PyPI mirror. "
            "If unavailable, evaluate feedparser + public Telegram RSS bridges as fallback. "
            "Document findings and chosen approach in description of sgx-socmint-03."
        ),
        "task_type": "research",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "sgx-socmint-02",
        "title": "DB migration — sg_socmint_signals table",
        "description": (
            "Create migration under tools/db/migrations/. "
            "Table sg_socmint_signals: id (text PK), platform (text), channel_id (text), "
            "message_id (text UNIQUE), text (text), media_url (text), "
            "posted_at (timestamptz), relevance_score (real), geo_hint (text), "
            "status (text default 'new'), created_at (timestamptz). "
            "Add to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "sgx-socmint-03",
        "title": "Create tools/strategos/socmint_harvester.py — Telegram milblog collector",
        "description": (
            "Implement SOCMINT harvester with two collection paths: "
            "(1) pyrogram/telethon if available, (2) feedparser + public RSS bridge as fallback. "
            "Default channels: UA-Telegram-Milblog, IDF official, Taiwan MND RSS. "
            "Config in args/strategos_config.yaml under socmint.channels[]. "
            "Write to sg_socmint_signals. Return {inserted, skipped, status}."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sgx-socmint-01",
    },
    {
        "id": "sgx-socmint-04",
        "title": "Wire socmint output to sg_raw_signals feed",
        "description": (
            "After socmint_harvester.run(), for each sg_socmint_signals row with relevance_score >= 0.4 "
            "insert into sg_raw_signals: "
            "url_hash=SHA256(message_id), title=text[:200], body=text, "
            "source=platform+':'+channel_id, signal_date=posted_at, "
            "geo_hint=geo_hint, processed=False. "
            "Dedup via url_hash ON CONFLICT DO NOTHING."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sgx-socmint-03",
    },
    {
        "id": "sgx-socmint-05",
        "title": "Schedule socmint harvester as Genesis reflex (6h cadence)",
        "description": (
            "Create tools/genesis/reflexes/socmint.py wrapping socmint_harvester.run(). "
            "Return {success, metric_value=inserted_count}. "
            "Register in args/genesis_config.yaml under reflexes.socmint: "
            "enabled: true, interval_seconds: 21600, tier: GREEN. "
            "Register in tools/genesis/reflex_registry.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sgx-socmint-04",
    },
    {
        "id": "sgx-socmint-06",
        "title": "Manifest + companion sync — sgx-socmint epic",
        "description": (
            "Add entry to tools/manifest/strategos.md for socmint_harvester.py. "
            "Add entry to tools/manifest/genesis.md for socmint reflex. "
            "Run: python tools/dx/companion.py --sync --write --json"
        ),
        "task_type": "chore",
        "priority": "low",
        "depends_on_task_id": "sgx-socmint-05",
    },
    {
        "id": "sgx-socmint-07",
        "title": "V&V — SOCMINT harvester end-to-end",
        "description": (
            "Run socmint_harvester.py --dry-run. Assert returns valid JSON. "
            "Assert sg_socmint_signals table exists. "
            "Run harvester with feedparser fallback — assert sg_raw_signals count increases. "
            "Assert Genesis socmint reflex returns success=True. "
            "Run: python tools/workflow/coherence_checker.py --all --gate"
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "sgx-socmint-06",
    },
]


def run() -> dict:
    conn = get_connection()
    inserted = 0
    skipped = 0
    now = _now()
    try:
        for t in TASKS:
            exists = conn.execute(
                "SELECT 1 FROM kanban_tasks WHERE id=%s", (t["id"],)
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "scheduled_at, depends_on_task_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,'scheduled',%s,%s,%s,%s)",
                (
                    t["id"], t["title"], t["description"],
                    t["task_type"], t["priority"],
                    now,
                    t["depends_on_task_id"],
                    now, now,
                ),
            )
            inserted += 1
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        return {"status": "error", "error": str(exc)}
    conn.close()
    return {"status": "ok", "inserted": inserted, "skipped": skipped}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
