# GovLift — DoD IL4 Cloud Migration Tool

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## GovLift Canvas (`/govlift`)

Dashboard canvas for orchestrating DoD workload migration to IL4 cloud (AWS GovCloud / Azure Government).
Blueprint: `tools/govlift/blueprint.py` · DB init: `tools/govlift/db/init_db.py` · Classification: CUI // SP-CTI

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Constants | tools/govlift/constants.py | All enum constants: statuses, types, severities, risk levels, classification markings | (import) | WORKLOAD_STATUSES, MIGRATION_STATUSES, STIG_SEVERITIES, etc. |
| DB Init | tools/govlift/db/init_db.py | Initialize 6 GovLift tables in icdev.db | (called at app startup) | schema OK |
| Workload Scanner | tools/govlift/workload_scanner.py | Inventory on-prem workloads; create/list/update; assign to migration waves | (library) | list_workloads(), create_workload(), get_scanner_summary() |
| Wave Planner | tools/govlift/wave_planner.py | Define and schedule migration waves; track workload counts and wave status | (library) | list_waves(), create_wave(), update_wave_status(), get_wave_summary() |
| Migration Executor | tools/govlift/migration_executor.py | Track migration jobs; start/complete/rollback; phase timings | (library) | list_migrations(), start_migration(), complete_migration(), rollback_migration(), get_migration_summary() |
| STIG Checker | tools/govlift/stig_checker.py | DISA STIG compliance checks; run_quick_scan() seeds 10 RHEL-09 checks; CAT1/2/3 severity | (library) | list_stig_checks(), run_quick_scan(), update_check_status(), get_stig_summary() |
| Audit Engine | tools/govlift/audit_engine.py | NIST AU append-only audit log; log_action() + list_audit_log(); retention 7 years | (library) | log_action(), list_audit_log(), get_audit_summary() |
| Blueprint | tools/govlift/blueprint.py | Flask Blueprint (url_prefix=""); 6 page routes + 18 API routes + IQE query route | (library) | create_govlift_blueprint() |
| IQE Adapter | tools/iqe/adapters/govlift.py | 5 IQE collections: workloads, waves, migrations, stig, audit | (registered by app.py) | register_collection() calls |

### DB Tables (icdev.db)
| Table | Append-Only | Description |
|-------|-------------|-------------|
| govlift_workloads | No | On-prem workload inventory |
| govlift_waves | No | Migration wave schedule |
| govlift_migrations | No | Migration job tracker |
| govlift_stig_checks | No | STIG compliance findings |
| govlift_audit_log | **Yes** | NIST AU immutable audit trail |
| govlift_integrations | No | External system integration registry |

### Page Routes
| Route | Template | Description |
|-------|----------|-------------|
| GET /govlift | govlift/index.html | Executive overview dashboard |
| GET /govlift/workloads | govlift/workloads.html | Workload inventory |
| GET /govlift/waves | govlift/waves.html | Migration wave planner |
| GET /govlift/executor | govlift/executor.html | Migration job tracker |
| GET /govlift/stig | govlift/stig.html | STIG compliance checker |
| GET /govlift/audit | govlift/audit.html | Audit log viewer |

### API Routes
| Route | Method | Description |
|-------|--------|-------------|
| /api/govlift/overview | GET | Executive summary (all 4 summaries) |
| /api/govlift/workloads | GET, POST | List / create workloads |
| /api/govlift/workloads/\<id\>/status | PATCH | Update workload migration status |
| /api/govlift/workloads/\<id\>/assign-wave | POST | Assign workload to wave |
| /api/govlift/waves | GET, POST | List / create waves |
| /api/govlift/waves/\<id\>/status | PATCH | Update wave status |
| /api/govlift/migrations | GET, POST | List / create migration jobs |
| /api/govlift/migrations/\<id\>/start | POST | Start migration job |
| /api/govlift/migrations/\<id\>/complete | POST | Complete migration (success/failure + log) |
| /api/govlift/migrations/\<id\>/rollback | POST | Rollback migration |
| /api/govlift/stig | GET | List STIG checks |
| /api/govlift/stig/scan/\<workload_id\> | POST | Run quick STIG scan for workload |
| /api/govlift/stig/\<id\>/status | PATCH | Update STIG check status |
| /api/govlift/audit | GET, POST | List / log audit entries |
| /api/govlift/integrations | GET | List external system integrations |
| /api/iqe-query (canvas=govlift) | POST | IQE natural-language query |
