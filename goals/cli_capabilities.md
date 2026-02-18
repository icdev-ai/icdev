// CUI // SP-CTI
// Distribution: Authorized personnel only
// Handling: In accordance with DoDI 5200.48

# Goal: CLI Capabilities — Optional Claude Code CLI Features

## Purpose

Guide customers in deciding whether to enable Claude Code CLI capabilities beyond the standard VSCode extension. Four optional capabilities — CI/CD automation, parallel agent execution, container-based execution, and scripted intake — are independently toggleable per project, with tenant-level ceilings in SaaS deployments.

**Why this matters:** The VSCode extension and CLI use the same engine (same model, same tools, same capabilities). The CLI unlocks headless, scripted, parallel, and containerized execution modes that some environments need and others cannot support. Forcing CLI on all customers creates friction; disabling it everywhere limits power users. Independent toggles let each project use what fits.

---

## Prerequisites

- [ ] Project initialized (`goals/init_project.md` completed)
- [ ] `args/cli_config.yaml` present (capability toggles, cost controls, detection settings)
- [ ] For SaaS deployments: tenant ceiling set by tenant admin
- [ ] `memory/MEMORY.md` loaded (session context)

---

## Decision Guide: CLI vs VSCode Extension

### When the VSCode Extension Is Sufficient (Default)

| Scenario | Why Extension Works |
|----------|-------------------|
| Interactive development | File context, inline diffs, visual feedback built in |
| Single developer workflow | No need for parallel execution |
| Manual requirements intake | Conversational Q&A works well interactively |
| GUI-preferred users | PMs, ISSOs, compliance officers prefer visual tools |
| No CI/CD pipeline yet | No runners to install CLI on |
| Restricted desktop environments | Some environments lock down terminal access |

**Recommendation:** Start with the extension. Enable CLI capabilities only when a specific need arises.

### When to Enable CLI Capabilities

#### Capability 1: CI/CD Pipeline Automation (`cicd_automation`)

**Enable when:**
- You have GitLab CI/CD or GitHub Actions runners
- You want AI-assisted build/test/review as automated pipeline stages
- Your runners have network access to the LLM endpoint (Bedrock or Anthropic API)
- You want pipeline stages that can reason about failures and self-correct

**Do NOT enable when:**
- No CI/CD infrastructure exists
- Runners are air-gapped with no LLM access
- Cost controls are strict (each pipeline run consumes tokens)
- Deterministic Python tools (`tools/`) already handle your pipeline needs

**Environment requirements:**
- Claude CLI installed on runner: `npm install -g @anthropic-ai/claude-code`
- API credentials available to runner (env var or secrets manager)
- Network egress to LLM endpoint
- Sufficient runner compute (CLI needs ~200MB RAM)

**Example GitLab CI stage:**
```yaml
icdev-review:
  stage: review
  image: icdev/agent-base:latest
  script:
    - claude -p "/icdev-review" --no-interactive --output-format json
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
```

---

#### Capability 2: Parallel Agent Execution (`parallel_agents`)

**Enable when:**
- You need to run independent SDLC phases concurrently (e.g., security scan + compliance check)
- Your API rate limits support multiple concurrent requests
- You use git worktrees for file isolation (`args/worktree_config.yaml`)
- Time-to-delivery matters more than token cost

**Do NOT enable when:**
- Phases are interdependent (plan must finish before build)
- API rate limits are restrictive
- Single-threaded execution meets your timeline
- Coordination overhead outweighs time savings

**Safe parallel combinations:**
| Parallel Group | Why Safe |
|----------------|----------|
| Security scan + Compliance check | Read-only analysis, no file changes |
| Unit tests + BDD tests | Independent test suites |
| Terraform plan + Ansible lint | Independent IaC validation |

**Always sequential:**
| Phase | Must Wait For |
|-------|--------------|
| Build | Plan completion |
| Test | Build completion |
| Deploy | All gates passed |

---

#### Capability 3: Container-Based Execution (`container_execution`)

**Enable when:**
- You run agents as K8s pods (not just local Python processes)
- You need STIG-hardened agent runtime environments
- You want resource limits enforced per agent (CPU, memory)
- You need container-level network isolation between agents

**Do NOT enable when:**
- Agents run locally on developer machines
- No container runtime available
- The existing Python A2A agent pattern meets your needs
- Container registry not available for custom images

**Security requirements (all enforced):**
- Non-root execution (UID 1000)
- Read-only root filesystem
- All capabilities dropped
- Secrets via K8s secrets or AWS Secrets Manager (never in image)
- Network policy: default-deny egress, whitelist LLM endpoint + internal A2A

---

#### Capability 4: Scripted / Batch Intake (`scripted_intake`)

**Enable when:**
- You have large volumes of existing requirements documents (SOWs, CDDs, CONOPS)
- You want to batch-process documents without interactive Q&A
- Your intake process is repeatable across similar projects
- You need programmatic intake (API or script-driven)

**Do NOT enable when:**
- Requirements are vague and need conversational clarification
- Customer prefers interactive guided intake
- Document quality is low (batch mode may miss ambiguities that conversation catches)
- You want gap detection to prompt follow-up questions in real-time

**Example batch command:**
```bash
# Pre-create session
python tools/requirements/intake_engine.py \
  --project-id "proj-123" \
  --customer-name "Jane Smith" \
  --customer-org "DoD PEO" \
  --impact-level IL5 --json > session.json

# Batch intake from document
SESSION_ID=$(jq -r '.session_id' session.json)
claude -p "/icdev-intake --session-id $SESSION_ID --batch" < sow.txt
```

---

## Process: Enabling CLI Capabilities

### Step 1: Check Environment Compatibility

**Tool:** Auto-detection via `args/cli_config.yaml` → `detection.auto_detect: true`

The system auto-checks CLI availability on first use. Result is logged to audit trail.

**Manual check:**
```bash
claude --version        # CLI installed?
claude --help           # Accessible?
python --version        # Python available for tools?
```

**Decision matrix:**

| Environment | CLI Available | Recommended Capabilities |
|-------------|--------------|-------------------------|
| Developer laptop (Windows/Mac) | Usually yes | None — use VSCode extension |
| GitLab runner (Linux) | Install required | cicd_automation |
| K8s pod (container) | Install in image | container_execution, cicd_automation |
| Air-gapped workstation | Maybe (offline install) | scripted_intake (if LLM accessible) |
| Cloud IDE (Gitpod, Codespaces) | Usually yes | parallel_agents, cicd_automation |
| Restricted government desktop | Unlikely | None — use VSCode extension |

**Error handling:**
- CLI not found → fallback to extension mode, log warning
- CLI found but no API key → log error, disable all CLI capabilities
- CLI found but rate-limited → reduce `max_concurrent` and `max_invocations_per_hour`

**Verify:** Detection result stored in audit trail. `args/cli_config.yaml` updated if auto-detect changes defaults.

---

### Step 2: Configure Tenant Ceiling (SaaS Only)

**Applies to:** SaaS multi-tenant deployments only. Skip for standalone installations.

Tenant admin sets the maximum CLI capabilities allowed for all projects in their organization.

**Tool:** Tenant portal (`tools/saas/portal/`) → Settings → CLI Capabilities

Or via API:
```bash
curl -X PUT https://platform/api/v1/tenant/settings \
  -H "Authorization: Bearer icdev_..." \
  -d '{"cli_ceiling": {"cicd_automation": true, "parallel_agents": false, "container_execution": true, "scripted_intake": true}}'
```

**Rules:**
- Tenant ceiling defaults to all-enabled for Enterprise tier
- Professional tier: all except `container_execution`
- Starter tier: `scripted_intake` only
- Project cannot exceed tenant ceiling (enforced at runtime)

**Verify:** Tenant settings stored in `platform.db` → `tenants` table `settings_json` column.

---

### Step 3: Enable Project-Level Capabilities

**Tool:** Edit `args/cli_config.yaml` → `project` section

For each capability:
1. Check tenant ceiling allows it (SaaS) or skip (standalone)
2. Verify prerequisites are met (see capability-specific sections above)
3. Set `enabled: true`
4. Configure capability-specific settings

**Example — enable CI/CD automation only:**
```yaml
project:
  cicd_automation:
    enabled: true
    runner_type: gitlab
    allowed_commands:
      - "/icdev-build"
      - "/icdev-test"
      - "/icdev-review"
  parallel_agents:
    enabled: false
  container_execution:
    enabled: false
  scripted_intake:
    enabled: false
```

**Verify:** Run `python tools/testing/health_check.py` — CLI capabilities section should show enabled/disabled status.

---

### Step 4: Set Cost Controls

**Tool:** Edit `args/cli_config.yaml` → `cost_controls` section

CLI invocations consume API tokens. Set budgets to prevent runaway costs.

**Recommended defaults by tier:**

| Tier | Daily Token Budget | Hourly Invocations | Alert Threshold |
|------|-------------------|-------------------|-----------------|
| Starter | 100,000 | 10 | 80% |
| Professional | 500,000 | 30 | 80% |
| Enterprise | 2,000,000 | 100 | 90% |

**Verify:** Token usage tracked in `agent_token_usage` table. Alerts fire when `alert_at_percent` reached.

---

### Step 5: Validate Configuration

Run the capability validation check:

```bash
# Check which capabilities are enabled and functional
python -c "
import yaml
with open('args/cli_config.yaml') as f:
    cfg = yaml.safe_load(f)
for cap in ['cicd_automation', 'parallel_agents', 'container_execution', 'scripted_intake']:
    proj = cfg['project'][cap]
    ceiling = cfg['tenant_ceiling'][cap]
    status = 'ENABLED' if proj['enabled'] and ceiling else 'DISABLED'
    if proj['enabled'] and not ceiling:
        status = 'BLOCKED (tenant ceiling)'
    print(f'  {cap}: {status}')
"
```

**Expected output:**
```
  cicd_automation: ENABLED
  parallel_agents: DISABLED
  container_execution: DISABLED
  scripted_intake: DISABLED
```

---

## Edge Cases

| Situation | Handling |
|-----------|---------|
| CLI installed but API key missing | Disable all CLI capabilities, log error, fall back to extension |
| Tenant ceiling blocks project toggle | Project toggle silently ignored, logged as "blocked by tenant ceiling" |
| CI/CD runner has CLI but times out | Respect `timeout_seconds`, fail the pipeline stage, log to audit |
| Parallel agents hit rate limit | Queue excess requests, log warning, reduce concurrency dynamically |
| Batch intake with malformed document | Return parse error, do NOT partially import, preserve original session state |
| Container image missing CLI | Build fails on startup, log error, pod restart with backoff |
| Cost budget exhausted mid-pipeline | Stop CLI invocations, complete in-flight work, alert admin, log to audit |
| Air-gapped environment with local Ollama | CLI works if `prefer_local: true` in `llm_config.yaml` — route to Ollama |

---

## Persona Guidance

| Persona | Likely Capabilities | Notes |
|---------|-------------------|-------|
| **Developer** | parallel_agents, cicd_automation | Comfortable with CLI, wants speed |
| **PM / Product Owner** | scripted_intake | Batch-process requirement docs, review output in dashboard |
| **ISSO / Compliance Officer** | None (use dashboard/extension) | Prefers GUI, reviews artifacts not processes |
| **DevOps Engineer** | cicd_automation, container_execution | Manages runners and K8s, CLI natural fit |
| **System Integrator** | All four | Needs full automation for multi-project delivery |

---

## Related Goals

| Goal | Relationship |
|------|-------------|
| CI/CD Integration | `cicd_automation` extends pipeline with Claude CLI stages |
| Parallel CI/CD | `parallel_agents` builds on git worktree isolation |
| Requirements Intake | `scripted_intake` adds batch mode to RICOAS intake |
| SaaS Multi-Tenancy | Tenant ceiling enforced via SaaS platform settings |
| Multi-Agent Orchestration | `container_execution` containerizes agent instances |

---

## Architecture Decision

**D132:** CLI capabilities are optional per-project toggles with tenant-level ceiling. Tenant sets maximum allowed capabilities; project enables within ceiling. Default is all-disabled — VSCode extension provides full functionality. CLI adds headless/scripted/parallel/containerized execution modes for environments that support them. Cost controls enforce token budgets. Detection auto-checks CLI availability and falls back gracefully.
