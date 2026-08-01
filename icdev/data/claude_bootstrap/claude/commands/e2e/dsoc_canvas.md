# E2E Test: DDoS & Security Ops Canvas (DSOC)

Verify the **DSOC canvas** at `/dsoc` renders and honors the contracts hardened
in the CNR production-readiness work (cnr-dsoc-01..05):

- **cnr-dsoc-01** — every DSOC route is fail-closed authenticated. Unauthenticated
  API/mutating requests return **401 JSON**; unauthenticated page GETs **redirect
  to /login**. The overview page shows a **Record-only / Simulation** badge — the
  engines emit apply-ready IOS-XR/JunOS config text for human review and never
  push routing changes to live routers.
- **cnr-dsoc-02** — threat/mitigation fields are HTML-escaped; a threat whose
  `source_prefix` contains `<img src=x onerror=alert(1)>` renders **inert** (no
  dialog, escaped text in the DOM).
- **cnr-dsoc-03** — the IQE widget on every DSOC page is wired
  (`/api/dsoc/iqe-query`, reads `question`) and answers a seed query.
- **cnr-dsoc-04** — list APIs never 500 on a missing table (graceful `[]`); the
  Active Mitigations table shows the real **Peak (Gbps)** value
  (`peak_traffic_gbps`, not the always-0 `peak_gbps`).
- **cnr-dsoc-05** — RTBH entries auto-expire (the `bgp_hijack_monitor` Genesis
  reflex calls `rtbh_manager.auto_expire_rtbh`).

## Prerequisites

- Flask dashboard running. Default port `5050` (`ICDEV_DASHBOARD_PORT`); if taken,
  start your own with `python tools/dashboard/app.py --port 5099` and read
  `<PORT>` as your bound port throughout.
- `ICDEV_DSOC_ENABLED=true` in `.env` — the canvas is gated behind this toggle.
- Database initialized. DSOC is PG-primary (`DSOC_STORAGE_BACKEND`); tables
  `dsoc_flowspec_rules`, `dsoc_rtbh_entries`, `dsoc_scrubbing_centers`,
  `dsoc_threats`, `dsoc_mitigations`, `dsoc_bgp_hijacks` present
  (`python -m tools.dsoc_canvas.db.init_db`).
- A valid dashboard session. When `ICDEV_DASHBOARD_API_KEY` is set, `/login`
  auto-authenticates; otherwise log in first, or send the key via the
  `X-ICDEV-API-Key` header on API calls.

## Scenario 1 — Fail-closed auth (cnr-dsoc-01)

1. With **no** session, `POST http://127.0.0.1:<PORT>/api/dsoc/rtbh` with body
   `{"prefix":"1.2.3.0/24","trigger_reason":"manual"}` → **401** with
   `{"error":"Authentication required"}`.
2. With no session, `GET /api/dsoc/threats` → **401**.
3. With no session, browse to `/dsoc` → **302 redirect to /login**.
4. Authenticate, reload `/dsoc` → **200**, and confirm the header shows both the
   **Record-only / Simulation** and **CUI // SP-CTI** badges.

## Scenario 2 — Stored-XSS is inert (cnr-dsoc-02)

1. Authenticated, ingest a poisoned threat:
   ```bash
   curl -s -X POST http://127.0.0.1:<PORT>/api/dsoc/threats \
     -H 'Content-Type: application/json' -H 'X-ICDEV-API-Key: <KEY>' \
     -d '{"source_prefix":"<img src=x onerror=alert(1)>","threat_type":"scanner","confidence_pct":90}'
   ```
2. Browse to `/dsoc` (Recent Threats) and `/dsoc/threats`. **No alert dialog**
   fires. In the DOM the payload appears as escaped text
   (`&lt;img src=x onerror=alert(1)&gt;`), not a live `<img>` element.
   `browser_console_messages` shows no evaluation of the payload.

## Scenario 3 — IQE widget answers a seed query (cnr-dsoc-03)

1. On `/dsoc`, expand the **IQE Query — DSOC** widget.
2. Click a seed chip (e.g. "Active RTBH") or type "show high confidence threats"
   and Run. The widget POSTs `{question, execute}` to `/api/dsoc/iqe-query` and
   renders a result table (or "No rows matched") — **not** the "endpoint not
   configured" error.
3. Repeat on `/dsoc/flowspec`, `/dsoc/rtbh`, `/dsoc/scrubbing`, `/dsoc/threats`,
   `/dsoc/mitigations`, `/dsoc/bgp-security` — each widget is wired.

## Scenario 4 — Graceful lists + peak Gbps (cnr-dsoc-04)

1. Authenticated, create a mitigation:
   ```bash
   curl -s -X POST http://127.0.0.1:<PORT>/api/dsoc/mitigations \
     -H 'Content-Type: application/json' -H 'X-ICDEV-API-Key: <KEY>' \
     -d '{"target_prefix":"198.51.100.0/24","mitigation_type":"scrubbing","peak_traffic_gbps":120.5}'
   ```
2. On `/dsoc`, the Active Mitigations table shows **120.5** under Peak (Gbps)
   (not 0), and the row is highlighted (>100).
3. `GET /api/dsoc/flowspec`, `/rtbh`, `/scrubbing`, `/threats`, `/mitigations`
   each return a **JSON array** (never a 500), even against an empty backend.

## Scenario 5 — RTBH auto-expiry reflex (cnr-dsoc-05)

1. Trigger an RTBH with a short window:
   `POST /api/dsoc/rtbh` `{"prefix":"192.0.2.0/24","trigger_reason":"manual","auto_withdraw_minutes":1}`
   → **201**, entry `status=active`.
2. Backdate its `created_at` beyond the window (or wait), then run the reflex:
   `python tools/genesis/reflexes/bgp_hijack_monitor.py --json`.
   Output reports `rtbh_expired >= 1`.
3. `GET /api/dsoc/rtbh` shows the entry now `status=withdrawn` with a
   `withdrawn_at` timestamp. A `--dry-run` invocation performs **no** writes
   (`rtbh_expired == 0`).

## Pass criteria

- Scenario 1: 401 on unauth API/mutating, 302 on unauth page, both badges shown.
- Scenario 2: XSS payload inert; escaped in DOM; no console evaluation.
- Scenario 3: IQE widget returns results, no "endpoint not configured".
- Scenario 4: peak Gbps displays the real value; all list APIs return arrays.
- Scenario 5: reflex expires overdue RTBH entries; dry-run is side-effect free.
