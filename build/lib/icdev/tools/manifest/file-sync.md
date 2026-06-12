# File Sync (`tools/filesync/`)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## File Sync (`tools/filesync/`)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Sync Engine | tools/filesync/sync_engine.py | Main orchestrator — job CRUD, sync execution, daemon mode, health | --create, --list, --run, --daemon, --health, --json | Job records / sync status |
| Sync Provider Base | tools/filesync/providers/base.py | `SyncTargetProvider` ABC — list_files, read_file, write_file, delete_file, get_file_info | (library) | SyncTargetProvider ABC |
| Local Provider | tools/filesync/providers/local.py | Local filesystem provider (stdlib Path/os.walk) | (library) | LocalProvider |
| SFTP Provider | tools/filesync/providers/sftp.py | SFTP provider (paramiko + subprocess ssh/scp fallback) | (library) | SFTPProvider |
| Cloud Provider | tools/filesync/providers/cloud.py | Cloud provider wrapping existing `StorageProvider` ABC | (library) | CloudProvider |
| Ignore Parser | tools/filesync/ignore_parser.py | `.syncignore` parser using stdlib `fnmatch` for gitignore-subset glob patterns | (library) | load_ignore_patterns(), filter_files() |
| Scanner | tools/filesync/scanner.py | File tree scanner — SHA-256 manifests with fast-skip (mtime+size) and FIPS 140-2 hash mode | (library) | File manifests |
| Change Detector | tools/filesync/change_detector.py | Manifest diffing → sync action plans (push + bidirectional modes) | (library) | detect_changes_push() |
| Conflict Resolver | tools/filesync/conflict_resolver.py | Strategy pattern for sync conflicts: last_write_wins, rename_both, source_wins, skip | (library) | ConflictResolver |
| Transfer | tools/filesync/transfer.py | ThreadPoolExecutor file transfer with bandwidth throttling and pre-transfer zlib/gzip compression | (library) | Transfer executor |
| Watcher | tools/filesync/watcher.py | File watcher — optional watchdog library with periodic scan fallback | (library) | FileWatcher |


## File Sync (D-SYNC-1 through D-SYNC-12)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Register Competitors | tools/filesync/register_competitors.py | Register competitor sources for sync | --register, --list, --json | Competitor records |
| Service Manager | tools/filesync/service_manager.py | File sync service lifecycle | --start, --stop, --status, --json | Service status |
| Versioner | tools/filesync/versioner.py | File version tracking | --snapshot, --diff, --json | Version records |

