#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed Kanban tasks for Network Migration Canvas Enhancement (NMCE).

Epic: nmce — Network Infrastructure Migration Canvas Enhancement
Plan: we-have-http-localhost-5050-migration-ca-encapsulated-hippo.md

Run: python tools/kanban/seed_nmce_kanban.py
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
    # ── Phase 1: DB Schema ──────────────────────────────────────────────────
    {
        "id": "nmce-db-01",
        "title": "NMCE: Add 3 new tables to NETWORK_MIGRATION_SCHEMA in init_db.py",
        "description": (
            "File: tools/migration_canvas/db/init_db.py\n\n"
            "Append 3 new table DDLs to the NETWORK_MIGRATION_SCHEMA string "
            "(idempotent CREATE TABLE IF NOT EXISTS — migration_canvas.db is self-managed):\n\n"
            "1. mc_net_ai_sessions(id, session_id REFS mc_net_sessions, role CHECK IN(engineer|assistant), "
            "message, model_used, created_at) + idx_mc_net_ai_session\n\n"
            "2. mc_net_protocol_plans(id, session_id REFS mc_net_sessions, protocol, src_config_json, "
            "tgt_config_json, migration_steps_json, risk_level CHECK IN(low|medium|high|critical), "
            "ai_notes, status, created_at, updated_at, UNIQUE(session_id, protocol)) + idx_mc_net_proto_session\n\n"
            "3. mc_net_parallel_timelines(id, session_id REFS mc_net_sessions, milestone_name, description, "
            "days_before_cutover INTEGER, phase CHECK IN(pre_migration|parallel_run|cutover|post_migration|decommission), "
            "owner, duration_hours, status CHECK IN(planned|in_progress|complete|blocked), notes, created_at) "
            "+ idx_mc_net_timeline_session\n\n"
            "These are appended after the existing mc_net_erb_metadata DDL block. "
            "_migrate_network_tables() already calls conn.executescript(NETWORK_MIGRATION_SCHEMA) — "
            "no further wiring needed."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },

    # ── Phase 2: Backend Functions ──────────────────────────────────────────
    {
        "id": "nmce-fn-01",
        "title": "NMCE: Add get_network_inventory() + load_device_config_from_db() to network_migration.py",
        "description": (
            "File: tools/migration_canvas/network_migration.py\n\n"
            "Add after compute_readiness():\n\n"
            "get_network_inventory(site='', device_type='', vendor='', eol_within_years=None) -> list[dict]:\n"
            "  - Query ni_devices in network_canvas.db\n"
            "  - LEFT JOIN ni_device_configs ON ni_device_configs.device_id = ni_devices.id "
            "to set has_config=bool, config_source='db'|'none'\n"
            "  - LEFT JOIN mc_net_sessions (migration_canvas.db) WHERE src_model = ni_devices.model "
            "to get active_session_id\n"
            "  - Falls back to nc_hardware_profiles rows if ni_devices is empty\n"
            "  - Returns {id, node_id, label, vendor, model, device_type, eol_date, eos_date, "
            "has_config, config_source, active_session_id}\n\n"
            "load_device_config_from_db(device_id: str) -> str | None:\n"
            "  - Query ni_device_configs WHERE device_id=? ORDER BY "
            "CASE config_type WHEN 'running' THEN 1 WHEN 'startup' THEN 2 ELSE 3 END, created_at DESC LIMIT 1\n"
            "  - Returns config_text or None"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-db-01",
    },
    {
        "id": "nmce-fn-02",
        "title": "NMCE: Add recommend_hardware() + ai_assist() to network_migration.py",
        "description": (
            "File: tools/migration_canvas/network_migration.py\n\n"
            "recommend_hardware(device_info: dict, engineer_notes: str, session_id: str) -> dict:\n"
            "  - Fetch nc_hardware_profiles WHERE device_type=device_info['device_type'] "
            "AND (eol_date IS NULL OR eol_date > date('now')) ORDER BY throughput_gbps DESC LIMIT 20\n"
            "  - Call LLMRouter().invoke('recommendation', LLMRequest(messages=[...], system_prompt='...', "
            "effort='high')) with device spec + engineer_notes + catalog JSON\n"
            "  - Ask for top 3 picks: profile_id, rationale, migration_considerations, risk_level, score(0-100)\n"
            "  - Deterministic fallback (LLMUnavailableError): rank by |throughput delta| + port count parity\n"
            "  - Save AI response to mc_net_ai_sessions (role='assistant')\n"
            "  - Return {recommendations: [...], model: str}\n\n"
            "ai_assist(session_id: str, engineer_prompt: str) -> dict:\n"
            "  - Pull last 5 turns from mc_net_ai_sessions ORDER BY created_at DESC\n"
            "  - Build system prompt with session state (src/tgt model, vendor, protocols from parsed config)\n"
            "  - Call LLMRouter with conversation history + new user prompt\n"
            "  - Save engineer + assistant turns to mc_net_ai_sessions\n"
            "  - Return {response: str, model: str}; degrade gracefully on LLMUnavailableError"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-fn-01",
    },
    {
        "id": "nmce-fn-03",
        "title": "NMCE: Add plan_protocol_migration() + protocol helpers to network_migration.py",
        "description": (
            "File: tools/migration_canvas/network_migration.py\n\n"
            "plan_protocol_migration(session_id: str) -> dict:\n"
            "  - Return error dict if src_config_raw is empty\n"
            "  - Call parse_source_config(src_config_raw) to extract: bgp_neighbors, ospf_areas, "
            "isis_nets, mpls_interfaces, ldp_interfaces, l3vpn_vrfs, l2vpn_instances, lag_count\n"
            "  - Detect tgt_vendor from nc_hardware_profiles WHERE model=sess.tgt_model\n"
            "  - Call per-protocol helpers (only for detected protocols):\n"
            "    _bgp_plan(parsed, src_vendor, tgt_vendor): peer-by-peer steps, AS notation, "
            "community remapping, route-policy syntax differences\n"
            "    _ospf_plan(parsed, src_vendor, tgt_vendor): area migration, passive adjacency, auth\n"
            "    _vlan_plan(parsed, src_vendor, tgt_vendor): trunk renegotiation, DTP disable on JunOS\n"
            "    _lag_plan(parsed, src_vendor, tgt_vendor): ae/po rename, LACP mode check\n"
            "    _mpls_plan(parsed, src_vendor, tgt_vendor): LDP/RSVP carryover steps\n"
            "    _acl_plan(parsed, src_vendor, tgt_vendor): ACL syntax translation notes\n"
            "  - UPSERT into mc_net_protocol_plans (INSERT OR REPLACE) for each protocol\n"
            "  - Return {protocols: {name: {steps, risk_level, count_info, ai_notes}}}"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-fn-02",
    },
    {
        "id": "nmce-fn-04",
        "title": "NMCE: Add build_parallel_timeline() to network_migration.py",
        "description": (
            "File: tools/migration_canvas/network_migration.py\n\n"
            "build_parallel_timeline(session_id: str) -> list[dict]:\n"
            "  - Read mc_net_sessions, mc_net_port_map (optic_change flags), "
            "mc_net_compat_checks (blocker count), parsed config (has_bgp, has_ospf, has_mpls)\n"
            "  - Generate base milestones (days_before_cutover: −30 to +30) with phases:\n"
            "    pre_migration: order hardware, rack/stack, initial config, stakeholder notif\n"
            "    parallel_run: load production config, establish passive routing adjacencies, "
            "dual-home test traffic\n"
            "    cutover: drain traffic (BGP MED/OSPF cost), cut interfaces, verify, go/no-go\n"
            "    post_migration: 24h monitor, update NMS/CMDB, close change\n"
            "    decommission: remove cables, configs, monitoring at +30 days\n"
            "  - Add conditional milestones: optic order if optic_change, blocker resolution "
            "if blockers>0, BGP passive session if has_bgp, OSPF passive adj if has_ospf, "
            "LDP adjacency if has_mpls\n"
            "  - DELETE existing mc_net_parallel_timelines for session, INSERT new set\n"
            "  - Return sorted list by days_before_cutover"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-fn-03",
    },

    # ── Phase 3: API Routes ─────────────────────────────────────────────────
    {
        "id": "nmce-api-01",
        "title": "NMCE: Add GET /network-migration/ page route + GET inventory API to blueprint.py",
        "description": (
            "File: tools/migration_canvas/blueprint.py\n\n"
            "Inside create_migration_blueprint(), add after existing /api/network-migration/<sid>/readiness:\n\n"
            "GET /network-migration/ (page):\n"
            "  - Call _nm.get_network_inventory() for all devices (no filters)\n"
            "  - Compute stats: total, eol_1yr_count, eol_2yr_count\n"
            "  - Query mc_net_sessions WHERE status!='complete' for active_count\n"
            "  - render_template('migration_canvas/network_inventory.html', ...)\n\n"
            "GET /api/network-migration/inventory:\n"
            "  - Accept ?site=&device_type=&vendor=&eol_within_years= from request.args\n"
            "  - Call _nm.get_network_inventory(site, device_type, vendor, eol_within_years)\n"
            "  - Return jsonify({devices: [...], total: int, eol_soon_count: int})\n\n"
            "Note: /network-migration/ page route MUST be placed BEFORE /network-migration/<session_id> "
            "in the blueprint to avoid the literal 'new' or empty string being matched as session_id."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-fn-04",
    },
    {
        "id": "nmce-api-02",
        "title": "NMCE: Enhance POST /api/network-migration session creation + add upload-config route",
        "description": (
            "File: tools/migration_canvas/blueprint.py\n\n"
            "Enhance existing POST /api/network-migration:\n"
            "  - If body has device_id: call _nm.load_device_config_from_db(device_id)\n"
            "  - If config found: set src_config_raw, config_parsed=1, "
            "run parse_source_config(), add config_source column (ALTER IF NOT EXISTS)\n"
            "  - If body has src_model omitted: query ni_devices.model from network_canvas.db\n"
            "  - Return includes config_auto_loaded: bool, parsed_summary: {vendor, hostname, "
            "interface_count, bgp_peer_count, ospf_area_count}\n\n"
            "New route POST /api/network-migration/<sid>/upload-config:\n"
            "  - Accept multipart/form-data with 'file' field (.txt/.conf/.cfg/.log)\n"
            "  - Also accept JSON {config_text: str, source: 'upload'|'paste'|'db'}\n"
            "  - For source='db': call _nm.load_device_config_from_db(data.get('device_id'))\n"
            "  - Run parse_source_config(), update mc_net_sessions.src_config_raw + config_parsed=1\n"
            "  - If session has device_id link: upsert row in ni_device_configs "
            "(source='manual_upload') to keep DB in sync\n"
            "  - Return same parse result structure as /parse-config; "
            "existing /parse-config delegates here internally"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-api-01",
    },
    {
        "id": "nmce-api-03",
        "title": "NMCE: Add /ai-recommend, /ai-assist, /protocol-plan, /parallel-timeline routes to blueprint.py",
        "description": (
            "File: tools/migration_canvas/blueprint.py\n\n"
            "POST /api/network-migration/<sid>/ai-recommend:\n"
            "  - Body: {device_info: {...}, engineer_notes: str}\n"
            "  - Call _nm.recommend_hardware(device_info, engineer_notes, sid)\n"
            "  - Return jsonify({recommendations: [...]})\n\n"
            "POST /api/network-migration/<sid>/ai-assist:\n"
            "  - Body: {prompt: str}\n"
            "  - Call _nm.ai_assist(sid, prompt)\n"
            "  - Return jsonify({response: str, model: str})\n\n"
            "GET /api/network-migration/<sid>/protocol-plan:\n"
            "  - Read mc_net_protocol_plans WHERE session_id=sid\n"
            "  - Return jsonify({protocols: {name: row_dict}})\n\n"
            "POST /api/network-migration/<sid>/protocol-plan:\n"
            "  - Call _nm.plan_protocol_migration(sid)\n"
            "  - Return jsonify({protocols: {...}})\n\n"
            "GET /api/network-migration/<sid>/parallel-timeline:\n"
            "  - Read mc_net_parallel_timelines WHERE session_id=sid ORDER BY days_before_cutover\n"
            "  - Return jsonify({milestones: [...]})\n\n"
            "POST /api/network-migration/<sid>/parallel-timeline:\n"
            "  - Call _nm.build_parallel_timeline(sid)\n"
            "  - Return jsonify({milestones: [...], count: int})"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-api-02",
    },

    # ── Phase 4: Templates ──────────────────────────────────────────────────
    {
        "id": "nmce-tpl-01",
        "title": "NMCE: Create network_inventory.html — inventory dashboard template",
        "description": (
            "File: tools/dashboard/templates/migration_canvas/network_inventory.html\n\n"
            "Extends base.html. CUI // SP-CTI marking in Jinja comment.\n\n"
            "Layout:\n"
            "- Page header: 'Network Infrastructure Migration' + [+ New Migration] button (opens wizard)\n"
            "- Stats bar: Total Devices | EOL ≤1yr (red) | EOL ≤2yr (orange) | Active Migrations\n"
            "- Filter row: [Device Type ▼] [Vendor ▼] [Site ▼] [EOL Status ▼] [Search input]\n"
            "- Inventory table columns: Hostname | Vendor/Model | Type | Site | EOL Date | Config | Actions\n"
            "- Config chip: green 'DB ✓' if has_config, gray 'Upload Reqd' if not\n"
            "- EOL chip: red if ≤12mo, orange if ≤24mo, green if >24mo\n"
            "- Actions: [→ Migrate] button + optional [Active: sid ↗] link\n\n"
            "JS:\n"
            "  loadInventory() on DOMContentLoaded → GET /migration-canvas/api/network-migration/inventory\n"
            "  applyFilters() on filter change → re-renders table client-side\n"
            "  startMigration(deviceId, model, label): POST /migration-canvas/api/network-migration "
            "with {device_id, src_model, src_device_name} → redirect to /migration-canvas/network-migration/<sid>\n"
            "  computeEolStatus(eolDate): returns 'urgent'|'soon'|'ok'|'unknown'"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-api-03",
    },
    {
        "id": "nmce-tpl-02",
        "title": "NMCE: Mirror network_inventory.html to icdev/ package",
        "description": (
            "File: icdev/tools/dashboard/templates/migration_canvas/network_inventory.html\n\n"
            "Copy network_inventory.html verbatim from "
            "tools/dashboard/templates/migration_canvas/network_inventory.html "
            "to icdev/tools/dashboard/templates/migration_canvas/network_inventory.html.\n\n"
            "This mirror is required for the icdev PyPI package distribution "
            "(the icdev/ tree is shipped via pip install icdev). "
            "The companion sync will keep it in sync going forward, "
            "but the initial copy must be done here."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-tpl-01",
    },
    {
        "id": "nmce-wiz-01",
        "title": "NMCE: Add Steps 7 (Protocol Plan) + 8 (Parallel Timeline) to network_wizard.html",
        "description": (
            "File: tools/dashboard/templates/migration_canvas/network_wizard.html\n\n"
            "Sidebar: Add two step links after existing step 6:\n"
            "  <div class='nw-step-link' data-step='7' onclick='goStep(7)'>"
            "<span class='step-num'>7</span>Protocol Plan</div>\n"
            "  <div class='nw-step-link' data-step='8' onclick='goStep(8)'>"
            "<span class='step-num'>8</span>Parallel Timeline</div>\n\n"
            "Step 7 page div (id='step7'):\n"
            "- Empty state if no config: 'Import device config first (Step 3)'\n"
            "- [Generate Protocol Plan] button → POST /api/network-migration/<sid>/protocol-plan\n"
            "- Render per-protocol collapsible accordion: BGP, OSPF, ISIS, VLAN, LAG, MPLS, ACL\n"
            "  Each card: protocol name + risk badge (low/medium/high/critical) + step count\n"
            "  Expanded: ordered steps list with checkbox, peer/area count, ai_notes italic\n\n"
            "Step 8 page div (id='step8'):\n"
            "- Cutover date picker (date input)\n"
            "- [Generate Timeline] → POST /api/network-migration/<sid>/parallel-timeline\n"
            "- Gantt visualization (pure CSS horizontal bars, no libs):\n"
            "  X-axis: day offsets −30 to +30; Y-axis: milestone names\n"
            "  Phase color bands: pre_migration=blue, parallel_run=teal, cutover=orange, "
            "post_migration=green, decommission=gray\n"
            "- Inline status select (planned/in_progress/complete/blocked) per milestone"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-tpl-02",
    },
    {
        "id": "nmce-wiz-02",
        "title": "NMCE: Add persistent AI Assistant panel to network_wizard.html",
        "description": (
            "File: tools/dashboard/templates/migration_canvas/network_wizard.html\n\n"
            "Add a fixed right-side AI panel after the .nw-main div:\n\n"
            "CSS:\n"
            "  #ai-panel { position: fixed; right: 0; top: 56px; width: 320px; "
            "height: calc(100vh - 56px); background: #0a1624; border-left: 1px solid #1e3a6e; "
            "display: flex; flex-direction: column; transition: transform 0.2s; }\n"
            "  #ai-panel.collapsed { transform: translateX(296px); }\n"
            "  .nw-main { margin-right: 320px; } /* offset for panel */\n\n"
            "HTML:\n"
            "  .ai-panel-header: 'AI Migration Assistant' label + collapse toggle button\n"
            "  #ai-history: scrollable message history div (engineer=right-aligned, assistant=left)\n"
            "  .ai-input-row: textarea + [Send] button\n\n"
            "JS:\n"
            "  toggleAI(): toggle #ai-panel.collapsed class\n"
            "  sendAI(): POST /migration-canvas/api/network-migration/<sid>/ai-assist "
            "with {prompt: textarea.value}; append both turns to #ai-history; scroll to bottom; clear input\n"
            "  Show loading spinner while waiting for AI response\n"
            "  sid is available from the template variable {{ session_id }}"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-wiz-01",
    },
    {
        "id": "nmce-wiz-03",
        "title": "NMCE: Update Step 1 with AI Recommendation UI + config auto-load banner",
        "description": (
            "File: tools/dashboard/templates/migration_canvas/network_wizard.html\n\n"
            "Config auto-load banner (shown in Step 1 when session has config_auto_loaded=true):\n"
            "  Green banner: '✓ Config loaded from database | Vendor: <vendor> | <N> interfaces | "
            "<N> BGP peers' + [Override with upload ↑] link that jumps to Step 3\n"
            "  Yellow banner if no config: 'No config on file — import below (Step 3)'\n"
            "  Load banner data from session response or GET /api/network-migration/<sid> on init\n\n"
            "AI Recommendation section (after target device dropdown):\n"
            "  [Engineer notes / constraints] textarea (optional)\n"
            "  [AI Recommend] button → POST /api/network-migration/<sid>/ai-recommend "
            "with {device_info: {from session src_model hw profile}, engineer_notes}\n"
            "  Result: 3 recommendation cards in a grid:\n"
            "    Card: vendor/model, score badge, risk level badge, rationale text, "
            "migration_considerations collapsed, [Select] button\n"
            "  [Select] button: fills tgt_model dropdown with that profile_id\n"
            "  Loading state: spinner replacing cards while waiting"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-wiz-02",
    },
    {
        "id": "nmce-wiz-04",
        "title": "NMCE: Update Step 3 with 3-tab config source UI (DB / Upload / Paste)",
        "description": (
            "File: tools/dashboard/templates/migration_canvas/network_wizard.html\n\n"
            "Replace the existing single textarea in Step 3 with a tabbed config source UI:\n\n"
            "Tab bar: [✓ From Database] [↑ Upload File] [✎ Paste Text]\n\n"
            "From Database tab (default if config_auto_loaded=true):\n"
            "  Show: 'Source: ni_device_configs | Collected: <date>'\n"
            "  [Reload from DB] → POST /upload-config with {source:'db', device_id:...}\n"
            "  [View Raw Config ▾] collapsible monospace block\n"
            "  Parse summary: vendor, hostname, interface count, protocol counts\n\n"
            "Upload File tab (default if no DB config):\n"
            "  Drag-and-drop zone + [Choose File] input (accept=.txt,.conf,.cfg,.log)\n"
            "  On file select: read as text, POST /upload-config (multipart or JSON)\n"
            "  Show parse summary after successful upload\n\n"
            "Paste Text tab:\n"
            "  Existing textarea behavior → POST /upload-config with {config_text, source:'paste'}\n\n"
            "After any config load: update banner in Step 1 with new parse summary; "
            "trigger readiness score refresh"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-wiz-03",
    },

    # ── Phase 5: V&V ────────────────────────────────────────────────────────
    {
        "id": "nmce-vv-01",
        "title": "NMCE: Companion sync + V&V — Playwright screenshot of inventory + wizard",
        "description": (
            "Run post-implementation validation:\n\n"
            "1. python tools/dx/companion.py --sync --write --json\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "3. Navigate to http://localhost:5050/migration-canvas/network-migration/\n"
            "   - Verify inventory table renders with device list from ni_devices\n"
            "   - Verify EOL chips, Config DB/upload chips display correctly\n"
            "4. Click '→ Migrate' on a device with DB config:\n"
            "   - Verify wizard opens with config auto-load banner\n"
            "   - Verify AI Recommend shows 3 cards\n"
            "5. Step 3: verify 3 tabs render; upload a .conf file and check parse summary\n"
            "6. Step 7: click Generate Protocol Plan → verify accordion renders\n"
            "7. Step 8: click Generate Timeline → verify Gantt renders\n"
            "8. AI panel: send 'How do I drain BGP before cutover?' → verify response\n"
            "9. Take Playwright screenshot → playwright/screenshots/migration-canvas-network-inventory.png\n"
            "10. Take Playwright screenshot → playwright/screenshots/migration-canvas-wizard-enhanced.png"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "nmce-wiz-04",
    },
]


def seed():
    conn = _conn()
    now = _NOW

    inserted = 0
    skipped = 0
    for t in TASKS:
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id=?", (t["id"],)
        ).fetchone()
        if existing:
            skipped += 1
            print(f"  SKIP (exists): {t['id']}")
            continue

        conn.execute(
            """INSERT INTO kanban_tasks
               (id, title, description, task_type, priority, status,
                scheduled_at, created_at, updated_at, depends_on_task_id,
                dispatch_source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t["id"],
                t["title"],
                t["description"],
                t.get("task_type", "build"),
                t.get("priority", "high"),
                t["status"],
                t.get("scheduled_at"),
                now,
                now,
                t.get("depends_on_task_id"),
                "seed:nmce",
            ),
        )
        inserted += 1
        print(f"  INSERTED: {t['id']} [{t['status']}] dep={t.get('depends_on_task_id')}")

    conn.commit()
    conn.close()
    print(f"\nDone — {inserted} inserted, {skipped} skipped.")
    print(f"First task 'nmce-db-01' is status=scheduled, scheduled_at={_NOW}")


if __name__ == "__main__":
    seed()
