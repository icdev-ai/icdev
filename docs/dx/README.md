# ICDEV Developer Experience (DX)

ICDEV is a meta-builder that autonomously builds Gov/DoD applications with full SDLC compliance. Under the hood it has 15 agents, 14 MCP servers, 146 database tables, and dozens of Python tools. **You don't need to know any of that.**

This guide explains how to integrate ICDEV into your workflow so it works invisibly, letting you focus on building software while ICDEV handles compliance, security, testing, and deployment.

---

## How Developers Interact with ICDEV

ICDEV offers three integration tiers. Pick the one that matches your team's workflow — or combine them.

| Tier | Interaction Model | Developer Effort | Best For |
|------|-------------------|------------------|----------|
| **[Tier 1: Invisible](integration-tiers.md#tier-1-invisible)** | Git-native pipeline | Zero (push code, ICDEV runs automatically) | Teams that want compliance-as-infrastructure |
| **[Tier 2: Conversational](integration-tiers.md#tier-2-conversational)** | Claude Code + 9 AI tools | Minimal (talk to your AI tool, it orchestrates) | Day-to-day development, feature building |
| **[Tier 3: Programmatic](integration-tiers.md#tier-3-programmatic)** | REST API / MCP / SDK | Explicit (call APIs directly) | Custom tooling, CI scripts, integrations |

Most teams use **Tier 1 + Tier 2**: the pipeline runs automatically, and developers talk to their AI coding tool when they need to build something new. ICDEV supports 10 AI coding tools out of the box — see the [AI Companion Guide](companion-guide.md).

---

## Documentation

| Document | Description |
|----------|-------------|
| [Quickstart](quickstart.md) | Get running in 5 minutes |
| [Integration Tiers](integration-tiers.md) | Detailed guide to all three abstraction layers |
| [icdev.yaml Specification](icdev-yaml-spec.md) | Project manifest reference — one file configures everything |
| [Claude Code Guide](claude-code-guide.md) | Using natural language to drive ICDEV workflows |
| [CI/CD Integration](ci-cd-integration.md) | Pipeline auto-attach for GitHub Actions and GitLab CI |
| [Dev Profiles](dev-profiles.md) | Tenant coding standards, style enforcement, and personalization |
| [AI Companion Guide](companion-guide.md) | Multi-tool setup — Claude Code, Codex, Gemini, Copilot, Cursor, and 5 more |
| [SDK Reference](sdk-reference.md) | Programmatic API for custom tooling |

---

## The Core Principle

> **Developers write code. ICDEV handles everything else.**

Compliance artifacts (SSP, POAM, STIG, SBOM), security scanning (SAST, secrets, dependencies, containers), testing (unit, BDD, E2E), CUI markings, ATO boundary management, deployment pipelines — all of it is automated. The developer's job is to describe what they want to build and write the application logic. ICDEV does the rest.

---

## Quick Comparison: Before and After ICDEV

### Before (Manual Compliance)

```
1. Write code
2. Manually run bandit, pip-audit, detect-secrets
3. Write STIG checklist by hand (200+ controls)
4. Generate SSP document (50+ pages)
5. Create POAM for findings
6. Generate SBOM manually
7. Apply CUI markings to every file
8. Submit for ATO review
9. Fix findings, repeat steps 2-8
10. Wait 6-18 months for ATO
```

### After (ICDEV)

```
1. Drop icdev.yaml in your repo
2. Write code and push
3. ICDEV auto-generates everything on every push
4. ATO artifacts stay current continuously (cATO)
```

---

## Architecture at a Glance

```
Developer
    |
    |-- pushes code
    |-- talks to Claude Code
    |-- (optional) calls REST API
    |
    v
+---------------------------+
|       icdev.yaml          |  <-- One config file
+---------------------------+
    |
    v
+---------------------------+
|    ICDEV Orchestration     |  <-- You never see this
|  (GOTCHA Framework)       |
|                           |
|  Goals -> Tools -> Args   |
|  Context -> Hard Prompts  |
+---------------------------+
    |
    +-- Compliance artifacts (SSP, POAM, STIG, SBOM)
    +-- Security scan results (SAST, deps, secrets)
    +-- Test suites (pytest, behave, Playwright)
    +-- CUI/classification markings
    +-- Deployment manifests (Terraform, K8s, Helm)
    +-- Dev profile enforcement (coding standards)
    +-- ATO boundary tracking
```
