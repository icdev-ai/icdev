# Application Modernization — 7Rs Migration Workflow

## Goal

Modernize legacy DoD applications using the 7 Rs of Cloud Migration Strategy. Provide systematic assessment, planning, and execution of migration from legacy systems (Python 2, Java 8, .NET Framework 4.x, Struts, EJB, WCF, WebForms) to modern, ATO-compliant architectures on AWS GovCloud.

**Why this matters:** Legacy systems are the number one blocker for ATO in DoD programs. Original developers leave, documentation rots, frameworks reach end-of-life, and tech debt compounds until the system is unmaintainable. This workflow turns an opaque legacy codebase into a documented, tested, compliant modern application — without losing functional equivalence or ATO coverage.

**The 7 Rs:**

| Strategy | Description | When to Use |
|----------|-------------|-------------|
| **Rehost** | Lift and shift to cloud | Working app, no code changes needed, just needs cloud infra |
| **Replatform** | Lift, tinker, shift | Minor changes (containerize, swap DB engine), no architecture change |
| **Refactor** | Upgrade in place | Same architecture, upgrade language/framework versions |
| **Rearchitect** | Decompose and rebuild | Monolith to microservices, new patterns, major structural change |
| **Rebuild** | Rewrite from scratch | Legacy is unsalvageable, but requirements are known |
| **Replace** | Buy COTS/SaaS | Commercial solution exists that meets requirements |
| **Retire** | Decommission | System no longer needed, migrate data and shut down |

---

## When to Use

- Customer has a legacy application that cannot achieve ATO on current stack
- Original developers are no longer available and institutional knowledge is lost
- Application documentation is missing, outdated, or nonexistent
- Tech debt is preventing feature development or security patching
- Framework or language version has reached end-of-life (no security patches)
- Monolithic architecture prevents scaling or independent deployment
- Compliance assessors have flagged unsupported software components
- Migration from on-prem to AWS GovCloud is required

---

## Prerequisites

- [ ] ICDEV project initialized (`/icdev-init` or `goals/init_project.md` completed)
- [ ] Legacy source code accessible at a known path on the filesystem
- [ ] Project defaults configured (`args/project_defaults.yaml`)
- [ ] `memory/MEMORY.md` loaded (session context)
- [ ] Customer has identified the legacy application and stated migration intent
- [ ] Access to legacy database (connection string or dump file) if applicable

---

## Process

### Step 1: Register and Analyze Legacy Application

**Tool:** `python tools/modernization/legacy_analyzer.py --register --name "<app-name>" --path "/path/to/legacy" --project-id <project_id>`

Then run full analysis:

**Tool:** `python tools/modernization/legacy_analyzer.py --analyze --app-id <app_id> --depth full`

**What it does:**
- AST parsing (Python, Java, C#) and regex fallback for unsupported languages
- Component extraction: modules, classes, functions, endpoints
- Dependency mapping: direct and transitive, with version detection
- Framework detection: identifies Struts, EJB, WCF, WebForms, Django, Flask, Spring, etc.
- API discovery: REST endpoints, SOAP WSDLs, RPC interfaces
- Database schema extraction: tables, columns, relationships, stored procedures
- Complexity metrics: cyclomatic complexity, lines of code, coupling scores
- Tech debt estimation: hours to remediate, risk hotspot identification

**Expected output:**
```
Legacy application registered: app-<id>
Analysis complete.

Components: <count> (modules: X, classes: Y, functions: Z)
Dependencies: <count> (direct: X, transitive: Y)
Framework: <detected_framework> <version>
Language: <language> <version>
APIs: <count> endpoints discovered
DB tables: <count>
Complexity: <avg_cyclomatic> avg cyclomatic, <loc> LOC
Tech debt estimate: <hours> hours
Risk hotspots: <count> files flagged
```

**Database populated:** `legacy_applications`, `legacy_components`, `legacy_dependencies`, `legacy_apis`

**Error handling:**
- Path not found → fail with clear error, do not guess paths
- Language not recognized → fall back to regex analysis, warn that AST metrics are unavailable
- Binary dependencies → log as unanalyzable, flag for manual review
- Empty source directory → fail, nothing to analyze

**Verify:** All component counts are nonzero. Dependencies resolved with versions. Framework detected.

---

### Step 2: Extract Architecture

**Tool:** `python tools/modernization/architecture_extractor.py --app-id <app_id> --extract summary`

**What it does:**
- Call graph generation: function-to-function, module-to-module
- Component diagram: logical grouping of related modules
- Data flow analysis: how data moves through the system (input → processing → storage → output)
- Service boundary detection: identifies natural seam lines for decomposition
- Coupling and cohesion scoring per component cluster
- External integration mapping: third-party services, APIs, file I/O

**Expected output:**
```
Architecture extraction complete.

Call graph: <node_count> nodes, <edge_count> edges
Component clusters: <count>
Data flows: <count> identified paths
Service boundaries: <count> suggested boundaries
  Boundary 1: [auth, users, sessions] — high cohesion (0.87)
  Boundary 2: [orders, payments, invoices] — high cohesion (0.82)
  Boundary 3: [reports, analytics, export] — moderate cohesion (0.71)

Coupling score (lower is better): <score>
Circular dependencies: <count>
```

**Error handling:**
- Circular dependencies detected → flag with full cycle path, recommend ACL (Anti-Corruption Layer) insertion points
- No clear boundaries → recommend refactor over rearchitect, monolith may be acceptable
- Dynamic dispatch (reflection, eval) → warn that call graph may be incomplete

**Verify:** Service boundaries have cohesion scores. Circular dependencies documented with resolution paths.

---

### Step 3: Generate Missing Documentation

**Tool:** `python tools/modernization/doc_generator.py --app-id <app_id> --output-dir "projects/<name>/docs/legacy" --type all`

**Types available:** `api`, `data-dictionary`, `component`, `dependency-map`, `tech-debt`, `all`

**What it does:**
- API documentation: endpoints, parameters, request/response schemas, auth requirements
- Data dictionary: all database tables, columns, types, constraints, relationships
- Component documentation: purpose, dependencies, public interfaces, complexity scores
- Dependency map: visual and textual dependency graph with version status (current/outdated/EOL)
- Tech debt report: prioritized list of remediation items with effort estimates

**Expected output:**
```
Documentation generated:
  - projects/<name>/docs/legacy/api_docs.md
  - projects/<name>/docs/legacy/data_dictionary.md
  - projects/<name>/docs/legacy/component_docs.md
  - projects/<name>/docs/legacy/dependency_map.md
  - projects/<name>/docs/legacy/tech_debt_report.md

Total pages: ~<count>
Coverage: <pct>% of components documented
```

**Error handling:**
- No test files found → note in tech debt report as critical gap
- Undocumented APIs (no docstrings, no comments) → infer from code, mark as "inferred — verify with SME"
- Database not accessible → generate partial data dictionary from ORM models or SQL files

**Verify:** Every component has at least a one-sentence description. API docs list all discovered endpoints.

---

### Step 4: Assess 7R Strategy

**Tool:** `python tools/modernization/seven_r_assessor.py --project-id <project_id> --app-id <app_id> --matrix`

**What it does:**
- Scores all 7 strategies against weighted criteria:
  - Technical complexity (weight: 0.25)
  - ATO impact (weight: 0.20)
  - Cost estimate (weight: 0.15)
  - Timeline (weight: 0.15)
  - Risk (weight: 0.15)
  - Team capability required (weight: 0.10)
- Generates a decision matrix with normalized scores
- Provides a recommendation with confidence level
- Estimates cost range and timeline for top 3 strategies

**Expected output:**
```
7R Assessment Matrix:

| Strategy     | Technical | ATO Impact | Cost  | Timeline | Risk  | Team  | TOTAL |
|-------------|-----------|------------|-------|----------|-------|-------|-------|
| Rehost      |   0.3     |    0.2     |  0.9  |   0.9    |  0.8  |  0.9  | 0.60  |
| Replatform  |   0.5     |    0.4     |  0.7  |   0.7    |  0.6  |  0.7  | 0.55  |
| Refactor    |   0.7     |    0.7     |  0.5  |   0.5    |  0.5  |  0.5  | 0.58  |
| Rearchitect |   0.9     |    0.9     |  0.3  |   0.3    |  0.3  |  0.3  | 0.52  |
| Rebuild     |   0.9     |    0.9     |  0.1  |   0.1    |  0.2  |  0.2  | 0.42  |
| Replace     |   N/A     |    N/A     |  N/A  |   N/A    |  N/A  |  N/A  |  —    |
| Retire      |   N/A     |    N/A     |  N/A  |   N/A    |  N/A  |  N/A  |  —    |

Recommendation: REFACTOR (score: 0.58, confidence: HIGH)
Estimated cost: $<low> – $<high>
Estimated timeline: <months> months
ATO impact: <description>
```

**DECISION POINT:** Present the matrix to the customer. Do NOT proceed until the customer approves a strategy. Log the decision:

**Tool:** `python tools/audit/decision_recorder.py --project-id <project_id> --decision "Approved 7R strategy: <strategy>" --rationale "<customer rationale>" --actor "customer"`

**Error handling:**
- All strategies score below 0.3 → recommend "Retire" or escalate for manual assessment
- Replace/Retire not scoreable → mark N/A in matrix, note in recommendation
- Insufficient analysis data → re-run Step 1 with `--depth full`

**Verify:** All scoreable strategies have complete rows. Recommendation aligns with highest score. Customer approval logged.

---

### Step 5: Create Migration Plan

Based on the approved strategy, generate the migration plan.

**For Rearchitect (monolith decomposition):**

**Tool:** `python tools/modernization/monolith_decomposer.py --app-id <app_id> --create-plan --strategy rearchitect --target microservices --project-id <project_id>`

**For Refactor (version/framework upgrade):**

**Tool:** `python tools/modernization/monolith_decomposer.py --app-id <app_id> --create-plan --strategy refactor --target "<language_version>" --project-id <project_id>`

**For Rehost/Replatform (containerization):**

**Tool:** `python tools/modernization/monolith_decomposer.py --app-id <app_id> --create-plan --strategy <rehost|replatform> --target containers --project-id <project_id>`

**Expected output:**
```
Migration plan created: plan-<id>

Strategy: <approved_strategy>
Phases: <count>
Tasks: <count>
Estimated duration: <weeks> weeks

Phase 1: Foundation (<duration>)
  - Task 1.1: <description>
  - Task 1.2: <description>
Phase 2: Migration (<duration>)
  - Task 2.1: <description>
  ...
Phase 3: Validation (<duration>)
  - Task 3.1: <description>
  ...
Phase 4: Cutover (<duration>)
  - Task 4.1: <description>
  ...

Dependencies: <count> cross-task dependencies
Critical path: <task_ids>
```

**Database populated:** `migration_plans`, `migration_tasks`

**Error handling:**
- Strategy not approved → refuse to create plan, redirect to Step 4
- Circular dependencies in task ordering → flag and request manual sequencing
- Estimated duration exceeds 12 months → recommend phased approach with PI milestones

**Verify:** Every task has a duration estimate, dependency list, and acceptance criteria. Critical path identified.

---

### Step 6: Version and Framework Migration

**Only for Refactor or Rearchitect strategies.** Skip if Rehost/Replatform.

**Version migration:**

**Tool:** `python tools/modernization/version_migrator.py --source "/path/to/legacy" --output "/path/to/migrated" --from-version "<old>" --to-version "<new>" --language <language>`

**Framework migration:**

**Tool:** `python tools/modernization/framework_migrator.py --source "/path/to/legacy" --output "/path/to/migrated" --from-framework "<old>" --to-framework "<new>"`

**Database migration planning:**

**Tool:** `python tools/modernization/db_migration_planner.py --app-id <app_id> --target postgresql --output-dir "projects/<name>/db-migration"`

**Expected output (version):**
```
Version migration complete.
  Files processed: <count>
  Transformations applied: <count>
  Manual review needed: <count> files

  Changes by category:
    - Syntax updates: <count>
    - API replacements: <count>
    - Deprecated feature removal: <count>
    - Type annotation additions: <count>

  Output: /path/to/migrated/
```

**Expected output (database):**
```
DB migration plan generated.
  Source: <source_db_type>
  Target: PostgreSQL (RDS)

  DDL scripts: projects/<name>/db-migration/ddl/
  Data migration: projects/<name>/db-migration/data/
  Validation queries: projects/<name>/db-migration/validation/
  Rollback scripts: projects/<name>/db-migration/rollback/

  Tables: <count>
  Views: <count>
  Stored procedures: <count> (converted to application logic)
  Incompatible types: <count> (see conversion report)
```

**Error handling:**
- Unsupported version pair → fail with supported migration paths
- Framework not in mapping catalog → fall back to manual migration guidance
- Database type not in mapping catalog → generate partial DDL, flag for manual review
- Stored procedures with business logic → extract to application layer, do not silently drop

**Verify:** Migrated code compiles/parses without syntax errors. DDL scripts are idempotent. Rollback scripts exist.

---

### Step 7: Generate Migration Code

**Only for Rearchitect strategy.** Skip if Refactor/Rehost/Replatform.

**Tool:** `python tools/modernization/migration_code_generator.py --plan-id <plan_id> --output "projects/<name>/src" --generate all`

**Types available:** `adapters`, `facades`, `services`, `dal`, `tests`, `rollback`, `all`

**What it does:**
- Adapter pattern: wraps legacy interfaces for new consumers
- Facade pattern: simplifies complex legacy subsystems
- Service scaffolds: microservice skeletons from decomposition plan
- Data Access Layer: repository pattern for database operations
- Test stubs: unit and integration test scaffolding matching legacy behavior
- Rollback scripts: undo migration changes if cutover fails

**Expected output:**
```
Migration code generated.
  Adapters: <count> files
  Facades: <count> files
  Service scaffolds: <count> services
  DAL modules: <count> files
  Test stubs: <count> files
  Rollback scripts: <count> files

  Total files: <count>
  Output: projects/<name>/src/
```

**Error handling:**
- Plan not found → fail, run Step 5 first
- Service boundary undefined → cannot generate scaffold, re-run Step 2
- Legacy interface too complex for adapter → generate partial adapter with TODO markers

**Verify:** Every generated service has a health endpoint. Every adapter has corresponding tests. Rollback scripts are tested.

---

### Step 8: Strangler Fig Pattern Management

**Only for incremental migrations (Rearchitect with phased cutover).** Skip for big-bang migrations.

**Create strangler fig plan:**

**Tool:** `python tools/modernization/strangler_fig_manager.py --plan-id <plan_id> --create`

**Route traffic to modern component:**

**Tool:** `python tools/modernization/strangler_fig_manager.py --plan-id <plan_id> --cutover --component-id <comp_id> --to modern`

**Rollback component to legacy:**

**Tool:** `python tools/modernization/strangler_fig_manager.py --plan-id <plan_id> --cutover --component-id <comp_id> --to legacy`

**Health check (verify both paths work):**

**Tool:** `python tools/modernization/strangler_fig_manager.py --plan-id <plan_id> --health`

**Expected output (health):**
```
Strangler Fig Status — Plan <plan_id>

Components: <total>
  Legacy active: <count>
  Modern active: <count>
  Dual-running: <count>

Health:
  Legacy path: <status> (latency: <ms>ms)
  Modern path: <status> (latency: <ms>ms)
  Data consistency: <pct>% match

Migration progress: <pct>%
Next cutover candidate: <component_name> (risk: <low|medium|high>)
```

**Error handling:**
- Health check fails on modern path → auto-rollback to legacy, alert
- Data consistency below 99% → halt cutover, investigate sync
- Both paths down → escalate immediately, this is a production incident

**Verify:** No component is in an undefined state. Every cutover has a rollback path tested.

---

### Step 9: Compliance Bridge

**CRITICAL:** ATO coverage must be maintained throughout migration. Controls from the legacy system must transfer to the modern system.

**Inherit controls from legacy:**

**Tool:** `python tools/modernization/compliance_bridge.py --plan-id <plan_id> --inherit`

**Validate coverage:**

**Tool:** `python tools/modernization/compliance_bridge.py --plan-id <plan_id> --validate`

**Expected output (validate):**
```
Compliance Bridge Validation — Plan <plan_id>

Legacy controls: <count>
Inherited to modern: <count>
New controls needed: <count>
Coverage gap: <count> controls

Coverage: <pct>%
Gate: <PASS|FAIL> (threshold: 95%)

Gaps:
  - AC-6: Least Privilege — not yet implemented in service-auth module
  - AU-3: Content of Audit Records — logging format incomplete
```

**GATE: Coverage must be >= 95% before any PI close.** If below 95%, migration gate FAILS.

**Error handling:**
- Legacy system had no documented controls → start from NIST 800-53 baseline, flag as new implementation
- Control cannot transfer (architecture mismatch) → document gap, add to POAM
- Shared controls between legacy and modern (dual-running) → count as covered

**Verify:** Coverage percentage is >= 95%. All gaps have POAM entries or remediation tasks.

---

### Step 10: Track Progress and PI Gates

**Take PI snapshot:**

**Tool:** `python tools/modernization/migration_tracker.py --plan-id <plan_id> --snapshot --pi <PI_number> --type pi_end`

**View dashboard:**

**Tool:** `python tools/modernization/migration_tracker.py --plan-id <plan_id> --dashboard`

**Run compliance gate:**

**Tool:** `python tools/modernization/migration_tracker.py --plan-id <plan_id> --gate --pi <PI_number>`

**Expected output (gate):**
```
PI Gate Check — Plan <plan_id>, PI <PI_number>

Tasks completed: <count>/<total> (<pct>%)
Tests passing: <count>/<total> (<pct>%)
Compliance coverage: <pct>%
Security findings: CAT1=<n>, CAT2=<n>, CAT3=<n>
Tech debt delta: <hours_reduced> hours reduced

Gate criteria:
  [x] All PI tasks completed
  [x] Test coverage >= 80%
  [x] Compliance coverage >= 95%
  [x] 0 CAT1 findings
  [ ] All CAT2 findings have POAM entries

Gate: <PASS|FAIL>
```

**Error handling:**
- PI not found → list available PIs
- Metrics unavailable → run security scan and compliance check first
- Gate fails → do NOT proceed to next PI, document blockers

**Verify:** Gate result is accurate. Failed criteria have clear remediation paths.

---

### Step 11: Generate Reports

**Assessment report (for stakeholders):**

**Tool:** `python tools/modernization/migration_report_generator.py --app-id <app_id> --type assessment --output "projects/<name>/docs/modernization"`

**Progress report (for PI reviews):**

**Tool:** `python tools/modernization/migration_report_generator.py --plan-id <plan_id> --type progress --pi <PI_number> --output "projects/<name>/docs/modernization"`

**ATO impact report (for assessors):**

**Tool:** `python tools/modernization/migration_report_generator.py --plan-id <plan_id> --type ato-impact --output "projects/<name>/docs/modernization"`

**Expected output:**
```
Report generated: projects/<name>/docs/modernization/<report_type>_<date>.md

Sections: <count>
Pages: ~<count>
Classification: CUI // SP-CTI
```

**Error handling:**
- Missing data for report → generate partial report with `[DATA NEEDED]` placeholders, do not block
- PI not yet complete → generate interim report, clearly labeled as draft

**Verify:** Reports have CUI markings. Data matches current database state. No stale metrics.

---

### Step 12: Log to Audit Trail

**Tool:** `python tools/audit/audit_logger.py --event-type "modernization.<step>" --actor "orchestrator" --action "<action_description>" --project-id <project_id>`

**Tool:** `python tools/memory/memory_write.py --content "Modernization <step> completed for <app_name>. Strategy: <strategy>. Progress: <pct>%." --type event --importance 7`

Log at each major milestone: registration, analysis, strategy approval, plan creation, each PI gate, cutover, and completion.

---

## Success Criteria

- [ ] Legacy application registered and fully analyzed
- [ ] Architecture extracted with service boundaries identified
- [ ] Missing documentation generated and reviewed
- [ ] 7R strategy scored, recommended, and customer-approved
- [ ] Migration plan created with tasks, dependencies, and critical path
- [ ] Code migration completed (version, framework, or architecture as applicable)
- [ ] Strangler fig pattern operational (if incremental migration)
- [ ] Compliance bridge validates >= 95% control coverage
- [ ] All PI gates pass (tests, compliance, security)
- [ ] Assessment, progress, and ATO impact reports generated
- [ ] Audit trail entries logged at every milestone
- [ ] Zero CAT1 security findings in migrated application

---

## Edge Cases and Notes

1. **No test files found in legacy** — Refactor/rearchitect risk increases significantly. Recommend generating characterization tests (tests that capture current behavior) before any code changes. Use `tools/builder/test_writer.py` against the legacy codebase first.
2. **Circular dependencies detected** — May require additional Anti-Corruption Layer (ACL) modules at service boundaries. Architecture extractor flags these; decomposer generates ACL stubs.
3. **Framework not recognized** — Falls back to generic regex analysis. Manual review required for accuracy. Log as a known limitation in the assessment report.
4. **Database type not in mapping catalog** — DDL generation is partial. Manual review of type conversions required. Flag incompatible types explicitly.
5. **ATO coverage drops below 95%** — Migration gate FAILS. No PI close until remediated. Add missing controls to POAM with 30-day deadlines.
6. **Dual-running systems (strangler fig)** — Both paths must be monitored. Data consistency checks run hourly. Any divergence halts further cutover.
7. **Legacy system has no version control** — Import into Git as initial commit before analysis. Preserve original file timestamps in commit metadata.
8. **Customer changes strategy mid-migration** — Re-run Step 4 assessment, create new plan. Do NOT reuse old plan with different strategy. Archive old plan.
9. **Multi-language monolith** — Analyzer handles each language separately. Architecture extractor merges results. Service boundaries may follow language lines naturally.
10. **Embedded secrets in legacy code** — Secret detector runs as part of analysis. Secrets are flagged but NEVER copied to migrated code. Use AWS Secrets Manager in modern architecture.

---

## Anti-Patterns

1. **Migrating without analyzing first** — You will miss hidden dependencies, undocumented APIs, and database triggers that silently enforce business rules.
2. **Big bang migration of a monolith** — High risk of total failure. Prefer strangler fig pattern for incremental cutover with rollback capability.
3. **Skipping the compliance bridge** — You will lose ATO coverage and have to re-certify from scratch. Controls must transfer.
4. **Not generating tests before migration** — Without characterization tests, there is no way to verify functional equivalence between legacy and modern systems.
5. **Ignoring tech debt hotspots** — Migrating problems into a new architecture just gives you modern problems. Address hotspots during migration.
6. **Treating Rehost as "done"** — Rehost is a starting point, not a destination. Plan the next R (usually Replatform or Refactor) before the team disperses.
7. **Skipping the decision matrix** — Gut-feel strategy selection leads to mid-migration pivots, wasted effort, and schedule overruns.

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Register and Analyze | Tools | `legacy_analyzer.py` |
| Extract Architecture | Tools | `architecture_extractor.py` |
| Generate Documentation | Tools | `doc_generator.py` |
| 7R Assessment | Tools + Context | `seven_r_assessor.py` + 7R catalog |
| Create Migration Plan | Tools + Context | `monolith_decomposer.py` + migration patterns |
| Version/Framework Migration | Tools + Context | `version_migrator.py`, `framework_migrator.py` + migration rules |
| Generate Migration Code | Tools | `migration_code_generator.py` |
| Strangler Fig Management | Tools | `strangler_fig_manager.py` |
| Compliance Bridge | Tools | `compliance_bridge.py` |
| Track Progress | Tools | `migration_tracker.py` |
| Generate Reports | Tools + Context | `migration_report_generator.py` + report templates |
| Strategy decisions | Orchestration | AI (you) + customer approval |
| Migration patterns | Context | `context/modernization/` |
| Behavior settings | Args | `args/project_defaults.yaml` modernization section |

---

## Related Files

- **Args:** `args/project_defaults.yaml` (modernization section: default strategies, thresholds, PI cadence)
- **Context:** `context/modernization/` (7R catalog, migration patterns, framework mappings, report templates)
- **Hard Prompts:** `hardprompts/modernization/` (analysis prompts, assessment prompts, planning prompts)
- **Tools:** `tools/modernization/legacy_analyzer.py`, `tools/modernization/architecture_extractor.py`, `tools/modernization/doc_generator.py`, `tools/modernization/seven_r_assessor.py`, `tools/modernization/monolith_decomposer.py`, `tools/modernization/version_migrator.py`, `tools/modernization/framework_migrator.py`, `tools/modernization/db_migration_planner.py`, `tools/modernization/migration_code_generator.py`, `tools/modernization/strangler_fig_manager.py`, `tools/modernization/compliance_bridge.py`, `tools/modernization/migration_tracker.py`, `tools/modernization/migration_report_generator.py`
- **MCP Server:** `tools/mcp/modernization_server.py` (10 MCP tools)
- **Skill:** `.claude/skills/icdev-modernize/SKILL.md`
- **Feeds from:** `goals/init_project.md` (project setup), `goals/security_scan.md` (findings for compliance bridge)
- **Feeds into:** `goals/compliance_workflow.md` (ATO artifacts), `goals/deploy_workflow.md` (deployment of modernized app), `goals/tdd_workflow.md` (test generation for migrated code)

---

## Changelog

- 2026-02-16: Initial creation
