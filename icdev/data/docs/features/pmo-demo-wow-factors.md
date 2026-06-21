# PMO Demo Wow Factors

**Status:** SHIPPED (2026-06-04)
**Epics:** brief, chart, bid, iqe, viewer
**Screenshots:** `playwright/screenshots/pmodemo-vv-*.png`

---

## What Was Built

Five "wow factor" capabilities shipped as 21 atomic Kanban tasks (`pmodemo-*`), targeting the CPMP / GovCon dashboard demo:

| Epic | Capability | Routes |
|------|-----------|--------|
| **Brief** | AI Brief banner on all 4 PMO pages | `/cpmp`, `/cpmp/deliverables`, `/proposals`, `/govcon` |
| **Chart** | Bubble chart: pWin × Weighted Value | `/proposals` |
| **Bid** | Bid/No-Bid/Maybe recommendation badges | `/proposals`, `/api/govcon/proposals/<id>/recommendation` |
| **IQE** | IQE query widget on all 4 pages | all 4 above |
| **Viewer** | PMO Weekly Brief Archive viewer | `/cpmp/reports` |

---

## LLM Fallback Notes

- `/cpmp` AI Brief: `get_pmo_recommendations()` returns "Recommendations unavailable" when no LLM key is configured. This is **expected** — the deterministic fallback renders contract health badges and structure correctly.
- `/proposals` AI Brief: `pipeline_value_rollup()` is fully deterministic (DB query only) — always shows real data.
- `/govcon` AI Brief: `get_status()` and `get_gaps()` are deterministic — always shows real data.
- `/cpmp/deliverables` AI Brief: `auto_detect_issues()` is deterministic — shows live overdue/compliance issues.

---

## 7-Step Demo Flow

### (a) Morning Brief — /cpmp
1. Open `http://localhost:5050/cpmp`
2. Wait ~5s for the **Contract PMO — AI Brief** card to appear at the top
3. Observe: two contracts listed with GREEN health badges
4. Click **W911NF-DEMO-24-C-0042** to drill into contract detail → AI Recommendations panel

**Talking point:** *"The system ran overnight and surfaced the top 3 contracts by risk — you walk in and immediately know where to look."*

### (b) Reports Archive — /cpmp/reports
1. Navigate to `http://localhost:5050/cpmp/reports`
2. Click **View PMO Briefs** (also reachable from the Portfolio button)
3. Select any weekly brief from the archive list
4. The iframe viewer renders the Markdown brief inline

**Talking point:** *"Every brief is preserved. You can replay any Monday morning from the past."*

### (c) Deliverable Command Center — /cpmp/deliverables
1. Navigate to `http://localhost:5050/cpmp/deliverables`
2. Observe **Deliverables — AI Brief** banner: 3 active issues (OVERDUE, SUBCONTRACTOR_COMPLIANCE, PENDING_MODIFICATIONS)
3. Open the **IQE widget** (bottom-right or inline)
4. Type or click chip: *"show overdue deliverables"*
5. Results table filters to overdue CDRLs

**Talking point:** *"Overdue deliverables surface automatically — no hunting through spreadsheets."*

### (d) Capture Portfolio — /proposals
1. Navigate to `http://localhost:5050/proposals`
2. Observe **Capture Portfolio — pWin × Weighted Value** bubble chart
   - Bubble size = ceiling; Y-axis = weighted value; click any bubble → opportunity detail
3. Observe **BID / NO-BID / MAYBE** badges in the proposals table column
4. Click a high-value opportunity to drill in

**Talking point:** *"The bubble chart makes portfolio balance visible in one glance — big bets vs. long shots."*

### (e) IQE — Low pWin Filter
1. On `/proposals`, open the IQE widget
2. Type or click chip: *"show opportunities with pWin < 30%"*
3. Results list filtered opportunities

**Talking point:** *"Natural-language querying replaces 20 minutes of filter configuration."*

### (f) Capability Gaps — /govcon
1. Navigate to `http://localhost:5050/govcon`
2. Open the IQE widget
3. Type: *"show capability gaps in NAICS 541512"*
4. Results show matched capability gap entries

**Talking point:** *"Cross-canvas — same IQE widget works everywhere with context-aware answers."*

### (g) Overnight Digest — /cpmp
1. Navigate back to `http://localhost:5050/cpmp`
2. Point to the **Contract PMO — AI Brief** card
3. *"Three things ran overnight without you..."* — read off the top 3 contract observations

**Talking point:** *"This is the 'Monday morning director brief' — generated autonomously, no analyst required."*

---

## Verification Results (2026-06-04)

| Page | Banner | No 500s | Screenshot |
|------|--------|---------|------------|
| `/proposals` | ✅ Real data (pipeline value, pWin rollup) | ✅ | `pmodemo-brief-proposals.png` |
| `/govcon` | ✅ Real data (337 opps, 48 reqs, 56 drafts) | ✅ | `pmodemo-brief-govcon.png` |
| `/cpmp` | ✅ Fallback (GREEN contracts, recs unavailable) | ✅ | `pmodemo-brief-cpmp.png` |
| `/cpmp/deliverables` | ✅ Real data (3 live issues) | ✅ | `pmodemo-brief-deliverables.png` |
| `/cpmp/reports` | N/A | ✅ | `pmodemo-vv-cpmp-reports.png` |

**Cold-start note:** `/cpmp` banner takes ~6s on first request after dashboard restart (LLM timeout warm-up). Subsequent loads are within 5s.
