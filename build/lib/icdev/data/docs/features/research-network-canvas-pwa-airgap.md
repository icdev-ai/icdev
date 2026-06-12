# Research: Network Canvas — Offline / Air-Gap PWA Mode
**Classification:** CUI // SP-CTI
**Phase:** Tier 4 Research
**Task ID:** task-6c0a4e0486
**Date:** 2026-03-28
**Priority:** Low

---

## Executive Summary

Network Canvas is currently an online-only tool: every save, load, simulation, and compliance check requires a live Flask API connection. For IL5/IL6 disconnected (SIPR, SIPRNet-adjacent, or air-gapped SCIF) environments, operators need full canvas editing—including topology creation, JointJS rendering, export, and offline compliance snapshots—without network access.

This document evaluates packaging Network Canvas as a Progressive Web App (PWA) with an offline-first architecture that satisfies ICDEV air-gap requirements and DoD disconnected operations guidance.

---

## 1. Problem Statement

| Constraint | Current State | IL5/IL6 Requirement |
|---|---|---|
| API connectivity | Required for every operation | Must work with zero network |
| Topology storage | Server-side SQLite (`network_canvas.db`) | Must persist locally in browser |
| Simulation engine | Python `simulation.py` on server | Must run client-side |
| Compliance checks | Python `compliance.py` on server | Snapshot or offline-capable rule engine |
| Assets (JS/CSS/fonts) | Served from Flask static | Must be pre-cached |
| Authentication | Session-based (server-required) | Must support offline token or bypass |
| LLM features | Ollama/Claude API calls | Graceful degradation (disabled) |

---

## 2. PWA Feasibility Assessment

### 2.1 What Already Works Offline

The canvas rendering engine (**JointJS**) is fully vendored in `tools/dashboard/static/vendor/`:
- `joint.js`, `joint.css`
- `jquery.min.js`, `lodash.min.js`, `backbone.min.js`, `dagre.min.js`

`network-canvas.js` (3,800 lines) handles all local graph state:
- Node creation, drag-drop, property editing, delete
- Undo/redo stack (in-memory)
- Heatmap overlays (computed from cached node data)
- Export to SVG (pure-client-side)

**Conclusion:** The entire rendering layer is already client-side. The API dependency is only for persistence and analysis.

### 2.2 What Requires Architecture Changes

| Feature | Server Role | Offline Strategy |
|---|---|---|
| Save topology | `PUT /api/topologies/<id>` → SQLite | IndexedDB via `idb` library |
| Load topology | `GET /api/topologies/<id>` | IndexedDB read |
| List topologies | `GET /api/topologies` | IndexedDB index scan |
| Simulation | `simulation.py` (Python) | Port core logic to JS or pre-compute |
| Compliance audit | `compliance.py` (Python) | Snapshot last online result; read-only offline |
| ATO generation | `ato_generator.py` (Python) | Queue for sync when online |
| Config generation | `config_generator.py` (Python) | Queue for sync when online |
| NetBox sync | External API | Disabled offline |
| AI review/chat | Ollama/Claude | Disabled offline |
| Authentication | Server session | Offline JWT or pre-issued token |

---

## 3. Recommended Architecture: Offline-First PWA

### 3.1 Service Worker Strategy

```
tools/dashboard/static/sw.js
```

Use **Cache-First for static assets** + **Network-First with IndexedDB fallback for API calls**.

```
Service Worker Caches:
├── static-cache-v1        ← JS, CSS, fonts, vendor libs (immutable)
├── template-cache-v1      ← HTML templates (long TTL)
└── api-cache-dynamic      ← Last-known API responses (topology list, etc.)
```

**Cache population:** On `install` event, precache all critical static assets. On `activate`, delete stale caches.

**Fetch interception strategy:**

```
Request Type          Strategy                        Fallback
─────────────         ─────────────────────           ────────────────────
Static assets         Cache-First                     Fail gracefully
GET /api/topologies   Network-First → IndexedDB        Show cached list
GET /api/topologies/  Network-First → IndexedDB        Show cached topology
PUT/POST /api/*       Network-First → Background Sync  Queue in sync store
POST /api/simulate    Network-First                   Return cached result
POST /api/compliance  Network-First                   Return last snapshot
```

### 3.2 Local Persistence — IndexedDB Schema

Use the `idb` library (3KB, MIT, air-gap safe via npm bundle or vendored):

```
DB name: icdev-network-canvas
Version: 1

Object Stores:
├── topologies        { keyPath: 'id' }          ← Full graph_json
├── versions          { keyPath: 'id' }          ← Version snapshots
├── templates         { keyPath: 'id' }          ← Template library
├── compliance_cache  { keyPath: 'topology_id' } ← Last compliance result
├── sync_queue        { keyPath: 'queueId', autoIncrement: true }
│                     { operation, endpoint, payload, created_at }
└── auth_store        { keyPath: 'key' }         ← Offline token storage
```

**Sync Queue:** Any write operation while offline is serialized into `sync_queue`. On reconnect, the service worker drains the queue using **Background Sync API** (`SyncEvent`).

### 3.3 Offline-Capable Simulation

The Python `simulation.py` implements BGP/OSPF/STP simulation as pure graph algorithms (no I/O beyond the topology JSON). These are **portable to JavaScript**:

**Phase 1 (Quick win):** Cache last simulation result in `api-cache-dynamic`. Show "Offline — last simulated 2026-03-28 14:32 UTC" banner.

**Phase 2 (Full port):** Implement `network-simulation-offline.js`:
- BGP convergence: walk AS paths in JointJS graph, detect black-holes
- OSPF: Dijkstra shortest path (already uses dagre/graph-lib)
- STP: Spanning tree on switch-only subgraph
- Failover: Remove selected node, re-run BGP/OSPF, diff reachability

This is ~600 lines of JS equivalent to `simulation.py`. Dagre (already vendored) provides the graph structure.

### 3.4 Web App Manifest

```json
{
  "name": "ICDEV Network Canvas",
  "short_name": "NetCanvas",
  "description": "IL5/IL6 disconnected network design and ATO tool",
  "start_url": "/network/",
  "display": "standalone",
  "background_color": "#0d1117",
  "theme_color": "#1f6feb",
  "orientation": "landscape",
  "icons": [
    { "src": "/static/icons/nc-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/icons/nc-512.png", "sizes": "512x512", "type": "image/png" }
  ],
  "categories": ["productivity", "utilities"],
  "prefer_related_applications": false
}
```

Add to `canvas.html` base template:
```html
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#1f6feb">
<meta name="mobile-web-app-capable" content="yes">
```

### 3.5 Authentication in Disconnected Mode

**Option A — Pre-issued Offline JWT (Recommended for IL5/IL6)**
- On last online session, server issues a signed JWT with 72h expiry
- JWT stored in `auth_store` IndexedDB (not localStorage — survives SW scope)
- Service worker validates JWT signature client-side (RSA public key embedded at build time)
- User info (name, role, classification level) extracted from JWT claims
- On reconnect, JWT refreshed automatically

**Option B — PIN-based unlock**
- Simpler: hash of PIN stored locally; PIN unlocks cached session
- Not cryptographically strong — avoid for IL6

**Option C — CAC/PIV reader integration**
- Hardware token validates against locally cached certificate chain
- Most appropriate for IL6 SIPR environments
- Requires WebAuthn or PC/SC API (limited browser support)

**Recommendation:** Option A for IL5, Option C for IL6.

### 3.6 Classification Banner in Offline Mode

Classification markings must remain visible even offline. Use `classification_manager.py` to embed the classification level into the JWT claims at login time. The service worker reads the claim and injects the banner CSS/HTML into all cached templates via a **stream transform** approach (no server required).

---

## 4. Air-Gap Deployment Package

### 4.1 Build Pipeline

```bash
# New build script: tools/network/build_pwa.py
python tools/network/build_pwa.py --output dist/network-canvas-pwa.zip

# What it does:
# 1. Collect all static assets (JS, CSS, fonts, vendor libs)
# 2. Run asset fingerprinting (content hash in filenames)
# 3. Generate sw.js with precache manifest (list of versioned URLs)
# 4. Bundle idb.js + sync queue module
# 5. Generate manifest.json with current version
# 6. Package into a zip for air-gap transfer
```

### 4.2 Deployment in Air-Gap

```
# On SIPR workstation (no internet):
1. Transfer network-canvas-pwa.zip via approved removable media (NSA-approved)
2. Extract to Flask static directory (or serve from NGINX/Apache)
3. Ensure HTTPS (required for service workers — use internal CA cert)
4. Navigate to /network/ — browser auto-installs SW on first load
5. Once SW installed, disconnect network — full functionality preserved
```

### 4.3 Update Mechanism (Air-Gap Safe)

Since there's no internet for auto-updates:
1. New version deployed via same removable media transfer
2. Service worker `install` event detects new cache version
3. Prompts user: "Update available — reload to apply"
4. Old cache replaced atomically

---

## 5. Feature Availability Matrix (Offline vs Online)

| Feature | Online | Offline | Notes |
|---|---|---|---|
| Canvas editing (create/move/connect nodes) | ✅ | ✅ | JointJS is client-side |
| Save topology | ✅ | ✅ | IndexedDB, syncs when online |
| Load topology | ✅ | ✅ | IndexedDB |
| Undo/redo | ✅ | ✅ | In-memory stack |
| Export SVG | ✅ | ✅ | JointJS built-in |
| Export draw.io | ✅ | ✅ | Client-side XML generation |
| Export Visio VSDX | ✅ | ⚠️ | Queue for server-side generation |
| Export Ansible/Terraform | ✅ | ⚠️ | Queue for server-side generation |
| Export device configs | ✅ | ⚠️ | Queue for server-side generation |
| Heatmap overlays | ✅ | ✅ | Computed from cached node data |
| Simulation (BGP/OSPF) | ✅ | ⚠️ Phase 2 | Phase 1: cached result only |
| Monte Carlo | ✅ | ❌ | Python-heavy, defer to online |
| Compliance audit | ✅ | ⚠️ | Show last cached snapshot |
| ATO package generation | ✅ | ⚠️ | Queue for server-side |
| STIG import (.ckl) | ✅ | ✅ | File parsed client-side |
| NetBox sync | ✅ | ❌ | External API |
| Network discovery | ✅ | ❌ | Requires SNMP/SSH reachability |
| AI review / chat | ✅ | ❌ | LLM API unavailable |
| Vulnerability overlay | ✅ | ⚠️ | Cache last uploaded scan |
| Template library | ✅ | ✅ | Pre-cached templates |
| Design patterns | ✅ | ✅ | Pre-cached |
| Classification banner | ✅ | ✅ | Embedded in JWT + SW |
| Audit trail | ✅ | ✅ | Local queue, syncs online |
| Project phase gates | ✅ | ⚠️ | Gate checks need server |

**Legend:** ✅ Full | ⚠️ Degraded/Cached | ❌ Unavailable

---

## 6. Implementation Phases

### Phase 1 — Service Worker + Static Cache (3 days)
**Deliverable:** App loads and renders canvas offline; read-only topology viewing works.

1. Create `tools/dashboard/static/sw.js` with static asset precaching
2. Create `tools/dashboard/static/manifest.json`
3. Register SW in `canvas.html` base template
4. Add `<link rel="manifest">` and theme-color meta
5. Implement IndexedDB topology store (`tools/dashboard/static/js/nc-offline.js`)
6. Intercept `GET /api/topologies*` — serve from IndexedDB if offline
7. Add offline status indicator (banner: "Offline Mode — Changes will sync when connected")

**Test:** Disconnect network → open canvas → verify topology renders from cache.

### Phase 2 — Offline Write + Sync Queue (3 days)
**Deliverable:** Full read-write offline; changes sync on reconnect.

1. Queue `PUT/POST /api/topologies*` to IndexedDB `sync_queue` when offline
2. Register Background Sync tag `topology-sync`
3. On `sync` event: drain queue, POST to server in order, handle conflicts
4. Implement conflict resolution: server-wins for IL5 (audit trail + server is authoritative)
5. Extend to queue ATO, config generation requests
6. Add visual sync indicator (queued changes count badge)

### Phase 3 — Offline JWT Auth (2 days)
**Deliverable:** Authenticated offline sessions with classification markings.

1. Server issues offline JWT on login (RS256, 72h TTL)
2. Embed RSA public key in `sw.js` at build time (part of air-gap package)
3. SW validates JWT on every fetch intercept
4. Inject classification banner from JWT `classification` claim
5. Handle JWT expiry: show re-authentication prompt

### Phase 4 — Client-Side Simulation (5 days)
**Deliverable:** BGP/OSPF/STP simulation runs fully offline.

1. Port `simulation.py` BGP convergence algorithm to `network-simulation-offline.js`
2. Port OSPF shortest path (Dijkstra using existing dagre graph)
3. Port STP spanning tree for switch subgraph
4. Integrate with existing `network-simulation.js` UI — detect online/offline, route to correct engine
5. Unit test JS simulation against Python fixture outputs for parity

### Phase 5 — PWA Build Pipeline + Air-Gap Package (2 days)
**Deliverable:** One-command zip for air-gap transfer.

1. `tools/network/build_pwa.py`: fingerprint assets, generate SW precache manifest, bundle idb.js
2. GitLab CI job: `build:pwa-airgap` produces artifact `network-canvas-pwa-{version}.zip`
3. Deployment runbook: `docs/runbooks/airgap-pwa-deploy.md`
4. Integrity check: SHA-256 manifest included in zip for verification on receiving end

---

## 7. Technical Constraints & Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Service workers require HTTPS | High | High | Internal CA cert must be pre-installed on SIPR workstation |
| Background Sync not supported in all browsers | Medium | Medium | Fallback: manual "Sync Now" button triggers queue drain on reconnect |
| IndexedDB storage quota exceeded (large topologies) | Low | Medium | Warn at 80% quota; prune oldest cached versions |
| JWT expiry during extended disconnected ops | Medium | High | Set 72h TTL; warn user at 48h; allow admin to pre-issue extended-duration tokens |
| Python simulation parity with JS port | Medium | Medium | Fixture-based regression tests comparing JSON outputs |
| idb.js vendor dependency | Low | Low | Idb is 3KB, MIT license; vendor it into the zip |
| Browser fingerprinting changes SW cache key | Low | Low | Use content-hash naming not URL-based cache keys |

---

## 8. Compliance Notes

### NIST 800-53 Controls Addressed
- **AC-17 (Remote Access):** Offline JWT tokens are scoped to specific classification levels
- **AU-9 (Audit Log Protection):** Local audit queue is integrity-protected (HMAC) before sync
- **SC-28 (Protection of Information at Rest):** IndexedDB encrypted via browser storage (FIPS 140 mode in NSS for Firefox on SIPR; Chrome sandboxed storage)
- **SI-2 (Flaw Remediation):** Air-gap update package includes SBOM and version manifest
- **CM-6 (Configuration Settings):** SW version pinned to deployment package; no auto-update from internet

### Air-Gap Transfer Checklist
- [ ] Transfer via NSA-approved removable media only
- [ ] Verify SHA-256 of zip before extraction
- [ ] HTTPS with internal CA cert configured before SW registration
- [ ] Classification level confirmed with ISO before deployment
- [ ] Offline JWT private key stored in HSM or HSM-backed key management

---

## 9. Effort Estimate

| Phase | Description | Effort |
|---|---|---|
| 1 | SW + static cache + IndexedDB read | 3 days |
| 2 | Offline write + Background Sync | 3 days |
| 3 | Offline JWT auth + classification banner | 2 days |
| 4 | Client-side simulation (JS port) | 5 days |
| 5 | Build pipeline + air-gap package | 2 days |
| **Total** | | **~15 days** |

Dependencies: HTTPS internal CA cert available; SIPR test workstation for E2E validation; approval from ISO for offline JWT design.

---

## 10. Recommended Next Steps

1. **Decide simulation scope:** Phase 4 (JS simulation port) is the highest-effort item. If IL5/IL6 operators primarily need read-only canvas viewing with cached compliance snapshots, skip Phase 4 and deliver a useful PWA in ~10 days.
2. **Prototype service worker:** A 2-hour spike with `sw.js` static cache will confirm browser compatibility on SIPR workstations (Firefox ESR or Chrome is typical).
3. **Coordinate with ISO:** Offline JWT design needs approval — the classification marking embedded in the token is a SORN/data-handling concern.
4. **Vendor idb.js:** Add `tools/dashboard/static/vendor/idb.min.js` to avoid npm in air-gap build.
