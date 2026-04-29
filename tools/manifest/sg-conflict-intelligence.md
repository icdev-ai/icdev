# CUI // SP-CTI
# Manifest: SG Conflict Intelligence

Tools for importing, enriching, and querying the `sg_conflict_events` table —
the canonical Strategos conflict/threat event store.

---

## tools/sg/frontline_importer.py

Import DeepState daily GeoJSON frontline snapshots into `sg_conflict_events`.

**Modes**

| Flag | Description |
|------|-------------|
| `--date YYYY-MM-DD` | Import a single snapshot date |
| `--start-date START --end-date END` | Import a date range (legacy bulk) |
| `--since YYYY-MM-DD` | Import all dates from SINCE through yesterday |
| `--bulk START END` | Bulk import START..END inclusive |
| `--incremental` | Auto-detect last date in DB, import from next day to yesterday |

**Options**

| Flag | Description |
|------|-------------|
| `--force` | Delete existing rows before re-importing |
| `--dry-run` | Fetch and transform without writing to DB |
| `--json` | JSON output (for scripting / kanban wiring) |

**Key functions**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `bulk_import` | `(start_date, end_date, *, force, dry_run) → dict` | Batch insert a date range via `executemany` |
| `incremental_import` | `(*, force, dry_run) → dict` | Query `max(snapshot_date)`, fetch next day → yesterday |
| `fetch_deepstate_geojson` | `(snapshot_date) → list` | Download + validate GeoJSON FeatureCollection |
| `run` | `(dates, *, force, dry_run, as_json) → dict` | Single-row upsert loop (used by `--date` / `--since`) |

**Usage examples**

```bash
# Single day
python tools/sg/frontline_importer.py --date 2024-06-01

# Bulk: load a historical range
python tools/sg/frontline_importer.py --bulk 2024-01-01 2024-06-30

# Incremental: pick up only dates not yet in DB (cron-friendly)
python tools/sg/frontline_importer.py --incremental

# Incremental with JSON output (for pipeline consumption)
python tools/sg/frontline_importer.py --incremental --json

# Dry-run preview of incremental delta
python tools/sg/frontline_importer.py --incremental --dry-run

# Force re-import a range (overwrites existing rows)
python tools/sg/frontline_importer.py --bulk 2024-06-01 2024-06-30 --force
```

**Environment**

```
DEEPSTATE_URL_TEMPLATE  URL pattern with {date} placeholder (YYYY-MM-DD).
                        Defaults to https://deepstatemap.live/api/history/{date}
```

**Output shape (incremental / bulk)**

```json
{
  "dry_run": false,
  "force": false,
  "dates_requested": 10,
  "total_inserted": 482,
  "total_skipped": 0,
  "total_failed": 0,
  "fetch_errors": 0,
  "per_date": [...]
}
```

Special top-level keys returned by `incremental_import` when there is nothing to do:

- `"no_baseline": true` — table has no `source='deepstate'` rows yet
- `"already_current": true` — `max(snapshot_date)` is already yesterday

---

## tools/sg/baseline_importer.py

Import baseline conflict datasets (ACLED, UCDP, etc.) into `sg_conflict_events`.

---

## tools/sg/supply_chain_bridge.py

Bridge supply-chain disruption signals into the conflict event store.

---

## SG Tools — Quick Reference
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Frontline Importer | tools/sg/frontline_importer.py | Imports DeepState daily GeoJSON frontline snapshots into sg_conflict_events; supports single-date, range, bulk, incremental, and dry-run modes with https:// enforcement | --date, --start-date/--end-date, --since, --bulk, --incremental, --force, --dry-run, --json | Imported row counts; JSON summary |
| Supply Chain Bridge | tools/sg/supply_chain_bridge.py | Bridges STRATEGOS theater supply chain (Ukraine) from geosigint.db (sg_entities/kg_nodes) to ICDEV (supply_chain_vendors/dependencies); seeds 12 logistics nodes and 10 NATO contractors | --init, --seed, --sync, --kg, --all, --json | Sync results; JSON summary |
