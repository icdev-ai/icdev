# Diplomatic Activity Tracker (DAT)

**Classification:** CUI  
**Module path:** `tools/dat/`, `tools/strategos/dat.py`  
**Dashboard:** `/strategos/dat`

---

## Overview

The Diplomatic Activity Tracker ingests signals from three intelligence sources and computes a **Diplomatic Tension Index (DTI)** score (0–100) for each monitored theater. A higher score indicates greater diplomatic instability. Scores are refreshed on a 6-hour cadence by a Windows Task Scheduler job and a Genesis reflex.

Supported theaters: `global`, `ukraine`, `pacific`, `middle_east`.

---

## Quick Start

```bash
# 1. Drop source files into the data directories (see "Configure Sources" below)

# 2. Run a one-shot ingestion + DTI refresh
python tools/dat/dti_update_runner.py --json

# 3. Check the current score via the API
curl http://localhost:5050/api/strategos/dat/score?theater=global
```

---

## Configure Sources

All source paths and ingestion settings live in `args/dat_config.yaml`. Edit that file — never hardcode paths in Python.

### 1. State Department Cable Traffic

```yaml
state_dept_cables:
  source_path: "data/dat/state_dept_cables"   # directory of CSV files
  file_glob: "*.csv"                           # which files to read
  csv_dialect: excel                           # Python csv.Dialect name
  extra_header_rows: 0                         # skip N extra header rows
  enabled: true
```

**Step-by-step:**

1. Place one or more `.csv` files in `data/dat/state_dept_cables/`.
2. Each file must contain these columns (order does not matter):

   | Column | Type | Notes |
   |--------|------|-------|
   | `cable_id` | string | Unique reference |
   | `origin` | string | Originating embassy/post |
   | `classification` | string | e.g., `CUI`, `CONFIDENTIAL` |
   | `subject` | string | Cable subject line |
   | `tension_level` | string | `low`, `medium`, `high`, or `critical` |
   | `keywords` | string | Comma-separated keywords |
   | `summary` | string | Free-text summary |
   | `received_at` | ISO-8601 datetime | e.g., `2026-05-16T14:30:00Z` |

3. To disable this source without deleting files, set `enabled: false`.

### 2. UNSC Meeting Schedule

```yaml
unsc_schedule:
  source_path: "data/dat/unsc_schedule"   # directory of JSON files
  file_glob: "*.json"
  records_key: "meetings"                 # top-level key containing the array
  enabled: true
```

**Step-by-step:**

1. Place one or more `.json` files in `data/dat/unsc_schedule/`.
2. Each file must have a top-level `meetings` array (or the key named in `records_key`):

   ```json
   {
     "meetings": [
       {
         "topic": "Ceasefire resolution vote",
         "agenda_item": "SC/15742",
         "emergency": true,
         "veto_cast": false,
         "walkout": false,
         "participating_states": ["USA", "RUS", "CHN"],
         "scheduled_at": "2026-05-15T10:00:00Z",
         "outcome": "veto blocked"
       }
     ]
   }
   ```

   | Field | Type | Notes |
   |-------|------|-------|
   | `emergency` | bool | Emergency session flag |
   | `veto_cast` | bool | Any P5 veto during session |
   | `walkout` | bool | Any delegation walkout |
   | `scheduled_at` | ISO-8601 | Session date/time |

3. To change the records key (e.g., `sessions`), update `records_key: sessions`.

### 3. Back-Channel Communication Logs

```yaml
backchannel_logs:
  source_path: "data/dat/backchannel_logs"   # directory of .log files
  file_glob: "*.log"
  comment_prefix: "#"                         # lines starting with this are skipped
  enabled: true
```

**Step-by-step:**

1. Place one or more `.log` files in `data/dat/backchannel_logs/`.
2. Each non-blank, non-comment line represents one back-channel event. Lines are parsed as JSON objects:

   ```json
   {"channel_type": "bilateral", "parties": ["US-STATE", "KREMLIN"], "frequency_delta": -0.35, "escalation_flag": true, "communication_breakdown": false, "metadata": {}, "observed_at": "2026-05-16T08:00:00Z"}
   ```

   | Field | Type | Notes |
   |-------|------|-------|
   | `channel_type` | string | `bilateral`, `multilateral`, etc. |
   | `parties` | array | Parties involved |
   | `frequency_delta` | float | Change in contact rate; negative = less contact |
   | `escalation_flag` | bool | Escalation event detected |
   | `communication_breakdown` | bool | Channel broken down |
   | `observed_at` | ISO-8601 | Observation timestamp |

3. Lines starting with `#` are treated as comments and ignored. Set `comment_prefix: ""` to disable.

### Disabling a Source

Set `enabled: false` for any source. The ingestion engine will skip it and report `skipped` status in the manifest. DTI will be computed from the remaining active sources.

---

## DTI Score Interpretation

The DTI score is a weighted composite of three component scores, each normalized to 0–100.

```
DTI = Cable Score × 0.40 + UNSC Score × 0.30 + Backchannel Score × 0.30
```

### Score Bands

| Score | Severity | Meaning |
|-------|----------|---------|
| 0–19 | Normal | Routine diplomatic activity; no elevated tension |
| 20–39 | Elevated | Increased cable traffic or UNSC engagement; monitor |
| 40–59 | Heightened | Multiple high-tension signals; review recommended |
| 60–79 | High | Active escalation indicators; consider action |
| 80–100 | Critical | Severe tension across multiple channels; urgent |

### Component Score Details

**Cable Score (40% weight)**

Computed from State Department cables received in the past 30 days. Each cable contributes a tension value based on its `tension_level` field:

| `tension_level` | Raw value |
|-----------------|-----------|
| `low` | 0.10 |
| `medium` | 0.40 |
| `high` | 0.75 |
| `critical` | 1.00 |

Older cables are down-weighted using exponential decay: a cable N days old counts at `exp(-0.05 × N)` of its face value. A 14-day-old cable has ~53% weight; a 30-day-old cable has ~22% weight.

**UNSC Score (30% weight)**

Computed from UNSC meeting events in the past 30 days. Point values:

| Event | Points |
|-------|--------|
| Emergency session | +25 |
| Veto cast | +20 |
| Delegation walkout | +15 |
| High meeting density | +0 to +10 (bonus) |

Score is capped at 100.

**Backchannel Score (30% weight)**

Computed from back-channel log events in the past 30 days:

| Signal | Weight |
|--------|--------|
| Escalation rate (% of events with `escalation_flag=true`) | 50% |
| Breakdown rate (% of events with `communication_breakdown=true`) | 35% |
| Negative frequency rate (% of events with `frequency_delta < -0.2`) | 15% |

Formula: `(escalation_rate × 0.50 + breakdown_rate × 0.35 + neg_freq_rate × 0.15) × 100`

### Lookback Window

All three components use a **30-day rolling window**. Records older than 30 days are excluded from scoring. Cable records also apply the decay function within this window.

---

## Automated Updates

### Windows Task Scheduler (every 6 hours)

Import the provided task XML to register the scheduler job:

```powershell
schtasks /Create /XML "tools\dat\icdev_dat_scheduler_task.xml" /TN "\ICDEV DAT DTI Update"
```

The job runs at system boot, at user logon (after a 2-minute delay), and every 6 hours thereafter. It executes:

```
python tools\dat\dti_update_runner.py --json
```

Working directory: `C:\AI\ICDev`. Timeout: 10 minutes. Retries on failure: 2× at 5-minute intervals.

### Genesis Reflex (programmatic)

The `dat_refresh` reflex is called by `dti_update_runner.py` and enforces a 6-hour cooldown to prevent duplicate runs within the same cadence window. To force a refresh bypassing the cooldown:

```bash
python tools/dat/dti_update_runner.py --bypass-cooldown --json
```

To preview what would run without writing to the database:

```bash
python tools/dat/dti_update_runner.py --dry-run
```

---

## API Reference

All endpoints are served by the Strategos blueprint. All responses include the header `X-Classification: CUI`.

### GET `/strategos/dat`

Renders the interactive DAT dashboard page.

**Query parameters:**

| Parameter | Values | Default |
|-----------|--------|---------|
| `theater` | `global`, `ukraine`, `pacific`, `middle_east` | `global` |

---

### GET `/api/strategos/dat/score`

Returns the current DTI score for a theater.

**Query parameters:**

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `theater` | string | `global` | Theater ID |
| `live` | bool | `false` | If `true`, recomputes DTI before returning |

**Response:**
```json
{
  "theater_id": "global",
  "dti_score": 47.25,
  "cable_score": 62.10,
  "unsc_score": 35.00,
  "backchannel_score": 28.50,
  "cable_count": 14,
  "unsc_count": 3,
  "backchannel_count": 22,
  "computed_at": "2026-05-16T12:00:00Z"
}
```

---

### GET `/api/strategos/dat/history`

Returns up to 48 historical DTI snapshots for a theater.

**Query parameters:**

| Parameter | Type | Default |
|-----------|------|---------|
| `theater` | string | `global` |
| `limit` | integer | `48` |

**Response:** Array of score objects in reverse chronological order.

---

### POST `/api/strategos/dat/ingest/cable`

Adds a single cable record.

**Request body:**
```json
{
  "theater_id": "global",
  "cable_ref": "KYIV-2026-001",
  "origin": "AMEMBASSY KYIV",
  "subject": "Situation update",
  "tension_level": "high",
  "summary": "Summary text",
  "received_at": "2026-05-16T08:00:00Z",
  "classification": "CUI",
  "keywords": ["escalation", "ceasefire"]
}
```

---

### POST `/api/strategos/dat/ingest/unsc`

Adds a single UNSC event record.

**Request body:**
```json
{
  "theater_id": "global",
  "event_type": "emergency",
  "topic": "Ceasefire resolution",
  "agenda_item": "SC/15742",
  "emergency": true,
  "veto_cast": false,
  "walkout": false,
  "participating_states": ["USA", "RUS", "CHN"],
  "scheduled_at": "2026-05-16T10:00:00Z",
  "outcome": "adopted"
}
```

---

### POST `/api/strategos/dat/ingest/backchannel`

Adds a single back-channel record.

**Request body:**
```json
{
  "theater_id": "global",
  "channel_type": "bilateral",
  "parties": ["US-STATE", "KREMLIN"],
  "frequency_delta": -0.35,
  "escalation_flag": true,
  "communication_breakdown": false,
  "metadata": {},
  "observed_at": "2026-05-16T08:00:00Z"
}
```

---

### POST `/api/strategos/dat/refresh`

Forces a DTI recomputation for a theater.

**Request body:**
```json
{ "theater_id": "global" }
```

**Response:** Updated score object (same structure as `/score`).

---

## Programmatic Usage

```python
from tools.strategos.dat import ingest_cable, ingest_unsc_event, ingest_backchannel, refresh_dti, get_latest_dti

# Ingest a cable
ingest_cable(theater_id="ukraine", tension_level="high", origin="AMEMBASSY KYIV", summary="...")

# Ingest a UNSC event
ingest_unsc_event(theater_id="global", emergency=True, veto_cast=False, walkout=False)

# Ingest a back-channel record
ingest_backchannel(theater_id="pacific", escalation_flag=True, frequency_delta=-0.4)

# Force a DTI recomputation and read the result
snap = refresh_dti("ukraine")
print(f"Ukraine DTI: {snap['dti_score']}")   # 0–100

# Read the latest persisted score without recomputing
snap = get_latest_dti("ukraine")
```

---

## Ingestion Manifest

After each ingestion run, a JSON manifest is written to `data/dat/ingestion_manifest.json`:

```json
{
  "manifest_id": "...",
  "run_at": "2026-05-16T12:00:00Z",
  "classification": "CUI",
  "sources": {
    "state_dept_cables": { "status": "ok", "record_count": 14 },
    "unsc_schedule":     { "status": "ok", "record_count": 3 },
    "backchannel_logs":  { "status": "partial", "record_count": 22 }
  },
  "total_records": 39,
  "status": "ok"
}
```

Source status values:

| Status | Meaning |
|--------|---------|
| `ok` | All files read successfully |
| `partial` | Some files had read errors; partial data ingested |
| `path_not_found` | Source directory does not exist or `enabled: false` |

---

## Database Tables

All tables live in `data/icdev.db`. The snapshot table is **append-only** — never UPDATE or DELETE rows.

| Table | Contents |
|-------|----------|
| `sg_dat_cables` | Cable records |
| `sg_dat_unsc_events` | UNSC event records |
| `sg_dat_backchannel` | Back-channel records |
| `sg_dat_dti_snapshots` | Immutable DTI snapshots (append-only) |

Audit log for standalone ingestion runs: `dat_ingestion_log` in `data/dat.db`.

---

## Testing

```bash
# Unit tests for DTI computation
pytest tests/strategos/test_dat.py -v

# Acceptance tests for 3-source ingestion engine
pytest tests/test_dat_ingestion_engine.py -v

# Full suite
pytest tests/ -v --tb=short -k "dat"
```

---

## Troubleshooting

**DTI score is 0.0 for all components**  
→ Check that source directories exist and contain files matching the configured glob pattern.  
→ Verify `received_at` / `scheduled_at` / `observed_at` fields are within the past 30 days.  
→ Run `python tools/dat/ingestion_engine.py --json` and inspect the manifest for `path_not_found` or `partial` status.

**Scheduler job does not appear in Task Scheduler**  
→ Run the `schtasks /Create` command from an elevated (Administrator) PowerShell prompt.

**`ModuleNotFoundError: No module named 'icdev'`**  
→ The repo root is not on `PYTHONPATH`. Activate the virtual environment or install in editable mode: `pip install -e .`

**Cooldown preventing a forced refresh**  
→ Use `--bypass-cooldown`: `python tools/dat/dti_update_runner.py --bypass-cooldown --json`
