# Research: Network Canvas — Live Telemetry Overlay

**Task ID:** task-44a0ec419b
**Tier:** 4 (Enhancement)
**Priority:** Low
**Date:** 2026-03-28

---

## Summary

Link SNMP/gNMI streaming telemetry to canvas nodes for real-time device status (up/down) and link utilization (%). Color-code links by utilization band and show device health via WebSocket. Turns the static design canvas into a live operational dashboard.

---

## Current State Analysis

### What Already Exists

| Component | Location | Status |
|-----------|----------|--------|
| SNMP v2c/v3 discovery | `tools/network/discovery.py` | Pull-based, one-shot — not streaming |
| IF-MIB OIDs (ifOperStatus, ifSpeed, ifDescr) | `discovery.py:64–113` | Used for topology discovery only |
| Flask-SocketIO WebSocket layer | `tools/dashboard/websocket.py` | Initialized; `broadcast_activity()` available |
| JointJS canvas renderer | `static/js/network-canvas.js` | 3,124 lines; `NODE_STYLES` + link attrs |
| Load heatmap (simulation) | `static/js/network-simulation.js:applyLoadHeatmap()` | Simulation-only; not live |
| Utilization display | `network-canvas.js:selectCell()` | Shows static property values only |
| Network blueprint routes | `tools/network/blueprint.py` | 80+ endpoints; no telemetry stream endpoint |
| Network DB init | `tools/network/db/init_db.py` | Has notifications, audit; no telemetry table |

### Gaps

- **No streaming telemetry**: SNMP is one-shot request-response; no background polling daemon
- **No gNMI client**: `pygnmi` library not integrated
- **No telemetry DB table**: No schema for storing counter deltas or utilization history
- **No live canvas update**: WebSocket events are `activity_event` only; no `telemetry_update` event
- **No telemetry config UI**: No way to configure polling targets/intervals per topology

---

## Proposed Architecture

### 1. Background SNMP Polling Daemon

**File:** `tools/network/telemetry_poller.py`

Runs as a per-topology background thread (started when user enables Live Telemetry in the canvas toolbar). Polls on a configurable interval (default 30s).

**SNMP counters to poll:**
```
IF-MIB::ifOperStatus.{ifIndex}       → 1=up, 2=down, 3=testing
IF-MIB::ifInOctets.{ifIndex}         → bytes in (64-bit: ifHCInOctets preferred)
IF-MIB::ifOutOctets.{ifIndex}        → bytes out (64-bit: ifHCOutOctets preferred)
IF-MIB::ifSpeed.{ifIndex}            → interface capacity in bps
IF-MIB::ifAlias.{ifIndex}            → user-defined interface label
```

**Utilization calculation:**
```
delta_in  = ifHCInOctets_t1 - ifHCInOctets_t0
delta_out = ifHCOutOctets_t1 - ifHCOutOctets_t0
interval_bits = (delta_in + delta_out) * 8
util_pct = (interval_bits / (ifSpeed * poll_interval)) * 100
```

**Node status:**
- Device up = at least one interface `ifOperStatus == 1`
- Device down = all interfaces down OR SNMP timeout

**Key functions:**
```python
def start_poller(topo_id: str, nodes: list, community: str, interval: int) -> str:
    """Start background polling thread. Returns poller_id."""

def stop_poller(poller_id: str) -> None:
    """Stop and clean up polling thread."""

def get_poller_status(poller_id: str) -> dict:
    """Return thread health + last poll timestamp."""
```

### 2. gNMI Streaming Client (Optional — Modern OS)

**File:** `tools/network/gnmi_client.py`
**Library:** `pygnmi` (pure Python gRPC; `pip install pygnmi`)

More efficient than polling for network OS that support gNMI (Cisco IOS-XR 6.x+, Arista EOS 4.20+, Juniper Junos 18.1+, Nokia SR OS).

**Subscription XPaths:**
```
/interfaces/interface/state/counters/in-octets
/interfaces/interface/state/counters/out-octets
/interfaces/interface/state/oper-status
/components/component/state/temperature/instant  (device health)
```

**Subscription mode:** `STREAM` with `SAMPLE` at 10s interval (or `ON_CHANGE` for status).

**Auth:** TLS mutual auth (certificates) or username/password over TLS. Requires self-signed cert support for lab environments.

**Air-gap note:** `grpcio` and `protobuf` wheels must be pre-staged. `pygnmi` has no external runtime dependencies beyond gRPC.

**Key functions:**
```python
def subscribe_gnmi(target: str, port: int, paths: list,
                   username: str, password: str,
                   cert_path: str | None,
                   callback: Callable) -> None:
    """Open gNMI STREAM subscription; call callback(update_dict) per notification."""
```

### 3. WebSocket Telemetry Events

**Extension to:** `tools/dashboard/websocket.py`

New event type: `telemetry_update` (separate from existing `activity_event`).

**New function:**
```python
def broadcast_telemetry(topo_id: str, updates: list[dict]) -> None:
    """Emit telemetry_update to topology-specific room."""
    if _socketio:
        _socketio.emit("telemetry_update", {
            "topo_id": topo_id,
            "ts": int(time.time()),
            "updates": updates   # list of {node_id, status, util_pct, interfaces}
        }, room=f"topo_{topo_id}")
```

**Room strategy:** Clients join `topo_{topo_id}` on canvas load. Only nodes in the active topology receive updates.

### 4. New Blueprint Endpoints

**In:** `tools/network/blueprint.py`

```python
POST /api/telemetry/<topo_id>/start
    body: {community, interval, method: "snmp"|"gnmi", gnmi_port, username, password}
    → starts poller, returns {poller_id}

POST /api/telemetry/<topo_id>/stop
    body: {poller_id}
    → stops poller

GET  /api/telemetry/<topo_id>/status
    → {running, poller_id, last_poll_ts, node_count}

GET  /api/telemetry/<topo_id>/history?node_id=X&limit=100
    → last N utilization samples for sparklines
```

### 5. Database Schema

**New table in network_canvas.db** (add to `tools/network/db/init_db.py`):

```sql
CREATE TABLE IF NOT EXISTS network_telemetry (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    topology_id      TEXT    NOT NULL,
    node_id          TEXT    NOT NULL,
    interface_name   TEXT    DEFAULT '',
    polled_at        INTEGER NOT NULL,          -- Unix timestamp
    oper_status      INTEGER DEFAULT 1,         -- 1=up, 2=down
    in_octets_delta  INTEGER DEFAULT 0,
    out_octets_delta INTEGER DEFAULT 0,
    capacity_bps     INTEGER DEFAULT 0,
    utilization_pct  REAL    DEFAULT 0.0,
    source           TEXT    DEFAULT 'snmp'     -- 'snmp' | 'gnmi'
);

CREATE INDEX IF NOT EXISTS idx_net_telem_topo
    ON network_telemetry(topology_id, polled_at DESC);

CREATE INDEX IF NOT EXISTS idx_net_telem_node
    ON network_telemetry(node_id, polled_at DESC);
```

**Retention policy:** Auto-prune rows older than 24h in the poller thread (configurable via `args/network_config.yaml`).

### 6. Canvas Frontend Overlay

**Extension to:** `static/js/network-canvas.js`

**Color bands for link utilization:**

| Utilization | Link Color | Label |
|-------------|------------|-------|
| 0% (down)   | `#888888` (gray) | DOWN |
| 0–50%       | `#00cc44` (green) | OK |
| 50–80%      | `#ffcc00` (yellow) | WARN |
| 80–95%      | `#ff6600` (orange) | HIGH |
| 95–100%     | `#ff2200` (red) | CRITICAL |

**Node status indicator:** Small colored ring around node icon (SVG overlay).

**New JS functions:**
```javascript
function initTelemetryOverlay(topoId) {
    // Connect to Socket.IO, join room topo_{topoId}
    // Listen for 'telemetry_update' events
    // Call applyTelemetryUpdate() per update
}

function applyTelemetryUpdate(updates) {
    updates.forEach(u => {
        const cell = graph.getCell(u.node_id);
        if (!cell) return;
        // Update node status ring
        cell.attr('body/stroke', u.status === 'up' ? '#00cc44' : '#888888');
        // Update connected links
        applyLinkUtilization(u.node_id, u.util_pct);
        // Update tooltip
        cell.attr('label/text', formatTelemetryLabel(u));
    });
}

function applyLinkUtilization(nodeId, utilPct) {
    const links = graph.getConnectedLinks(graph.getCell(nodeId));
    links.forEach(link => {
        link.attr('line/stroke', utilToColor(utilPct));
        link.attr('line/strokeWidth', utilToWidth(utilPct));
        link.label(0, { attrs: { text: { text: utilPct.toFixed(1) + '%' }}});
    });
}

function utilToColor(pct) {
    if (pct === null) return '#888888';  // down/unknown
    if (pct < 50)   return '#00cc44';
    if (pct < 80)   return '#ffcc00';
    if (pct < 95)   return '#ff6600';
    return '#ff2200';
}
```

**Toolbar button:** Add "Live Telemetry" toggle button next to "Simulate" in canvas toolbar. Opens a config drawer for SNMP community / gNMI credentials.

**Fallback (no WebSocket):** HTTP long-poll via `GET /api/telemetry/<topo_id>/status` every 30s. Graceful degradation when Flask-SocketIO not installed.

---

## Implementation Phases

| Phase | Scope | Dependencies |
|-------|-------|-------------|
| **Phase A** (MVP) | SNMP poller + SQLite storage + REST polling fallback (no WebSocket) | `pysnmp` (already conditionally imported) |
| **Phase B** | WebSocket broadcast via Flask-SocketIO | `flask-socketio` (already in dashboard) |
| **Phase C** | Canvas overlay — color-coded links + node status rings | JointJS (already loaded) |
| **Phase D** | gNMI streaming client | `pygnmi`, `grpcio` (new deps) |
| **Phase E** | Telemetry config UI + history sparklines in config panel | Chart.js (already loaded) |

---

## Dependency Analysis

| Library | Purpose | Air-gap Risk | Existing? |
|---------|---------|-------------|-----------|
| `pysnmp` | SNMP polling | Low (pure Python) | Conditionally imported |
| `flask-socketio` | WebSocket push | Low | Already in dashboard |
| `pygnmi` | gNMI streaming | Medium (needs `grpcio`) | Not installed |
| `grpcio` | gRPC transport for gNMI | High (C extension, VRAM-agnostic) | Not installed |

**Air-gap recommendation:** Phases A–C have zero new dependencies. Phase D (`pygnmi`) requires pre-staging `grpcio` wheels — verify Python 3.14 compatibility before adding.

---

## Canvas Integration Points

- **`tools/network/blueprint.py`** — add 4 telemetry endpoints
- **`tools/network/db/init_db.py`** — add `network_telemetry` table
- **`tools/dashboard/websocket.py`** — add `broadcast_telemetry()` function
- **`static/js/network-canvas.js`** — add `initTelemetryOverlay()`, `applyTelemetryUpdate()`, toolbar button
- **`static/css/network-canvas.css`** — telemetry status ring + link color transitions
- **`templates/network/canvas.html`** — telemetry config drawer, Live Telemetry toolbar button

---

## Security Considerations

- SNMP community strings stored encrypted in `network_canvas.db` (not plaintext)
- gNMI credentials: use Flask session, never embed in JS
- Telemetry endpoints restricted to authenticated users (`@login_required`)
- Rate-limit poller start endpoint to prevent thread exhaustion (max 5 pollers/user)
- Canvas room isolation: SocketIO room per topology ID prevents cross-topology leakage

---

## Effort Estimate

| Phase | New Files | Modified Files | Complexity |
|-------|-----------|---------------|------------|
| A (SNMP poller + DB) | `telemetry_poller.py` | `init_db.py`, `blueprint.py` | Medium |
| B (WebSocket) | — | `websocket.py`, `blueprint.py` | Low |
| C (Canvas overlay) | — | `network-canvas.js`, `canvas.html`, `network-canvas.css` | Medium |
| D (gNMI) | `gnmi_client.py` | `telemetry_poller.py`, `blueprint.py` | High |
| E (Config UI + sparklines) | — | `canvas.html`, `network-canvas.js` | Medium |

**Total new code estimate:** ~800–1,200 lines Python + ~400 lines JS/CSS.
