# NMCE — Network Migration Canvas Enhancement

**Date:** 2026-05-08  
**Classification:** CUI // SP-CTI  
**Feature flag:** `ICDEV_MIGRATION_CANVAS_ENABLED=true`

## Summary

Extends the existing 8-step network device migration wizard at `/migration-canvas/` with five new capabilities:

1. **Inventory-driven entry point** — `/migration-canvas/network-migration/` lists all devices from `ni_devices` with EOL chips, config-source status, and active migration links. Click "→ Migrate" to create a session with `src_model` pre-filled.
2. **AI hardware recommendation** — "AI Recommend" button in Step 1 calls `recommend_hardware()` which ranks `nc_hardware_profiles` targets via LLM (deterministic fallback: closest throughput match).
3. **Per-protocol migration plan** — Step 9 generates BGP/OSPF/VLAN/LAG/MPLS/ACL migration steps with risk levels from the parsed config. Upserts `mc_net_protocol_plans`.
4. **Parallel operation timeline** — Step 10 generates 15–20 conditional milestones (D-30 to D+30) as a color-coded Gantt chart. Writes `mc_net_parallel_timelines`.
5. **Always-on AI assistant** — Fixed right panel (`#ai-panel`) for free-form migration guidance, backed by `mc_net_ai_sessions` audit trail (NIST AU).

## New Files

| File | Purpose |
|------|---------|
| `tools/dashboard/templates/migration_canvas/network_inventory.html` | Inventory dashboard template |
| `icdev/tools/dashboard/templates/migration_canvas/network_inventory.html` | Mirror to icdev package |

## Modified Files

| File | Change |
|------|--------|
| `tools/migration_canvas/db/init_db.py` | +3 tables: `mc_net_ai_sessions`, `mc_net_protocol_plans`, `mc_net_parallel_timelines` |
| `tools/migration_canvas/network_migration.py` | +6 public functions + 6 protocol helpers (~640 lines) |
| `tools/migration_canvas/blueprint.py` | +12 routes: inventory, inventory API, create-from-inventory, upload-config, ai-recommend, ai-assist, protocol-plan (GET/POST), parallel-timeline (GET/POST) |
| `tools/dashboard/templates/migration_canvas/network_wizard.html` | Steps 9+10 added; AI panel; `showToast` shim; Config auto-load banner |

## DB Tables (migration_canvas.db — self-managed)

| Table | Type | Purpose |
|-------|------|---------|
| `mc_net_ai_sessions` | Append-only (NIST AU) | AI conversation audit trail per session |
| `mc_net_protocol_plans` | Mutable (UNIQUE session+protocol) | Per-protocol migration steps, risk level, AI notes |
| `mc_net_parallel_timelines` | Mutable (delete+insert) | Parallel operation milestones relative to cutover |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/migration-canvas/network-migration/` | Inventory dashboard page |
| GET | `/migration-canvas/api/network-migration/inventory` | Device list JSON with filters |
| POST | `/migration-canvas/api/network-migration/create-from-inventory` | Create session from inventory click |
| POST | `/migration-canvas/api/network-migration/<sid>/upload-config` | Upload/paste/reload device config |
| POST | `/migration-canvas/api/network-migration/<sid>/ai-recommend` | AI hardware recommendation |
| POST | `/migration-canvas/api/network-migration/<sid>/ai-assist` | AI chat (engineer prompt → response) |
| GET/POST | `/migration-canvas/api/network-migration/<sid>/protocol-plan` | Per-protocol migration plan |
| GET/POST | `/migration-canvas/api/network-migration/<sid>/parallel-timeline` | Parallel operation milestones |

## Reused Infrastructure

- `ni_devices` / `ni_device_configs` — network_canvas.db live inventory + ingested configs
- `nc_hardware_profiles` — 15-entry standard hardware catalog
- `parse_source_config()` — existing vendor-agnostic config parser
- `LLMRouter` — existing multi-provider LLM router with `LLMUnavailableError` fallback
- `mdc_login_required` / `_audit()` — existing auth and audit helpers
