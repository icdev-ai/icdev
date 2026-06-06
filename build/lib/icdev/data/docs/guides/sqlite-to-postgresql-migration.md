# CUI // SP-CTI
# SQLite to PostgreSQL Migration Guide

## Overview

ICDEV™ supports both SQLite (zero-config fallback) and PostgreSQL (production primary) via a transparent storage abstraction layer. This guide covers migrating from SQLite to PostgreSQL.

## Prerequisites

- Docker (for PostgreSQL container) OR native PostgreSQL 14+
- Python 3.10+ with `psycopg2-binary` installed
- Existing ICDEV™ installation with SQLite data

## Step 1: Start PostgreSQL

### Option A: Docker (recommended)
```bash
docker run -d \
  --name icdev-postgres \
  -e POSTGRES_USER=icdev \
  -e POSTGRES_PASSWORD=<your-password> \
  -e POSTGRES_DB=icdev \
  -p 5432:5432 \
  -v icdev_pgdata:/var/lib/postgresql/data \
  postgres:16-alpine
```

### Option B: Native PostgreSQL
```bash
createuser icdev
createdb -O icdev icdev
```

## Step 2: Configure Environment

Add to `.env`:
```env
ICDEV_STORAGE_BACKEND=postgresql
ICDEV_PG_HOST=localhost
ICDEV_PG_PORT=5432
ICDEV_PG_USER=icdev
ICDEV_PG_PASSWORD=<your-password>
ICDEV_PG_DATABASE=icdev
```

## Step 3: Initialize Schema

```bash
# Create all 399 tables and 777 indexes from SQLite DDL
python tools/db/pg_init.py --init --json

# Verify
python tools/db/pg_init.py --verify --json
```

The `pg_init.py` tool automatically translates SQLite DDL to PostgreSQL:
- `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY`
- `BLOB` → `BYTEA`
- `datetime('now')` → `NOW()`
- `FOREIGN KEY` constraints → deferred (tables created without FK for ordering)

## Step 4: Migrate Data (optional)

If you have existing data in SQLite:
```bash
python tools/db/pg_init.py --migrate-data --json
```

This copies all rows from `data/icdev.db` to PostgreSQL.

## Step 5: Verify

```bash
# Health check
python tools/db/storage.py --health --json

# Should show:
# {"status": "healthy", "backend": "postgresql", "connected": true}
```

## How It Works

### Storage Abstraction Layer

All ICDEV™ tools use `get_connection()` from `tools/db/storage.py`. This returns a `StorageConnection` wrapper that:

1. **Selects backend** from `ICDEV_STORAGE_BACKEND` env var (default: `sqlite`)
2. **Translates SQL** automatically:
   - `?` placeholder → `%s`
   - `datetime('now')` → `NOW()::text`
   - `datetime('now', '-N days')` → `(NOW() - INTERVAL 'N days')::text`
   - `INSERT OR REPLACE` → `INSERT ... ON CONFLICT DO UPDATE`
   - `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO NOTHING`
   - `PRAGMA` → no-op `SELECT 1`
3. **Normalizes results** — PG `RealDictCursor` rows wrapped in `DictRow` that supports both `row['name']` and `row[0]` access (like `sqlite3.Row`)

### No Code Changes Required

Existing code like:
```python
from tools.db.storage import get_connection
conn = get_connection()
rows = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchall()
```

Works identically on both SQLite and PostgreSQL — the translator handles everything.

## Switching Back to SQLite

```env
ICDEV_STORAGE_BACKEND=sqlite
# or simply remove the line (sqlite is default)
```

No schema changes needed — SQLite continues to use `data/icdev.db`.

## Docker Management

```bash
# Stop PostgreSQL
docker stop icdev-postgres

# Start PostgreSQL
docker start icdev-postgres

# Reset schema (DESTRUCTIVE)
python tools/db/pg_init.py --reset --json

# Check PG logs
docker logs icdev-postgres --tail 20
```

## Troubleshooting

### "database is locked" (SQLite only)
Switch to PostgreSQL — PG handles concurrent writes natively via MVCC.

### "relation does not exist"
Run `python tools/db/pg_init.py --init --json` to create missing tables.

### "operator does not exist: text <= timestamp"
The storage layer auto-casts `NOW()` expressions to `::text` for TEXT column comparisons. If you see this error, a query may be bypassing `get_connection()`. Migrate it to use the storage abstraction.
