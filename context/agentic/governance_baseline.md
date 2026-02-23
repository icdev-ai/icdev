# [TEMPLATE: CUI // SP-CTI]

# Governance Baseline for Generated Applications

## Purpose

This document defines the mandatory governance requirements that every ICDEV-generated child application must satisfy. These requirements are non-negotiable and are enforced during generation (Step 5) and verified during post-generation checks (Step 6) of the agentic generation workflow.

---

## Classification

All generated applications inherit classification from the parent ICDEV instance. The impact level determines markings, encryption, and network constraints.

| Impact Level | Classification Marking | Network | Encryption | Cloud Region |
|-------------|----------------------|---------|------------|--------------|
| IL2 | Public — no markings required | Internet OK | TLS 1.2+ | Any commercial |
| IL4 | CUI // SP-CTI | GovCloud only | FIPS 140-2 | us-gov-west-1 |
| IL5 | CUI // SP-CTI | Dedicated GovCloud | FIPS 140-2 | us-gov-west-1 (dedicated) |
| IL6 | SECRET // NOFORN | SIPR only | NSA Type 1 | Air-gapped SIPR |

**Enforcement:** The `classification_manager.py` tool generates all markings. Do NOT hard-code CUI banners. The child app's classification is set at generation time via the blueprint and cannot be escalated without regeneration.

**File marking rules:**
- All Python files: CUI marking in file header comment
- All Markdown files: CUI marking as first line
- All YAML/JSON config files: CUI marking in top-level comment or metadata field
- All generated reports: CUI banner at top and bottom, page-level portion markings

---

## Compliance Inheritance

Child applications with the compliance capability enabled inherit support for all 9 compliance frameworks:

| # | Framework | Catalog Source | Assessor Tool |
|---|-----------|---------------|---------------|
| 1 | NIST 800-53 Rev 5 | `nist_800_53.json` | `control_mapper.py` |
| 2 | FedRAMP Moderate | `fedramp_moderate_baseline.json` | `fedramp_assessor.py` |
| 3 | FedRAMP High | `fedramp_high_baseline.json` | `fedramp_assessor.py` |
| 4 | NIST 800-171 | `nist_800_171_controls.json` | via crosswalk |
| 5 | CMMC Level 2/3 | `cmmc_practices.json` | `cmmc_assessor.py` |
| 6 | DoD CSSP (DI 8530.01) | `dod_cssp_8530.json` | `cssp_assessor.py` |
| 7 | CISA Secure by Design | `cisa_sbd_requirements.json` | `sbd_assessor.py` |
| 8 | IEEE 1012 IV&V | `ivv_requirements.json` | `ivv_assessor.py` |
| 9 | DoDI 5000.87 DES | `des_requirements.json` | `des_assessor.py` |

**Crosswalk inheritance:** When the child app implements a NIST 800-53 control (e.g., AC-2), the crosswalk engine automatically maps it to the corresponding controls in FedRAMP, CMMC, and NIST 800-171. This is inherited behavior from ICDEV's `crosswalk_engine.py`.

**Compliance catalogs:** JSON catalog files are copied into the child app's `context/compliance/` directory during generation. The child app uses its own local copies, not references back to ICDEV.

---

## Security Requirements

### Container Security
- All containers run as non-root (UID 1000)
- Read-only root filesystem enforced (`readOnlyRootFilesystem: true`)
- All Linux capabilities dropped (`drop: ["ALL"]`)
- No privilege escalation allowed (`allowPrivilegeEscalation: false`)
- Resource limits enforced (CPU and memory)
- STIG-hardened base image (`docker/Dockerfile.agent-base`)

### Network Security
- Default-deny network policies in Kubernetes namespace
- Agent-to-agent traffic restricted to cluster-internal only
- Mutual TLS (mTLS) for all A2A communication
- X.509 certificates issued by cluster CA
- Ingress restricted to authenticated endpoints only

### Secret Management
- No secrets in code, config files, or environment variables checked into version control
- AWS Secrets Manager (GovCloud) or K8s secrets for runtime secrets
- `.env.example` provided with placeholder values; `.env` is in `.gitignore`
- Secret detection runs as part of every CI/CD pipeline stage

### Encryption
- TLS 1.2+ for all data in transit
- AES-256 for all data at rest
- FIPS 140-2 validated modules required for IL4+ impact levels
- NSA Type 1 encryption required for IL6 (SECRET)

---

## Audit Requirements

### Append-Only Audit Trail
The audit trail is immutable. No UPDATE or DELETE operations are permitted on audit tables. This satisfies NIST 800-53 AU-family controls:

| Control | Requirement | Implementation |
|---------|-------------|----------------|
| AU-2 | Event logging | All agent actions, A2A messages, tool executions logged |
| AU-3 | Content of audit records | Timestamp, actor, action, project_id, result, classification |
| AU-6 | Audit review | Query tools for filtering and analysis |
| AU-9 | Protection of audit info | Append-only schema, no delete permissions |
| AU-11 | Audit retention | Configurable retention (default: 7 years) |

### What Gets Logged
- Every A2A task lifecycle event (created, assigned, in_progress, completed, failed)
- Every tool execution with input parameters and output summary
- Every compliance assessment and gate check result
- Every security scan finding
- All user decisions and approvals
- Generation events (when the child app was created, by which ICDEV instance)

### Audit Schema
```sql
CREATE TABLE audit_trail (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT NOT NULL
);
-- NO UPDATE OR DELETE triggers/permissions
```

---

## Grandchild Prevention

Generated applications MUST NOT generate their own child applications. This is enforced at three independent levels to prevent bypass:

### Level 1: Configuration Flag
The child app's `args/project_defaults.yaml` contains:
```yaml
agentic_generation:
  enabled: false
  reason: "Child applications cannot generate grandchild applications"
```
The scaffolder checks this flag at startup and refuses to run with `--agentic` if `enabled: false`.

### Level 2: Tool Exclusion
The following ICDEV tools are excluded from the child app's file manifest and are never copied:
- `tools/builder/agentic_fitness.py`
- `tools/builder/app_blueprint.py`
- Any templates in `context/agentic/` related to generation (fitness rubric, architecture patterns, governance baseline are retained as reference)

### Level 3: CLAUDE.md Documentation
The child app's CLAUDE.md contains the following statement in a prominent section:
```
## Limitations
This application CANNOT generate child applications. Agentic generation
is only available in the parent ICDEV system. This restriction is enforced
by configuration, tool exclusion, and this documentation.
```

### Verification
During governance review (Step 6), all three levels are checked:
```bash
# Level 1: Config flag
grep "enabled: false" args/project_defaults.yaml

# Level 2: Tool exclusion
ls tools/builder/ | grep -E "(agentic_fitness|app_blueprint)"
# Should return empty

# Level 3: CLAUDE.md
grep "CANNOT generate child" CLAUDE.md
# Should match
```

---

## Memory System Requirements

Every child application includes a memory system with dual storage:

| Storage | Format | Purpose |
|---------|--------|---------|
| `memory/MEMORY.md` | Markdown | Human-readable curated facts and preferences |
| `data/memory.db` | SQLite | Searchable database with embeddings support |
| `memory/logs/YYYY-MM-DD.md` | Markdown | Daily session logs |

**Session protocol:** The child app's AI orchestrator must read `MEMORY.md` and the current day's log at the start of every session, exactly as ICDEV does.

---

## CI/CD Requirements

Every child application includes a CI/CD pipeline with these mandatory stages:

| Stage | Gate | Blocking Condition |
|-------|------|--------------------|
| Build | Compilation | Syntax errors, missing dependencies |
| Lint | Code quality | Linting violations above threshold |
| Test | Coverage | Coverage < 80%, any test failures |
| Security | SAST + deps | CAT1 findings, critical vulnerabilities, secrets detected |
| Compliance | CUI markings | Missing markings on any file at IL4+ |
| Deploy | All gates | Any blocking condition from prior stages |

---

## Related Files

- **Goal:** `goals/agentic_generation.md` — Workflow that enforces this baseline
- **Context:** `context/agentic/architecture_patterns.md` — Architecture patterns for child apps
- **Context:** `context/agentic/fitness_rubric.md` — Fitness scoring rubric
- **Tools:** `tools/compliance/classification_manager.py` — Classification marking generation
- **Tools:** `tools/compliance/crosswalk_engine.py` — Multi-framework control crosswalk
- **Args:** `args/security_gates.yaml` — Gate thresholds and blocking conditions
