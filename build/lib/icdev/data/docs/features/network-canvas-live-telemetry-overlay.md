# Network Canvas: Live Telemetry Overlay — Research Document

**Task ID:** task-44a0ec419b
**Type:** Research (Tier 4)
**Date:** 2026-03-27
**Classification:** CUI // SP-CTI

---

## 1. Objective

Link SNMP/gNMI streaming telemetry to network canvas nodes for real-time operational status (up/down, utilization %). Color-code links by utilization, show device health via WebSocket. Transform the canvas from a design tool into a live operational dashboard.

---

## 2. Current State Analysis

### 2.1 Canvas Infrastructure (Ready)
- **Renderer:** JointJS graph + paper (`network-canvas.js`)
- **180+ node types** with SVG icons, drag-drop palette, undo/redo
- **Heatmap overlay already exists** (`network-simulation.js:1422-1530`):
  - 5-stop color gradient (green → yellow → orange → red → dark-red)
  - Metrics: bandwidth, vulnerability, STIG compliance, equipment age
  - Saves/restores original node & link styles
  - API: `GET /api/heatmap/<topo_id>?metric=<metric>`
  - This is the **primary extension point** for live telemetry colors
- **Autosave:** 3-second debounce, HTTP fetch to blueprint API

### 2.2 WebSocket Infrastructure (Ready)
- **Flask-SocketIO** (`tools/dashboard/websocket.py`): threading async mode, room-based broadcast
- **SSE Manager** (`tools/dashboard/sse_manager.py`): client queues, 15s heartbeat
- **HTTP polling** (`tools/dashboard/api/events.py`): 3s poll interval (D103 decision — DoD proxy compat)
- Currently used for activity feed only — no canvas events

### 2.3 Network Backend (Partial)
- **NetBox integration** (`tools/network/netbox_client.py`): device inventory pull (REST API)
- **Simulation engine** (`tools/network/simulation.py`): pure-function BGP/OSPF/STP/failover — no real devices
- **Config generation**: Cisco IOS, Arista EOS, Juniper JunOS templates
- **No SNMP/gNMI collectors exist today**

### 2.4 Database
- Separate `data/network_canvas.db` with topology, node, link, simulation tables
- Extensible schema for adding telemetry time-series tables

---

## 3. Architecture Design

### 3.1 Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Browser (Canvas)                          │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ JointJS  │  │ Heatmap      │  │ Telemetry Overlay     │ │
│  │ Graph    │←─│ Engine       │←─│ (new) WebSocket recv  │ │
│  └──────────┘  │ (existing)   │  │ status badges, link   │ │
│                └──────────────┘  │ color, health ring     │ │
│                                  └───────────┬───────────┘ │
└──────────────────────────────────────────────┼─────────────┘
                                               │ WebSocket / HTTP poll
┌──────────────────────────────────────────────┼─────────────┐
│                 Flask Backend                 │             │
│  ┌───────────────┐  ┌────────────────────────▼──────────┐  │
│  │ blueprint.py   │  │ telemetry_broker.py (new)        │  │
│  │ /api/telemetry │  │ Aggregates poller + gNMI data    │  │
│  │ endpoints      │  │ Broadcasts via SocketIO room     │  │
│  └───────────────┘  └──────┬────────────┬───────────────┘  │
└─────────────────────────────┼────────────┼─────────────────┘
                              │            │
┌─────────────────────────────┼────────────┼─────────────────┐
│         Collectors          │            │                  │
│  ┌──────────────────┐  ┌───┴────────────┴────────────────┐ │
│  │ snmp_poller.py   │  │ gnmi_collector.py               │ │
│  │ (new)            │  │ (new)                           │ │
│  │ PySNMP / easysnmp│  │ pygnmi / grpcio                │ │
│  │ Poll interval:   │  │ gNMI Subscribe (STREAM mode)   │ │
│  │ 30s–300s config  │  │ ON_CHANGE for oper-status      │ │
│  └──────────────────┘  │ SAMPLE for counters (10s)      │ │
│                         └────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
                              │
                     ┌────────┴────────┐
                     │  Network Devices │
                     │  (SNMP agents,  │
                     │   gNMI targets)  │
                     └─────────────────┘
```

### 3.2 New Modules

| Module | Path | Purpose |
|--------|------|---------|
| `snmp_poller.py` | `tools/network/snmp_poller.py` | SNMPv2c/v3 polling — interface counters, oper-status, CPU, memory |
| `gnmi_collector.py` | `tools/network/gnmi_collector.py` | gNMI Subscribe — streaming interface stats, oper-state changes |
| `telemetry_broker.py` | `tools/network/telemetry_broker.py` | Aggregation layer — normalizes SNMP + gNMI into unified metrics, pushes to WebSocket |
| `telemetry_overlay.js` | `tools/dashboard/static/js/telemetry-overlay.js` | Client-side WebSocket consumer — updates canvas nodes/links in real-time |

### 3.3 Data Model

#### 3.3.1 Telemetry Metric (Normalized)

```json
{
  "node_id": "uuid",
  "device_ip": "10.0.1.1",
  "timestamp": "2026-03-27T14:30:00Z",
  "metrics": {
    "oper_status": "up",           // up | down | degraded
    "cpu_percent": 42.3,
    "memory_percent": 68.1,
    "interfaces": {
      "GigabitEthernet0/0": {
        "oper_status": "up",
        "in_octets_rate": 524288000,   // bytes/sec
        "out_octets_rate": 312000000,
        "in_errors": 0,
        "out_errors": 0,
        "bandwidth": 1000000000,       // bits/sec (link speed)
        "utilization_in": 0.42,        // ratio 0.0-1.0
        "utilization_out": 0.25
      }
    }
  }
}
```

#### 3.3.2 DB Tables (network_canvas.db)

```sql
-- Telemetry source configuration (which devices to poll)
CREATE TABLE IF NOT EXISTS telemetry_sources (
    id TEXT PRIMARY KEY,
    topology_id TEXT NOT NULL REFERENCES topologies(id),
    node_id TEXT NOT NULL,          -- canvas node UUID
    device_ip TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK(protocol IN ('snmpv2c','snmpv3','gnmi')),
    port INTEGER DEFAULT 161,
    credential_ref TEXT,            -- reference to secrets vault, not plaintext
    poll_interval_sec INTEGER DEFAULT 60,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(topology_id, node_id)
);

-- Time-series telemetry data (ring buffer — keep 24h, prune older)
CREATE TABLE IF NOT EXISTS telemetry_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES telemetry_sources(id),
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    metric_type TEXT NOT NULL,      -- oper_status, cpu, memory, if_util_in, if_util_out, if_errors
    metric_key TEXT,                -- interface name for per-interface metrics
    value_num REAL,
    value_text TEXT,
    UNIQUE(source_id, timestamp, metric_type, metric_key)
);

CREATE INDEX idx_telemetry_ts ON telemetry_metrics(source_id, timestamp DESC);

-- Alerts / threshold violations
CREATE TABLE IF NOT EXISTS telemetry_alerts (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES telemetry_sources(id),
    alert_type TEXT NOT NULL,       -- link_down, high_cpu, high_util, errors
    severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
    message TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    acknowledged INTEGER DEFAULT 0
);
```

### 3.4 SNMP Poller Design

**Library:** `pysnmp` (pure Python, no C dependencies — air-gap compatible)

**OIDs to poll:**

| Metric | OID | MIB |
|--------|-----|-----|
| Interface oper status | 1.3.6.1.2.1.2.2.1.8 (ifOperStatus) | IF-MIB |
| Interface in octets | 1.3.6.1.2.1.31.1.1.1.6 (ifHCInOctets) | IF-MIB |
| Interface out octets | 1.3.6.1.2.1.31.1.1.1.10 (ifHCOutOctets) | IF-MIB |
| Interface speed | 1.3.6.1.2.1.31.1.1.1.15 (ifHighSpeed) | IF-MIB |
| Interface in errors | 1.3.6.1.2.1.2.2.1.14 (ifInErrors) | IF-MIB |
| Interface out errors | 1.3.6.1.2.1.2.2.1.20 (ifOutErrors) | IF-MIB |
| CPU 1-min avg | 1.3.6.1.4.1.9.2.1.57 (avgBusy1) | CISCO-PROCESS-MIB |
| Memory used | 1.3.6.1.4.1.9.9.48.1.1.1.5 (ciscoMemoryPoolUsed) | CISCO-MEMORY-POOL-MIB |
| sysUpTime | 1.3.6.1.2.1.1.3.0 | SNMPv2-MIB |

**Utilization calculation:**
```
utilization = delta(ifHCInOctets) * 8 / (poll_interval * ifHighSpeed * 1_000_000)
```

**SNMPv3 auth:** Support `authPriv` (SHA-256 + AES-256) — required for DoD/STIG compliance. SNMPv2c kept for lab environments only.

**Thread model:** One `threading.Thread` per topology with active telemetry. Poll all sources in that topology sequentially (avoid SNMP flooding). Configurable interval 30s–300s.

### 3.5 gNMI Collector Design

**Library:** `pygnmi` (gRPC-based, supports Subscribe RPC)

**Subscription paths (OpenConfig):**
```
/interfaces/interface[name=*]/state/oper-status      → ON_CHANGE
/interfaces/interface[name=*]/state/counters          → SAMPLE (10s)
/components/component[name=CPU*]/cpu/utilization      → SAMPLE (30s)
/system/memory/state                                   → SAMPLE (60s)
```

**Subscription modes:**
- `ON_CHANGE` for oper-status (instant up/down detection)
- `SAMPLE` for counters (configurable interval)
- `STREAM` mode (persistent gRPC connection)

**TLS:** Mandatory mutual TLS for gNMI per ICDEV ZTA guardrails. Certificate paths configurable per source.

**Thread model:** One long-lived gRPC stream per device. Reconnect with exponential backoff (1s → 2s → 4s → ... → 60s max).

### 3.6 Telemetry Broker Design

Central aggregation that normalizes SNMP poll results and gNMI stream updates into the unified metric format.

**Responsibilities:**
1. Receive metrics from pollers/collectors
2. Compute derived values (utilization ratios, delta rates)
3. Persist to `telemetry_metrics` table (ring buffer, 24h retention)
4. Evaluate alert thresholds
5. Broadcast to WebSocket room `telemetry:<topology_id>`
6. Serve REST API for initial load and historical queries

**Alert thresholds (configurable in `args/telemetry_config.yaml`):**

| Condition | Default | Severity |
|-----------|---------|----------|
| oper_status = down | — | critical |
| utilization > 80% | 0.80 | warning |
| utilization > 95% | 0.95 | critical |
| CPU > 85% | 0.85 | warning |
| Memory > 90% | 0.90 | warning |
| Interface errors > 100/interval | 100 | warning |

### 3.7 Frontend Overlay Design

#### 3.7.1 WebSocket Integration

```javascript
// telemetry-overlay.js
const socket = io();
socket.emit('join', { room: `telemetry:${currentTopoId}` });

socket.on('telemetry_update', (data) => {
  updateNodeStatus(data.node_id, data.metrics);
  updateLinkUtilization(data.node_id, data.metrics.interfaces);
});
```

**Fallback:** If WebSocket unavailable (D103 DoD proxy scenario), poll `GET /api/telemetry/<topo_id>/latest` every 5s.

#### 3.7.2 Visual Indicators

**Node status badges:**
```
┌─────────────────┐
│  ● UP           │   ● = green circle (oper_status up)
│  [Router Icon]  │   ○ = red circle (down)
│  Router-Core-1  │   ◐ = yellow half-circle (degraded)
│  CPU: 42% M:68% │   Small text below label
└─────────────────┘
```

Implementation: Add SVG overlay elements to JointJS `NetworkNode`:
- **Status ring:** 3px colored ring around node (green/yellow/red)
- **Health bar:** Tiny bar below node showing CPU+memory as stacked bar
- **Pulse animation:** CSS `@keyframes pulse` on critical nodes (red glow)

**Link utilization coloring:**
- Reuse existing `_heatmapColor()` gradient function
- Map `max(utilization_in, utilization_out)` → gradient color
- Animate stroke-width proportional to utilization (1px idle → 4px saturated)
- Dashed stroke for links with errors > threshold

**Link labels (on hover):**
```
GigabitEthernet0/0 ↔ GigabitEthernet0/1
  ↓ 420 Mbps (42%)  ↑ 250 Mbps (25%)
  Errors: 0/0
```

#### 3.7.3 Toggle Controls

Add to existing canvas toolbar:
```html
<button onclick="toggleTelemetry()" title="Live Telemetry">
  <i class="bi bi-broadcast"></i> Live
</button>
<select id="telemetry-refresh" onchange="setTelemetryRefresh(this.value)">
  <option value="5">5s</option>
  <option value="10" selected>10s</option>
  <option value="30">30s</option>
  <option value="60">60s</option>
</select>
```

### 3.8 API Endpoints (New)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/telemetry/<topo_id>/sources` | List configured telemetry sources |
| POST | `/api/telemetry/<topo_id>/sources` | Add telemetry source to a node |
| PUT | `/api/telemetry/<topo_id>/sources/<src_id>` | Update source config |
| DELETE | `/api/telemetry/<topo_id>/sources/<src_id>` | Remove source |
| GET | `/api/telemetry/<topo_id>/latest` | Latest metrics for all nodes (HTTP poll fallback) |
| GET | `/api/telemetry/<topo_id>/history/<node_id>` | Historical metrics (last 24h) |
| GET | `/api/telemetry/<topo_id>/alerts` | Active alerts |
| POST | `/api/telemetry/<topo_id>/alerts/<alert_id>/ack` | Acknowledge alert |
| POST | `/api/telemetry/<topo_id>/start` | Start polling/streaming for topology |
| POST | `/api/telemetry/<topo_id>/stop` | Stop polling/streaming |

---

## 4. Configuration

### 4.1 `args/telemetry_config.yaml`

```yaml
telemetry:
  enabled: true

  snmp:
    default_version: "v3"           # v2c | v3
    default_community: "public"     # v2c only
    default_auth_protocol: "sha256" # v3: md5, sha, sha256
    default_priv_protocol: "aes256" # v3: des, aes128, aes256
    poll_interval_sec: 60
    timeout_sec: 5
    retries: 2

  gnmi:
    default_port: 6030
    tls_required: true              # per ZTA guardrails
    sample_interval_sec: 10
    reconnect_max_sec: 60

  broker:
    retention_hours: 24
    prune_interval_min: 30
    websocket_room_prefix: "telemetry"

  alerts:
    link_down: { severity: "critical" }
    util_warning: { threshold: 0.80, severity: "warning" }
    util_critical: { threshold: 0.95, severity: "critical" }
    cpu_warning: { threshold: 0.85, severity: "warning" }
    memory_warning: { threshold: 0.90, severity: "warning" }
    error_rate: { threshold: 100, severity: "warning" }

  ui:
    default_refresh_sec: 10
    status_ring_width: 3
    pulse_animation: true
    link_width_min: 1
    link_width_max: 4
```

---

## 5. Dependency Analysis

| Package | Version | Size | Air-gap | VRAM | Notes |
|---------|---------|------|---------|------|-------|
| `pysnmp` | >=6.0 | ~2MB | Yes (pure Python) | 0 | SNMPv1/v2c/v3, async-capable |
| `pyasn1` | >=0.6 | ~500KB | Yes (dep of pysnmp) | 0 | ASN.1 codec |
| `pygnmi` | >=0.8 | ~100KB | Yes (wraps grpcio) | 0 | gNMI Subscribe RPC |
| `grpcio` | >=1.60 | ~15MB | Needs wheel | 0 | gRPC transport for gNMI |
| `protobuf` | >=4.25 | ~5MB | Yes | 0 | gNMI proto definitions |
| `flask-socketio` | >=5.3 | ~200KB | Yes | 0 | Already in project |

**Air-gap concern:** `grpcio` has platform-specific wheels. Pre-download wheels for target OS/arch. Alternatively, for SNMP-only deployments, `pygnmi`/`grpcio` are optional.

**Python 3.13+ compat:** `pysnmp` v6+ supports 3.13. `grpcio` supports 3.13 as of 1.62+. Verify before installing.

---

## 6. Security Considerations

### 6.1 Credential Management
- SNMP community strings and gNMI credentials MUST NOT be stored in plaintext
- Use `credential_ref` field pointing to encrypted vault or environment variable
- SNMPv3 `authPriv` mandatory for production (STIG V-220539)
- gNMI mutual TLS mandatory (ZTA control)

### 6.2 STIG Compliance
- **V-220539:** SNMP must use v3 with authPriv (SHA + AES minimum)
- **V-220541:** SNMP community strings must not be "public" or "private" in production
- **V-220295:** Management plane traffic must be encrypted (gNMI TLS satisfies this)
- Canvas compliance audit (`compliance.py`) already flags insecure SNMP — telemetry sources will inherit this check

### 6.3 Network Segmentation
- Telemetry polling should originate from management VLAN/VRF only
- gNMI streams traverse management plane — ensure firewall rules permit gRPC port
- Rate-limit SNMP polling to avoid impacting device control plane

---

## 7. Implementation Phases

### Phase A: SNMP Poller + DB (Estimated effort: Medium)
1. Create `args/telemetry_config.yaml`
2. Create `tools/network/snmp_poller.py` — SNMPv2c/v3 polling with pysnmp
3. Add `telemetry_sources`, `telemetry_metrics`, `telemetry_alerts` tables to `network_canvas.db`
4. Add REST endpoints for source CRUD in `blueprint.py`
5. Unit tests with mock SNMP agent

### Phase B: Telemetry Broker + WebSocket (Estimated effort: Medium)
1. Create `tools/network/telemetry_broker.py` — aggregation, rate computation, alerting
2. Add SocketIO room `telemetry:<topo_id>` to `websocket.py`
3. Add HTTP poll fallback endpoint `/api/telemetry/<topo_id>/latest`
4. Data retention pruning (24h ring buffer)
5. Integration tests

### Phase C: Frontend Overlay (Estimated effort: Medium)
1. Create `telemetry-overlay.js` — WebSocket consumer + canvas rendering
2. Extend JointJS `NetworkNode` with status ring, health bar, pulse animation
3. Extend link rendering with utilization coloring (reuse `_heatmapColor`)
4. Add toolbar toggle and refresh controls
5. Selenium E2E tests

### Phase D: gNMI Streaming (Estimated effort: Medium-High)
1. Create `tools/network/gnmi_collector.py` — gNMI Subscribe with pygnmi
2. ON_CHANGE subscriptions for instant oper-status
3. SAMPLE subscriptions for counters
4. mTLS certificate configuration
5. Integration tests with gNMI simulator (e.g., gnmic)

### Phase E: Alerting + History (Estimated effort: Low)
1. Alert threshold evaluation in broker
2. Alert badges on canvas nodes
3. Historical chart panel (sparklines for 24h utilization trend)
4. Alert acknowledgement UI

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `grpcio` wheel unavailable for air-gapped target | gNMI won't work | SNMP-only fallback; pre-download wheels |
| SNMP polling overwhelms device CPU | Device instability | Rate-limit polls, configurable interval, bulk GET |
| WebSocket blocked by DoD proxy | No live updates | HTTP poll fallback (D103 pattern) already planned |
| SQLite write contention from high-frequency telemetry | DB locks | Batch writes every 5s, WAL mode (already enabled) |
| pysnmp v6 API breaking changes | Poller breaks | Pin version, test in CI |
| gNMI vendor-specific OpenConfig deviations | Wrong paths | Per-vendor path mapping in config |

---

## 9. Alternatives Considered

### 9.1 External TSDB (InfluxDB/TimescaleDB)
**Pros:** Purpose-built for time-series, better query performance at scale.
**Cons:** Additional infrastructure dependency, breaks single-binary SQLite simplicity, air-gap complexity.
**Decision:** Start with SQLite ring buffer (24h). Migrate to TSDB if >100 nodes need telemetry.

### 9.2 Telegraf/Prometheus as Collector
**Pros:** Battle-tested collectors, extensive plugin ecosystem.
**Cons:** External process management, doesn't integrate with canvas node mapping, additional binaries for air-gap.
**Decision:** Build lightweight collectors in-process. Add Prometheus scrape endpoint export later if needed for integration with existing monitoring.

### 9.3 NETCONF Instead of gNMI
**Pros:** Wider vendor support, XML/YANG native.
**Cons:** Polling-only (no streaming subscribe), heavier XML parsing, slower.
**Decision:** SNMP for legacy, gNMI for modern. NETCONF can be added as Phase F if needed.

---

## 10. Conclusion

The existing canvas infrastructure is well-positioned for telemetry overlay:
- **Heatmap system** provides the color-coding engine (just needs a new "live" metric source)
- **WebSocket infrastructure** exists and is tested
- **Blueprint API** pattern is established for adding new endpoints
- **No new LLM cost** — telemetry is purely deterministic (scanner-tier at most)

The primary work is building the SNMP/gNMI collectors and the aggregation broker. The frontend overlay is relatively straightforward given the existing heatmap foundation.

Recommended implementation order: **A → C → B → D → E** (get SNMP polling + visual overlay working first for a demo-able MVP, then add WebSocket streaming and gNMI).
