# CUI // SP-CTI
# DAT Ingestion Pipeline — Manifest Shard

Modular ingestion pipeline for Diplomatic Analysis & Telemetry (DAT) data.
Reads from three source types and emits a unified JSON manifest with per-source record counts.

## Tools

| File | Description | Input | Output |
|------|-------------|-------|--------|
| `tools/dat/ingestion_engine.py` | Main ingestion engine — reads all three sources, writes manifest JSON, persists audit row | `args/dat_config.yaml`; source directories | `data/dat/ingestion_manifest.json`; audit row in `dat_ingestion_log` |
| `tools/dat/dti_update_runner.py` | One-shot scheduler runner — ingestion + DTI refresh in sequence; exits non-zero if > 10 min | `args/dat_config.yaml`; `--bypass-cooldown` for testing | stdout JSON; DTI snapshot in `sg_dat_dti_snapshots` |
| `tools/dat/icdev_dat_scheduler_task.xml` | Windows Task Scheduler XML — repeats every 6 hours; hard-caps execution at 10 min | Task Scheduler import | Windows scheduled task `\ICDEV DAT DTI Update` |

## Source Types

| Source Key | Format | Reader Function | Config Key |
|------------|--------|-----------------|------------|
| `state_dept_cables` | CSV files | `read_state_dept_cables()` | `args/dat_config.yaml › state_dept_cables` |
| `unsc_schedule` | JSON feeds | `read_unsc_schedule()` | `args/dat_config.yaml › unsc_schedule` |
| `backchannel_logs` | Plain-text log files | `read_backchannel_logs()` | `args/dat_config.yaml › backchannel_logs` |

## CLI Commands

```bash
# Run full ingestion pipeline (JSON output)
python tools/dat/ingestion_engine.py --json

# Dry run — read sources but do not write manifest or DB
python tools/dat/ingestion_engine.py --dry-run

# Custom config path
python tools/dat/ingestion_engine.py --config args/dat_config.yaml --json

# Verbose logging
python tools/dat/ingestion_engine.py --verbose --json

# One-shot ingestion + DTI update (as called by Task Scheduler)
python tools/dat/dti_update_runner.py --json

# Bypass 6-hour cooldown — use for smoke testing
python tools/dat/dti_update_runner.py --bypass-cooldown --json

# Register Windows Task Scheduler task (run once as admin)
schtasks /Create /XML tools\dat\icdev_dat_scheduler_task.xml /TN "\ICDEV DAT DTI Update"

# Trigger a manual test run via Task Scheduler
schtasks /Run /TN "\ICDEV DAT DTI Update"

# Remove the scheduled task
schtasks /Delete /TN "\ICDEV DAT DTI Update" /F
```

## Manifest Output Schema

```json
{
  "manifest_id": "<uuid>",
  "run_at": "<ISO-8601>",
  "config_path": "args/dat_config.yaml",
  "classification": "CUI",
  "sources": {
    "state_dept_cables":  { "record_count": N, "files_read": N, "status": "ok|partial|path_not_found|disabled", "errors": [] },
    "unsc_schedule":      { "record_count": N, "files_read": N, "status": "...", "errors": [] },
    "backchannel_logs":   { "record_count": N, "files_read": N, "status": "...", "errors": [] }
  },
  "total_records": N,
  "status": "ok|partial",
  "manifest_path": "data/dat/ingestion_manifest.json"
}
```

## Database

| Table | Type | Schema |
|-------|------|--------|
| `dat_ingestion_log` | append-only audit | `id, run_at, config_path, state_dept_count, unsc_count, backchannel_count, total_count, status, manifest_path, detail_json, created_at` |

DB path: `data/dat.db` (overridden by `ICDEV_STORAGE_BACKEND` env var).

## Config

`args/dat_config.yaml` — source paths, glob patterns, CSV dialect, JSON records key,
output manifest path, DB path. Each source has an `enabled` flag for selective ingestion.
