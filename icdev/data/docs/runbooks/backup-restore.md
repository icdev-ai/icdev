# Backup & Restore Runbook

> CUI // SP-CTI

## Overview

ICDEV manages multiple SQLite databases (operational, memory, platform, per-tenant) and supports PostgreSQL for SaaS deployments. This runbook covers backup procedures, restore operations, integrity verification, encryption, migration management, and disaster recovery.

---

## Databases

| Database | Path | Purpose |
|----------|------|---------|
| `data/icdev.db` | Main operational DB | 193 tables: projects, agents, compliance, audit trail |
| `data/platform.db` | SaaS platform DB | 6 tables: tenants, users, API keys, subscriptions |
| `data/memory.db` | Memory system | 3 tables: entries, daily logs, access log |
| `data/activity.db` | Task tracking | 1 table: tasks |
| `data/tenants/{slug}.db` | Per-tenant DBs | Isolated copy of icdev.db schema per tenant |

---

## Backup Operations

### Backup a Single Database

```bash
python tools/db/backup.py --backup --db icdev --json
```

Output includes the backup file path, size, checksum, and timestamp.

### Backup All Databases

```bash
python tools/db/backup.py --backup --all --json
```

Backs up `icdev.db`, `platform.db`, `memory.db`, and `activity.db`.

### Backup Tenant Databases

```bash
# All tenants
python tools/db/backup.py --backup --tenants --json

# Specific tenant
python tools/db/backup.py --backup --tenants --slug acme --json
```

### Backup Implementation

| Database Type | Method | Details |
|--------------|--------|---------|
| SQLite | `sqlite3.backup()` API | WAL-safe online backup (D152). No need to stop the application. |
| PostgreSQL | `pg_dump` | Standard PostgreSQL dump utility for SaaS deployments. |

---

## Restore Operations

### Restore from Backup

```bash
python tools/db/backup.py --restore --backup-file /path/to/backup.bak
```

The restore operation:
1. Verifies backup file integrity (checksum validation).
2. Stops active connections to the target database.
3. Restores the database from the backup file.
4. Verifies the restored database integrity.

### Restore with Encryption

If the backup was encrypted, provide the decryption key:

```bash
python tools/db/backup.py --restore --backup-file /path/to/backup.bak.enc
```

The tool detects encrypted backups automatically and prompts for the key if not provided via environment variable.

---

## Integrity Verification

### Verify a Backup File

```bash
python tools/db/backup.py --verify --backup-file /path/to/backup.bak
```

Verification checks:
- File checksum matches recorded checksum.
- SQLite integrity check (`PRAGMA integrity_check`).
- Table count matches expected schema.
- Backup metadata is valid JSON.

---

## Backup Management

### List Available Backups

```bash
python tools/db/backup.py --list --json
```

Lists all backups with:
- Database name
- Backup timestamp
- File size
- Checksum
- Encryption status

### Prune Old Backups

```bash
python tools/db/backup.py --prune --retention-days 30
```

Removes backup files older than the specified retention period. Default retention is configured in `args/db_config.yaml`.

---

## Encryption

Backup encryption is optional but recommended for CUI environments. Uses AES-256-CBC with PBKDF2 key derivation (600,000 iterations) via the `cryptography` package (D152).

### Enable Encryption

Set the encryption key in your environment or secrets manager:

```bash
export ICDEV_BACKUP_ENCRYPTION_KEY="your-secret-key"
```

When the environment variable is set, backups are automatically encrypted. Encrypted files have the `.enc` extension.

### Encryption Details

| Parameter | Value |
|-----------|-------|
| Algorithm | AES-256-CBC |
| Key Derivation | PBKDF2 |
| Iterations | 600,000 |
| Salt | Random 16 bytes per backup |
| Package | `cryptography` (Python) |

---

## Configuration

### args/db_config.yaml

```yaml
backup:
  retention_days: 30
  encryption:
    enabled: false              # Set true or use env var
    key_env_var: "ICDEV_BACKUP_ENCRYPTION_KEY"
  schedules:
    icdev:
      interval_hours: 24
      retain_count: 7
    platform:
      interval_hours: 12
      retain_count: 14
    memory:
      interval_hours: 24
      retain_count: 7
    activity:
      interval_hours: 24
      retain_count: 7
  tenant_backup_policy:
    interval_hours: 24
    retain_count: 7

migration:
  auto_migrate: false
  checksum_validation: true
  lock_timeout_seconds: 30
```

---

## Database Migrations

ICDEV uses a lightweight migration runner (D150, stdlib only, no Alembic dependency).

### Check Migration Status

```bash
python tools/db/migrate.py --status --json
```

### Apply Pending Migrations

```bash
# Apply all pending
python tools/db/migrate.py --up

# Apply up to a specific version
python tools/db/migrate.py --up --target 005

# Dry run (preview without applying)
python tools/db/migrate.py --up --dry-run
```

### Roll Back Migrations

```bash
# Roll back latest
python tools/db/migrate.py --down

# Roll back to a specific version
python tools/db/migrate.py --down --target 003
```

### Validate Checksums

```bash
python tools/db/migrate.py --validate --json
```

Ensures applied migrations match their source files (no tampering).

### Create a New Migration

```bash
python tools/db/migrate.py --create "add_feature_table"
```

Scaffolds a new migration file in the migrations directory.

### Mark Existing DB as Migrated

For existing databases that predate the migration system:

```bash
python tools/db/migrate.py --mark-applied 001
```

### Apply to All Tenant Databases

```bash
python tools/db/migrate.py --up --all-tenants
```

### Migration File Format

Migrations support `.sql` and `.py` files with `@sqlite-only` and `@pg-only` directives for database-specific SQL.

The baseline migration (v001) delegates to `init_icdev_db.py` rather than duplicating schema definitions (D151).

---

## Disaster Recovery Procedures

### Scenario 1: Single Database Corruption

1. Stop the affected service (dashboard, agent, or gateway).
2. Verify the most recent backup:
   ```bash
   python tools/db/backup.py --list --json
   python tools/db/backup.py --verify --backup-file /path/to/latest.bak
   ```
3. Restore from the verified backup:
   ```bash
   python tools/db/backup.py --restore --backup-file /path/to/latest.bak
   ```
4. Verify the restored database:
   ```bash
   python tools/db/migrate.py --validate --json
   ```
5. Restart the service.

### Scenario 2: Full System Recovery

1. Restore all databases:
   ```bash
   python tools/db/backup.py --restore --backup-file /path/to/icdev.bak
   python tools/db/backup.py --restore --backup-file /path/to/platform.bak
   python tools/db/backup.py --restore --backup-file /path/to/memory.bak
   python tools/db/backup.py --restore --backup-file /path/to/activity.bak
   ```
2. Restore tenant databases:
   ```bash
   # For each tenant backup file
   python tools/db/backup.py --restore --backup-file /path/to/tenants/acme.bak
   ```
3. Apply any pending migrations:
   ```bash
   python tools/db/migrate.py --up --all-tenants
   ```
4. Run health check:
   ```bash
   python tools/testing/health_check.py --json
   ```
5. Verify system status:
   ```bash
   python tools/testing/production_audit.py --json
   ```

### Scenario 3: Tenant Database Recovery

1. Identify the tenant:
   ```bash
   python tools/db/backup.py --list --json | grep "acme"
   ```
2. Verify the tenant backup:
   ```bash
   python tools/db/backup.py --verify --backup-file /path/to/tenants/acme.bak
   ```
3. Restore the tenant database:
   ```bash
   python tools/db/backup.py --restore --backup-file /path/to/tenants/acme.bak
   ```
4. Verify migrations are current:
   ```bash
   python tools/db/migrate.py --status --json
   ```

### Scenario 4: Migration Failure Recovery

1. Check migration status to identify the failed migration:
   ```bash
   python tools/db/migrate.py --status --json
   ```
2. Roll back to the last successful migration:
   ```bash
   python tools/db/migrate.py --down --target <last-good-version>
   ```
3. Fix the migration file.
4. Re-apply:
   ```bash
   python tools/db/migrate.py --up
   ```
5. Validate checksums:
   ```bash
   python tools/db/migrate.py --validate --json
   ```

---

## Recommended Backup Schedules

### Production Environment

| Database | Frequency | Retention | Encryption |
|----------|-----------|-----------|------------|
| `icdev.db` | Every 6 hours | 30 days | Required for CUI |
| `platform.db` | Every 6 hours | 30 days | Required for CUI |
| `memory.db` | Daily | 14 days | Recommended |
| `activity.db` | Daily | 14 days | Recommended |
| Tenant DBs | Every 12 hours | 30 days per tenant | Required for CUI |

### Development Environment

| Database | Frequency | Retention | Encryption |
|----------|-----------|-----------|------------|
| `icdev.db` | Daily | 7 days | Optional |
| `platform.db` | Daily | 7 days | Optional |
| `memory.db` | Weekly | 7 days | Optional |
| `activity.db` | Weekly | 7 days | Optional |

### Air-Gapped Environment

| Database | Frequency | Retention | Encryption |
|----------|-----------|-----------|------------|
| All databases | Every 4 hours | 60 days | Required (AES-256) |
| Tenant DBs | Every 4 hours | 60 days per tenant | Required (AES-256) |

Transfer encrypted backups to offline storage media per organizational security policy.

---

## Audit Trail Considerations

The audit trail tables (`audit_trail`, `hook_events`, and others listed in `APPEND_ONLY_TABLES`) are append-only and immutable per D6. Backup and restore operations preserve this immutability. Never attempt to modify audit records during restore.

If a restore operation would result in audit trail data loss (restoring an older backup), document the gap in the audit log after restoration:

```bash
python tools/audit/audit_logger.py \
  --event-type "system.restore" \
  --actor "admin" \
  --action "Restored icdev.db from backup dated YYYY-MM-DD. Audit entries from X to Y may be missing." \
  --project-id "system"
```

---

## Automation

### Cron-Based Backup (Linux/macOS)

```bash
# Every 6 hours
0 */6 * * * cd /path/to/icdev && python tools/db/backup.py --backup --all --json >> /var/log/icdev-backup.log 2>&1

# Daily tenant backup
0 2 * * * cd /path/to/icdev && python tools/db/backup.py --backup --tenants --json >> /var/log/icdev-backup.log 2>&1

# Weekly prune
0 3 * * 0 cd /path/to/icdev && python tools/db/backup.py --prune --retention-days 30 >> /var/log/icdev-backup.log 2>&1
```

### Kubernetes CronJob

For K8s deployments, add a CronJob resource that runs the backup tool inside the ICDEV container with the data volume mounted.
