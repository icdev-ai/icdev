#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed: STRATEGOS Digital Twin epic (sg-twin-01..07).

Formalizes the three proto-digital-twins already present in STRATEGOS
(Ghost Track dead reckoning, I&W PMESII-PT scorers, Lanchester ORBAT attrition)
under the ICDEV canvas digital twin framework, wires the cross-canvas event bus
for NDC->cyber and FathomDesk->war-endurance paths, and registers STRATEGOS
twin nodes with the awareness engine.

Run: python tools/db/seeds/seed_sg_twin.py
"""
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


def _task(task_id, title, description, priority="high", depends_on=None):
    return (
        task_id,
        title,
        description,
        "build",
        priority,
        "scheduled",
        NOW,
        "claude_cli",
        "sg_twin",
        depends_on,
        NOW,
        NOW,
    )


TASKS = [
    _task(
        "sg-twin-01",
        "Formalize Ghost Track as a predictive twin — confidence intervals, stale invalidation, twin node",
        (
            "Ghost Track's dead reckoning output (predicted vessel position/heading/speed) is a "
            "digital twin prediction layer. Formalize it: "
            "  1. Add confidence_interval field to ghost track prediction output dict "
            "     (e.g., {'lat_sigma': 0.02, 'lon_sigma': 0.02, 'hdg_sigma': 5.0}). "
            "  2. Add stale_at = predicted_at + MAX_RECKONING_HOURS to the prediction record "
            "     (default 12h). When AIS signal returns, mark prediction as invalidated. "
            "  3. Register Ghost Track as a digital twin node in the awareness KG: "
            "     node_type='predictive_twin', component='ghost_track', "
            "     update_cadence='on_ais_gap_detect', twin_of='vessel_surface_picture'. "
            "  4. Wire stale invalidation: when AIS ingester receives a signal for a vessel "
            "     with an active prediction, call ghost_track.invalidate_prediction(mmsi). "
            "Only touch apps/geosigint/ghost_track.py (or equivalent) and the awareness "
            "registration call. No new tables needed."
        ),
        priority="high",
    ),
    _task(
        "sg-twin-02",
        "Formalize I&W Engine as a geopolitical condition twin — versioned PMESII-PT nodes + drift alerting",
        (
            "The I&W Engine's PMESII-PT score vectors model a country's pre-war state. "
            "Formalize as a versioned twin: "
            "  1. Each time PMESII-PT scores are recomputed, write a snapshot row to "
            "     iw_twin_snapshots(country_iso, snapshot_at, scores_json, weibull_days_to_conflict). "
            "     Create migration 0XX_iw_twin_snapshots/up.py with this table. "
            "  2. Drift alerting: after each snapshot, compare to the previous snapshot. "
            "     If any score domain changes by >= 10 points (configurable), emit a "
            "     twin_drift event via the cross-canvas event bus: "
            "     {'source': 'sg-iw', 'entity': country_iso, 'drift_domain': 'Political', "
            "      'delta': 12.3, 'new_score': 74.1}. "
            "  3. Register I&W as a twin node in the awareness KG: "
            "     node_type='condition_twin', component='iw_engine', "
            "     twin_of='country_prewar_state', update_cadence='daily'. "
            "  4. /api/strategos/iw/twin/<country_iso> returns the last 30 snapshots "
            "     for time-series chart in the STRATEGOS UI. "
            "Touch: I&W engine module, new migration, awareness registration. "
            "Add iw_twin_snapshots to APPEND_ONLY_TABLES in pre_tool_use.py."
        ),
        priority="high",
        depends_on="sg-twin-01",
    ),
    _task(
        "sg-twin-03",
        "Formalize War Game force-state twin — ORBAT attrition snapshots + Monte Carlo twin-state input",
        (
            "Lanchester/Blotto war game runs against static ORBAT estimates today. "
            "Formalize the battle space state as a managed twin: "
            "  1. After each war game simulation run, write a battle_state_snapshots row: "
            "     (run_id, theater, snapshot_at, orbat_json, frontline_geojson, "
            "      attrition_rate, supply_pct, morale_estimate). "
            "     Create migration 0XX_battle_state_snapshots/up.py. "
            "  2. Monte Carlo COA runs in war_game.py consume current twin-state: "
            "     Load the latest battle_state_snapshots row for the theater as the "
            "     baseline ORBAT instead of the static constants file. "
            "  3. Add twin_confidence field (0-1) based on data freshness: "
            "     confidence = max(0, 1 - hours_since_last_import / 48). "
            "  4. Register War Game as a twin node: "
            "     node_type='attrition_twin', component='war_game', "
            "     twin_of='force_state_orbat'. "
            "Touch: war_game.py, new migration. "
            "Add battle_state_snapshots to APPEND_ONLY_TABLES."
        ),
        priority="high",
        depends_on="sg-twin-02",
    ),
    _task(
        "sg-twin-04",
        "Wire cross-canvas event bus: NDC C2 network twin -> STRATEGOS cyber domain",
        (
            "The NDC (Network Design Canvas) maintains a twin of C2 network infrastructure. "
            "Wire it to the STRATEGOS cyber domain: "
            "  1. In tools/network/canvas.py (or equivalent NDC module), after any network "
            "     topology update, emit a cross-canvas event: "
            "     POST /api/events/ingest  {'source': 'ndc', 'event_type': 'twin_update', "
            "       'canvas': 'network', 'payload': {'nodes_changed': N, 'topology_hash': H}}. "
            "  2. In the STRATEGOS cyber domain module, subscribe to ndc twin_update events "
            "     on startup: register a handler that re-runs the C2 attack path analysis "
            "     when the NDC topology hash changes. "
            "  3. Add a twin_source field to the cyber domain's attack_path_analysis output: "
            "     {'ndc_topology_hash': H, 'ndc_snapshot_at': ISO, 'attack_paths': [...]}. "
            "  4. If NDC module is not yet built, create a stub event emitter in "
            "     tools/network/twin_emitter.py that can be called manually for testing. "
            "Only touch cyber domain module, NDC module (or stub), events ingest. "
            "No new tables needed — uses existing hook_events."
        ),
        priority="medium",
        depends_on="sg-twin-03",
    ),
    _task(
        "sg-twin-05",
        "Wire cross-canvas event bus: FathomDesk supply chain -> STRATEGOS war endurance twin",
        (
            "FathomDesk tracks supply chain health (semiconductor, energy, defense logistics). "
            "Wire it to STRATEGOS war endurance: "
            "  1. In tools/fathomdesk/ supply chain signal path, after any supply chain "
            "     score update for a defense sector (sector_type in ['defense_logistics', "
            "     'semiconductor', 'energy']), emit a cross-canvas event: "
            "     POST /api/events/ingest  {'source': 'fathomdesk', 'event_type': 'supply_chain_update', "
            "       'payload': {'sector': 'defense_logistics', 'score': 72.4, "
            "                   'risk_level': 'elevated', 'tickers': ['LMT', 'RTX']}}. "
            "  2. In the STRATEGOS sc (supply chain) module, subscribe to fathomdesk "
            "     supply_chain_update events: recompute war endurance estimate when "
            "     defense_logistics or semiconductor risk_level changes tier. "
            "  3. Expose the linkage in /api/strategos/sc/war-endurance: add "
            "     fathomdesk_last_sync and supply_chain_risk_level to the response. "
            "  4. If FathomDesk supply chain signal is not yet wired, create a stub in "
            "     tools/fathomdesk/twin_emitter.py for manual testing. "
            "Only touch STRATEGOS sc module, FathomDesk module (or stub). "
            "No new tables."
        ),
        priority="medium",
        depends_on="sg-twin-03",
    ),
    _task(
        "sg-twin-06",
        "Register all STRATEGOS twin nodes with the awareness engine — /components-map + drift/stale health probing",
        (
            "Make STRATEGOS twin nodes visible in /components-map and monitorable by "
            "the awareness engine's health prober + drift detector: "
            "  1. In tools/awareness/component_indexer.py (or a STRATEGOS-specific "
            "     extension), register the 3 STRATEGOS twin nodes: "
            "     - ghost_track_twin: node_type=predictive_twin, health_check=ghost_track.health() "
            "     - iw_engine_twin: node_type=condition_twin, health_check=iw_engine.health() "
            "     - war_game_twin: node_type=attrition_twin, health_check=war_game.health() "
            "  2. Health check contract: each .health() method returns "
            "     {'status': 'ok'|'stale'|'error', 'last_update': ISO, 'staleness_hours': N}. "
            "  3. In tools/awareness/health_prober.py, add probe logic for "
            "     node_type in ('predictive_twin', 'condition_twin', 'attrition_twin'): "
            "     call .health(), surface staleness_hours > 48 as a gap. "
            "  4. In tools/awareness/gap_detector.py, add gap rule: "
            "     'strategos_twin_stale': fires when any STRATEGOS twin has "
            "     staleness_hours > 48 (no data import in 2 days). "
            "Run python tools/awareness/component_indexer.py --scan --json to verify nodes appear. "
            "Only touch component_indexer.py, health_prober.py, gap_detector.py, and each twin module."
        ),
        priority="medium",
        depends_on="sg-twin-04",
    ),
    _task(
        "sg-twin-07",
        "V&V: end-to-end STRATEGOS twin lifecycle — snapshot, drift, cross-canvas event delivery",
        (
            "Verify the full STRATEGOS digital twin lifecycle works end-to-end: "
            ""
            "Test 1 — Ghost Track twin: "
            "  1. Call ghost_track.predict_dead_reckoning(mmsi='123456789'). "
            "  2. Verify prediction has confidence_interval and stale_at fields. "
            "  3. Call ghost_track.invalidate_prediction('123456789'). "
            "  4. Verify prediction is marked stale in the output. "
            ""
            "Test 2 — I&W twin snapshot + drift: "
            "  1. Run I&W PMESII-PT scoring for a test country. "
            "  2. Verify iw_twin_snapshots table gains a new row. "
            "  3. Manually update a score by 15 points and re-run. "
            "  4. Verify a twin_drift event appears in hook_events. "
            "  5. Call GET /api/strategos/iw/twin/<country> and verify 2 snapshots returned. "
            ""
            "Test 3 — War Game twin-state Monte Carlo: "
            "  1. Insert a test row into battle_state_snapshots. "
            "  2. Run a war game Monte Carlo COA. "
            "  3. Verify it reads from battle_state_snapshots (not static file). "
            "  4. Verify twin_confidence field is in the COA output. "
            ""
            "Test 4 — Cross-canvas events: "
            "  1. POST a test ndc twin_update event to /api/events/ingest. "
            "  2. Verify STRATEGOS cyber domain handler fires (log entry or response update). "
            "  3. POST a test fathomdesk supply_chain_update event. "
            "  4. Verify STRATEGOS sc war endurance endpoint reflects new risk_level. "
            ""
            "Test 5 — Awareness engine: "
            "  1. Run python tools/awareness/component_indexer.py --scan --json. "
            "  2. Verify ghost_track_twin, iw_engine_twin, war_game_twin nodes appear. "
            "  3. Run health_prober.py --run-all --json. "
            "  4. Verify STRATEGOS twin nodes are probed with status and staleness_hours. "
            ""
            "Document pass/fail per check. Fix root-cause failures before marking done."
        ),
        priority="high",
        depends_on="sg-twin-06",
    ),
]


def main():
    conn = get_connection()
    cur = conn.cursor()
    inserted = 0
    skipped = 0
    for task in TASKS:
        cur.execute(INSERT_SQL, task)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    conn.close()
    print(f"[seed_sg_twin] done -- {inserted} inserted, {skipped} skipped (conflict)")
    print("  sg-twin-01  Ghost Track predictive twin -- confidence intervals + stale invalidation")
    print("  sg-twin-02  I&W condition twin -- PMESII-PT snapshots + drift alerting")
    print("  sg-twin-03  War Game attrition twin -- ORBAT snapshots + Monte Carlo twin-state")
    print("  sg-twin-04  Cross-canvas bus: NDC C2 -> STRATEGOS cyber")
    print("  sg-twin-05  Cross-canvas bus: FathomDesk supply chain -> STRATEGOS war endurance")
    print("  sg-twin-06  Awareness engine registration -- /components-map + health probing")
    print("  sg-twin-07  V&V -- full twin lifecycle end-to-end")
    print()
    print("View at: http://localhost:5050/kanban")


if __name__ == "__main__":
    main()
