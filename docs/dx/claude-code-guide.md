# Claude Code Guide

Claude Code is the primary interface for developers working with ICDEV. You talk in natural language, and Claude orchestrates the right tools, applies your dev profile, and handles compliance automatically.

---

## Setup

1. Install Claude Code:
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

2. Open your project (must have `icdev.yaml` or be an initialized ICDEV project):
   ```bash
   cd my-project
   claude
   ```

3. Claude automatically reads:
   - `icdev.yaml` — project configuration
   - `CLAUDE.md` — orchestration instructions (ICDEV provides this)
   - Dev profile — your tenant/project coding standards (via cascade resolution)
   - Memory — previous session context

---

## Conversation Patterns

### Building Features

Talk about what you want to build. Claude follows the ATLAS workflow (Architect, Trace, Link, Assemble, Stress-test) automatically.

> Build a REST API endpoint for user profile management. It should support GET, PUT, and DELETE with role-based access control.

Claude will:
1. Write failing tests first (TDD RED)
2. Generate implementation (GREEN)
3. Refactor for quality (REFACTOR)
4. Apply your dev profile's coding standards (line length, naming conventions, imports)
5. Add CUI markings if required
6. Map to relevant NIST 800-53 controls
7. Run security scans

### Compliance Queries

Ask about compliance status in plain English:

> What's our FedRAMP status?
> Which STIG controls are failing?
> Show me our open POAM items.
> Are we ready for our ATO review?

### Fixing Issues

Point Claude at a problem and it fixes it:

> Fix the CAT1 STIG finding in the auth module.
> The dependency audit found a critical vuln in requests 2.28. Upgrade it.
> Our CUI markings are missing from the new files. Add them.

### Requirements Intake

Start a structured conversation with stakeholders:

> We need a mission planning tool for special operations. Start the intake.

Claude guides a multi-turn conversation extracting requirements, detecting gaps, and scoring readiness. When ready, it decomposes into SAFe hierarchy (Epic > Feature > Story) with BDD acceptance criteria.

### Deployment

> Deploy the staging build to GovCloud.
> Generate Terraform for our database infrastructure.
> Create the K8s manifests for production.

### Security

> Run a full security scan.
> Check for hardcoded secrets in the codebase.
> Generate our SBOM.

---

## Slash Commands Reference

Slash commands are shortcuts for common workflows. They're optional — you can always use natural language instead.

### Build & Test
| Command | Purpose |
|---------|---------|
| `/icdev-build` | Build using TDD (RED-GREEN-REFACTOR) |
| `/icdev-test` | Run full test suite (unit + BDD + coverage) |

### Compliance & Security
| Command | Purpose |
|---------|---------|
| `/icdev-comply` | Generate ATO artifacts (SSP, POAM, STIG, SBOM) |
| `/icdev-secure` | Full security scan (SAST, deps, secrets, containers) |
| `/icdev-devsecops` | DevSecOps maturity assessment and pipeline security |
| `/icdev-zta` | Zero Trust Architecture scoring and configuration |
| `/icdev-mosa` | DoD MOSA modularity analysis and ICD generation |

### Project Management
| Command | Purpose |
|---------|---------|
| `/icdev-init` | Initialize new project with compliance scaffolding |
| `/icdev-status` | Project status dashboard |
| `/icdev-intake` | AI-driven requirements intake session |
| `/icdev-simulate` | Digital Program Twin simulation and COA generation |

### Infrastructure
| Command | Purpose |
|---------|---------|
| `/icdev-deploy` | Generate IaC and deployment pipeline |
| `/icdev-review` | Enforce code review gates |
| `/icdev-maintain` | Dependency audit, CVE check, remediation |

### Knowledge & Integration
| Command | Purpose |
|---------|---------|
| `/icdev-knowledge` | Query/update learning knowledge base |
| `/icdev-integrate` | Sync with Jira, ServiceNow, GitLab, DOORS NG |
| `/icdev-mbse` | MBSE integration (SysML, digital thread) |
| `/icdev-monitor` | Production monitoring and self-healing |
| `/icdev-market` | GOTCHA asset marketplace |
| `/icdev-agentic` | Generate agentic child application |
| `/icdev-boundary` | ATO boundary impact and supply chain risk |

### Language-Specific Build Plans
| Command | Language |
|---------|----------|
| `/plan_python` | Python (Flask/FastAPI, pytest, bandit) |
| `/plan_java` | Java (Spring Boot, Cucumber, SpotBugs) |
| `/plan_go` | Go (Gin, godog, gosec) |
| `/plan_rust` | Rust (Actix-web, clippy, cargo-audit) |
| `/plan_csharp` | C# (ASP.NET, SpecFlow, SecurityCodeScan) |
| `/plan_typescript` | TypeScript (Express, cucumber-js, eslint) |

---

## How Dev Profiles Affect Claude's Output

When your project has a dev profile (either from a template or custom), Claude automatically applies those standards to all generated code. You don't need to tell Claude about your coding standards — it already knows.

**Example: Without dev profile**
Claude generates Python with its own defaults (4 spaces, 88-char lines, black formatting).

**Example: With DoD baseline profile**
Claude generates Python matching the profile: 4 spaces, 100-char lines, snake_case, type hints required, Google-style docstrings, FIPS 140-2 compliant crypto, CUI markings.

The dev profile is injected into Claude's context per task type:
- **Code generation**: language, style, testing, security dimensions
- **Code review**: testing, security, compliance, documentation dimensions
- **Architecture**: architecture, operations, security dimensions
- **Documentation**: documentation, compliance dimensions

See [Dev Profiles](dev-profiles.md) for full details.

---

## Tips for Effective Conversations

### Be Specific About Scope
> **Good**: "Build a JWT authentication middleware for the Flask API"
> **Vague**: "Add auth"

### Reference Impact Level When Relevant
> "We need to handle PII at IL5 — generate the appropriate data classification markings"

### Let Claude Handle the Workflow
Don't tell Claude which tools to use. Describe the outcome you want:
> **Good**: "Make sure our code is secure and compliant before we merge"
> **Unnecessary**: "Run bandit, then pip-audit, then detect-secrets, then stig-checker"

### Use Follow-up Questions
Claude maintains context across turns:
> "Build the auth module"
> *(Claude builds it)*
> "Now add rate limiting to it"
> *(Claude adds rate limiting to the same module)*
> "What NIST controls does this satisfy?"
> *(Claude shows control mappings)*

### Ask About Trade-offs
> "Should we use JWT or session-based auth for this IL5 system? What are the compliance implications?"

Claude considers your project's impact level, compliance frameworks, and dev profile when advising.

---

## Troubleshooting

### Claude doesn't know about my project's settings
Make sure `icdev.yaml` exists in your project root and the ICDEV database has been initialized:
```bash
python tools/db/init_icdev_db.py
```

### Claude generates code that doesn't match our standards
Check that your dev profile is loaded:
> Show me the resolved dev profile for this project

If no profile exists, create one:
> Create a dev profile for this project using the DoD baseline template

### Claude can't find the right tool
ICDEV tools are registered as MCP servers. Verify they're configured in `.mcp.json`:
```bash
cat .mcp.json
```

### Claude gives generic answers instead of ICDEV-specific ones
Make sure you're in an ICDEV project directory with `CLAUDE.md` present. The `CLAUDE.md` file contains all orchestration instructions.
