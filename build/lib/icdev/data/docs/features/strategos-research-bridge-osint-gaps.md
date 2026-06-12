# CUI // SP-CTI
# Strategos — Research Bridge, OSINT Epics & Infrastructure Gap Fixes

**Phase:** SGX (Strategos Extension Pack)  
**Date:** 2026-04-30  
**Status:** Complete (54/57 tasks done; sgx-octi-04/05/06 in progress)

---

## Overview

Two related workstreams shipped in this phase:

1. **Research Bridge** — wired the Industry Research Engine (`vert-strategos` vertical) to fan scored intelligence into all four live Strategos tables, replacing manual seeding.
2. **Infrastructure Gap Fixes** — resolved three data-pipe disconnects that left the OSINT panel, DIB Supply Chain, and theater tracking unpopulated.

---

## Research Bridge (`tools/strategos/research_bridge.py`)

The bridge runs the 8-stage research pipeline (SCOPE → DOSSIER) against the `strategos` vertical and fans output to:

| Target Table | Content |
|---|---|
| `sg_ghost_signals` | High-score challenges → ghost signals (lat/lon from geo_hint) |
| `sg_hitl_items` | Challenges scoring 0.5–0.79 → HITL review queue |
| `sg_pir_requirements` | Regulatory + compliance challenges → PIR/CCIR/EEI requirements |
| `sg_intelligence_briefs` | Synthesized forecast predictions → Intel Briefs (sitrep/assessment/iir/warnord) |

**Key fixes applied:**
- `_run_new_session()` passed `--vertical strategos` (slug) instead of `vert-strategos` (id)
- `_score_to_priority()` returns integers 1–4, not strings (PostgreSQL INTEGER column)
- `_write_intelligence_brief()` uses `analyst_reviewed=0` and `brief_type='assessment'` (CHECK constraint)

**Results after first run:** 662 signals ingested, 590 challenges scored, 51 ghost signals, 87 HITL items, 62 PIR requirements, 21 intel briefs.

---

## OSINT Extension Epics (42 tasks → 54 done + 3 in progress)

Five epics queued and executed by the Kanban scheduler:

| Epic | Focus | Status |
|---|---|---|
| sgx-cve | CVE bridge — live vulnerability feed to sg_cve_feed | Done |
| sgx-bbot | bbot OSINT scanner wired to sg_osint_results | Done |
| sgx-ais | Live AIS vessel tracking via RTL-SDR / AISHub | Done |
| sgx-dw | Dark web monitor (Tor + Ahmia fallback) | Done |
| sgx-octi | OpenCTI threat intel platform bridge | 6/9 done, 3 in progress |

---

## Infrastructure Gap Fixes

### Gap 1 — OSINT Scan Endpoint (`apps/strategos/blueprint.py:2354`)

**Problem:** `/api/strategos/osint/scan` was a pure stub returning only a UUID.  
**Fix:** Now calls `osint_prestage` (stage RSS signals) then `osint_harvester.harvest(target=...)`. Target derived from POST body or highest-priority active PIR topic if omitted.

### Gap 2 — Supply Chain Edges (`tools/db/seeds/seed_supply_kg_edges.py`)

**Problem:** `sg_kg_edges` had 0 PRODUCES/DEPENDS_ON_SUPPLY edges; supply visualization showed 20 nodes floating disconnected.  
**Fix:** Seeded 13 logical logistics edges across INDOPACOM, Black Sea, and South China Sea theaters. The `/api/strategos/supply/sync` endpoint now re-applies edges on every sync (idempotent).

### Gap 3 — Theaters & Tracks (`tools/db/seeds/seed_sg_theaters.py`)

**Problem:** `sg_theaters` and `sg_tracks` were empty, blocking `reverse_cascade_inference.py` from resolving theater context.  
**Fix:** Seeded 4 theaters (INDOPACOM/EUCOM/CENTCOM/AFRICOM) with AOR bounding boxes and named commanders. Derived 8 vessel tracks from existing `sg_vessel_tracks` data (5 INDOPACOM, 3 EUCOM) with auto-seeded `sg_entities` records.

---

## Genesis Daemon Fixes

| Reflex | Problem | Fix |
|---|---|---|
| `canvas_indexer` | Circuit breaker open — reflex never returned `success` key | Added `"success": errors == 0, "metric_value": float(errors)` |
| `synthesize` | `metric_threshold_not_met` — no `success` key on any return path | Added `success`/`metric_value` to all 4 return paths |
| `experiment` | Disabled, scheduled at 21:00 | Enabled, rescheduled to 15:00 |

---

## Dashboard Health After This Phase

| Page | Table | Rows | Status |
|---|---|---|---|
| Ghost Signals | sg_ghost_signals | 51 | ✅ |
| HITL Queue | sg_hitl_items | 87 | ✅ |
| PIR/CCIR/EEI | sg_pir_requirements | 62 | ✅ |
| Intel Briefs | sg_intelligence_briefs | 21 | ✅ |
| Cyber/CVE | sg_cve_feed | 129 | ✅ |
| Maritime | sg_vessel_tracks | 89 | ✅ |
| OSINT Panel | sg_raw_signals | 260 | ✅ |
| DIB Supply Chain | sg_supply_nodes / sg_kg_edges | 20 / 13 | ✅ |
| Map Dashboard | sg_theaters / sg_tracks | 4 / 8 | ✅ |
| Dark Web | sg_darkweb_signals | 1 | ⚠️ Needs Tor |
| Wargame | sg_nash_scenarios | 0 | ✅ On-demand |
