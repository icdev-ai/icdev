# Database Schema Reference

CUI // SP-CTI

## Overview

ICDEV uses 5 SQLite databases for its internal operations. SQLite was chosen for zero-config portability (D1); applications built by ICDEV use PostgreSQL. The SaaS platform layer supports both SQLite (development) and PostgreSQL (production) via a compatibility layer.

---

## Database Inventory

```
data/
+-- icdev.db               # Main operational DB (193 tables)
+-- platform.db            # SaaS platform DB (6 tables)
+-- memory.db              # Memory system (3 tables)
+-- activity.db            # Task tracking (1 table)
+-- tenants/
    +-- {slug}.db           # Per-tenant isolated databases
```

| Database | Tables | Purpose | Size Estimate |
|----------|--------|---------|---------------|
| `data/icdev.db` | 183 | Core operational data: projects, agents, compliance, security, knowledge | Primary |
| `data/platform.db` | 6 | SaaS multi-tenancy: tenants, users, API keys, subscriptions | SaaS only |
| `data/tenants/{slug}.db` | 183 each | Isolated copy of icdev.db schema per tenant (D60) | Per-tenant |
| `data/memory.db` | 3 | Memory entries with embeddings, daily logs, access tracking | All installs |
| `data/activity.db` | 1 | Task tracking | All installs |

---

## Table Groups (icdev.db — 183 Tables)

### Projects and Core

| Table | Purpose | Mutable |
|-------|---------|---------|
| `projects` | Project definitions, metadata, status | Yes |
| `project_settings` | Per-project configuration | Yes |
| `project_information_types` | FIPS 199 SP 800-60 information types per project | Yes |

### Agents and A2A Communication

| Table | Purpose | Mutable |
|-------|---------|---------|
| `agents` | Agent registry (15 agents, ports, status) | Yes |
| `a2a_tasks` | Inter-agent task dispatch and tracking | Yes |
| `agent_token_usage` | Per-agent LLM token consumption (with user_id, D177) | Yes |
| `agent_workflows` | DAG workflow definitions and execution state | Yes |
| `agent_subtasks` | Individual subtasks within DAG workflows | Yes |
| `agent_mailbox` | Asynchronous agent messaging (HMAC-SHA256 signed, D41) | Yes |
| `agent_vetoes` | Domain authority veto records | **Append-only** |
| `agent_memory` | Scoped agent memories (agent_id, project_id) | Yes |
| `agent_collaboration_history` | Collaboration pattern execution records | Yes |

### Audit Trail (NIST AU Compliance)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `audit_trail` | All system actions, append-only (D6) | **Append-only** |
| `hook_events` | Claude Code hook execution events | **Append-only** |

### Compliance — NIST 800-53, FedRAMP, CMMC

| Table | Purpose | Mutable |
|-------|---------|---------|
| `nist_controls` | NIST 800-53 Rev 5 control implementations | Yes |
| `control_mappings` | Activity-to-control mapping records | Yes |
| `ssp_documents` | System Security Plan artifacts | Yes |
| `poam_items` | Plan of Action and Milestones items | Yes |
| `stig_findings` | STIG check results (CAT1/CAT2/CAT3) | Yes |
| `sbom_entries` | Software Bill of Materials components | Yes |
| `fedramp_assessments` | FedRAMP baseline assessment results | Yes |
| `cmmc_assessments` | CMMC Level 2/3 practice assessments | Yes |
| `atlas_assessments` | MITRE ATLAS AI threat assessments | Yes |
| `owasp_llm_assessments` | OWASP LLM Top 10 assessment results | Yes |
| `nist_ai_rmf_assessments` | NIST AI RMF 1.0 assessment results | Yes |
| `iso42001_assessments` | ISO/IEC 42001:2023 assessment results | Yes |

### Compliance — CSSP, SbD, IV&V, OSCAL

| Table | Purpose | Mutable |
|-------|---------|---------|
| `cssp_assessments` | DI 8530.01 CSSP functional area assessments | Yes |
| `sbd_assessments` | CISA Secure by Design assessment results | Yes |
| `ivv_assessments` | IEEE 1012 IV&V process area assessments | Yes |
| `oscal_documents` | OSCAL machine-readable compliance artifacts | Yes |
| `des_assessments` | DoDI 5000.87 Digital Engineering assessments | Yes |

### Compliance — FIPS 199/200, Security Categorization

| Table | Purpose | Mutable |
|-------|---------|---------|
| `fips199_categorizations` | FIPS 199 security categorization results | Yes |
| `project_information_types` | SP 800-60 information types per project | Yes |
| `fips200_assessments` | FIPS 200 17-area minimum security assessments | Yes |

### Compliance — eMASS, cATO, PI Tracking

| Table | Purpose | Mutable |
|-------|---------|---------|
| `emass_sync_records` | eMASS synchronization history | Yes |
| `cato_evidence` | Continuous ATO evidence records | Yes |
| `pi_compliance_tracking` | Program Increment compliance velocity | Yes |

### Compliance — Universal Compliance Platform

| Table | Purpose | Mutable |
|-------|---------|---------|
| `data_classifications` | Universal data classification categories (10 types) | Yes |
| `framework_applicability` | Which compliance frameworks apply per project | Yes |
| `compliance_detection_log` | Auto-detection results (advisory, D110) | Yes |
| `crosswalk_bridges` | Cross-framework control mappings | Yes |
| `framework_catalog_versions` | Independent version tracking per framework (D112) | Yes |
| `cjis_assessments` | CJIS Security Policy assessment results | Yes |
| `hipaa_assessments` | HIPAA Security Rule assessment results | Yes |
| `hitrust_assessments` | HITRUST CSF v11 assessment results | Yes |
| `soc2_assessments` | SOC 2 Type II trust criteria assessments | Yes |
| `pci_dss_assessments` | PCI DSS v4.0 assessment results | Yes |
| `iso27001_assessments` | ISO/IEC 27001:2022 assessment results | Yes |

### Knowledge and Self-Healing

| Table | Purpose | Mutable |
|-------|---------|---------|
| `knowledge_patterns` | Detected patterns for self-healing | Yes |
| `self_heal_records` | Self-healing execution history | Yes |
| `recommendations` | ML-generated improvement recommendations | Yes |

### Deployments

| Table | Purpose | Mutable |
|-------|---------|---------|
| `deployments` | Deployment history and status | Yes |
| `rollback_records` | Rollback execution records | Yes |

### Metrics and Alerts

| Table | Purpose | Mutable |
|-------|---------|---------|
| `metrics` | Prometheus-compatible metric snapshots | Yes |
| `alerts` | Alert definitions and trigger history | Yes |

### Maintenance

| Table | Purpose | Mutable |
|-------|---------|---------|
| `dependency_scans` | Dependency scan results | Yes |
| `vulnerability_checks` | CVE check results | Yes |
| `maintenance_audits` | Maintenance audit scores and reports | Yes |
| `remediation_records` | Auto-remediation execution history | Yes |

### MBSE (Model-Based Systems Engineering)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `mbse_models` | SysML model imports (XMI parsed elements) | Yes |
| `mbse_requirements` | DOORS NG requirements (ReqIF parsed) | Yes |
| `digital_thread_links` | N:M model-code-test-control traceability (D12) | Yes |
| `mbse_code_elements` | Generated code elements linked to model | Yes |
| `model_control_mappings` | Model element to NIST control mappings | Yes |
| `mbse_drift_records` | Model-code drift detection results | Yes |
| `mbse_pi_snapshots` | PI-level model snapshots (SHA-256 hashed, D11) | Yes |

### Modernization (7Rs Migration)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `legacy_applications` | Registered legacy application metadata | Yes |
| `architecture_extractions` | Extracted architecture analysis | Yes |
| `seven_r_assessments` | 7R assessment scores and recommendations | Yes |
| `migration_plans` | Migration plan definitions | Yes |
| `migration_tasks` | Individual migration task tracking | Yes |
| `strangler_fig_status` | Strangler fig pattern progress | Yes |
| `compliance_bridge_records` | ATO coverage validation during migration | Yes |
| `migration_pi_snapshots` | PI-level migration progress snapshots | Yes |

### RICOAS (Requirements, Intake, COA, Approval)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `intake_sessions` | Requirements intake session state | Yes |
| `intake_requirements` | Extracted requirements from sessions | Yes |
| `intake_documents` | Uploaded document metadata | Yes |
| `gap_analysis_results` | Gap detection findings | Yes |
| `readiness_scores` | 5-dimension readiness scoring | Yes |
| `safe_decomposition` | SAFe hierarchy (Epic > Capability > Feature > Story) | Yes |
| `boundary_assessments` | ATO boundary impact (4-tier: GREEN/YELLOW/ORANGE/RED) | Yes |
| `ato_systems` | Registered ATO boundary systems | Yes |
| `boundary_alternatives` | RED item alternative COAs | Yes |

### RICOAS — Supply Chain

| Table | Purpose | Mutable |
|-------|---------|---------|
| `vendors` | Supply chain vendor registry | Yes |
| `vendor_dependencies` | Dependency graph adjacency list (D27) | Yes |
| `isa_agreements` | Information Sharing Agreement lifecycle | Yes |
| `scrm_assessments` | NIST 800-161 SCRM vendor assessments | Yes |
| `cve_triage_records` | CVE triage decisions and SLA tracking | Yes |

### RICOAS — Simulation

| Table | Purpose | Mutable |
|-------|---------|---------|
| `simulation_scenarios` | What-if simulation scenario definitions | Yes |
| `simulation_results` | 6-dimension simulation execution results | Yes |
| `monte_carlo_results` | Monte Carlo estimation outputs | Yes |
| `coa_definitions` | Course of Action definitions (Speed/Balanced/Comprehensive) | Yes |
| `coa_comparisons` | COA comparison analysis | Yes |

### RICOAS — External Integration

| Table | Purpose | Mutable |
|-------|---------|---------|
| `integration_configs` | Jira/ServiceNow/GitLab connection settings | Yes |
| `sync_records` | Bidirectional sync execution history | Yes |
| `approval_workflows` | Approval chain definitions and decisions | Yes |
| `traceability_matrix` | Requirements Traceability Matrix (RTM) | Yes |

### Operations and Automation

| Table | Purpose | Mutable |
|-------|---------|---------|
| `agent_executions` | Agent executor run records (JSONL output, D35) | **Append-only** |
| `nlq_queries` | NLQ-to-SQL query log (read-only enforcement, D34) | **Append-only** |
| `ci_worktrees` | Git worktree task isolation state | Yes |
| `gitlab_task_claims` | GitLab tag-to-workflow task claims | Yes |

### Multi-Agent Orchestration

| Table | Purpose | Mutable |
|-------|---------|---------|
| `agent_token_usage` | LLM token consumption per agent (with user_id) | Yes |
| `agent_workflows` | DAG workflow state | Yes |
| `agent_subtasks` | Subtask execution within workflows | Yes |
| `agent_mailbox` | Agent-to-agent messaging (HMAC signed) | Yes |
| `agent_vetoes` | Domain authority veto records | **Append-only** |
| `agent_memory` | Scoped agent memories | Yes |
| `agent_collaboration_history` | Collaboration pattern history | Yes |

### Agentic Generation (Child Apps)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `child_app_registry` | Registered child application metadata | Yes |
| `agentic_fitness_assessments` | Fitness scoring for agentic generation | Yes |

### Marketplace

| Table | Purpose | Mutable |
|-------|---------|---------|
| `marketplace_assets` | Published GOTCHA asset metadata | Yes |
| `marketplace_versions` | Published asset versions (immutable, D80) | **Append-only** |
| `marketplace_reviews` | ISSO/security officer review records | **Append-only** |
| `marketplace_installations` | Asset installation records per tenant | Yes |
| `marketplace_scan_results` | 9-gate security scan results | **Append-only** |
| `marketplace_ratings` | Community asset ratings | Yes |
| `marketplace_embeddings` | Semantic search embeddings for assets | Yes |
| `marketplace_dependencies` | Asset dependency declarations | Yes |

### DevSecOps and Zero Trust Architecture

| Table | Purpose | Mutable |
|-------|---------|---------|
| `devsecops_profiles` | Per-project DevSecOps maturity profiles | Yes |
| `zta_maturity_scores` | 7-pillar ZTA maturity assessments | Yes |
| `zta_posture_evidence` | ZTA posture evidence for cATO | Yes |
| `nist_800_207_assessments` | NIST SP 800-207 compliance assessments | Yes |
| `devsecops_pipeline_audit` | Pipeline security audit trail | **Append-only** |

### MOSA (Modular Open Systems Approach)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `mosa_assessments` | DoD MOSA (10 U.S.C. 4401) assessments | Yes |
| `icd_documents` | Interface Control Documents | Yes |
| `tsp_documents` | Technical Standards Profiles | Yes |
| `mosa_modularity_metrics` | Coupling/cohesion metrics time-series | Yes |

### Remote Command Gateway

| Table | Purpose | Mutable |
|-------|---------|---------|
| `remote_user_bindings` | User-to-channel binding records | Yes |
| `remote_command_log` | All remote command executions | **Append-only** |
| `remote_command_allowlist` | Per-channel command permissions | Yes |

### Schema Migrations

| Table | Purpose | Mutable |
|-------|---------|---------|
| `schema_migrations` | Migration version tracking (D150) | Yes |

### Spec-Kit (Requirements Quality)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `project_constitutions` | Per-project design principles (D158) | Yes |
| `spec_registry` | Spec directory registration and tracking | Yes |

### Proactive Monitoring

| Table | Purpose | Mutable |
|-------|---------|---------|
| `heartbeat_checks` | Heartbeat daemon check results | Yes |
| `auto_resolution_log` | Auto-resolver execution history | **Append-only** |

### Dashboard Auth and BYOK

| Table | Purpose | Mutable |
|-------|---------|---------|
| `dashboard_users` | Dashboard user accounts | Yes |
| `dashboard_api_keys` | API key hashes (SHA-256, D169) | Yes |
| `dashboard_auth_log` | Authentication event log | **Append-only** |
| `dashboard_user_llm_keys` | BYOK LLM keys (AES-256 Fernet encrypted, D175) | Yes |

### Dev Profiles (Personalization)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `dev_profiles` | Developer profiles (version-based immutability, D183) | **Append-only** |
| `dev_profile_locks` | Dimension lock governance | Yes |
| `dev_profile_detections` | Auto-detected profile dimensions | Yes |

### Innovation Engine

| Table | Purpose | Mutable |
|-------|---------|---------|
| `innovation_signals` | Discovered signals from web/internal scanning | **Append-only** |
| `innovation_triage_log` | Triage decision records | **Append-only** |
| `innovation_solutions` | Generated solution specifications | Yes |
| `innovation_trends` | Detected technology trends | Yes |
| `innovation_competitor_scans` | Competitive intelligence scan results | Yes |
| `innovation_standards_updates` | Standards body change monitoring | Yes |
| `innovation_feedback` | Feedback calibration records | Yes |

### AI Security (Phase 37)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `prompt_injection_log` | Prompt injection detection events | **Append-only** |
| `ai_telemetry` | AI usage telemetry (SHA-256 hashed, D216) | **Append-only** |
| `ai_bom` | AI Bill of Materials components | Yes |
| `atlas_red_team_results` | ATLAS red teaming execution results | Yes |

### Evolutionary Intelligence (Phase 36)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `child_capabilities` | Child app capability declarations | Yes |
| `child_telemetry` | Child app health telemetry | Yes |
| `child_learned_behaviors` | Behaviors reported from children | Yes |
| `genome_versions` | Capability genome version history (semver + SHA-256) | Yes |
| `capability_evaluations` | 7-dimension capability scoring | Yes |
| `staging_environments` | Staging isolation for capability testing | Yes |
| `propagation_log` | Genome propagation records (HITL approval required) | **Append-only** |

### Cloud-Agnostic Architecture (Phase 38)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `cloud_provider_status` | CSP health check status per service | Yes |
| `cloud_tenant_csp_config` | Per-tenant CSP configuration overrides | Yes |
| `csp_region_certifications` | Region-to-framework certification mapping | Yes |

### Cross-Language Translation (Phase 43)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `translation_jobs` | Translation pipeline job tracking | Yes |
| `translation_units` | Individual translation unit results | Yes |
| `translation_dependency_mappings` | Cross-language dependency equivalents | Yes |
| `translation_validations` | Validation and repair cycle results | Yes |

### Innovation Adaptation (Phase 44)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `chat_contexts` | Multi-stream parallel chat contexts | Yes |
| `chat_messages` | Chat message queue per context | Yes |
| `chat_tasks` | Chat-initiated task tracking | Yes |
| `extension_registry` | Active extension hook registrations | Yes |
| `extension_execution_log` | Extension hook execution records | **Append-only** |
| `memory_consolidation_log` | AI-driven memory merge/replace decisions | **Append-only** |

### OWASP Agentic AI Security (Phase 45)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `tool_chain_events` | Tool chain validation events | **Append-only** |
| `agent_trust_scores` | Dynamic trust score records | **Append-only** |
| `agent_output_violations` | Output content safety violations | **Append-only** |

### Observability, Traceability & XAI (Phase 46)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `otel_spans` | OpenTelemetry-compatible trace spans | **Append-only** |
| `prov_entities` | W3C PROV-AGENT entities | **Append-only** |
| `prov_activities` | W3C PROV-AGENT activities | **Append-only** |
| `prov_relations` | W3C PROV-AGENT relations (wasGeneratedBy, used, etc.) | **Append-only** |
| `shap_attributions` | AgentSHAP Shapley value attributions | **Append-only** |
| `xai_assessments` | XAI compliance assessment results | **Append-only** |

### Production Readiness (Phase 47)

| Table | Purpose | Mutable |
|-------|---------|---------|
| `production_audits` | Production readiness audit results (30 checks) | **Append-only** |
| `remediation_audit_log` | Auto-fix remediation execution trail | **Append-only** |

---

## Append-Only Tables

The following 29 tables are protected by the pre-tool-use hook (`.claude/hooks/pre_tool_use.py`). Any `UPDATE`, `DELETE`, `DROP`, or `TRUNCATE` operation on these tables is blocked at the hook level, enforcing NIST 800-53 AU controls (D6).

```
APPEND_ONLY_TABLES = [
    # Core audit
    "audit_trail",
    "hook_events",
    # Phase 44
    "extension_execution_log",
    "memory_consolidation_log",
    # Phase 29
    "auto_resolution_log",
    # Phase 36
    "propagation_log",
    # Phase 37
    "prompt_injection_log",
    "ai_telemetry",
    # Phase 22
    "marketplace_reviews",
    "marketplace_scan_results",
    "marketplace_versions",
    # Multi-Agent
    "agent_vetoes",
    # Dashboard Auth
    "dashboard_auth_log",
    # Phase 24
    "devsecops_pipeline_audit",
    # Phase 28
    "remote_command_log",
    # Phase 35
    "innovation_signals",
    "innovation_triage_log",
    # Phase 39
    "agent_executions",
    # Phase 40
    "nlq_queries",
    # Phase 34
    "dev_profiles",
    # Phase 45
    "tool_chain_events",
    "agent_trust_scores",
    "agent_output_violations",
    # Phase 46
    "otel_spans",
    "prov_entities",
    "prov_activities",
    "prov_relations",
    "shap_attributions",
    "xai_assessments",
    # Phase 47
    "production_audits",
    "remediation_audit_log",
]
```

**Guardrail**: When adding a new append-only/immutable DB table, ALWAYS add it to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`. The governance validator (`tools/testing/claude_dir_validator.py`) detects drift between `init_icdev_db.py` and the hook list.

---

## Database Migration System (D150)

ICDEV uses a lightweight migration runner (stdlib only, no Alembic) for schema versioning.

### Migration Files

```
tools/db/migrations/
+-- 001_baseline.sql           # Delegates to init_icdev_db.py (D151)
+-- 002_add_feature.sql        # SQL migration
+-- 003_data_migration.py      # Python migration
+-- ...
```

### Migration Table

```sql
CREATE TABLE schema_migrations (
    version     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    checksum    TEXT NOT NULL,    -- SHA-256 of migration file
    applied_at  TEXT NOT NULL,
    duration_ms INTEGER
);
```

### Migration Commands

```bash
# Show migration status
python tools/db/migrate.py --status [--json]

# Apply pending migrations
python tools/db/migrate.py --up [--target 005] [--dry-run]

# Roll back migrations
python tools/db/migrate.py --down [--target 003]

# Validate checksums (detect tampered migrations)
python tools/db/migrate.py --validate [--json]

# Scaffold new migration
python tools/db/migrate.py --create "add_feature_table"

# Mark existing DB as already migrated
python tools/db/migrate.py --mark-applied 001

# Apply to all tenant databases
python tools/db/migrate.py --up --all-tenants
```

### Migration Directives

Migration files support database-specific directives:

```sql
-- @sqlite-only
CREATE TABLE IF NOT EXISTS feature_flags (...);

-- @pg-only
CREATE TABLE IF NOT EXISTS feature_flags (...) PARTITION BY RANGE (created_at);
```

---

## Backup and Restore System (D152)

### Backup Commands

```bash
# Backup single database
python tools/db/backup.py --backup [--db icdev] [--json]

# Backup all databases
python tools/db/backup.py --backup --all [--json]

# Backup tenant databases
python tools/db/backup.py --backup --tenants [--slug acme]

# Restore from backup
python tools/db/backup.py --restore --backup-file path/to/backup.bak

# Verify backup integrity
python tools/db/backup.py --verify --backup-file path/to/backup.bak

# List available backups
python tools/db/backup.py --list [--json]

# Prune old backups
python tools/db/backup.py --prune [--retention-days 30]
```

### Backup Technology

| Database | Method | Notes |
|----------|--------|-------|
| SQLite | `sqlite3.backup()` API | WAL-safe online backup, no downtime |
| PostgreSQL | `pg_dump` | Standard logical backup |

### Encryption (Optional)

- Algorithm: AES-256-CBC
- Key derivation: PBKDF2 with 600,000 iterations
- Package: `cryptography` (optional dependency)
- Configuration: `args/db_config.yaml`

---

## Database Initialization

```bash
# Initialize all 193 tables in icdev.db
python tools/db/init_icdev_db.py

# Initialize SaaS platform database
python tools/saas/platform_db.py --init
```

The init script is idempotent -- it uses `CREATE TABLE IF NOT EXISTS` for all tables.

---

## Platform Database (SaaS — data/platform.db)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `tenants` | Tenant organizations | id, name, slug, il, tier, status, approved_at |
| `users` | User accounts | id, tenant_id, email, name, role, status |
| `api_keys` | API key hashes (SHA-256) | id, user_id, key_hash, name, expires_at |
| `subscriptions` | Subscription tier and limits | id, tenant_id, tier, max_projects, max_users |
| `usage_records` | API usage tracking per tenant | id, tenant_id, endpoint, timestamp, tokens |
| `audit_platform` | Platform-level audit trail | id, tenant_id, action, actor, timestamp |

### Tenant Isolation Model (D60)

```
+-- data/platform.db             (shared: tenants, users, keys)
|
+-- data/tenants/
    +-- acme.db                   (full icdev.db schema, ACME data only)
    +-- contractor-a.db           (full icdev.db schema, Contractor A only)
    +-- dod-unit-x.db             (full icdev.db schema, DoD Unit X only)
```

Each tenant gets its own SQLite file (or PostgreSQL database in production). There is no row-level tenant filtering -- isolation is at the database level for the strongest security boundary.

---

## Memory Database (data/memory.db)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `memory_entries` | Memory facts, preferences, events, insights | id, content, type, importance, embedding (BLOB), created_at |
| `daily_logs` | Daily session log entries | id, date, content, created_at |
| `memory_access_log` | Memory read/search access tracking | id, query, results_count, timestamp |

### Embedding Storage

Embeddings are stored as BLOBs (1536-dimension float arrays) in the `memory_entries` table. Generated by:
- Cloud: OpenAI `text-embedding-3-small`
- Air-gapped: Ollama `nomic-embed-text`

---

## Activity Database (data/activity.db)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `tasks` | Task tracking and status | id, description, status, created_at, completed_at |

---

## Configuration Reference

### Database Config (args/db_config.yaml)

```yaml
migration:
  auto_migrate: false          # Run migrations on startup
  checksum_validation: true    # Verify migration file integrity
  lock_timeout_ms: 5000        # Migration lock timeout

backup:
  retention_days: 30           # Keep backups for 30 days
  encryption: false            # Optional AES-256-CBC
  schedule:
    icdev: daily               # Backup icdev.db daily
    platform: daily            # Backup platform.db daily
    memory: weekly             # Backup memory.db weekly

tenant_backup:
  enabled: true
  schedule: daily
  retention_days: 90           # Longer retention for tenant data
```

---

## Schema Governance

The `.claude/hooks/pre_tool_use.py` hook enforces schema integrity at runtime:

1. **Append-only enforcement**: Blocks UPDATE/DELETE/DROP/TRUNCATE on 29 protected tables
2. **Governance validator**: `python tools/testing/claude_dir_validator.py --json` detects drift between `init_icdev_db.py` and the hook's `APPEND_ONLY_TABLES` list
3. **Security gate**: `claude_config_alignment` gate blocks on unprotected append-only tables (NIST AU-2, CM-3, SA-11)

### Adding a New Table Checklist

1. Add `CREATE TABLE IF NOT EXISTS` to `tools/db/init_icdev_db.py`
2. Create a migration file: `python tools/db/migrate.py --create "add_table_name"`
3. If append-only: add table name to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
4. Run governance validator: `python tools/testing/claude_dir_validator.py --json`
5. Update this document with the new table
