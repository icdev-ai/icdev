# Genesis v2.0 — Autonomous Research Lab

## Overview
Genesis v2.0 is a continuous self-improvement engine for ICDEV™. It runs 12 autonomous "Reflexes" on schedules — researching threats, auditing code quality, generating tests, publishing content, and evolving the platform. Knowledge flows from the v2.0 research lab to v1.x production via Genesis Knowledge Packets (GKP).

## Architecture

### Dual-Loop Design
- **v2.0 Genesis Lab**: 12 Reflexes run autonomously, producing insights
- **v1.x Production Core**: Receives vetted knowledge via GKP promotion pipeline
- **Trust Kernel**: GREEN/YELLOW/ORANGE risk tiers with circuit breakers

### Components
| Component | Path | Purpose |
|-----------|------|---------|
| Daemon | `tools/genesis/daemon.py` | Scheduler, reflex runner, circuit breakers |
| Promoter | `tools/genesis/promoter.py` | GKP export/promotion pipeline |
| Feedback Collector | `tools/genesis/feedback_collector.py` | v1.x telemetry → reflex priorities |
| Reporter | `tools/genesis/reporter.py` | Weekly status reports |

### 12 Reflexes

| Reflex | Tier | Schedule | Purpose |
|--------|------|----------|---------|
| research | GREEN | every 6h | RSS/Atom feed scanning (CISA, KEV, OWASP) |
| scout | GREEN | daily 07:00 | GitHub competitor monitoring |
| audit | GREEN | daily 06:00 | Code quality + SAST scanning |
| comply | GREEN | daily 09:00 | cATO evidence, crosswalk, SbD checks |
| ingest | GREEN | every 4h | Feed data → knowledge graph |
| market | GREEN | daily 10:00 | Marketplace module stats + improvement suggestions |
| report | GREEN | weekly Sun | Generate weekly Genesis status reports |
| publish | YELLOW | daily 08:00 | Demand-driven blog draft generation |
| test | YELLOW | nightly 03:00 | Auto-generate tests for untested modules |
| learn | YELLOW | nightly 04:00 | Training pair generation for fine-tuning |
| heal | YELLOW | continuous | Pattern-based auto-remediation |
| evolve | ORANGE | nightly 02:00 | Code mutation proposals (human review required) |

### Trust Kernel
- **GREEN**: Non-destructive reads/writes, air-gap safe
- **YELLOW**: Reversible writes with cooldown, scanner-tier LLM only
- **ORANGE**: Code mutation — worktree sandbox + test gate + human review
- Circuit breaker: 3 consecutive failures → trip (auto-reset after cooldown)

## Dashboard
- Route: `/genesis`
- API endpoints: `/api/genesis/status`, `/api/genesis/reflex/<name>` (POST), `/api/genesis/promoter/stats`, `/api/genesis/feedback/priorities`
- Features: daemon status cards, 12-reflex table with run buttons, GKP promoter stats, feedback priorities grid

## E2E Test
- Spec: `.claude/commands/e2e/genesis.md`
- Screenshots: `playwright/screenshots/genesis-{desktop,tablet,mobile}-*.png`

## Key Decisions
- Scanner-tier only (qwen3.5/phi4-reasoning) — zero Claude tokens for autonomous ops
- GKP promotion requires human review for YELLOW/ORANGE artifacts
- Feedback loop adjusts reflex priorities based on v1.x telemetry
- All reflexes return `{success, metric_value, details}` interface
