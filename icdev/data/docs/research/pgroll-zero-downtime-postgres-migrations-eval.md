# pgroll Evaluation: PostgreSQL Zero-Downtime Schema Migrations

**Date:** 2026-04-10  
**Task ID:** task-0bab14eb89  
**Source:** research_challenges (session rsess-7518c1c1e901)  
**Candidate for:** ICDEV cloud migration and database migration adapter

---

## Executive Summary

**pgroll** (by Xata.io) is an Apache 2.0-licensed, open-source CLI tool that provides zero-downtime, reversible PostgreSQL schema migrations via a dual-schema versioning (expand-contract) approach.

**Recommendation: GO** — Integrate pgroll as an optional database migration adapter for ICDEV's cloud PostgreSQL deployments. It significantly reduces operational risk during production schema changes but requires careful planning for hybrid SQLite/PostgreSQL support.

---

## 1. What Is pgroll

- **Language:** Go — ships as a single cross-platform binary with zero external runtime dependencies
- **License:** Apache License 2.0 (permissive; no vendor lock-in)
- **Repository:** https://github.com/xataio/pgroll
- **Maturity:** 5k+ GitHub stars; production use at Xata.io and third parties; v0.14+

### Core Concept: Expand-Contract Pattern

Instead of direct DDL (which causes table locks and downtime), pgroll automates the **expand-contract workflow**:

1. **Expand:** Adds new schema elements alongside old ones; installs dual-write triggers; creates versioned PostgreSQL views for both old and new schema versions
2. **Backfill:** Migrates data in configurable batches (default 10k rows); triggers keep old/new columns in sync during the window
3. **Switchover:** Applications change their PostgreSQL `search_path` to point at the new schema version; old instances continue working uninterrupted
4. **Contract:** `pgroll complete` drops old views, triggers, and columns; database is clean

**Result:** Zero table locks, concurrent reads/writes throughout, instant rollback at any point.

---

## 2. Key Features

| Feature | Details |
|---|---|
| **Zero-Downtime** | No table locks; DDL via views + triggers only |
| **Multi-Version Schema** | Old + new schema versions coexist simultaneously |
| **Instant Rollback** | `pgroll rollback` reverts instantly, no data loss |
| **Automatic Dual-Write** | PostgreSQL triggers handle sync — no app-layer dual-write needed |
| **Backfill Control** | `--backfill-batch-size` (default 10k), `--backfill-batch-delay` for rate limiting |
| **v0.7.0+ Perf** | ~80% reduction in backfill duration (released late 2024) |
| **Migration State** | Stored in `pgroll` schema in target database; fully auditable |
| **Air-Gap Compatible** | Single binary; no network dependencies post-install |

---

## 3. Comparison with Alternatives

| Aspect | pgroll | Flyway | Liquibase | Atlas | Sqitch |
|---|---|---|---|---|---|
| **License** | Apache 2.0 | OSS + Commercial | OSS + Commercial | MPL 2.0 | Artistic 2.0 |
| **Zero-Downtime** | Yes (inherent) | No | No | Partial | No |
| **Reversible** | Yes (instant) | Limited | Yes | Yes | Yes |
| **Multi-Version** | Yes (native) | No | No | No | No |
| **Auto Dual-Write** | Yes (triggers) | No (app layer) | No (app layer) | No | No |
| **Backfill Automation** | Yes (batched) | No | No | No | No |
| **Lock Duration** | None | Long (exclusive) | Long (exclusive) | Minimal | Long |
| **PostgreSQL-Only** | Yes | No | No | No | No |
| **Go Binary** | Yes | JVM | JVM | Yes | Perl |

**pgroll is the only tool with inherent zero-downtime AND automatic dual-write management.**

---

## 4. PostgreSQL Compatibility & Language Support

### Supported Versions
- **PostgreSQL 14+** (required minimum)
- **PostgreSQL 13 and earlier:** Not supported
- **Cloud:** AWS RDS, Aurora PostgreSQL, Google Cloud SQL, Azure Database for PostgreSQL, Neon, Supabase, DigitalOcean

### Language Integration
pgroll is CLI-first. No native SDK for Python, Java, or Node.js. Integration pattern:
```python
import subprocess

def migrate_postgres(url: str, migration_file: str) -> bool:
    result = subprocess.run(
        ['pgroll', 'start', '--postgres-url', url, migration_file],
        capture_output=True
    )
    return result.returncode == 0
```

Works with any CI/CD system (GitLab CI, GitHub Actions, Jenkins) via shell invocation.

---

## 5. Limitations & Gotchas

| Limitation | Impact | Mitigation |
|---|---|---|
| PostgreSQL-only | SQLite not supported | Use Alembic/ORM for SQLite; pgroll for cloud PG only |
| `search_path` dependency | Apps must switch schema version explicitly | Design app config for schema version switching |
| View version limitations | Cannot provide multiple versions of same view | Architecture constraint; rarely hit in practice |
| Write amplification | Triggers add slight write latency during migration window | Acceptable tradeoff vs. table locks; use batch delays |
| ~2x storage during window | Dual schema uses extra space temporarily | Dropped after `pgroll complete` |
| No daemon/scheduler | CLI-only; must be invoked by CI/CD or cron | Standard for migration tools |
| No JVM/Python SDK | Must shell out to CLI | Subprocess wrapper is trivial |

### Air-Gap Compatibility
- Pre-built binary: no runtime network dependencies
- Works in disconnected environments once installed
- Only requires network to the target PostgreSQL instance (expected)

---

## 6. Government / Federal Compliance Fit

| Compliance Need | pgroll Support |
|---|---|
| **Audit Trail** | Partial — pgroll schema tracks migration history; pair with `pgAudit` for full DDL audit |
| **Reversibility** | Yes — instant rollback without data loss |
| **Change Tracking** | Yes — versioned migration files in Git |
| **Data Integrity** | Yes — ACID via PostgreSQL + trigger synchronization |
| **Access Control** | Via PostgreSQL RBAC (not pgroll itself) |
| **Open Source / SBOM** | Apache 2.0; Go binary with minimal dependency graph |
| **No Vendor Lock-In** | Yes — can fork/modify for agency requirements |
| **FedRAMP** | pgroll is a tool, not a service; PostgreSQL must be FedRAMP-certified |

**Recommended Federal Compliance Stack:**
```
pgroll (schema versioning + zero-downtime)
+ pgAudit (DDL audit trail)
+ PostgreSQL RBAC (access control)
+ ICDEV CUI marking layer (classification banners)
+ SIEM aggregation (Splunk / CloudWatch)
```

---

## 7. Installation & Operation

### Install (air-gap friendly)
```bash
# Download pre-built binary from GitHub Releases
# https://github.com/xataio/pgroll/releases
# Add to $PATH

# Or build from source (requires Go 1.24+)
go install github.com/xataio/pgroll@latest
```

### Basic Workflow
```bash
# 1. Initialize pgroll state in target database
pgroll init --postgres-url postgres://user:pass@host:5432/dbname

# 2. Create migration YAML
cat > 002_add_email_column.yaml << EOF
name: add_email_column
operations:
  - add_column:
      table: users
      column:
        name: email
        type: varchar(255)
        nullable: true
EOF

# 3. Start migration (expand phase)
pgroll start --postgres-url $PGURL 002_add_email_column.yaml

# 4. Application switches search_path to new version; old version still works

# 5. Complete migration (contract phase)
pgroll complete --postgres-url $PGURL

# 6. Or roll back instantly
pgroll rollback --postgres-url $PGURL
```

---

## 8. ICDEV Integration Architecture

### Recommended: Hybrid Migration Strategy

```
ICDEV Database Layer
├─ SQLite (On-Prem / Edge / Default)
│  └─ Tool: Alembic / Django ORM migrations
│     (Zero-downtime less critical; single-process local DB)
│
└─ PostgreSQL (Cloud Deployments — GovCloud, Neon, etc.)
   └─ Tool: pgroll
      (Multi-tenant, production-critical; zero-downtime required)
```

### Migration Adapter Pattern (Python)

```python
class ICDEVMigrator:
    def migrate(self, db_url: str, migration_file: str) -> bool:
        if db_url.startswith('sqlite'):
            return self._migrate_sqlite(db_url, migration_file)
        elif 'postgres' in db_url:
            return self._migrate_postgres(db_url, migration_file)
    
    def _migrate_postgres(self, url: str, migration_file: str) -> bool:
        result = subprocess.run(
            ['pgroll', 'start', '--postgres-url', url, migration_file],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.error(f"pgroll failed: {result.stderr}")
            return False
        return True
```

### Suggested Directory Structure

```
icdev-repo/
├─ migrations/
│  ├─ postgres/          # pgroll YAML files
│  │  ├─ 001_init.yaml
│  │  ├─ 002_add_column.yaml
│  │  └─ 003_add_constraint.yaml
│  └─ sqlite/            # Alembic / ORM migrations
│     └─ versions/
└─ .gitlab-ci.yml        # pgroll invoked in migrate job
```

### GitLab CI Integration

```yaml
migrate_database:
  stage: deploy
  script:
    - |
      if [[ "$DB_TYPE" == "postgres" ]]; then
        pgroll start --postgres-url $POSTGRES_URL migrations/postgres/${MIGRATION_FILE}
        pgroll complete --postgres-url $POSTGRES_URL
      else
        python -m alembic upgrade head
      fi
  only:
    - main
```

---

## 9. Adoption Roadmap

### Phase 1 — Pilot (Weeks 1–4)
- [ ] Install pgroll on GitLab CI runners
- [ ] Set up PostgreSQL staging instance (RDS or local Docker)
- [ ] Test add_column, rename_column, add_constraint migrations
- [ ] Measure backfill performance on ~1M row test table
- [ ] Verify search_path switching with sample ICDEV app

### Phase 2 — Integration (Weeks 5–8)
- [ ] Build ICDEVMigrator adapter (SQLite + PostgreSQL paths)
- [ ] Integrate into GitLab CI pipeline
- [ ] Configure pgAudit on staging PostgreSQL
- [ ] Create migration templates for common ICDEV schema ops
- [ ] Write rollback runbook

### Phase 3 — Hardening (Weeks 9–12)
- [ ] Security review: RBAC, secrets management
- [ ] Compliance review: pgAudit log aggregation
- [ ] Load test: backfill on production-scale data
- [ ] Disaster recovery drill
- [ ] Team training workshop (~2 hours)

### Phase 4 — Production
- [ ] Pilot on non-critical cloud PostgreSQL
- [ ] Gradual rollout to production
- [ ] Establish migration SLOs

---

## 10. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| `search_path` complexity | Medium | Medium | Runbooks + training |
| Backfill performance at scale | Low | Medium | Staging load tests; batch tuning |
| PostgreSQL-only scope | Low | Low | Separate SQLite tooling (acceptable) |
| Tool immaturity | Low | Medium | 5k+ stars; Xata production use; active maintainers |
| Compliance audit gaps | Medium | High | pgAudit + SIEM integration |

---

## 11. Cost & Effort

| Activity | Effort |
|---|---|
| Pilot & POC | ~2 weeks |
| Integration development | ~3 weeks |
| Compliance & security review | ~2 weeks |
| Team training | ~1 week |
| Production rollout | ~1 week |
| **Total** | **~9 weeks** |

**Licensing cost: $0** (Apache 2.0)  
**Operational overhead: Minimal** — CLI; integrates into existing CI/CD

---

## 12. Final Decision

**RECOMMENDATION: ADOPT for cloud PostgreSQL deployments**

pgroll is production-ready and purpose-built for the exact problem ICDEV faces with cloud database evolution. Key reasons:

1. Zero-downtime is inherent, not bolted on
2. Instant rollback eliminates high-risk manual procedures
3. Single Go binary — air-gap compatible, SBOM-friendly
4. Apache 2.0 — no vendor lock-in; suitable for federal environments
5. Multi-version schema enables decoupled app/DB deployments (critical for rolling deploys on K8s/OpenShift)

**Next action:** Allocate 2-week pilot in staging PostgreSQL. Assign to database/DevSecOps team.

---

## References

- https://github.com/xataio/pgroll
- https://xata.io/blog/pgroll-schema-migrations-postgres
- https://xata.io/blog/pgroll-expand-contract
- https://xata.io/blog/pgroll-internals
- https://neon.com/guides/pgroll
- https://opensource-db.com/pgroll-in-action-client-side-evaluation-of-zero-downtime-schema-migrations/
