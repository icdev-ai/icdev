# Phase 27 — CLI Capabilities

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 27 |
| Title | Optional Claude Code CLI Capabilities |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 21 (SaaS Multi-Tenancy), Phase 15 (CI/CD Integration) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

The VSCode extension and the Claude Code CLI use the same engine (same model, same tools, same capabilities), but the CLI unlocks headless, scripted, parallel, and containerized execution modes that certain environments require. Forcing CLI capabilities on all customers creates friction for teams that prefer GUI-based workflows, while disabling CLI everywhere limits power users who need automated pipeline integration, batch document processing, or concurrent agent execution. Without independent toggles, every project must either accept all CLI capabilities or none, and there is no mechanism to enforce organizational limits on token consumption or concurrent invocations.

In SaaS multi-tenant deployments, different subscription tiers should grant different CLI capability ceilings: a Starter tier customer should not be able to enable container-based agent execution (which requires dedicated K8s resources), while an Enterprise tier customer should have access to all four capabilities. Without tenant-level ceilings, subscription tiers lose meaningful differentiation for power users, and cost controls become unenforceable across the organization. Projects within a tenant also need different configurations: a CI/CD pipeline project needs automation enabled, while a requirements gathering project only needs scripted intake.

Phase 27 introduces four independently toggleable CLI capabilities (CI/CD pipeline automation, parallel agent execution, container-based execution, scripted batch intake) with per-project configuration and tenant-level ceilings in SaaS deployments. Each capability has clear prerequisites, environment requirements, and cost controls. Auto-detection checks CLI availability on first use and falls back gracefully when the CLI is not installed or API credentials are unavailable.

---

## 2. Goals

1. Define **4 independently toggleable CLI capabilities** (CI/CD automation, parallel agents, container execution, scripted intake) that extend the standard VSCode extension experience
2. Enforce **tenant-level ceilings** in SaaS deployments so subscription tiers control the maximum CLI capabilities available to any project within the organization
3. Implement **per-project configuration** via `args/cli_config.yaml` where each capability is enabled/disabled with capability-specific settings
4. Provide **cost controls** (daily token budgets, hourly invocation limits, alert thresholds) to prevent runaway API consumption from automated CLI usage
5. **Auto-detect CLI availability** on first use, checking for CLI installation, API credentials, and network connectivity to LLM endpoints
6. Provide **clear decision guidance** per persona (developer, PM, ISSO, DevOps engineer, system integrator) on which capabilities to enable
7. Support **air-gapped environments** where CLI routes to local Ollama via `prefer_local: true` in `llm_config.yaml`

---

## 3. Architecture

### 3.1 Capability Overview

```
+-----------------------------------------------------------------------+
|                     Claude Code CLI Capabilities                       |
|                                                                       |
|  +-------------------+  +-------------------+                         |
|  | CI/CD Automation  |  | Parallel Agents   |                         |
|  | (pipeline stages) |  | (concurrent SDLC) |                         |
|  +-------------------+  +-------------------+                         |
|                                                                       |
|  +-------------------+  +-------------------+                         |
|  | Container Exec    |  | Scripted Intake   |                         |
|  | (K8s agent pods)  |  | (batch documents) |                         |
|  +-------------------+  +-------------------+                         |
|                                                                       |
|  Controls: Tenant Ceiling -> Project Toggle -> Cost Budget -> Detect  |
+-----------------------------------------------------------------------+
```

### 3.2 Enforcement Hierarchy

```
Tenant Ceiling (SaaS tier maximum)
  |
  v
Project Toggle (args/cli_config.yaml per capability)
  |
  v
Cost Controls (daily token budget, hourly invocation limit)
  |
  v
Environment Detection (CLI installed? API key? Network?)
  |
  v
Capability Active or Graceful Fallback
```

### 3.3 Subscription Tier Ceilings

| Feature | Starter | Professional | Enterprise |
|---------|---------|-------------|------------|
| cicd_automation | No | Yes | Yes |
| parallel_agents | No | Yes | Yes |
| container_execution | No | No | Yes |
| scripted_intake | Yes | Yes | Yes |

---

## 4. Requirements

### 4.1 Capability Toggles

#### REQ-27-001: Independent Toggles
The system SHALL provide 4 independently toggleable CLI capabilities: CI/CD pipeline automation, parallel agent execution, container-based execution, and scripted batch intake.

#### REQ-27-002: Default Disabled
All CLI capabilities SHALL default to disabled. The VSCode extension provides full functionality without any CLI capability enabled.

#### REQ-27-003: Per-Project Configuration
Each capability SHALL be configurable per project in `args/cli_config.yaml` with capability-specific settings (allowed commands, runner type, max concurrent, etc.).

### 4.2 Tenant Governance

#### REQ-27-004: Tenant Ceiling Enforcement
In SaaS deployments, the system SHALL enforce tenant-level ceilings so no project can enable a CLI capability that exceeds the tenant's subscription tier allowance.

#### REQ-27-005: Silent Blocking
When a project toggle is enabled but the tenant ceiling blocks it, the system SHALL silently ignore the project toggle and log the event as "blocked by tenant ceiling."

### 4.3 Cost Controls

#### REQ-27-006: Token Budget
The system SHALL enforce daily token budgets per project with configurable thresholds by subscription tier (Starter: 100K, Professional: 500K, Enterprise: 2M).

#### REQ-27-007: Invocation Limits
The system SHALL enforce hourly CLI invocation limits per project with configurable thresholds by subscription tier.

#### REQ-27-008: Budget Exhaustion
When a cost budget is exhausted mid-pipeline, the system SHALL stop new CLI invocations, complete in-flight work, alert the admin, and log to the audit trail.

### 4.4 Environment Detection

#### REQ-27-009: Auto-Detection
The system SHALL auto-detect CLI availability on first use by checking for CLI installation, API credentials, and network connectivity to LLM endpoints.

#### REQ-27-010: Graceful Fallback
When the CLI is not available, the system SHALL fall back to extension mode with a warning logged to the audit trail.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `agent_token_usage` | Token consumption tracking per project, per user (extended with user_id) |

Configuration is primarily file-based (`args/cli_config.yaml`) with tenant ceilings stored in `platform.db` -> `tenants` table `settings_json` column for SaaS deployments.

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `args/cli_config.yaml` | Per-project CLI capability configuration (4 toggles, cost controls, detection) |
| `tools/testing/health_check.py` | Includes CLI capability status in health check output |
| `tools/agent/token_tracker.py` | Token usage tracking and cost breakdown |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D132 | CLI capabilities are optional per-project toggles with tenant-level ceiling | Default all-disabled; VSCode extension provides full functionality; CLI adds headless/scripted/parallel/containerized execution |
| D132 | Tenant sets maximum allowed capabilities; project enables within ceiling | Prevents Starter-tier customers from using Enterprise-tier features |
| D132 | Cost controls enforce token budgets with auto-detection fallback | Prevents runaway API costs from automated CLI invocations |
| D132 | Detection auto-checks CLI availability and falls back gracefully | No hard failures when CLI is unavailable; extension always works |

---

## 8. Security Gate

**CLI Cost Control Gate:**
- Daily token budget not exceeded (blocking: CLI invocations stop)
- Hourly invocation limit not exceeded (blocking: new invocations queued)
- API credentials valid and accessible (blocking: CLI disabled)

**CLI Tenant Ceiling Gate:**
- Project capabilities within tenant subscription tier ceiling
- Container execution requires Enterprise tier (blocking)

---

## 9. Commands

```bash
# Environment detection
claude --version
claude --help
python --version

# Check CLI configuration
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

# Token usage tracking
python tools/agent/token_tracker.py --action summary --project-id "proj-123"
python tools/agent/token_tracker.py --action cost --project-id "proj-123"

# Example CI/CD pipeline stage (GitLab CI)
# icdev-review:
#   stage: review
#   image: icdev/agent-base:latest
#   script:
#     - claude -p "/icdev-review" --no-interactive --output-format json
#   variables:
#     ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY

# Batch intake
python tools/requirements/intake_engine.py \
  --project-id "proj-123" --customer-name "Jane Smith" \
  --customer-org "DoD PEO" --impact-level IL5 --json > session.json
SESSION_ID=$(jq -r '.session_id' session.json)
claude -p "/icdev-intake --session-id $SESSION_ID --batch" < sow.txt

# Health check (includes CLI status)
python tools/testing/health_check.py --json
```
