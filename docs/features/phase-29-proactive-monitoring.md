# Phase 29 — Proactive Monitoring

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 29 |
| Title | Proactive Monitoring |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 8 (Self-Healing System), Phase 9 (Monitoring & Observability), Phase 28 (Remote Command Gateway) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV's existing monitoring is reactive -- the system waits for failures to occur, then matches them against known patterns for remediation. In Gov/DoD environments operating at IL4/IL5/IL6 impact levels, reactive monitoring is insufficient. Compliance evidence expires silently, certificate renewals are missed, dependency vulnerabilities accumulate unnoticed, and operator workload spikes without warning. By the time a human notices, the damage is done: an ATO lapses, a critical CVE goes unpatched past SLA, or a pipeline breaks during a mission-critical deployment window.

Proactive monitoring addresses this gap through four complementary capabilities. First, a heartbeat daemon continuously checks system health across 7 configurable dimensions with per-check intervals, detecting drift before it becomes failure. Second, webhook-triggered auto-resolution receives external alerts (from Prometheus, CloudWatch, or other monitoring systems) and automatically analyzes, diagnoses, and fixes known issues -- creating branches and pull requests for the fixes. Third, selective skill injection dynamically loads only the Claude Code skills relevant to the current task context, reducing token overhead and improving response quality. Fourth, time-decay memory ranking ensures that recent, relevant memories surface first while stale information naturally fades, improving the quality of AI-assisted decision-making.

Together, these four capabilities transform ICDEV from a system that reacts to failure into one that anticipates and prevents it, while simultaneously improving the quality of AI interactions through smarter context management.

---

## 2. Goals

1. Implement a heartbeat daemon with 7 configurable health checks (cATO evidence freshness, certificate expiry, dependency currency, pipeline health, agent responsiveness, DB integrity, disk usage) running at independently configurable intervals
2. Enable webhook-triggered auto-resolution that receives external alerts, applies the 3-tier confidence model (auto-fix >= 0.7, suggest 0.3-0.7, escalate < 0.3), and creates fix branches/PRs via the existing VCS abstraction
3. Provide selective skill injection that matches task context to relevant Claude Code skills using deterministic keyword-based category matching across 9 categories, reducing unnecessary skill loading
4. Implement time-decay memory ranking using an exponential decay formula with per-memory-type half-lives (fact=90d, event=7d, insight=30d, task=14d, preference=180d, relationship=120d) to surface the most relevant memories
5. Fan out heartbeat notifications to 3 sinks: append-only audit trail (always), SSE dashboard events (if dashboard running), and gateway channels (if configured)
6. Enforce rate limiting on auto-resolution (max 5/hour) and require human approval for infrastructure-level fixes regardless of confidence
7. Maintain backward compatibility -- all new features are opt-in via CLI flags and configuration, with no changes to existing behavior when flags are omitted

---

## 3. Architecture

```
+---------------------------------------------------------------+
|                    Proactive Monitoring Layer                   |
|                                                                |
|  +--------------------+    +-----------------------------+     |
|  | Heartbeat Daemon   |    | Webhook Auto-Resolver       |     |
|  | (7 checks, YAML    |    | /alert-webhook endpoint     |     |
|  |  configurable       |    | 3-tier confidence model     |     |
|  |  intervals)        |    | VCS branch/PR creation      |     |
|  +--------+-----------+    +-------------+---------------+     |
|           |                              |                     |
|           v                              v                     |
|  +---------------------------------------------------+        |
|  |              Notification Fan-Out                  |        |
|  |  [Audit Trail] + [SSE Dashboard] + [Gateway]      |        |
|  +---------------------------------------------------+        |
|                                                                |
|  +--------------------+    +-----------------------------+     |
|  | Skill Selector     |    | Time-Decay Memory Ranker    |     |
|  | 9 categories,      |    | Exponential decay formula   |     |
|  | keyword matching,   |    | Per-type half-lives         |     |
|  | file-based detect   |    | Integrated with hybrid      |     |
|  +--------------------+    | search via --time-decay      |     |
|                            +-----------------------------+     |
+---------------------------------------------------------------+
```

### Heartbeat Check Registry

| Check | Default Interval | Description |
|-------|-----------------|-------------|
| cATO Evidence Freshness | 6 hours | Verify no expired critical evidence |
| Certificate Expiry | 12 hours | Check TLS/mTLS certificates |
| Dependency Currency | 24 hours | Scan for known CVEs |
| Pipeline Health | 1 hour | Verify CI/CD pipeline status |
| Agent Responsiveness | 5 minutes | Ping all registered agents |
| DB Integrity | 12 hours | SQLite integrity checks |
| Disk Usage | 1 hour | Monitor storage thresholds |

---

## 4. Requirements

### 4.1 Heartbeat Daemon

#### REQ-29-001: Configurable Health Checks
The system SHALL maintain a heartbeat daemon with 7 configurable health checks, each with an independently settable interval defined in YAML configuration (D26 pattern).

#### REQ-29-002: Per-Check Cadence
Each health check type SHALL run at its own cadence (e.g., agent responsiveness every 5 minutes, dependency currency every 24 hours) as configured in `args/monitoring_config.yaml`.

#### REQ-29-003: Single-Pass Mode
The heartbeat daemon SHALL support a `--once` flag for single-pass execution and a `--check <name>` flag for running a specific check, in addition to continuous daemon mode.

#### REQ-29-004: Notification Fan-Out
Heartbeat results SHALL fan out to 3 sinks: the append-only audit trail (always), SSE dashboard events (if the dashboard is running), and gateway channels (if configured per Phase 28).

### 4.2 Webhook Auto-Resolution

#### REQ-29-005: Alert Webhook Endpoint
The system SHALL extend the existing webhook server with an `/alert-webhook` endpoint that receives external alerts from Prometheus, CloudWatch, or other monitoring systems.

#### REQ-29-006: Three-Tier Auto-Resolution
The auto-resolver SHALL apply the existing 3-tier self-healing decision engine: auto-fix at confidence >= 0.7, suggest fix at 0.3-0.7, and escalate with full context at < 0.3.

#### REQ-29-007: VCS Branch and PR Creation
When auto-resolution produces a fix, the system SHALL create a fix branch and pull request via the existing VCS abstraction (`tools/ci/modules/vcs.py`), with the fix code and explanation.

#### REQ-29-008: Rate Limiting
Auto-resolution SHALL enforce a maximum of 5 auto-fix actions per hour and a 10-minute cooldown between fixes targeting the same component.

### 4.3 Selective Skill Injection

#### REQ-29-009: Keyword-Based Category Matching
The skill selector SHALL match task context (user query text) against 9 skill categories using deterministic keyword matching, without requiring an LLM call.

#### REQ-29-010: File-Based Detection
The skill selector SHALL support file-based detection, inferring relevant categories from file extensions and path patterns present in the working directory.

#### REQ-29-011: Injection-Ready Output
The skill selector SHALL produce markdown-formatted context blocks suitable for direct injection into Claude Code sessions, including relevant commands, goals, and context directories.

#### REQ-29-012: Confidence Threshold
Skill matches below the configured confidence threshold (default 0.5) SHALL be excluded from injection to prevent irrelevant context loading.

### 4.4 Time-Decay Memory Ranking

#### REQ-29-013: Exponential Decay Formula
The system SHALL rank memory entries using the formula `2^(-(age / half_life))` where age is the time since last access and half_life is configured per memory type.

#### REQ-29-014: Per-Type Half-Lives
The system SHALL support configurable half-lives per memory type: fact (90 days), preference (180 days), event (7 days), insight (30 days), task (14 days), relationship (120 days).

#### REQ-29-015: Hybrid Search Integration
Time-decay ranking SHALL integrate with the existing hybrid search system via an opt-in `--time-decay` flag (D44 backward compatible pattern), combining relevance (0.60), recency (0.25), and importance (0.15) weights.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `heartbeat_checks` | Per-check status records: check_name, last_run, result, next_scheduled, alert_level |
| `auto_resolution_log` | Append-only record of auto-resolution actions: alert_source, confidence, action_taken, branch_name, pr_url, result |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/monitor/heartbeat_daemon.py` | Continuous or single-pass health checking with 7 configurable checks |
| `tools/monitor/auto_resolver.py` | Webhook-triggered alert analysis, fix generation, branch/PR creation |
| `tools/agent/skill_selector.py` | Keyword-based skill category matching with file-based detection fallback |
| `tools/memory/time_decay.py` | Exponential time-decay scoring and ranking for memory entries |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D162 | Heartbeat daemon uses configurable check registry with per-check intervals in YAML | Each check type has its own cadence; D26 declarative pattern enables adding checks without code changes |
| D163 | Heartbeat notifications fan out to 3 sinks: audit trail (always), SSE (if dashboard), gateway channels (if configured) | Ensures visibility across all operator interfaces without coupling to any single one |
| D164 | Auto-resolver extends existing webhook_server.py with `/alert-webhook` endpoint | Avoids second Flask app, reuses HMAC verification and existing infrastructure |
| D165 | Auto-resolver reuses existing 3-tier self-healing decision engine (>= 0.7 auto, 0.3-0.7 suggest, < 0.3 escalate) and rate limits (5/hour) | Consistent behavior with Phase 8 self-healing; operators learn one decision model |
| D166 | Auto-resolver creates fix branches/PRs via existing VCS abstraction (`tools/ci/modules/vcs.py`) | Reuses Phase 13 CI/CD infrastructure; fixes are reviewable before merge |
| D167 | Selective skill injection via deterministic keyword-based category matching | No LLM required, declarative YAML config (D26 pattern), air-gap safe, reproducible |
| D168 | Time-decay uses exponential formula `2^(-(age/half_life))` with per-memory-type half-lives, opt-in via `--time-decay` flag | Natural decay model; events fade fast while facts persist; backward compatible (D44 pattern) |

---

## 8. Security Gate

**Proactive Monitoring Gate:**
- Auto-resolution rate limited to 5 actions per hour with 10-minute cooldown per target
- Infrastructure-level fixes (rollback, scale, failover) require human approval regardless of confidence score
- All heartbeat results and auto-resolution actions recorded in append-only audit trail (NIST AU-2, IR-4, SI-5)
- HMAC-SHA256 signature verification required on all alert webhook payloads
- Alert webhook endpoint validates source IP against configured allowlist
- Auto-generated PRs require code review approval before merge

---

## 9. Commands

```bash
# Heartbeat daemon
python tools/monitor/heartbeat_daemon.py                # Foreground daemon (7 configurable checks)
python tools/monitor/heartbeat_daemon.py --once          # Single pass of all checks
python tools/monitor/heartbeat_daemon.py --check cato_evidence  # Specific check
python tools/monitor/heartbeat_daemon.py --status --json # Show all check statuses

# Webhook-triggered auto-resolution
python tools/monitor/auto_resolver.py --analyze --alert-file alert.json --json   # Analyze without acting
python tools/monitor/auto_resolver.py --resolve --alert-file alert.json --json   # Full pipeline: analyze + fix + PR
python tools/monitor/auto_resolver.py --history --json                            # Resolution history

# Selective skill injection
python tools/agent/skill_selector.py --query "fix the login tests" --json         # Keyword-based category matching
python tools/agent/skill_selector.py --detect --project-dir /path --json          # File-based detection
python tools/agent/skill_selector.py --query "deploy to staging" --format-context # Injection-ready markdown

# Time-decay memory ranking
python tools/memory/time_decay.py --score --entry-id 42 --json                    # Score single entry
python tools/memory/time_decay.py --rank --query "keyword" --top-k 10 --json      # Time-decay ranked search
python tools/memory/hybrid_search.py --query "test" --time-decay                   # Integrated time-decay search
```

**CUI // SP-CTI**
