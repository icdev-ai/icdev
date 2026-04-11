# CUI // SP-CTI
# Goal: Genesis Daemon — Autonomous Research Lab

**Version:** 2.0.0-alpha
**Classification:** CUI // SP-CTI
**Owner:** Genesis Daemon (autonomous)
**Decisions:** D-GEN-1 through D-GEN-12

---

## Purpose

Genesis is a permanent, always-on autonomous research engine that runs 12
Reflexes to continuously discover, validate, and export improvements to the
v1.x production core.  It operates on branch `v2-genesis` (never merges to
main) and transfers knowledge via GKP artifacts through the Promoter gateway.

**Not publicly available.  Private experimental infrastructure only.**

---

## Prerequisites

- `ICDEV_GENESIS_ENABLED=true` (disabled by default — D-GEN-9)
- Ollama running locally with `qwen3.5` model available
- `args/genesis_config.yaml` present with valid configuration
- `data/genesis/` directory structure exists
- Genesis tables exist in icdev.db (auto-created by daemon)

---

## Architecture

### Single Daemon, 12 Threads (D-GEN-1)

```
GenesisDaemon (tools/genesis/daemon.py)
├── Trust Kernel (risk tiers, circuit breakers, action whitelists)
├── Schedule Engine (parses cron-like schedules per reflex)
├── Reflex Runner (dispatches to tools/genesis/reflexes/*.py)
└── Audit Logger (append-only genesis_audit table)
```

### Risk Tiers (Trust Kernel)

| Tier | Color | Gate | Auto-Approve | Sandbox |
|------|-------|------|-------------|---------|
| Non-destructive | GREEN | Audit only | Yes | No |
| Reversible writes | YELLOW | Sandbox + rollback | Yes | Worktree |
| Code mutation | ORANGE | Test gate + human review | No | Worktree |

### Circuit Breaker (D-GEN-4)

- 3 consecutive failures → circuit breaker OPENS → reflex disabled
- Human must reset: `python tools/genesis/daemon.py --reset <reflex>`
- Auto-reenable: disabled (conservative default)
- Cooldown: 60 minutes minimum between resets

---

## The 12 Reflexes

### GREEN Tier (read-only, auto-approve)

#### 1. Research (every 6h)
- **What:** Scrape NIST/CISA/DoD RSS feeds, SAM.gov, GitHub trending
- **Tools:** Web scraping (deterministic) → Ollama qwen3.5 synthesis
- **Output:** Research signals → GKP `research_signal` artifacts
- **Metric:** `signals_ingested > 0`
- **Sources:** Configured in `context/genesis/feeds.yaml`

#### 2. Scout (daily 07:00)
- **What:** Monitor competitor/adjacent GitHub repos for stars, releases, patterns
- **Tools:** GitHub API (public) → pattern extraction → intel brief generation
- **Output:** Intel briefs in `data/genesis/reports/`
- **Metric:** `briefs_generated > 0`
- **Targets:** Configured in `context/genesis/competitors.yaml`

#### 3. Audit (daily 06:00)
- **What:** Self-audit: code quality, security scan, compliance, STIG, SbD
- **Tools:** `code_analyzer.py`, `sast_runner.py`, `stig_checker.py`, `sbd_assessor.py`
- **Output:** Audit report + auto-created POAMs for regressions
- **Metric:** Always succeeds (informational)

#### 4. Comply (daily 09:00)
- **What:** Refresh cATO evidence, regenerate stale SSPs, run crosswalk
- **Tools:** `cato_scheduler.py`, `ssp_generator.py`, `crosswalk_engine.py`
- **Output:** Fresh compliance evidence → GKP `compliance_knowledge` artifacts
- **Metric:** `evidence_freshness_pct >= 90%`

#### 5. Ingest (every 4h)
- **What:** Watch NIST NVD, CISA KEV, FedRAMP updates → knowledge graph
- **Tools:** RSS fetch → `knowledge_graph/ingester.py`
- **Output:** Knowledge graph nodes → GKP `compliance_knowledge` artifacts
- **Metric:** `nodes_added > 0`

#### 6. Market (daily 10:00)
- **What:** Track marketplace module usage/feedback, suggest improvements
- **Tools:** Query marketplace tables → pattern analysis → suggestions
- **Output:** Improvement suggestions in `data/genesis/reports/`
- **Metric:** `suggestions_generated > 0`

#### 7. Report (weekly Sun 20:00)
- **What:** Generate comprehensive autonomous status report
- **Tools:** `tools/genesis/reporter.py`
- **Output:** Markdown report in `data/genesis/reports/genesis-report-YYYY-MM-DD.md`
- **Metric:** Report delivered (boolean)
- **Includes:** Reflex activity, promotions/rejections, circuit breakers, recommendations

### YELLOW Tier (reversible writes, sandbox + rollback)

#### 8. Publish (daily 08:00)
- **What:** End-to-end Pulse article pipeline: demand topic → draft → WriteGuard → staging
- **Tools:** `demand_detector.py` → `drafter.py` → `writeguard.py` → staging export
- **Output:** Draft article in staging queue (NEVER production — D-GEN staging/draft only)
- **Metric:** `writeguard_score >= 80`
- **Gates:** WriteGuard pass, CUI check pass, max 2 articles/day

#### 9. Test (nightly 03:00)
- **What:** Identify under-tested tools, generate tests, run, commit passing tests
- **Tools:** `code_analyzer.py` (coverage gaps) → test generation → `pytest`
- **Output:** New test files committed to worktree branch
- **Metric:** `coverage_delta > 0` (net new coverage)
- **Sandbox:** Git worktree, tests run in isolation

#### 10. Learn (nightly 04:00)
- **What:** Generate training pairs from approved outputs, fine-tune local Ollama
- **Tools:** `pair_generator.py` → LoRA fine-tuning
- **Output:** Training pairs (unapproved, human review required) + model checkpoint
- **Metric:** `validation_loss_delta < 0` (model improved)
- **Gate:** Pairs stored as unapproved — human must approve before training

#### 11. Heal (continuous, every 5min)
- **What:** Pattern-based auto-remediation for known failure patterns
- **Tools:** `pattern_detector.py` → remediation → verification
- **Output:** Self-healing events → GKP `proven_pattern` artifacts when confidence >= 0.7
- **Metric:** `mttr_reduction < 0` (MTTR decreased)
- **Rate limit:** Max 5 auto-heals/hour, 10-min cooldown per target

#### (new) Awareness (every 3h)
- **What:** Internal Awareness Engine — enumerate all ICDEV components, probe health, detect regressions, scan structural gaps, promote Suggested kanban cards
- **Tools:** `component_indexer.py` → `health_prober.py` → `drift_detector.py` → `gap_detector.py` → `suggested_card_writer.py`
- **Output:** 5 rows in `awareness_run_log` per cycle; `oracle_predictions` rows; `kanban_tasks` (status=suggested) for confidence ≥ 0.7
- **Metric:** `awareness_cycle_complete >= 0` (always informational — never fails the daemon)
- **LLM:** Zero in hot path. Optional narration (Scanner tier) only when `/components-map?narrate=true` is requested.
- **Cadence:** 3 hours (10 800 s). 60-minute cooldown between cycles.
- **Enablement:** Reads `.env` flags at cycle start. If flags changed since last cycle → full re-index. Disabled modules are indexed (dimmed) but not probed.
- **Config:** `args/awareness_config.yaml` (probes, gaps, oracle, narrative settings)
- **Run manually:**
  ```bash
  python tools/genesis/daemon.py --reflex awareness --json
  ```

### YELLOW Tier (reversible writes, sandbox + rollback)

### ORANGE Tier (code mutation, human review required)

#### 12. Evolve (nightly 02:00)
- **What:** Autoresearch-style: pick worst-quality tool → improve → test → keep/discard
- **Tools:** `code_analyzer.py` → scanner-tier LLM → edit → `pytest` → `code_analyzer.py`
- **Output:** Code patch on worktree branch → GKP `code_patch` artifact
- **Metric:** `test_pass_rate_delta >= 0` AND `complexity_delta <= 0`
- **Constraints:**
  - ONE file per cycle (Autoresearch lesson)
  - Never touches `CLAUDE.md`, `.env`, `tools/db/storage.py`, `tools/genesis/daemon.py`
  - All changes in git worktree (never main)
  - Human must cherry-pick to main (D-GEN-7)

---

## Operations

### Start Daemon
```bash
ICDEV_GENESIS_ENABLED=true python tools/genesis/daemon.py
```

### Single Pass (run all due, then exit)
```bash
python tools/genesis/daemon.py --once --json
```

### Run One Reflex
```bash
python tools/genesis/daemon.py --reflex research --json
```

### Status
```bash
python tools/genesis/daemon.py --status --json
```

### Enable/Disable/Reset
```bash
python tools/genesis/daemon.py --enable research
python tools/genesis/daemon.py --disable evolve
python tools/genesis/daemon.py --reset heal  # Reset circuit breaker
```

---

## Error Handling

1. **Reflex throws exception** → Caught, logged to `genesis_audit`, failure count incremented
2. **3 consecutive failures** → Circuit breaker trips, reflex disabled, WARNING printed
3. **Kill switch** → Set `ICDEV_GENESIS_ENABLED=false` → daemon shuts down within 10 seconds
4. **Signal (SIGINT/SIGTERM)** → Graceful shutdown, PID file cleaned up
5. **Config file missing** → Falls back to default config (all reflexes disabled)

---

## Guardrails

- Genesis is NOT publicly available — private experimental infrastructure only
- v2-genesis branch NEVER merges wholesale to main
- Knowledge transfers ONLY through GKP artifacts via Promoter (D-GEN-4)
- All autonomous decisions logged to append-only `genesis_audit` table (NIST AU)
- Scanner-tier LLM only — zero Claude API cost for autonomous operations (D-GEN-2)
- Evolve Reflex cannot modify itself (no self-modification of daemon code)
- Publish Reflex targets staging/draft only — never production WordPress
- Code patches always require human cherry-pick to main (D-GEN-7)
- Training pairs always stored as unapproved — human review before training
