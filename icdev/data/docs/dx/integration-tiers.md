# Integration Tiers

ICDEV abstracts its complexity through three tiers. Each tier gives developers a different level of interaction — from fully invisible to fully explicit. Most teams combine Tier 1 (automatic pipeline) with Tier 2 (Claude Code conversations).

---

## Tier 1: Invisible (Zero Developer Interaction)

### Concept

ICDEV runs as infrastructure. Developers push code. Compliance, security, testing, and artifact generation happen automatically in the CI/CD pipeline. The developer never types an ICDEV command.

### How It Works

```
Developer pushes code
        |
        v
  CI/CD Pipeline (GitHub Actions / GitLab CI)
        |
        +-- SAST scan (bandit/gosec/eslint-security)
        +-- Dependency audit (pip-audit/npm audit/govulncheck)
        +-- Secret detection (detect-secrets)
        +-- CUI marking validation
        +-- STIG compliance check
        +-- Unit + BDD test execution
        +-- SBOM generation (CycloneDX)
        |
        v
  PR Status Checks (pass/fail)
        |
  On merge to main:
        |
        +-- SSP regeneration
        +-- POAM update
        +-- cATO evidence refresh
        +-- Deployment pipeline trigger
```

### Setup

1. Drop an [`icdev.yaml`](icdev-yaml-spec.md) in your repo root
2. Add the ICDEV GitHub Action or GitLab CI include (see [CI/CD Integration](ci-cd-integration.md))
3. Push. That's it.

### What the Developer Sees

- Green/red status checks on PRs
- Inline PR comments for security findings
- Automated compliance reports in the `artifacts/` directory
- Nothing else. ICDEV is invisible.

### When to Use

- You want compliance as a zero-effort default
- Your team shouldn't need to think about NIST 800-53, STIG, or CUI markings
- You want every push automatically validated against your security posture
- You're running cATO and need evidence to stay current continuously

---

## Tier 2: Conversational (Natural Language via Claude Code)

### Concept

Developers talk to Claude Code in plain English. Claude reads the project's `icdev.yaml`, dev profile, and compliance posture, then orchestrates the right ICDEV tools automatically. The developer never calls a Python script.

### How It Works

```
Developer: "Build a REST API for user management with RBAC"
        |
        v
  Claude Code reads:
    - icdev.yaml (project config)
    - Dev profile (coding standards via cascade resolution)
    - CLAUDE.md (orchestration instructions)
    - goals/build_app.md (ATLAS workflow)
        |
        v
  Claude orchestrates (developer doesn't see this):
    1. tools/builder/test_writer.py      -- TDD RED phase
    2. tools/builder/code_generator.py   -- TDD GREEN phase
    3. tools/builder/linter.py           -- REFACTOR phase
    4. tools/security/sast_runner.py     -- Security scan
    5. tools/compliance/control_mapper.py -- Map to NIST controls
    6. tools/compliance/cui_marker.py    -- Apply CUI markings
        |
        v
  Developer gets: working code, tests, compliance artifacts
```

### Common Conversations

**Building features:**
> Build a user authentication module with CAC/PIV support

**Checking compliance:**
> What's our FedRAMP compliance status? Are there any open POAM items?

**Fixing issues:**
> Fix the failing STIG check on the database module

**Running security scans:**
> Run a full security scan and fix any critical findings

**Managing dev profiles:**
> Show me our project's coding standards. Update the line length to 120.

**Generating artifacts:**
> Generate our SSP and SBOM for the next ATO review

**Intake and planning:**
> We need a mission planning tool for IL5. Start the requirements intake.

### Slash Commands (Shortcuts)

For frequently used workflows, ICDEV provides slash commands that are shorthand for common requests:

| Command | What It Does | Equivalent Natural Language |
|---------|-------------|----------------------------|
| `/icdev-build` | TDD build cycle | "Build this feature using TDD" |
| `/icdev-test` | Run full test suite | "Run all tests" |
| `/icdev-comply` | Generate ATO artifacts | "Generate compliance artifacts" |
| `/icdev-secure` | Security scanning | "Run security scans" |
| `/icdev-deploy` | Generate IaC + pipeline | "Deploy this to staging" |
| `/icdev-intake` | Requirements intake | "Start requirements intake" |
| `/icdev-status` | Project dashboard | "Show project status" |
| `/icdev-review` | Code review gates | "Review this code for merge" |

You don't need to memorize these. Just talk to Claude naturally — it knows when to use them.

### When to Use

- Day-to-day feature development
- Exploring compliance status or fixing findings
- Requirements intake with stakeholders
- Any task where you'd normally need to look up which ICDEV tool to run

---

## Tier 3: Programmatic (REST API / MCP / Direct CLI)

### Concept

For teams building automation on top of ICDEV — custom CI/CD scripts, integration tools, or batch operations — ICDEV exposes its full capability through REST API, MCP Streamable HTTP, and direct Python CLI.

### REST API

The SaaS API gateway exposes all ICDEV functionality via standard HTTP:

```bash
# Authenticate
export ICDEV_TOKEN="icdev_abc123..."

# Create a project
curl -X POST https://icdev.example.com/api/v1/projects \
  -H "Authorization: Bearer $ICDEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app", "type": "microservice", "impact_level": "IL4"}'

# Run compliance assessment
curl https://icdev.example.com/api/v1/projects/proj-123/compliance \
  -H "Authorization: Bearer $ICDEV_TOKEN"

# Generate SSP
curl -X POST https://icdev.example.com/api/v1/compliance/ssp \
  -H "Authorization: Bearer $ICDEV_TOKEN" \
  -d '{"project_id": "proj-123"}'

# Resolve dev profile
curl https://icdev.example.com/api/v1/dev-profiles/resolve/project/proj-123 \
  -H "Authorization: Bearer $ICDEV_TOKEN"
```

Full API documentation: Swagger UI at `/api/v1/docs` when running the API gateway.

### MCP Streamable HTTP

For Claude Code clients connecting to ICDEV as an MCP server:

```
POST /mcp/v1/
Content-Type: application/json

{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "scaffold", "arguments": {"type": "python-backend", "name": "my-app"}}, "id": 1}
```

14 MCP servers expose 100+ tools. See CLAUDE.md for the full tool list.

### Direct Python CLI

Every ICDEV tool can be called directly. This is the lowest-level interface:

```bash
# Scaffold a project
python tools/builder/scaffolder.py --type python-backend --name my-app

# Run compliance scan
python tools/compliance/ssp_generator.py --project-id proj-123

# Resolve dev profile cascade
python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 --resolve --json

# Auto-detect coding standards from repo
python tools/builder/profile_detector.py --repo-path /path/to/repo --json
```

### When to Use

- Custom CI/CD pipeline scripts
- Integration with external tools (Jira, ServiceNow, GitLab)
- Batch operations across multiple projects
- Building custom dashboards or reporting tools
- Automated testing of ICDEV itself

---

## Choosing Your Tier

```
                              Developer Effort
                    Low ◄─────────────────────► High

Tier 1 (Invisible)  ████░░░░░░░░░░░░░░░░░░░░░░
Tier 2 (Claude)     ░░░░████████░░░░░░░░░░░░░░
Tier 3 (API/CLI)    ░░░░░░░░░░░░░░████████████

                              Control & Flexibility
                    Low ◄─────────────────────► High

Tier 1 (Invisible)  ████████░░░░░░░░░░░░░░░░░░
Tier 2 (Claude)     ░░░░░░████████████░░░░░░░░
Tier 3 (API/CLI)    ░░░░░░░░░░░░░░░░██████████
```

### Recommended Combinations

| Team Profile | Recommended Tiers | Why |
|-------------|-------------------|-----|
| **App Dev Team** | Tier 1 + Tier 2 | Pipeline auto-runs compliance; Claude handles feature building |
| **DevSecOps Team** | Tier 1 + Tier 3 | Pipeline enforces gates; API for custom tooling and reporting |
| **Platform Team** | Tier 2 + Tier 3 | Claude for exploration; API for multi-tenant management |
| **ISSO/Compliance** | Tier 2 | Claude for querying compliance status, generating reports |
| **Solo Developer** | Tier 2 | Just talk to Claude. It handles everything. |

---

## How the Tiers Connect

All three tiers ultimately call the same underlying ICDEV tools. The difference is the interface:

```
Tier 1 (Pipeline)     Tier 2 (Claude)     Tier 3 (API)
      |                     |                   |
      v                     v                   v
+----------------------------------------------------------+
|                    GOTCHA Framework                        |
|  Goals -> Orchestration -> Tools -> Args -> Context       |
+----------------------------------------------------------+
      |
      v
+----------------------------------------------------------+
|              Deterministic Python Tools                    |
|  (Same tools, same outputs, regardless of entry point)   |
+----------------------------------------------------------+
      |
      v
+----------------------------------------------------------+
|                    SQLite / PostgreSQL                     |
|  (146 tables: compliance, security, profiles, audit)      |
+----------------------------------------------------------+
```

This means:
- A compliance artifact generated by the pipeline (Tier 1) is identical to one generated by Claude (Tier 2) or the API (Tier 3)
- Dev profile resolution works the same everywhere
- Audit trail captures actions from all tiers
- Security gates enforce the same rules regardless of entry point
