# CUI // SP-CTI
# Database Schemas

Reference documentation for ICDEV™ database tables. Tables are organized by canvas/subsystem.

---

## domain_coverage

**Migration:** `206_domain_coverage`  
**Canvas:** GovCon / Gap Analysis  
**Backend:** PostgreSQL (primary), SQLite (fallback — init only)

Tracks coverage metrics per knowledge or data domain, including domains that become orphaned when their source canvas no longer exists or their backing data source is removed. Used by the GovCon capability gap analysis engine to cache L/M/N grade counts per requirement domain.

### Append-Only Design

This table follows the ICDEV append-only audit pattern (NIST AU). Status transitions (`active` → `orphaned` → `resolved`) are recorded as new rows rather than updates, preserving the full audit trail. Never `UPDATE` or `DELETE` rows in this table.

### CREATE TABLE

```sql
CREATE TABLE IF NOT EXISTS domain_coverage (
    id               BIGSERIAL PRIMARY KEY,          -- SQLite: INTEGER AUTOINCREMENT
    domain_key       TEXT    NOT NULL,               -- slug identifier, e.g. "govcon.proposals"
    domain_name      TEXT    NOT NULL,               -- human-readable label
    domain_type      TEXT    NOT NULL DEFAULT 'knowledge'
                     CHECK (domain_type IN ('knowledge', 'data', 'system', 'integration', 'canvas')),
    source_canvas    TEXT,                           -- originating canvas slug (nullable)
    coverage_score   REAL    NOT NULL DEFAULT 0.0
                     CHECK (coverage_score >= 0.0 AND coverage_score <= 1.0),
    gap_count        INTEGER NOT NULL DEFAULT 0,     -- number of open gaps detected
    status           TEXT    NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'orphaned', 'resolved', 'pending')),
    orphan_reason    TEXT
                     CHECK (orphan_reason IS NULL OR orphan_reason IN (
                         'no_canvas', 'no_source', 'schema_mismatch',
                         'stale_data', 'removed_integration'
                     )),
    last_checked_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at      TEXT,                           -- set when status = 'resolved'
    detail           TEXT    NOT NULL DEFAULT '{}',  -- JSON blob for extra metadata
    tenant_id        TEXT    NOT NULL DEFAULT 'default',
    classification   TEXT    NOT NULL DEFAULT 'CUI',
    created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_domain_coverage_key` | `domain_key` | fast domain lookup |
| `idx_domain_coverage_status` | `status` | filter active/orphaned rows |
| `idx_domain_coverage_canvas` | `source_canvas` | canvas-scoped queries |
| `idx_domain_coverage_score` | `coverage_score` | rank by coverage |
| `idx_domain_coverage_tenant` | `tenant_id` | RLS partition |
| `idx_domain_coverage_checked` | `last_checked_at` | freshness sweeps |

### Column Reference

| Column | Type | Notes |
|--------|------|-------|
| `domain_key` | TEXT | Stable slug used as a join key. Namespaced by canvas, e.g. `govcon.proposals`. |
| `domain_name` | TEXT | Display label for UI and reports. |
| `domain_type` | TEXT | `knowledge` \| `data` \| `system` \| `integration` \| `canvas` |
| `source_canvas` | TEXT | Canvas that owns this domain; NULL if cross-cutting. |
| `coverage_score` | REAL | 0.0–1.0 fraction of requirements met. |
| `gap_count` | INTEGER | Count of open requirement gaps for this domain. |
| `status` | TEXT | `active` = healthy; `orphaned` = source missing; `resolved` = gap closed; `pending` = scan in progress. |
| `orphan_reason` | TEXT | Populated when `status = 'orphaned'`. See CHECK constraint for allowed values. |
| `last_checked_at` | TEXT | ISO-8601 timestamp of the most recent coverage scan. |
| `resolved_at` | TEXT | ISO-8601 timestamp when orphan was resolved; NULL otherwise. |
| `detail` | TEXT | JSON blob for arbitrary per-domain metadata (e.g. L/M/N grade breakdown). |
| `tenant_id` | TEXT | RLS partition key. Default `'default'` for single-tenant installs. |
| `classification` | TEXT | CUI marking; propagated from security context. |

### Allowed Values

```python
# Python constants (single source of truth — SQL CHECK constraints derived from these)
DOMAIN_TYPES   = ('knowledge', 'data', 'system', 'integration', 'canvas')
STATUS_VALUES  = ('active', 'orphaned', 'resolved', 'pending')
ORPHAN_REASONS = ('no_canvas', 'no_source', 'schema_mismatch', 'stale_data', 'removed_integration')
```

### RLS

Queries must go through `get_connection()` from `tools.db.storage`. The RLS predicate filters on `tenant_id` and enforces read-down on `classification`. Do not use `sqlite3.connect()` directly.

### Usage Example

```python
from icdev.tools.db.storage import get_connection

with get_connection() as conn:
    conn.execute(
        """
        INSERT INTO domain_coverage
            (domain_key, domain_name, domain_type, coverage_score, gap_count, detail)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        ("govcon.proposals", "GovCon Proposals", "canvas", 0.72, 3,
         '{"L": 12, "M": 5, "N": 2}'),
    )
```

### Related

- Migration file: `tools/db/migrations/206_domain_coverage.sql`
- GovCon gap analysis engine: `tools/govcon/` (capability gap scan)
- `orphan_db_table` coherence rule: flags tables not referenced in this schema doc
