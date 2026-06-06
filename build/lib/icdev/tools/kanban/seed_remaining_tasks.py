#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed all remaining Kanban tasks across active epics.

Covers:
  - FathomDesk 7.12 close-out (ad712-e2e, ad712-vv)
  - Wargame Dashboard Enhancements (wge)
  - STRATEGOS Chat verification (sgc)
  - System Graph V&V (sysgraph)
  - Canvas Architecture Gaps (canvas-gap)
  - STRATEGOS Full Platform (sg) foundation epics

Run: python tools/kanban/seed_remaining_tasks.py
"""


import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402


_NOW = datetime.now(timezone.utc).isoformat()


def _conn():
    c = get_connection()
    return c


TASKS = [

    # ══════════════════════════════════════════════════════════════════
    # EPIC: FathomDesk 7.12 Close-Out
    # ══════════════════════════════════════════════════════════════════

    {
        "id": "ad712-e2e-01",
        "title": "FD7.12: Selenium E2E test — /orders page + POST /api/orders/place",
        "description": (
            "File: tools/testing/e2e_fathomdesk_orders.py (NEW)\n\n"
            "Write a Selenium headless Chrome E2E test covering:\n"
            "1. Navigate to /orders (authenticated as test user)\n"
            "2. Verify Place Order panel is visible: #place-order-card, #po-ticker, #po-type\n"
            "3. Select order_type=limit → verify #po-limit-group becomes visible\n"
            "4. Select order_type=stop_limit → verify both #po-limit-group + #po-stop-group visible\n"
            "5. Select order_type=market → verify price fields hidden\n"
            "6. POST /api/orders/place (unauthenticated) → expect 401\n"
            "7. POST /api/orders/place with bad side ('hold') → expect 400 with error msg\n"
            "8. POST /api/orders/place with order_type=limit, missing limit_price → expect 400\n"
            "9. POST /api/orders/place authenticated, market order → expect 200, ok=true\n"
            "   (will route to broker sim — Alpaca credentials absent in test env)\n\n"
            "Use the pattern from tools/testing/e2e_fathomdesk.py for auth session.\n"
            "Login creds: FATHOMDESK_TEST_EMAIL / FATHOMDESK_TEST_PASSWORD from .env.\n"
            "Run: python tools/testing/e2e_fathomdesk_orders.py"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "ad712-vv-01",
        "title": "FD7.12: V&V — manifest shard + feature doc + companion sync",
        "description": (
            "Three steps:\n\n"
            "1. tools/manifest/fathomdesk.md — append Phase 7.12 section:\n"
            "   ## Phase 7.12 — Live Broker Limit/Stop Orders + Trap Close-Out\n"
            "   - POST /api/orders/place: market|limit|stop|stop_limit; auth-gated; validates limit_price/stop_price\n"
            "   - Place Order panel in /orders: dynamic price fields, all 4 TIF options\n"
            "   - ad_trap_events write path: daemon._run_trap_scanner every 15min; trap_db.insert_trap() with 4h dedup\n"
            "   - GET /api/traps: filterable by ticker, pattern, min_confidence\n"
            "   - /signals page: trap history panel with filter controls\n\n"
            "2. docs/features/phase-7-12-orders-traps.md (NEW) — 3-section feature doc:\n"
            "   - What shipped\n"
            "   - Architecture (order_manager.place_order → broker router → Alpaca/sim)\n"
            "   - Trap pipeline (detect_traps → insert_trap → /api/traps → /signals panel)\n\n"
            "3. python tools/dx/companion.py --sync --write --json\n"
            "4. python tools/workflow/coherence_checker.py --all --fix --gate"
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "ad712-e2e-01",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: Wargame Dashboard Enhancements (wge)
    # ══════════════════════════════════════════════════════════════════

    {
        "id": "wge-verify-01",
        "title": "WGE: Verify + wire Turn Engine advance-turn button in wargame.html",
        "description": (
            "File: tools/dashboard/templates/strategos/wargame.html\n\n"
            "Verify the Advance Turn button calls POST /wargame/<id>/turn/advance correctly:\n"
            "1. Confirm the button has onclick calling advanceTurn() or similar\n"
            "2. Confirm the JS fetch hits /wargame/<wid>/turn/advance via POST\n"
            "3. Confirm the turn log table (#turn-log-body) refreshes after advance\n"
            "4. If any wiring is broken, fix the JS fetch and response handling\n"
            "5. After advance, the turn counter and turn-log should reflect the new turn\n\n"
            "Backend: tools/strategos/wargame_turn_engine.advance_turn() is complete.\n"
            "Blueprint route: /wargame/<wargame_id>/turn/advance (POST) in blueprint.py.\n"
            "Verify with Playwright: navigate /strategos/wargame?id=<id>, click Advance Turn,\n"
            "confirm turn-log-body has a new row."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "wge-advisor-01",
        "title": "WGE: AI Strategic Advisor panel — war_council route + wargame.html section",
        "description": (
            "Two files:\n\n"
            "1. tools/dashboard/templates/strategos/wargame.html\n"
            "   Add an 'AI Strategic Advisor' collapsible card below the AI Assessment card.\n"
            "   - Button: 'Get War Council Brief' → POST /api/wargame/<wid>/war-council\n"
            "   - Renders markdown brief in a scrollable pre block\n"
            "   - Shows confidence score badge and recommended COA\n\n"
            "2. tools/strategos/blueprint.py\n"
            "   Add route: POST /api/wargame/<wargame_id>/war-council\n"
            "   - Load wargame + current OODA/Lanchester state\n"
            "   - Call tools.strategos.war_council.generate_war_council_brief(wargame_id)\n"
            "     (function already exists in war_council.py)\n"
            "   - Return {brief_text, confidence, recommended_coa, generated_at}\n\n"
            "If war_council.generate_war_council_brief() doesn't exist, create a stub:\n"
            "  def generate_war_council_brief(wargame_id) -> dict:\n"
            "    loads sg_wargames + sg_wargame_turns, returns structured dict with\n"
            "    brief_text (markdown), confidence (0-1), recommended_coa string."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wge-verify-01",
    },
    {
        "id": "wge-signal-01",
        "title": "WGE: Signal→OODA live refresh — 30s poll + domain flash badge",
        "description": (
            "File: tools/dashboard/templates/strategos/wargame.html\n\n"
            "Add live polling to the OODA tab:\n"
            "1. Every 30 seconds, fetch GET /wargame/<wid>/ooda-live\n"
            "   (route already exists in blueprint.py)\n"
            "2. Update OODA radar chart data without full page reload\n"
            "3. For each domain that changed since last poll, flash the domain label\n"
            "   with a brief CSS animation (pulse class, 1.5s, then remove)\n"
            "4. Add a 'Live' badge next to the OODA tab label that shows 'LIVE' when\n"
            "   polling is active and 'PAUSED' when not\n"
            "5. Stop polling when user leaves the OODA tab; resume when they return\n\n"
            "The OODA live API returns: {domains: {air, land, sea, cyber, space, info},\n"
            "tempo: {blue, red}, updated_at}."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "wge-verify-01",
    },
    {
        "id": "wge-export-01",
        "title": "WGE: Export to Intel Brief — button in wargame.html + PDF/MD download",
        "description": (
            "Two files:\n\n"
            "1. tools/dashboard/templates/strategos/wargame.html\n"
            "   Add 'Export Intel Brief' button in the wargame header toolbar:\n"
            "   - Button triggers GET /wargame/<wid>/export/brief?format=md\n"
            "   - Response is a markdown file download\n"
            "   - Show a spinner while generating; display success toast on complete\n\n"
            "2. tools/strategos/blueprint.py\n"
            "   Add route: GET /wargame/<wargame_id>/export/brief\n"
            "   - Accept ?format=md (default) or ?format=pdf\n"
            "   - Call tools.strategos.wargame_exporter.export_brief(wargame_id)\n"
            "     (wargame_exporter.py exists — check export_brief() signature)\n"
            "   - Stream as application/octet-stream with filename=intel-brief-<id>.md\n\n"
            "If wargame_exporter.export_brief() is missing, add it:\n"
            "  Load sg_wargames + sg_wargame_turns + sg_coas + lanchester state\n"
            "  Render brief_export.md.j2 template (already exists in templates/)\n"
            "  Return rendered string"
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "wge-verify-01",
    },
    {
        "id": "wge-orbat-01",
        "title": "WGE: ORBAT pre-population — seed Lanchester b0/r0 from sg_orbat_units",
        "description": (
            "File: tools/strategos/blueprint.py + wargame.html\n\n"
            "When a user opens the Lanchester tab, auto-fill b0 and r0 from the\n"
            "wargame's ORBAT (sg_orbat_units table) rather than leaving defaults at 1000/800.\n\n"
            "1. Blueprint: api_wargame_orbat_seed() already exists at\n"
            "   POST /wargame/<id>/orbat/seed — verify it returns {b0, r0, blue_units, red_units}\n\n"
            "2. wargame.html: On tab switch to 'Lanchester', if #lch-b0 still has default values,\n"
            "   fetch GET /wargame/<wid>/orbat/seed → populate #lch-b0 and #lch-r0\n"
            "   Show a small 'From ORBAT' badge below the inputs when populated this way\n\n"
            "3. If api_wargame_orbat_seed() returns error or empty, fall through to defaults silently."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "wge-verify-01",
    },
    {
        "id": "wge-vv-01",
        "title": "WGE: V&V — Playwright E2E + manifest shard + companion sync",
        "description": (
            "1. Playwright E2E (add to existing playwright test or new script):\n"
            "   - Navigate /strategos/wargame (with a seeded wargame)\n"
            "   - Click Advance Turn → turn log shows new row\n"
            "   - Click Get War Council Brief → brief text appears\n"
            "   - Click Export Intel Brief → download triggered\n"
            "   - Switch to OODA tab → live badge shows LIVE\n\n"
            "2. tools/manifest/wargame-enhancements.md (NEW shard):\n"
            "   Document all 6 enhancements with routes and module references.\n\n"
            "3. python tools/dx/companion.py --sync --write --json\n"
            "4. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "5. Screenshot: playwright/screenshots/wge-wargame-full.png"
        ),
        "task_type": "verify",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "wge-export-01",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: STRATEGOS Chat Verification (sgc)
    # ══════════════════════════════════════════════════════════════════

    {
        "id": "sgc-verify-01",
        "title": "SGC: Verify STRATEGOS Chat end-to-end — panel, API, right-click handlers",
        "description": (
            "STRATEGOS Chat exists (strategos_chat.py, _chat_panel.html, strategos-chat.js,\n"
            "blueprint API routes). Verify the full flow works:\n\n"
            "1. Playwright: navigate /strategos → verify chat panel toggle button is visible\n"
            "2. Click toggle → verify #sgc-panel slides open\n"
            "3. Type a message → POST /api/strategos/chat/<ctx>/send → response appears\n"
            "4. Verify right-click on a maritime asset shows context menu with 'Ask STRATEGOS'\n"
            "5. If any step fails, diagnose and fix the broken wiring\n\n"
            "Key files to check if broken:\n"
            "- tools/dashboard/static/js/strategos-chat.js (panel toggle, fetch logic)\n"
            "- tools/dashboard/templates/strategos/_chat_panel.html (DOM structure)\n"
            "- tools/strategos/blueprint.py routes: /api/strategos/chat/* (init/inject/send/poll)\n"
            "- tools/strategos/strategos_chat.py StrategosChat class\n\n"
            "Screenshot: playwright/screenshots/sgc-chat-panel.png"
        ),
        "task_type": "verify",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "sgc-fix-01",
        "title": "SGC: Fix any broken wiring found in sgc-verify-01 and re-verify",
        "description": (
            "Depends on sgc-verify-01. If any wiring issues were found:\n"
            "1. Fix broken JS fetch calls in strategos-chat.js\n"
            "2. Fix blueprint route registration if routes 404\n"
            "3. Fix strategos_chat.py LLM routing if responses are empty\n"
            "4. Re-run Playwright verification after fixes\n"
            "5. Companion sync + coherence gate\n\n"
            "If sgc-verify-01 passed cleanly, mark this task done immediately."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sgc-verify-01",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: System Graph V&V
    # ══════════════════════════════════════════════════════════════════

    {
        "id": "sysgraph-vv-01",
        "title": "SysGraph: V&V — community detection legend filter + Playwright E2E",
        "description": (
            "System Graph Tier 1 is complete (Sigma.js, federation, community detection).\n"
            "Community detection uses greedy_modularity_communities from networkx in\n"
            "tools/system_graph/graph_builder.py.\n\n"
            "Verify and document:\n"
            "1. Navigate /components-map → nodes are colored by cluster_color\n"
            "2. Legend filter: clicking a cluster label filters to that cluster only\n"
            "   (uses Sigma reducers, not server round-trip)\n"
            "3. Verify cluster_count stat in the stats bar is non-zero\n"
            "4. Screenshot: playwright/screenshots/sysgraph-community.png\n\n"
            "If legend filter is broken:\n"
            "- Check tools/dashboard/templates/components_map.html for\n"
            "  the cluster filter click handler\n"
            "- The filter should call sigma.setSetting('nodeReducer', ...) to show\n"
            "  only nodes with matching cluster_id\n\n"
            "5. tools/manifest/system-graph.md — verify community detection entry exists.\n"
            "6. python tools/workflow/coherence_checker.py --all --fix --gate"
        ),
        "task_type": "verify",
        "priority": "medium",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: Canvas Architecture Gaps (canvas-gap)
    # ══════════════════════════════════════════════════════════════════

    {
        "id": "canvas-gap-01",
        "title": "Canvas Gap: Add args config files for IDC, DDC, BDC, ODC",
        "description": (
            "IDC/DDC/BDC/ODC have no canvas-specific config files in args/ (gap).\n"
            "NDC, SDC, PDC all have args/network_canvas_config.yaml etc.\n\n"
            "Create 4 minimal YAML config files:\n\n"
            "1. args/infra_canvas_config.yaml\n"
            "   max_nodes: 200\n"
            "   export_formats: [terraform, ansible, pulumi]\n"
            "   enable_cost_estimation: true\n"
            "   default_cloud: aws\n\n"
            "2. args/data_canvas_config.yaml\n"
            "   max_nodes: 150\n"
            "   export_formats: [dbt, airflow, spark]\n"
            "   enable_lineage_graph: true\n\n"
            "3. args/boundary_canvas_config.yaml\n"
            "   max_nodes: 100\n"
            "   export_formats: [puml, drawio]\n"
            "   enable_compliance_overlay: true\n\n"
            "4. args/ops_canvas_config.yaml\n"
            "   max_nodes: 150\n"
            "   export_formats: [ansible, helm, docker-compose]\n"
            "   enable_runbook_generation: true\n\n"
            "Then wire each config into its blueprint's create_app() via:\n"
            "  app.config['CANVAS_CONFIG'] = yaml.safe_load(open('args/infra_canvas_config.yaml'))\n"
            "or load lazily in a get_canvas_config() helper in each constants.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "canvas-gap-02",
        "title": "Canvas Gap: E2E test parity for IDC, DDC, BDC, ODC",
        "description": (
            "IDC/DDC/BDC/ODC only have nav+index E2E tests (tools/testing/e2e_new_canvases.py).\n"
            "NDC/SDC/PDC have full lifecycle E2E (create design → add node → assess → export).\n\n"
            "Extend tools/testing/e2e_new_canvases.py to add for each of IDC, DDC, BDC, ODC:\n\n"
            "1. GET /<canvas>/ → 200 (already exists)\n"
            "2. POST /api/<canvas>/designs → create design → 201, returns design_id\n"
            "3. GET /api/<canvas>/designs/<id> → 200\n"
            "4. GET /api/<canvas>/designs/<id>/graph → 200, {nodes: [], edges: []}\n"
            "5. POST /api/<canvas>/designs/<id>/nodes → add a node → 200\n"
            "6. GET /api/<canvas>/templates → 200, array of templates\n\n"
            "Canvas prefixes:\n"
            "  IDC: /infra + /api/infra\n"
            "  DDC: /data-canvas + /api/data-canvas\n"
            "  BDC: /boundary + /api/boundary\n"
            "  ODC: /ops-canvas + /api/ops-canvas\n\n"
            "Run: python tools/testing/e2e_new_canvases.py --all"
        ),
        "task_type": "test",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "canvas-gap-01",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: STRATEGOS Full Platform — Foundation (sg)
    # ══════════════════════════════════════════════════════════════════

    {
        "id": "sg-foundation-01",
        "title": "STRATEGOS: Audit existing module coverage vs 22-epic roadmap",
        "description": (
            "tools/strategos/ has 40+ Python modules. Before building new epics,\n"
            "audit what's already wired vs what's missing.\n\n"
            "For each sg epic, determine status:\n"
            "  foundation: blueprint.py + DB schema (sg_* tables)\n"
            "  import: ais_importer, adsb_importer, tle_importer, gdelt_importer etc.\n"
            "  viz: Leaflet/D3/Cytoscape templates\n"
            "  land: ground_vehicle_importer, orbat page\n"
            "  sea: ais_importer, maritime.html page\n"
            "  cyber: cisa_kev_importer, opencti_bridge, cyber.html\n"
            "  space: tle_importer, space domain page\n"
            "  info: gdelt_importer, info.html\n"
            "  ew: ew_monitor, ew.html\n"
            "  ghost: ghost.html (ghost track)\n"
            "  iw: iw_engine, iw_indicators, indicators.html\n"
            "  game: wargame.html, wargame_turn_engine\n"
            "  agent: ooda.py, strategy_agent.py\n"
            "  oracle: oracle.html\n"
            "  reflex: (check Genesis reflexes for strategos)\n"
            "  analyst: ipb.html, intsum.html, pir.html\n\n"
            "Output: create docs/strategos-audit.md listing each epic with:\n"
            "  status: complete | partial | missing\n"
            "  missing_pieces: list of specific gaps\n\n"
            "This audit drives the next sg-* tasks so they don't rebuild what exists."
        ),
        "task_type": "research",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "sg-foundation-02",
        "title": "STRATEGOS: Wire all existing pages into nav + verify blueprint registration",
        "description": (
            "Many STRATEGOS pages exist in templates but may not be routed or in the nav.\n\n"
            "1. Check tools/strategos/blueprint.py for all routes — identify any template\n"
            "   files in tools/dashboard/templates/strategos/ that have NO route\n\n"
            "2. For each missing route, add a @bp.route() pointing to the template\n\n"
            "3. Verify the STRATEGOS nav dropdown in base.html links to all pages:\n"
            "   index, airspace, maritime, land/orbat, cyber, space, info, ew,\n"
            "   ghost, iw/indicators, wargame, oracle, analyst (ipb/intsum/pir),\n"
            "   kg (knowledge graph), map, signals, sources, darkweb\n\n"
            "4. Playwright: visit each STRATEGOS page → confirm 200 and no JS console errors\n\n"
            "5. Screenshot all pages that were previously missing routes:\n"
            "   playwright/screenshots/sg-<page>.png"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sg-foundation-01",
    },
    {
        "id": "sg-import-01",
        "title": "STRATEGOS: Wire data importers into /api/strategos/ingest endpoint",
        "description": (
            "tools/strategos/ has these importers that may not be reachable from the UI:\n"
            "  ais_importer.py, adsb_importer.py, tle_importer.py, gdelt_importer.py,\n"
            "  ground_vehicle_importer.py, uas_importer.py, osm_importer.py,\n"
            "  eo_importer.py, stix_importer.py, cisa_kev_importer.py\n\n"
            "1. Check which importers have blueprint routes in blueprint.py\n"
            "2. For missing ones, add POST /api/strategos/ingest/<source> route:\n"
            "   sources: ais, adsb, tle, gdelt, ground, uas, osm, eo, stix, kev\n"
            "   Each route calls the corresponding importer module's main ingest function\n"
            "   Returns {inserted, updated, errors, source, elapsed_ms}\n"
            "3. Add an 'Ingest' panel to tools/dashboard/templates/strategos/sources.html:\n"
            "   One row per source with a 'Run Ingest' button and last-run timestamp\n\n"
            "Key: importers must be air-gap safe — no external CDN calls, read from local\n"
            "data files or environment-configured endpoints only."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sg-foundation-02",
    },
    {
        "id": "sg-oracle-01",
        "title": "STRATEGOS: Intelligence Oracle — 5 war intelligence lenses wired to /oracle",
        "description": (
            "File: tools/dashboard/templates/strategos/oracle.html + blueprint.py\n\n"
            "STRATEGOS Oracle provides 5 intelligence lenses:\n"
            "1. Threat Assessment — current threat levels by domain (air/sea/land/cyber/space)\n"
            "2. OODA Advantage — blue vs red tempo comparison with recommendation\n"
            "3. Supply Chain Risk — DIB vulnerability score from dib_mapper.py\n"
            "4. Escalation Risk — I&W engine score from iw_engine.py\n"
            "5. COA Comparison — rank current COAs from sg_coas table\n\n"
            "1. Add GET /api/strategos/oracle/assessment route to blueprint.py\n"
            "   Returns all 5 lens outputs in one JSON response\n"
            "   Each lens: {score, label, trend, recommendation, evidence[]}\n\n"
            "2. oracle.html — add 5 lens cards with score badges + trend arrows\n"
            "   'Refresh Assessment' button → fetches API → updates all 5 cards\n"
            "   Auto-refresh every 5 minutes\n\n"
            "3. Screenshot: playwright/screenshots/sg-oracle.png"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sg-foundation-02",
    },
    {
        "id": "sg-analyst-01",
        "title": "STRATEGOS: Analyst Workbench — PIR/CCIR → Intel Brief pipeline",
        "description": (
            "Analyst Workbench ties together: PIR (Priority Intelligence Requirements),\n"
            "CCIR triggers, INTSUM generation, and OPORD export.\n\n"
            "1. tools/dashboard/templates/strategos/pir.html:\n"
            "   - PIR form: requirement text, collection status, update cadence\n"
            "   - POST /api/strategos/pir → saves to sg_pir table\n"
            "   - CCIR trigger: when PIR threshold crossed, auto-generate alert\n\n"
            "2. tools/dashboard/templates/strategos/intsum.html:\n"
            "   - 'Generate INTSUM' button → POST /api/strategos/intsum/generate\n"
            "   - Calls tools/strategos/intsum.py:generate_intsum()\n"
            "   - Renders formatted INTSUM with KEY POINTS + ASSESSMENT sections\n\n"
            "3. Export chain: INTSUM → Intel Brief (existing brief_export.md.j2)\n"
            "   - 'Export Brief' button on intsum.html → reuses wge-export-01 endpoint\n\n"
            "4. Check if these pages already have routes; add missing ones to blueprint.py.\n"
            "5. Playwright verification of full PIR→INTSUM→Brief flow.\n"
            "6. Screenshot: playwright/screenshots/sg-analyst-intsum.png"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sg-foundation-02",
    },
    {
        "id": "sg-vv-01",
        "title": "STRATEGOS: V&V gate — manifest shard + E2E + companion sync",
        "description": (
            "After sg-foundation, sg-import, sg-oracle, sg-analyst complete:\n\n"
            "1. tools/manifest/strategos.md — update with all newly wired routes\n"
            "   and module coverage\n\n"
            "2. Playwright E2E sweep of all STRATEGOS pages — all must return 200\n"
            "   Screenshot grid: playwright/screenshots/sg-*.png\n\n"
            "3. python tools/testing/health_check.py --json — verify no import errors\n\n"
            "4. python tools/dx/companion.py --sync --write --json\n\n"
            "5. python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "6. docs/features/strategos-full-platform.md — feature doc covering:\n"
            "   all domains, importer pipeline, oracle lenses, analyst workbench"
        ),
        "task_type": "verify",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sg-analyst-01",
    },
]


def seed():
    conn = _conn()
    inserted = 0
    skipped = 0

    for t in TASKS:
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = ?", (t["id"],)
        ).fetchone()
        if existing:
            print(f"  SKIP  {t['id']} (already exists)")
            skipped += 1
            continue

        conn.execute(
            """INSERT INTO kanban_tasks
               (id, title, description, task_type, priority,
                status, scheduled_at, depends_on_task_id,
                executor_type, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t["id"],
                t["title"],
                t.get("description", ""),
                t.get("task_type", "build"),
                t.get("priority", "medium"),
                t.get("status", "backlog"),
                t.get("scheduled_at"),
                t.get("depends_on_task_id"),
                "claude_cli",
                _NOW,
                _NOW,
            ),
        )
        print(f"  ADD   {t['id']} [{t['status']}] — {t['title'][:60]}")
        inserted += 1

    conn.commit()
    conn.close()
    print(f"\n  Done: {inserted} added, {skipped} skipped.")


if __name__ == "__main__":
    seed()
