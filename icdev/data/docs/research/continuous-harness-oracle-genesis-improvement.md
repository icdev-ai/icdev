# Research: Continuous Harness — Improving Oracle and Genesis

**Date:** 2026-05-25  
**Task ID:** task-ac94ba1589  
**Type:** Research

---

## Executive Summary

The "continuous harness" is the closed-loop autonomous feedback system formed by three integrated components: **Genesis Daemon** (scheduler/runtime), **Oracle** (predictive intelligence), and the **Internal Awareness Engine** (observability). This document maps how they currently work together and identifies concrete improvements to tighten the loop.

---

## Component Overview

### Genesis Daemon (`tools/genesis/daemon.py`)

Version 2.0.0-alpha — a single long-running process managing **25 reflexes** as managed threads.

**Trust tiers:**
- `GREEN` — read-only, non-destructive (research, audit, comply, ingest)
- `YELLOW` — reversible writes in worktree sandbox (heal, awareness, oracle_triage)
- `ORANGE` — code mutation, requires human review (evolve)

**Circuit breaker:** 3 consecutive failures → reflex disabled until human reset.

**State:** `genesis_reflex_state` (last run, circuit breaker status) + `genesis_audit` (append-only event log).

**Relevant reflexes in the harness loop:**
| Reflex | Trust | Cadence | Role |
|--------|-------|---------|------|
| `awareness` | YELLOW | 3h | Probes all components, feeds Oracle |
| `oracle_triage` | YELLOW | 3h | Verifies predictions → promotes to backlog |
| `heal` | YELLOW | 5min | Pattern-based auto-remediation |
| `evolve` | ORANGE | nightly 02:00 | Code mutation proposals |
| `freshness_guardian` | YELLOW | 1h | Quality rule evaluation |
| `log_triage` | YELLOW | 2h | Log analysis + failure pattern detection |

---

### Oracle (`tools/oracle/`, `intelligence/oracle/`)

Two modes of operation:

1. **Quality lenses** (`tools/oracle/lenses/`) — predict regressions, code improvement opportunities, gate failure patterns.
2. **Strategic lenses** (`intelligence/oracle/sio_engine.py`) — threat_posture, behavior_pattern, intent_assessment, convergence for military/strategic scenarios.

**Output:** `OraclePrediction` with `confidence` (0.0–1.0), `severity` (critical/high/medium/low/info), `lens_name`, `horizon_days`.

**API routes:** `/api/oracle/predictions`, `/api/oracle/proposals/history`, `/api/oracle/summary`.

**Scoring rule:** confidence ≥ 0.7 → auto-create `status='suggested'` kanban task.

---

### Internal Awareness Engine (`tools/awareness/`)

Phases 1–6 implementation. Current status: Phases 1–2 deployed, Phases 3–6 in progress.

**Capabilities:**
- Discovers 900+ tools, 21 skills, 24 MCP servers, 8 canvases, 60+ goals, 80+ routes, 391 DB tables
- 7 gap detection rules: `route_not_listed`, `tool_not_in_manifest`, `orphan_db_table`, `skill_undocumented`, `canvas_without_iqe`, `reflex_no_test`, `goal_no_acceptance_criteria`
- HTTP health probes, import checks, DB schema queries every 3h
- Feeds findings to Oracle for confidence scoring
- UI at `/components-map` with JointJS force-directed graph

---

## Current Information Flow

```
Awareness (3h probe)
  ↓
  gap_detector.py  →  oracle_predictions (confidence ≥ 0.7 = HIGH)
  ↓
oracle_triage reflex (3h)
  ↓
  verifiers: file_exists, grep_route, migration_check
  ↓ confidence ≥ 0.7 confirmed?
  YES → promote kanban_task (suggested → backlog)
  NO  → dismiss (suggested → done)
  ↓
Kanban listener
  ↓
  creates .tmp/kanban/task-<id>.md prompt file
  dispatches to Claude CLI
  ↓
Claude CLI (or heal reflex)
  ↓
  executes fix, commits, moves task → done
  ↓
Next awareness cycle (3h):
  re-probes, confirms gap resolved → no new prediction
```

---

## Identified Gaps and Improvement Opportunities

### 1. No Sequenced Awareness → Triage Pipeline

**Problem:** Awareness (3h) and oracle_triage (3h) run independently. If awareness completes at 01:00 and triage ran at 01:15, triage may process stale oracle_predictions from the previous cycle while new findings wait an hour.

**Improvement:** Make `oracle_triage` a **downstream callback** of the `awareness` reflex rather than an independent timer. Genesis should chain them: awareness completes → oracle_triage triggers immediately.

**Implementation path:**
- Add `post_reflex_chain` field to `genesis_config.yaml`:
  ```yaml
  reflexes:
    awareness:
      post_chain: [oracle_triage]
  ```
- In `daemon.py`, after a GREEN/YELLOW reflex succeeds, check `post_chain` and enqueue dependent reflexes.
- Avoids race conditions with no added infrastructure.

---

### 2. Oracle Confidence Calibration Has No Feedback Loop

**Problem:** Oracle assigns confidence scores at prediction time, but there is no mechanism to update confidence based on whether predictions were correct. If `oracle_triage` dismisses a prediction (false positive), Oracle doesn't learn from it.

**Improvement:** Add a `prediction_outcome` table and a **confidence recalibrator** that adjusts per-lens priors based on historical false-positive/false-negative rates.

**Implementation path:**
- Table: `oracle_prediction_outcomes(prediction_id, outcome ENUM[confirmed, false_positive, false_negative, resolved], resolved_at, notes)`
- `oracle_triage.py`: write outcome row on every promote/dismiss decision.
- New `goal_learner`-style reflex: `oracle_calibrate` (GREEN, weekly) reads outcomes, recomputes per-lens calibration factor, stores in `oracle_lens_calibration(lens_name, calibration_factor, updated_at)`.
- Oracle scoring multiplies raw confidence × calibration factor before threshold check.

**Expected benefit:** Reduces false-positive rate over time; fewer noise kanban tasks.

---

### 3. Genesis Has No "Harness Health" Visibility

**Problem:** There is no single dashboard view showing the end-to-end harness pipeline health: how many gaps awareness found, how many Oracle scored as high-confidence, how many triage promoted, how many heal resolved.

**Improvement:** Add a **Harness Health** panel to the Genesis dashboard page (`/genesis`) showing the pipeline funnel:

```
Awareness found: 47 gaps
  Oracle scored ≥0.7: 12
    Triage promoted: 8
    Triage dismissed: 4
      Heal executed: 5
      Pending kanban: 3
        Resolved: 5
        Regressed: 0
```

**Implementation path:**
- New API route: `GET /api/genesis/harness-health` — joins `awareness_run_log`, `oracle_predictions`, `kanban_tasks`, `genesis_audit`.
- Add panel to `tools/dashboard/templates/genesis/page.html`.
- No new DB tables needed — all data already exists.

---

### 4. Oracle Quality Lens Doesn't Consume Genesis Audit Data

**Problem:** Genesis `genesis_audit` records every reflex run with success/failure/duration. Oracle quality lenses currently don't read this data, missing a rich signal source for predicting which reflexes are degrading.

**Improvement:** New Oracle lens: `lens_genesis_health.py` — analyzes `genesis_audit` for:
- Reflexes with rising failure rates (circuit breaker approach risk)
- Reflexes with increasing duration (potential performance degradation)
- Reflexes that haven't run in ≥2× their scheduled interval (silent failures)

**Implementation path:**
- `tools/oracle/lenses/lens_genesis_health.py` following pattern of `lens_quality.py`
- Register in `tools/oracle/oracle_runner.py` lens list
- 3 gap rules added to Oracle lens registry

---

### 5. Heal Reflex Patterns Not Shared with Oracle

**Problem:** The `heal` reflex applies pattern-based remediation from a hardcoded pattern library. When heal successfully fixes an issue, Oracle doesn't receive that as a training signal for future predictions.

**Improvement:** After each successful heal action, write a `oracle_predictions` row with `outcome='pre_empted'` to record what heal fixed. This feeds both the calibration loop (#2) and gives Oracle visibility into what heal is catching.

**Implementation path:**
- `tools/genesis/reflexes/heal.py`: after successful remediation, call `oracle_triage.record_heal_outcome(pattern_name, file_path, confidence=0.9)`
- Simple — no new tables needed.

---

### 6. No Master Circuit Breaker for Full Harness

**Problem:** Individual reflex circuit breakers exist, but there is no way to pause the entire autonomous loop (e.g., during a production incident investigation where automated changes would be disruptive).

**Improvement:** Add `ICDEV_HARNESS_PAUSE=true` environment variable that Genesis checks at the top of each reflex execution. When set:
- GREEN reflexes continue (read-only, safe)
- YELLOW/ORANGE reflexes skip execution and log `skipped_paused` to `genesis_audit`
- Dashboard shows a banner: "⚠ Harness paused — YELLOW/ORANGE reflexes disabled"

**Implementation path:**
- 3-line check in `daemon.py` `_run_reflex()` method
- Banner in `base.html` checking `/api/genesis/harness-status`
- No new tables needed.

---

### 7. Awareness Gap Rules Not Continuously Evolving

**Problem:** The 7 gap detection rules in `gap_detector.py` are static. As the codebase grows, new structural patterns emerge that aren't covered.

**Improvement:** Connect the `goal_learner` reflex to gap rule generation. When `goal_learner` detects a novel fix pattern (e.g., "every canvas needs an IQE adapter"), it should emit a new candidate gap rule to `awareness_gap_rules_proposed` table. Developers review and promote rules weekly.

**Implementation path:**
- Table: `awareness_gap_rules_proposed(id, rule_name, description, detection_logic_json, proposed_by, proposed_at, status ENUM[pending, approved, rejected])`
- `goal_learner.py`: when generating new goal files, also emit gap rule candidates based on the pattern detected
- Weekly `goal_learner` run triggers notification to engineering team for review

---

## Prioritized Improvement Roadmap

| Priority | Improvement | Effort | Impact | Risk |
|----------|-------------|--------|--------|------|
| P0 | #1 Sequenced awareness→triage pipeline | Low (config + 20 lines) | High (eliminates race conditions) | Low |
| P0 | #6 Master harness pause circuit breaker | Low (env check + banner) | High (operational safety) | Low |
| P1 | #3 Harness health dashboard panel | Medium (1 API + 1 template) | High (visibility) | Low |
| P1 | #4 Oracle lens for Genesis health | Medium (new lens file) | Medium (proactive alerting) | Low |
| P2 | #2 Oracle confidence calibration | High (new table + reflex) | High (long-term quality) | Medium |
| P2 | #5 Heal→Oracle feedback loop | Low (3 lines in heal.py) | Medium (signal enrichment) | Low |
| P3 | #7 Evolving gap rules via goal_learner | High (new table + logic) | Medium (long-term coverage) | Medium |

---

## Existing Strengths to Preserve

- **Append-only audit trail** (NIST AU-9) — do not add UPDATE/DELETE to any audit table
- **Confidence threshold at 0.7** — well-calibrated; don't lower without feedback loop in place first
- **Circuit breaker per reflex** — prevents cascade failures; master pause (#6) must not bypass individual breakers
- **Worktree sandbox for YELLOW/ORANGE** — all autonomous code changes are isolated; preserve this invariant
- **Human-in-the-loop for ORANGE** — evolve proposals must not auto-merge without review

---

## Files to Read Before Implementing

1. `tools/genesis/daemon.py` — reflex execution model, circuit breaker logic
2. `tools/genesis/reflexes/oracle_triage.py` — current triage + verification logic
3. `tools/awareness/gap_detector.py` — 7 gap rules
4. `tools/oracle/lenses/lens_quality.py` — Oracle lens pattern to follow
5. `args/genesis_config.yaml` — reflex scheduling configuration
6. `goals/genesis_daemon.md` — goal workflow for daemon operation
