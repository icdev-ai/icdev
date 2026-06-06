# NOC Operations Canvas (NOCC) — Tool Manifest

Canvas key: `nocc` | Feature flag: `ICDEV_NOCC_ENABLED` | DB: `data/noc_canvas.db` (PostgreSQL default)

## Modules

| Module | Path | Functions |
|--------|------|-----------|
| DB init | `tools/noc_canvas/db/init_db.py` | `init_db()`, `get_connection()` |
| Constants | `tools/noc_canvas/constants.py` | `INCIDENT_SEVERITIES`, `INTENT_RULES`, `ALARM_STORM_THRESHOLD` |
| Alarm Correlator | `tools/noc_canvas/alarm_correlator.py` | `correlate_alarms()`, `get_active_alarms()`, `get_alarm_summary()`, `create_alarm()`, `acknowledge_alarm()` |
| SLA Predictor | `tools/noc_canvas/sla_predictor.py` | `compute_sla_burn_rate()`, `predict_breach()`, `get_sla_dashboard()` |
| MOP Generator | `tools/noc_canvas/mop_generator.py` | `generate_mop()`, `validate_mop_steps()`, `mop_to_markdown()`, `save_mop()` |
| Maintenance Planner | `tools/noc_canvas/maintenance_planner.py` | `get_upcoming_windows()`, `check_window_conflicts()`, `create_window()`, `notify_customers()` |
| NOC Aggregator | `tools/noc_canvas/noc_aggregator.py` | `get_noc_overview()` |
| Blueprint | `tools/noc_canvas/blueprint.py` | `create_noc_canvas_blueprint()` |
| IQE Adapter | `tools/iqe/adapters/nocc.py` | 6 collections: `noc.alarms`, `noc.incidents`, `noc.rfcs`, `noc.mops`, `noc.maintenance_windows`, `noc.sla_records` |

## DB Tables

- `noc_incidents` — P1-P4 incidents with SLA breach tracking
- `noc_alarms` — NMS-ingested alarms with correlation to incidents
- `noc_rfcs` — Change requests (emergency/standard/normal)
- `noc_mops` — Methods of Procedure (manual or AI-generated)
- `noc_maintenance_windows` — Scheduled maintenance with conflict detection
- `noc_sla_records` — Per-circuit SLA tracking and breach accounting
- `noc_audit` — Append-only audit trail (NIST AU)

## Routes

```
GET  /noc                    — overview dashboard
GET  /noc/alarms             — alarm management
GET  /noc/incidents          — incident lifecycle
GET  /noc/rfcs               — RFC change requests
GET  /noc/mops               — MOP library
GET  /noc/maintenance        — maintenance windows
GET  /noc/sla                — SLA burn rate dashboard
POST /api/noc/alarms         — ingest alarm
POST /api/noc/incidents      — create incident
POST /api/noc/mops/generate  — AI-generate MOP
POST /api/noc/iqe-query      — IQE NL query
```

## IQE Seed Queries

Located at `context/iqe/queries/noc_canvas/`:
- `01_active_critical_alarms.iqe`
- `02_open_p1_incidents.iqe`
- `03_sla_breached_circuits.iqe`
- `04_upcoming_maintenance.iqe`
- `05_pending_rfcs.iqe`
