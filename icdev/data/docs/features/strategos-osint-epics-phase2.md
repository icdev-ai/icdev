# CUI // SP-CTI
# Strategos — OSINT Epics Phase 2 (sgx-sigint, sgx-geoint, sgx-socmint)

**Phase:** SGX Phase 2  
**Date:** 2026-04-30  
**Status:** Complete (22/22 tasks done, 2026-05-15)

---

## Overview

Three new OSINT epics derived from deep mining of the `vert-strategos` research session signals (662 arxiv papers, forum threads, and GitHub repos). These extend Strategos beyond the Phase 1 epics (CVE/bbot/AIS/DW/OpenCTI) into academic-grade collection capabilities.

---

## Epic: sgx-sigint — SIGINT / RF Attribution

**Research signal:** ARCANE paper (cross-campaign attacker re-identification via passive beacon telemetry) + AI-RAN adversary RF pattern prediction.  
**Existing hook:** `tools/strategos/ais_sdr_receiver.py` (RTL-SDR receiver already present).

| Task | Description |
|---|---|
| sgx-sigint-01 | RTL-SDR + PySDR Python 3.14 air-gap compat check |
| sgx-sigint-02 | DB migration: sg_sigint_events |
| sgx-sigint-03 | Extend ais_sdr_receiver.py → beacon telemetry emission |
| sgx-sigint-04 | Create rf_attribution.py — ARCANE passive fingerprinting |
| sgx-sigint-05 | Wire attribution output → sg_iw_cascade_events (currently empty) |
| sgx-sigint-06 | /api/strategos/sigint/events endpoint |
| sgx-sigint-07 | Manifest + companion sync |
| sgx-sigint-08 | V&V end-to-end |

**Key design decision:** Attribution score = `log(occurrence_count) / log(total_campaigns)`. Attribution gate: score >= 0.6 promotes to IW cascade events.

---

## Epic: sgx-geoint — Satellite / EO Intelligence

**Research signal:** Earth Observation Satellite Systems (EOSS) benchmark paper.  
**Existing hook:** `tools/strategos/gdelt_importer.py` exists but not wired to OSINT scan; `sg_theaters.area_wkt` provides ready-made AOI bounding boxes.

| Task | Description |
|---|---|
| sgx-geoint-01 | Wire gdelt_importer into /osint/scan auto-call |
| sgx-geoint-02 | DB migration: sg_eo_signals |
| sgx-geoint-03 | Create eo_importer.py — Copernicus Sentinel-2 open EO |
| sgx-geoint-04 | Write EO findings → sg_osint_results (finding_type='geo_intelligence') |
| sgx-geoint-05 | Satellite imagery bbox overlay on Map Dashboard |
| sgx-geoint-06 | Manifest + companion sync |
| sgx-geoint-07 | V&V end-to-end |

**Key design decision:** Copernicus Open Access Hub requires no API key for public Sentinel-2 data. AOI derived from `sg_theaters.area_wkt`. Relevance gate: score >= 0.5.

---

## Epic: sgx-socmint — Social Media OSINT

**Research signal:** `UA-Telegram-Milblog`, `Twitter-OSINT-IDF`, and `TaiwanMND-Statement` already appear as sources in `sg_raw_signals` (260 rows) — the data model supports SOCMINT but has no active collection pipeline.

| Task | Description |
|---|---|
| sgx-socmint-01 | pyrogram/telethon Python 3.14 air-gap compat check |
| sgx-socmint-02 | DB migration: sg_socmint_signals |
| sgx-socmint-03 | Create socmint_harvester.py — Telegram + RSS milblog collector |
| sgx-socmint-04 | Wire output → sg_raw_signals (dedup via url_hash) |
| sgx-socmint-05 | Schedule as Genesis reflex (GREEN tier, 6h cadence) |
| sgx-socmint-06 | Manifest + companion sync |
| sgx-socmint-07 | V&V end-to-end |

**Key design decision:** Two collection paths — pyrogram/telethon (primary) and feedparser + public Telegram RSS bridge (fallback). Relevance gate: score >= 0.4 promotes to sg_raw_signals. Genesis reflex returns `metric_value = inserted_count`.

---

## Dependency Graph

```
sgx-sigint: 01 → 03 → 04 → 05 → 06 → 07 → 08
            02 ──────────────────────────────────┘ (parallel with 01)

sgx-geoint: 01 (standalone, auto-call GDELT)
            02 → 03 → 04 → 05 → 06 → 07

sgx-socmint: 01 → 03 → 04 → 05 → 06 → 07
             02 ──────────────────────────┘ (parallel with 01)
```

---

## Tables Populated by These Epics

| Epic | New Table | Feeds Into |
|---|---|---|
| sigint | sg_sigint_events | sg_iw_cascade_events |
| geoint | sg_eo_signals | sg_osint_results, map overlay |
| socmint | sg_socmint_signals | sg_raw_signals |
