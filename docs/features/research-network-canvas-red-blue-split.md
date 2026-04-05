# Research: Network Canvas — Red Team / Blue Team Split View

**Task ID:** task-28f1a79e01
**Tier:** 4 (Enhancement)
**Priority:** Low
**Date:** 2026-03-28
**Classification:** CUI // SP-CTI

---

## Summary

Dual-pane canvas mode where the Blue Team sees the approved, authorized topology (STIG compliance, FIPS 140 coverage, Nessus vulnerability tinting) while the Red Team overlays attack paths, pivot points, and lateral movement arrows on the same graph. Views toggle independently or merge into a combined ATO briefing layer. Enables live red-cell/blue-cell adversarial analysis during ATO events without leaving the canvas.

---

## Current State Analysis

### What Already Exists

| Component | Location | Status |
|-----------|----------|--------|
| Nessus/ACAS vulnerability overlay | `tools/network/vuln_overlay.py` | Implemented — 8 endpoints, 3 DB tables |
| STIG import + compliance coloring | `tools/network/stig_import.py` | Implemented — `.ckl`/XCCDF, per-node tint |
| FIPS 140 encryption coverage toggle | `canvas.html` toolbar | Implemented — highlights unencrypted links |
| Heatmap engine (vuln, bandwidth, age, STIG) | `canvas.html` + `simulation.py` | Implemented — color-coded node tinting |
| Change Request markup (green/red/yellow) | `canvas.html` CR mode | Implemented — layer concept already proven |
| MITRE ATLAS red team scanner | `tools/security/atlas_red_team.py` | Implemented — 12 static AI/LLM technique checks |
| Red team technique registry | `tools/security/red_team_registry.py` | Implemented |
| JointJS canvas renderer | `static/js/network-canvas.js` | 3,124 lines — supports layered SVG overlays |
| ATO package generator | `tools/network/ato_generator.py` | Implemented — boundary diagram, PPS matrix |
| AI Topology Reviewer | `canvas.html` + LLM integration | Implemented — SPOF, redundancy, STIG gap analysis |

### Gaps

- **No network-level attack path data model**: No DB tables for attacker-controlled nodes, pivot points, lateral movement edges, or MITRE ATT&CK TTP annotations
- **No Red Team canvas layer**: JointJS paper has no second overlay layer for attack arrows; all overlays mutate node/link attrs in-place
- **No split-pane layout**: Canvas is single-pane; no side-by-side Blue/Red view
- **No attack path generator**: No tool to derive likely attack paths from Nessus CVE data + network topology (reachability graph)
- **No MITRE ATT&CK TTP picker**: No UI to assign techniques (T1078, T1021.002, etc.) to attack edges
- **No ATO merge export**: No combined Blue+Red briefing export (currently generates Blue Team artifacts only)
- **Existing red team tools are AI/LLM-focused** (`atlas_red_team.py`), not network-topology-focused

---

## Proposed Architecture

### 1. Data Model — Attack Graph Tables

**New tables in `network_canvas.db`** (add to `tools/network/db/init_db.py`):

```sql
-- Attacker-controlled nodes and pivot points
CREATE TABLE IF NOT EXISTS nc_attack_nodes (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT NOT NULL,
    node_id         TEXT NOT NULL,              -- FK → nc_objects.id
    pivot_type      TEXT DEFAULT 'compromised', -- 'initial_access' | 'pivot' | 'persistence' | 'c2'
    attacker_dwell  INTEGER DEFAULT 0,          -- days attacker has been present (for briefings)
    techniques      TEXT DEFAULT '[]',          -- JSON array of MITRE ATT&CK technique IDs
    notes           TEXT DEFAULT '',
    discovered_at   TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_nc_atk_nodes_topo
    ON nc_attack_nodes(topology_id);

-- Lateral movement and attack path edges
CREATE TABLE IF NOT EXISTS nc_attack_edges (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT NOT NULL,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    technique_id    TEXT DEFAULT '',            -- MITRE ATT&CK T-number (e.g. T1021.002)
    technique_name  TEXT DEFAULT '',
    tactic          TEXT DEFAULT '',            -- 'lateral-movement' | 'exfiltration' | etc.
    protocol        TEXT DEFAULT '',            -- e.g. 'SMB', 'RDP', 'SSH', 'WMI'
    port            INTEGER DEFAULT 0,
    confidence      REAL DEFAULT 1.0,           -- 0.0–1.0 (auto-derived vs manually marked)
    is_verified     INTEGER DEFAULT 0,          -- 1 = pen-tester confirmed
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_nc_atk_edges_topo
    ON nc_attack_edges(topology_id);

-- Red Team sessions / exercises
CREATE TABLE IF NOT EXISTS nc_redteam_sessions (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT NOT NULL,
    session_name    TEXT NOT NULL,
    operator        TEXT DEFAULT '',
    started_at      TEXT NOT NULL,
    ended_at        TEXT DEFAULT NULL,
    objective       TEXT DEFAULT '',            -- e.g. 'Reach domain controller from DMZ'
    status          TEXT DEFAULT 'active',      -- 'active' | 'complete' | 'archived'
    summary_json    TEXT DEFAULT '{}',          -- finding counts, techniques used, max depth
    classification  TEXT DEFAULT 'CUI'
);
```

### 2. Attack Path Auto-Derivation

**New file:** `tools/network/attack_path_builder.py`

Consumes Nessus findings (from `nc_vuln_findings`) and topology reachability (from `nc_edges`) to auto-suggest likely lateral movement paths. This is not simulation — it outputs candidate attack edges with confidence scores for analyst review.

**Algorithm:**

```
For each host with critical/high CVE in nc_vuln_hosts:
    1. Mark as potential initial_access node if exposed to untrusted zone
    2. Walk nc_edges graph: for each adjacent node reachable on exploitable ports:
         - Check nc_vuln_findings for credential exposure (T1078), SMB (T1021.002),
           RDP (T1021.001), SSH (T1021.004), WMI (T1047), DCOM (T1021.003)
         - If matching CVE + open port found → create candidate nc_attack_edge
           with confidence = min(cvss/10.0, reachability_score)
    3. Mark high-value targets (domain controllers, jump hosts, SIEM) as objective nodes
```

**Key functions:**
```python
def build_attack_graph(topology_id: str, session_id: str) -> dict:
    """
    Derive candidate attack paths from vuln data + topology.
    Returns summary: {nodes_marked, edges_suggested, max_depth, objectives_reachable}
    """

def compute_blast_radius(topology_id: str, source_node_id: str) -> list[dict]:
    """
    BFS from compromised node — returns all reachable nodes within N hops
    weighted by network reachability (nc_edges) and open port data.
    """

def score_attack_path(path: list[str], topology_id: str) -> float:
    """
    Score an attack path by: avg CVE exploitability × path length penalty.
    Higher score = more likely / dangerous path.
    """
```

**CLI usage:**
```
python tools/network/attack_path_builder.py --topology-id <id> --session-id <id> --json
python tools/network/attack_path_builder.py --topology-id <id> --blast-radius <node_id> --json
```

### 3. New Blueprint Endpoints

**In:** `tools/network/blueprint.py`

```python
# Red Team Session Management
POST   /api/redteam/sessions                        # Create session for topology
GET    /api/redteam/sessions/<topo_id>              # List sessions
DELETE /api/redteam/sessions/<session_id>           # Archive session

# Attack Graph (manual annotation)
POST   /api/redteam/nodes                           # Mark node as attacker-controlled
DELETE /api/redteam/nodes/<id>                      # Remove mark
POST   /api/redteam/edges                           # Add lateral movement edge
DELETE /api/redteam/edges/<id>                      # Remove edge
PATCH  /api/redteam/edges/<id>                      # Update TTP, confidence, verify

# Auto-derivation
POST   /api/redteam/auto-derive/<topo_id>           # Run attack_path_builder, return candidates
POST   /api/redteam/blast-radius/<topo_id>/<node_id> # Compute blast radius

# Overlay data (canvas consumption)
GET    /api/redteam/overlay/<topo_id>               # All attack nodes + edges for canvas
GET    /api/redteam/overlay/<topo_id>/<session_id>  # Overlay for specific session
```

### 4. Canvas Dual-Pane Layout

**Split View Modes (toolbar toggle):**

| Mode | Layout | Use Case |
|------|--------|----------|
| **Blue Only** | Full-width — STIG/Nessus/FIPS overlays | Normal design / compliance review |
| **Red Only** | Full-width — attack paths + pivot points on base topology | Red cell adversary emulation |
| **Split H** | Left: Blue ∥ Right: Red — synchronized pan/zoom | Side-by-side defensive vs offensive view |
| **Split V** | Top: Blue ∥ Bottom: Red — synchronized pan/zoom | Tall-monitor ATO briefing layout |
| **Merged** | Single pane — both overlays simultaneously | Combined ATO briefing / risk acceptance |

**CSS layout for split pane:**
```css
.nc-split-container {
    display: flex;
    flex-direction: row;   /* or column for Split V */
    height: 100%;
    gap: 4px;
}
.nc-pane-blue, .nc-pane-red {
    flex: 1;
    position: relative;
    overflow: hidden;
    border: 2px solid var(--pane-color);
}
.nc-pane-blue { --pane-color: #1a6bbd; }
.nc-pane-red  { --pane-color: #c0392b; }

.nc-pane-label {
    position: absolute;
    top: 8px; left: 12px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 2px 8px;
    border-radius: 3px;
    z-index: 10;
}
.nc-pane-blue .nc-pane-label { background: #1a6bbd; color: #fff; content: 'BLUE TEAM'; }
.nc-pane-red  .nc-pane-label { background: #c0392b; color: #fff; content: 'RED TEAM'; }
```

**JointJS dual-paper strategy:**

Two separate `joint.dia.Paper` instances sharing one `joint.dia.Graph` (read-only clone for Red) or one shared graph with overlay cell sets:

```javascript
// Option A: Two papers, one shared graph (recommended)
// Blue paper renders nc_objects/nc_edges with blue-team attrs
// Red paper renders same base graph + nc_attack_edges as overlay cells

const bluePaper = new joint.dia.Paper({
    el: document.getElementById('nc-pane-blue'),
    model: sharedGraph,
    // ... blue team render options
});

const redPaper = new joint.dia.Paper({
    el: document.getElementById('nc-pane-red'),
    model: sharedGraph,
    // ... base topology same as blue
});

// Attack graph cells added only to redPaper via a secondary graph
const attackGraph = new joint.dia.Graph();
const attackOverlayPaper = new joint.dia.Paper({
    el: document.getElementById('nc-pane-red'),  // same container, z-order above
    model: attackGraph,
    interactive: false,
    background: { color: 'transparent' }
});
```

**Synchronized pan/zoom in split mode:**
```javascript
bluePaper.on('translate', (tx, ty) => {
    if (splitMode === 'split-h' || splitMode === 'split-v') {
        redPaper.translate(tx, ty);
        attackOverlayPaper.translate(tx, ty);
    }
});
bluePaper.on('scale', (sx, sy, ox, oy) => {
    if (splitMode !== 'single') {
        redPaper.scale(sx, sy, ox, oy);
        attackOverlayPaper.scale(sx, sy, ox, oy);
    }
});
```

### 5. Red Team Overlay Rendering

**Attack node styling:**

```javascript
const ATTACK_NODE_STYLES = {
    initial_access: {
        stroke: '#e74c3c', strokeWidth: 3, strokeDasharray: '8,4',
        fill: 'rgba(231,76,60,0.15)', badge: '🚩'
    },
    pivot: {
        stroke: '#e67e22', strokeWidth: 3, strokeDasharray: '4,4',
        fill: 'rgba(230,126,34,0.15)', badge: '⚡'
    },
    persistence: {
        stroke: '#9b59b6', strokeWidth: 3, strokeDasharray: '2,2',
        fill: 'rgba(155,89,182,0.15)', badge: '🔒'
    },
    c2: {
        stroke: '#c0392b', strokeWidth: 4, strokeDasharray: '12,3',
        fill: 'rgba(192,57,43,0.20)', badge: '📡'
    }
};

function applyAttackNodeOverlay(nodeId, pivotType) {
    const cell = sharedGraph.getCell(nodeId);
    if (!cell) return;
    const style = ATTACK_NODE_STYLES[pivotType] || ATTACK_NODE_STYLES.pivot;
    // Apply via attrs — does not mutate blue-team paper (separate paper instance)
    cell.attr({
        'body/stroke':            style.stroke,
        'body/strokeWidth':       style.strokeWidth,
        'body/strokeDasharray':   style.strokeDasharray,
        'body/fill':              style.fill,
        'attack-badge/text':      style.badge
    });
}
```

**Lateral movement arrow styling:**

```javascript
// Tactic → color mapping (MITRE ATT&CK tactic colors)
const TACTIC_COLORS = {
    'initial-access':       '#e74c3c',  // red
    'execution':            '#e67e22',  // orange
    'persistence':          '#9b59b6',  // purple
    'privilege-escalation': '#f39c12',  // amber
    'defense-evasion':      '#1abc9c',  // teal
    'credential-access':    '#e91e63',  // pink
    'discovery':            '#2196f3',  // blue
    'lateral-movement':     '#ff5722',  // deep orange
    'collection':           '#607d8b',  // blue-grey
    'exfiltration':         '#795548',  // brown
    'command-and-control':  '#c0392b',  // dark red
    'impact':               '#000000'   // black
};

function renderAttackEdge(edge) {
    const color = TACTIC_COLORS[edge.tactic] || '#e74c3c';
    const link = new joint.shapes.standard.Link({
        id:     'atk_' + edge.id,
        source: { id: edge.source_node_id },
        target: { id: edge.target_node_id },
        attrs: {
            line: {
                stroke:          color,
                strokeWidth:     edge.is_verified ? 3 : 2,
                strokeDasharray: edge.is_verified ? '0' : '8,4',
                targetMarker: { type: 'path', fill: color,
                    d: 'M 10 -5 0 0 10 5 z' }  // arrowhead
            }
        },
        labels: [{
            position: 0.5,
            attrs: {
                text: { text: edge.technique_id || edge.tactic,
                        fontSize: 9, fill: color },
                rect: { fill: 'rgba(255,255,255,0.8)', rx: 2 }
            }
        }]
    });
    attackGraph.addCell(link);
}
```

### 6. MITRE ATT&CK TTP Picker

**Inline context menu** on right-click of a canvas edge in Red mode:

```html
<!-- TTP assignment modal (partial) -->
<div id="ttp-picker-modal" class="nc-modal" style="display:none">
  <div class="nc-modal-header">
    <span>Assign MITRE ATT&CK Technique</span>
    <button onclick="closeTTPPicker()">×</button>
  </div>
  <div class="nc-modal-body">
    <input id="ttp-search" type="text" placeholder="Search T-number or name…"
           oninput="filterTTPs(this.value)">
    <select id="ttp-tactic-filter">
      <option value="">All Tactics</option>
      <option value="lateral-movement">Lateral Movement</option>
      <option value="persistence">Persistence</option>
      <option value="privilege-escalation">Privilege Escalation</option>
      <option value="credential-access">Credential Access</option>
      <option value="exfiltration">Exfiltration</option>
      <option value="command-and-control">C2</option>
    </select>
    <ul id="ttp-list" class="ttp-list-scroll">
      <!-- Populated by JS from static MITRE ATT&CK subset -->
    </ul>
    <label>
      <input type="checkbox" id="ttp-verified"> Mark as pen-tester verified
    </label>
    <input type="text" id="ttp-notes" placeholder="Optional notes…">
  </div>
  <div class="nc-modal-footer">
    <button onclick="assignTTP()">Assign</button>
    <button onclick="closeTTPPicker()">Cancel</button>
  </div>
</div>
```

**Static MITRE ATT&CK subset** (network-relevant techniques, embedded as JS object, no external API call — air-gap safe):

```javascript
const MITRE_NETWORK_TTPS = [
    // Lateral Movement
    { id: 'T1021.001', name: 'Remote Desktop Protocol', tactic: 'lateral-movement', protocol: 'RDP',  port: 3389 },
    { id: 'T1021.002', name: 'SMB/Windows Admin Shares',tactic: 'lateral-movement', protocol: 'SMB',  port: 445  },
    { id: 'T1021.003', name: 'Distributed COM',          tactic: 'lateral-movement', protocol: 'DCOM', port: 135  },
    { id: 'T1021.004', name: 'SSH',                      tactic: 'lateral-movement', protocol: 'SSH',  port: 22   },
    { id: 'T1021.006', name: 'Windows Remote Management',tactic: 'lateral-movement', protocol: 'WinRM',port: 5985 },
    { id: 'T1047',     name: 'WMI',                      tactic: 'lateral-movement', protocol: 'WMI',  port: 135  },
    { id: 'T1072',     name: 'Software Deployment Tools', tactic: 'lateral-movement', protocol: '',     port: 0    },
    // Credential Access
    { id: 'T1078',     name: 'Valid Accounts',           tactic: 'credential-access', protocol: '',    port: 0    },
    { id: 'T1110',     name: 'Brute Force',              tactic: 'credential-access', protocol: '',    port: 0    },
    { id: 'T1557',     name: 'Adversary-in-the-Middle',  tactic: 'credential-access', protocol: 'ARP', port: 0    },
    // Initial Access
    { id: 'T1133',     name: 'External Remote Services', tactic: 'initial-access',   protocol: 'VPN',  port: 0    },
    { id: 'T1190',     name: 'Exploit Public-Facing App',tactic: 'initial-access',   protocol: 'HTTP', port: 443  },
    { id: 'T1195',     name: 'Supply Chain Compromise',  tactic: 'initial-access',   protocol: '',     port: 0    },
    // C2
    { id: 'T1071.001', name: 'Web Protocols (C2)',       tactic: 'command-and-control',protocol:'HTTP', port: 443 },
    { id: 'T1090',     name: 'Proxy',                    tactic: 'command-and-control',protocol:'SOCKS',port: 1080},
    { id: 'T1572',     name: 'Protocol Tunneling',       tactic: 'command-and-control',protocol:'DNS',  port: 53  },
    // Exfiltration
    { id: 'T1048',     name: 'Exfil Over Alt Protocol',  tactic: 'exfiltration',     protocol: 'FTP',  port: 21   },
    { id: 'T1041',     name: 'Exfil Over C2 Channel',    tactic: 'exfiltration',     protocol: 'HTTPS',port: 443  },
];
```

### 7. ATO Briefing Merge View

**Merged overlay rendering** (Blue + Red simultaneously):

- Blue team attributes (STIG color, FIPS badge, Nessus severity tint) preserved on nodes
- Red team attack edges rendered on top as dashed arrows
- Compromised nodes get a red dashed border overlay (does not replace STIG color)
- A legend panel shows both Blue (compliance) and Red (attack) color codes

**ATO Briefing Export:**

New button in toolbar: "Export ATO Brief (PDF)"

```python
# tools/network/ato_generator.py — new function
def generate_ato_brief(topology_id: str, session_id: str | None, output_path: str) -> dict:
    """
    Combined Blue+Red ATO briefing package:
      - Page 1: Executive summary (risk score, attack paths found, compliance %)
      - Page 2: Blue Team topology (STIG/FIPS/Nessus heatmap)
      - Page 3: Red Team topology (attack graph, pivot points, lateral movement)
      - Page 4: Merged view (dual overlay)
      - Page 5: Attack path narrative (per path: technique, CVE exploited, impact)
      - Page 6: Recommended mitigations (per attack edge: NIST 800-53 controls)
    Returns {path, page_count, risk_score, classification}
    """
```

**Risk score calculation:**
```
risk_score = (
    (critical_vulns × 10 + high_vulns × 5 + medium_vulns × 2) ×
    (1 + attack_path_count × 0.15) ×
    (1 - stig_compliance_pct / 100 × 0.3)
) / normalization_factor
```
Capped 0–100. Displayed as color-coded gauge in briefing header.

### 8. Canvas Toolbar Integration

**New toolbar button group** (added to `canvas.html` toolbar row):

```html
<!-- Red / Blue view toggle group -->
<div class="nc-toolbar-group" id="view-mode-group">
  <label class="nc-toolbar-label">View</label>
  <div class="btn-group btn-group-sm" role="group">
    <button type="button" class="btn btn-outline-primary active"
            onclick="setViewMode('blue')" title="Blue Team view">
      <i class="bi bi-shield-check"></i> Blue
    </button>
    <button type="button" class="btn btn-outline-danger"
            onclick="setViewMode('red')" title="Red Team overlay">
      <i class="bi bi-bug"></i> Red
    </button>
    <button type="button" class="btn btn-outline-secondary"
            onclick="setViewMode('split-h')" title="Split view">
      <i class="bi bi-layout-split"></i> Split
    </button>
    <button type="button" class="btn btn-outline-warning"
            onclick="setViewMode('merged')" title="ATO merged view">
      <i class="bi bi-layers"></i> ATO
    </button>
  </div>
</div>

<!-- Red Team session selector (shown when view=red/merged) -->
<div class="nc-toolbar-group" id="redteam-session-group" style="display:none">
  <label class="nc-toolbar-label">Red Session</label>
  <select id="redteam-session-select" onchange="loadRedTeamSession(this.value)"
          class="form-select form-select-sm" style="width:180px">
    <option value="">— Select session —</option>
  </select>
  <button class="btn btn-sm btn-outline-danger" onclick="newRedTeamSession()"
          title="New red team session">
    <i class="bi bi-plus"></i>
  </button>
  <button class="btn btn-sm btn-outline-secondary" onclick="autoDerivePaths()"
          title="Auto-derive attack paths from Nessus data">
    <i class="bi bi-magic"></i>
  </button>
</div>
```

---

## Implementation Phases

| Phase | Scope | New Files | Dependencies |
|-------|-------|-----------|-------------|
| **A** (Data model) | DB tables `nc_attack_nodes`, `nc_attack_edges`, `nc_redteam_sessions`; 8 blueprint endpoints | None | None (SQLite) |
| **B** (Manual annotation) | Red Team toolbar toggle; attack node/edge mark via right-click context menu; TTP picker modal | None | JointJS (loaded) |
| **C** (Auto-derivation) | `attack_path_builder.py` — BFS graph walk from Nessus findings | `attack_path_builder.py` | NetworkX (pure Python, or stdlib BFS fallback) |
| **D** (Split pane) | Dual JointJS paper layout; synchronized pan/zoom; Split H/V toggle | None | JointJS (loaded) |
| **E** (ATO merge export) | `ato_generator.py` — combined briefing; risk score; PDF pages | None | WeasyPrint (already used) |

---

## Dependency Analysis

| Library | Purpose | Air-gap Risk | Existing? |
|---------|---------|-------------|-----------|
| JointJS | Dual-paper split pane | None | Already loaded |
| SQLite / stdlib | `nc_attack_*` tables | None | Already used |
| NetworkX | BFS for blast radius (Phase C) | Low (pure Python) | Not installed — can replace with stdlib `collections.deque` BFS |
| WeasyPrint | PDF export for ATO brief | Low | Used in `ato_generator.py` |
| Bootstrap Icons | Toolbar icon glyphs | None | Already loaded |

**Air-gap recommendation:** Phases A–B and D have zero new dependencies. Phase C uses a pure-Python BFS fallback (no NetworkX required). Phase E requires WeasyPrint already present.

---

## Canvas Integration Points

| File | Change |
|------|--------|
| `tools/network/db/init_db.py` | Add `nc_attack_nodes`, `nc_attack_edges`, `nc_redteam_sessions` tables |
| `tools/network/blueprint.py` | Add 10 Red Team endpoints |
| `tools/network/attack_path_builder.py` | New: BFS-based auto-derivation from Nessus + topology |
| `tools/network/ato_generator.py` | Add `generate_ato_brief()` for Blue+Red combined export |
| `static/js/network-canvas.js` | `setViewMode()`, `renderAttackEdge()`, `applyAttackNodeOverlay()`, TTP picker, split-pane sync |
| `static/css/network-canvas.css` | `.nc-split-container`, `.nc-pane-blue`, `.nc-pane-red`, `.nc-pane-label`, attack edge animations |
| `templates/network/canvas.html` | View-mode toolbar group, Red Team session selector, TTP picker modal |

---

## Security Considerations

- **Classification markings enforced**: All Red Team session data stored with `classification = 'CUI'`; ATO brief PDF includes header/footer classification banners via `classification_manager.py`
- **RBAC**: Red Team write operations (mark nodes, create sessions) restricted to `redteam` role; Blue Team users get read-only overlay of attack graph
- **No external TTP data calls**: MITRE ATT&CK subset is a static JS object (air-gap safe; no live `attack.mitre.org` API calls)
- **Audit trail**: All `POST/DELETE` to `/api/redteam/*` logged to `nc_audit` (`ATTACK_NODE_CREATE`, `ATTACK_EDGE_CREATE`, `SESSION_START`, etc.) — append-only (D6)
- **Session isolation**: Red Team sessions are topology-scoped; no cross-topology leakage of attack graph data
- **Auto-derivation is suggestion-only**: `attack_path_builder.py` inserts rows with `is_verified = 0`; analyst must explicitly verify before they appear in ATO export

---

## Effort Estimate

| Phase | New Files | Modified Files | Estimated LOC |
|-------|-----------|---------------|---------------|
| A (DB + endpoints) | None | `init_db.py`, `blueprint.py` | ~300 Python |
| B (Manual annotation UI) | None | `network-canvas.js`, `canvas.html`, `network-canvas.css` | ~500 JS/HTML/CSS |
| C (Auto-derivation) | `attack_path_builder.py` | `blueprint.py` | ~350 Python |
| D (Split pane) | None | `network-canvas.js`, `canvas.html`, `network-canvas.css` | ~400 JS/CSS |
| E (ATO brief export) | None | `ato_generator.py`, `blueprint.py` | ~250 Python |

**Total estimate:** ~1,200–1,500 lines across 7 files. No new dependencies required for Phases A–D.
