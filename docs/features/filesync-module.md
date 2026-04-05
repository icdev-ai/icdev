<!-- CUI // SP-CTI -->
# File & Folder Sync Module

| Field | Value |
|-------|-------|
| **Module** | File Sync |
| **ADRs** | D-SYNC-1 through D-SYNC-16 |
| **DB Tables** | `sync_jobs`, `sync_state`, `sync_log` (append-only), `sync_conflicts`, `sync_file_versions` |
| **Config** | `args/filesync_config.yaml` |
| **Dashboard** | `/filesync` |
| **CLI** | `python tools/filesync/sync_engine.py` |

---

## Problem Statement

ICDEV™ projects require file and folder synchronization between local directories, remote SFTP servers, and cloud storage backends (S3, Azure Blob, GCS). Existing solutions (rsync, rclone) are external dependencies that complicate air-gapped deployments and don't integrate with ICDEV™'s audit trail, conflict resolution, or dashboard.

## Goals

1. Syncthing-inspired file sync with SHA-256 content hashing for change detection
2. Push/pull/bidirectional sync modes with configurable conflict resolution
3. Multiple provider backends: local filesystem, SFTP, cloud storage (S3/Azure/GCS)
4. `.syncignore` pattern support (gitignore-subset via `fnmatch`)
5. Optional file watching (watchdog) with periodic scan fallback
6. Full audit trail with append-only `sync_log` (NIST AU compliance)
7. Dashboard UI for job management, execution, and activity monitoring
8. Zero external dependencies for core operation (stdlib only; paramiko/watchdog optional)

## Architecture

### Provider Abstraction (D-SYNC-1, D-SYNC-12)

```
SyncTargetProvider (ABC)
├── LocalSyncProvider    — stdlib Path/os.walk
├── SFTPSyncProvider     — paramiko + subprocess ssh/scp fallback
└── CloudSyncProvider    — wraps existing StorageProvider ABC
```

### Sync Pipeline (10 steps)

1. Load job config from DB + merge YAML defaults
2. Parse `.syncignore` from source root
3. Scan source → manifest `{path: {hash, size, mtime}}`
4. Scan dest (or load cached state from `sync_state` for fast-path)
5. Diff manifests → action list: `[{action, path, reason}]`
6. Resolve conflicts per configured strategy
7. Execute transfers via `ThreadPoolExecutor` (respect bandwidth limit)
8. Optionally verify transferred files (re-hash dest)
9. Update `sync_state` with new hashes
10. Append to `sync_log` + audit trail

### Change Detection (D-SYNC-2, D-SYNC-3)

- **Fast-skip**: If `mtime + size` unchanged vs cached state, skip expensive SHA-256 hash
- **Full hash**: SHA-256 of entire file content (files ≤ 4MiB)
- **Block hash**: 128KiB block-level hashing for files > 4MiB

### File Watching (D-SYNC-8)

Two detection backends with automatic fallback:

| Backend | Detection | Latency | Dependency |
|---------|-----------|---------|------------|
| **watchdog** | OS-level inotify/FSEvents/ReadDirectoryChangesW | Real-time (~2s debounce) | `pip install watchdog` |
| **Periodic scan** | `os.walk` + hash comparison | Configurable (default 60s) | None (stdlib) |

**Watcher lifecycle:**
1. `watch_job(job_id)` runs an initial sync to establish baseline
2. Starts `FileWatcher` with watchdog (or polling fallback)
3. On file changes detected, debounces for 2 seconds, then triggers incremental sync
4. Watcher runs in a background thread — dashboard remains responsive
5. `stop_watching(job_id)` cleanly stops the watcher and resets job status to "idle"

**Dashboard integration:** Watch/Stop Watch buttons in the Actions column, "Watching" stat card (teal), watchers API endpoint.

### Daemon Mode (D-SYNC-9)

Continuous synchronization daemon that manages all jobs:

```bash
python tools/filesync/sync_engine.py --daemon --json
```

- Scans all jobs with `schedule_interval_seconds > 0` and runs them on schedule
- Starts file watchers for jobs with watcher enabled
- Respects quiet hours (default 02:00–06:00 UTC) from `args/filesync_config.yaml`
- Graceful shutdown on SIGINT/SIGTERM

### File Version Control (D-SYNC-16)

Before overwriting a file during sync, the previous version is preserved:

```
dest_root/
├── report.docx              ← current version
└── .versions/
    ├── report.docx.v1       ← first version
    ├── report.docx.v2       ← second version
    └── report.docx.v3       ← third version (oldest pruned when max reached)
```

**Key behaviors:**
- SHA-256 content hash deduplication — skip snapshot if identical hash already versioned
- Configurable max versions per file (default: 10, 0 = unlimited)
- Automatic pruning of oldest versions beyond the limit
- Restore any version via API (creates a restore-point before overwriting)
- Version metadata tracked in `sync_file_versions` DB table

**Implementation:** `tools/filesync/versioner.py` — `FileVersioner` class with `snapshot_before_overwrite()`, `list_versions()`, `restore_version()`, `get_version_stats()`.

### Conflict Resolution (D-SYNC-5)

| Strategy | Behavior |
|----------|----------|
| `last_write_wins` | Newer mtime wins (default) |
| `source_wins` | Source always overwrites dest |
| `rename_both` | Keep both with `.conflict-{timestamp}` suffix |
| `skip` | Log conflict, take no action |

## DB Schema

### `sync_jobs` (mutable)
- `id`, `name`, `source_path`, `source_provider`, `dest_path`, `dest_provider`
- `sync_mode` (push/pull/bidirectional), `conflict_strategy`, `ignore_file`
- `status` (idle/scanning/syncing/completed/failed/paused/watching)
- `schedule_interval_seconds`, `bandwidth_limit_kbps`, `max_workers`
- `delete_orphans`, `last_run_at`, `last_success_at`
- `files_synced`, `files_skipped`, `files_conflicted`, `bytes_transferred`
- `error_message`, `config_json`, `classification`, `project_id`, `created_by`

### `sync_state` (mutable — per-file hash cache)
- `job_id`, `relative_path`, `content_hash`, `file_size`, `mtime_epoch`
- `side` (source/dest), `last_synced_at`, `last_synced_hash`
- UNIQUE(job_id, relative_path, side)

### `sync_log` (append-only — NIST AU)
- `job_id`, `action`, `relative_path`, `source_hash`, `dest_hash`
- `bytes_transferred`, `duration_ms`, `resolution`, `error_detail`
- `classification`, `created_at`

### `sync_conflicts` (mutable)
- `id`, `job_id`, `relative_path`
- Source/dest: `hash`, `mtime`, `size`
- `resolution` (pending/source_wins/dest_wins/renamed/skipped/manual)

### `sync_file_versions` (append-only — D-SYNC-16)
- `id`, `job_id`, `relative_path`, `version_number`
- `content_hash` (SHA-256), `file_size`, `version_path`
- `action` (auto/restore), `created_by`, `created_at`
- Index on `(job_id, relative_path, version_number)`

## Configuration

`args/filesync_config.yaml` controls:
- Detection settings (hash algorithm, fast-skip, block size threshold)
- Watcher settings (watchdog vs periodic scan, debounce interval)
- Transfer settings (max workers, bandwidth limit, verify after transfer)
- SFTP defaults (port, known hosts check, timeout)
- Provider config (cloud storage bucket/prefix defaults)
- Scheduling (daemon interval, quiet hours)

## CLI Commands

```bash
# Job management
python tools/filesync/sync_engine.py --create --name "Backup" --source /src --dest /dst --json
python tools/filesync/sync_engine.py --list --json
python tools/filesync/sync_engine.py --status --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --delete --job-id "fsync-xxx" --json

# Sync execution
python tools/filesync/sync_engine.py --run --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --run --job-id "fsync-xxx" --dry-run --json
python tools/filesync/sync_engine.py --run-all --json

# Conflict management
python tools/filesync/sync_engine.py --conflicts --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --resolve --conflict-id "fc-xxx" --resolution source_wins --json

# Daemon & watch modes
python tools/filesync/sync_engine.py --daemon --json
python tools/filesync/sync_engine.py --watch --job-id "fsync-xxx" --json

# Health
python tools/filesync/sync_engine.py --health --json
```

## Dashboard

`/filesync` page provides:
- **Stat grid**: Total Jobs, Active, Watching (teal), Completed, Failed, Conflicts, Transferred bytes
- **Sync Jobs table**: Name, Source, Dest, Mode, Status, Last Run, Files, Actions (Run/Watch/FIM/Delete)
- **Recent Sync Activity**: Time, Job, Action, Path, Bytes, Duration, Detail
- **Create Sync Job modal**: 7-field form (name, source/dest paths, providers, mode, conflict strategy)
- **Bulk actions**: Run All, Refresh

**Interactive actions per job:**
- **Run** — Execute one-shot sync
- **Watch** — Start real-time file watcher (watchdog/polling) with auto-sync on changes
- **FIM** — Run File Integrity Monitoring check
- **Delete** — Remove job (with confirmation)

## REST API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/filesync/stats` | Module statistics |
| GET | `/api/filesync/jobs` | List all sync jobs |
| POST | `/api/filesync/jobs` | Create new sync job |
| GET | `/api/filesync/jobs/<id>` | Get job details |
| DELETE | `/api/filesync/jobs/<id>` | Delete a sync job |
| POST | `/api/filesync/jobs/<id>/run` | Execute sync job |
| POST | `/api/filesync/run-all` | Run all idle jobs |
| GET | `/api/filesync/log` | Recent sync log |
| GET | `/api/filesync/conflicts` | Unresolved conflicts |
| POST | `/api/filesync/conflicts/<id>/resolve` | Resolve a conflict |
| POST | `/api/filesync/jobs/<id>/fim` | Run FIM integrity check |
| POST | `/api/filesync/jobs/<id>/watch` | Start file watcher |
| DELETE | `/api/filesync/jobs/<id>/watch` | Stop file watcher |
| GET | `/api/filesync/watchers` | List active watchers |
| GET | `/api/filesync/jobs/<id>/versions` | List file versions |
| POST | `/api/filesync/versions/<id>/restore` | Restore a file version |
| GET | `/api/filesync/health` | Module health check |

## Architecture Decisions

| ADR | Decision |
|-----|----------|
| D-SYNC-1 | `SyncTargetProvider` ABC with Local/SFTP/Cloud implementations (D66 pattern) |
| D-SYNC-2 | SHA-256 content hash with fast-skip (mtime+size unchanged → skip hash) |
| D-SYNC-3 | Block-level hashing (128KiB) for files >4MiB; full-file transfer for remote |
| D-SYNC-4 | `.syncignore` parsed via stdlib `fnmatch` (gitignore-subset) |
| D-SYNC-5 | Conflict strategies per job: last_write_wins, rename_both, source_wins, skip |
| D-SYNC-6 | `ThreadPoolExecutor` for parallel transfers (D-SC-1 pattern) |
| D-SYNC-7 | `sync_log` append-only (NIST AU); other tables allow UPDATE |
| D-SYNC-8 | File watching via optional watchdog; periodic os.walk fallback |
| D-SYNC-9 | Daemon mode with quiet hours (D359 pattern) |
| D-SYNC-10 | Bandwidth throttle via `time.sleep()` between chunks (zero deps) |
| D-SYNC-11 | `.syncignore` auto-excludes `.git/`, `__pycache__/`, `.env` by default |
| D-SYNC-12 | Provider abstraction allows mixed-provider sync (local→S3, SFTP→local) |
| D-SYNC-16 | File version control — snapshot files in `.versions/` before overwrite, SHA-256 dedup, configurable max versions, restore via API |

## Testing

```bash
pytest tests/test_filesync.py -v    # 71 tests covering all components
```

Test coverage: providers (local, SFTP, cloud), ignore parser, scanner, change detector, conflict resolver, transfer engine, sync engine (job CRUD, execution, dry run, health), API endpoints, dashboard API.

## Security Considerations

- `sync_log` is append-only (NIST AU-2 compliance) — added to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- SHA-256 content hashing for tamper detection
- Classification markings preserved during sync (`classification` column on all tables)
- SFTP supports key-based authentication (no password storage in DB)
- Cloud provider wraps existing `StorageProvider` ABC with established credential handling
- Bandwidth throttling prevents network saturation in shared environments
<!-- CUI // SP-CTI -->
