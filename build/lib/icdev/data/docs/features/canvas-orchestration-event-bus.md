# Canvas Orchestration Event Bus

**Classification:** CUI // SP-CTI
**Sprint:** Canvas Orchestration (CVO)
**Status:** Shipped — V&V gate passed 2026-04-25

---

## Overview

The Canvas Orchestration Event Bus is a cross-canvas publish/subscribe system that enables the 7 ICDEV™ design canvases (NDC, SDC, PDC, BDC, DDC, ODC, IDC) to communicate state changes without tight coupling. Events are persisted to `canvas_events` (append-only, NIST AU-compliant) and dispatched both in-process and on next poll.

---

## Architecture

### Core Module

**`tools/canvas/event_bus.py`** — three public functions:

| Function | Signature | Description |
|---|---|---|
| `publish` | `(source, event_type, payload, *, target=None) → event_id` | Write event to DB + fire in-process listeners immediately |
| `subscribe` | `(canvas_id, event_type, handler_fn) → None` | Register in-process listener; use `"*"` as wildcard event type |
| `dispatch_pending` | `(canvas_id) → int` | Replay unconsumed events from DB for a canvas; marks rows consumed |

### Persistence

**Migration 039** (`tools/db/migrations/039_canvas_events/`) creates `canvas_events` in the main `icdev.db` / PostgreSQL:

```sql
canvas_events (
    id             TEXT PRIMARY KEY,   -- UUID
    source_canvas  TEXT NOT NULL,      -- "pdc", "bdc", etc.
    target_canvas  TEXT,               -- NULL = broadcast
    event_type     TEXT NOT NULL,      -- "pipeline_deployed", "isa_expiring_soon", …
    payload_json   TEXT NOT NULL,      -- JSON blob
    created_at     TIMESTAMPTZ,
    consumed_at    TIMESTAMPTZ         -- NULL = pending
)
```

Indexes on `source_canvas`, `target_canvas`, `event_type`, `consumed_at`.
Rows are **append-only** — only `consumed_at` may be updated post-insert.

---

## Wired Event Flows

### PDC → SDC: `pipeline_deployed`

**Publisher:** `tools/pipeline/blueprint.py` — fires on successful pipeline run completion.

**Subscriber:** `tools/security_canvas/bus_subscriber.py` (`register()`)

Handler actions:
1. Marks `sc_threats.is_stale = 1` for all designs linked to the deployed pipeline (via `sc_assessments.source_entity_id`).
2. Upserts `genesis_reflex_state` to advance the `audit` reflex `next_run_at` to now, triggering an immediate Genesis audit cycle.

### BDC: `isa_expiring_soon`

**Publisher:** `tools/boundary_canvas/isa_expiry.py` — ISA expiry check job (runs periodically).

Fires for each ISA expiring within 90 days. Payload includes `isa_id`, `isa_name`, `expires_at`, `days_remaining`, `partner_org`.

---

## Compliance Dashboard

Route `/canvas/compliance` (`tools/canvas/blueprint.py`) renders a 7-card compliance posture view — one card per canvas — backed by live `canvas_events` data. The page displays recent events, unresolved stale markers, and ISA expiry warnings.

---

## V&V Results

| Gate | Result |
|---|---|
| Coherence checker (`--all --fix --gate`) | **PASS** — 17/17 checks |
| `tests/e2e_canvas_orchestration.py` | **PASS** — 5/5 tests |
| `tests/e2e_ndc_sops.py` (regression) | **PASS** — 9/9 tests |
| Companion sync (10 platforms) | **PASS** — 63 skills synced |

---

## Usage

```python
from tools.canvas.event_bus import publish, subscribe, dispatch_pending

# Publish an event
event_id = publish("pdc", "pipeline_deployed", {"pipeline_id": "pipe-abc123"}, target="sdc")

# Subscribe (call once at app startup)
subscribe("sdc", "pipeline_deployed", my_handler)

# Replay unconsumed events (e.g. at canvas boot)
fired = dispatch_pending("sdc")
```

```bash
# Run the ISA expiry check (publishes isa_expiring_soon events)
python tools/boundary_canvas/isa_expiry.py

# Apply migration
python tools/db/migrations/039_canvas_events/up.py
```

---

## Related

- `tools/canvas/event_bus.py` — bus implementation
- `tools/security_canvas/bus_subscriber.py` — PDC→SDC subscriber
- `tools/boundary_canvas/isa_expiry.py` — BDC expiry publisher
- `tools/db/migrations/039_canvas_events/` — DB migration
- `tests/e2e_canvas_orchestration.py` — E2E test suite
- `tests/test_bdc_isa_expiry.py` — unit tests
