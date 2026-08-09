# Genesis v2.0 — Autonomous Research Lab

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Genesis v2.0 — Autonomous Research Lab

### Core Engine

| Tool | Path | Purpose |
|------|------|---------|
| daemon | `tools/genesis/daemon.py` | Always-on daemon: 14 Reflexes, Trust Kernel, circuit breakers, schedule engine (D-GEN-1). Subclass of DaemonBase. Per-reflex config surface (crx-gen-03): `reflexes.<name>.depends_on: [names]` orders a reflex after its dependencies within a cycle (best-effort intra-cycle topo-sort in `DaemonBase.run_due_reflexes` via `topological_reflex_order`; not-due deps ignored; cycle-safe); `reflexes.<name>.max_execution_seconds` (alias: `timeout_seconds`; default `defaults.reflex_timeout_seconds`) is the hard watchdog cap enforced in `run_reflex_impl` — a breach becomes a `genesis_audit` failure row and counts toward the circuit breaker. **A reflex needs BOTH `REFLEX_NAMES` and an `args/genesis_config.yaml` block** — `__init__` builds a schedule only from the config block, and `run_due_reflexes` skips a name with no schedule silently, forever; `tests/test_reflex_dispatch_parity.py` fails on any new drift between the two (hgx-obs-02). ORANGE-tier reflexes no longer return before `importlib`: `_run_orange_proposal` runs the module under a proposal-mode overlay (`proposal_only`/`require_human_merge`/`auto_apply: false`/`dry_run`) and stages the outcome as an `orange_proposal` GKP at `pending_review`; `ICDEV_GENESIS_ORANGE_PROPOSALS=0` restores the old early return |
| promoter | `tools/genesis/promoter.py` | Knowledge Bridge: GKP export/import, dedup, auto-promote, human review gateway (D-GEN-4). Artifact type `orange_proposal` (hgx-obs-02) carries the staged outcome of an ORANGE-tier reflex run; `_import_orange_proposal` only records the reviewer's acknowledgement — it writes to no v1.x store, and the type is listed under `promoter.human_approve` and deliberately absent from `promoter.auto_promote`, which is what stops an ORANGE reflex approving its own proposal |
| feedback_collector | `tools/genesis/feedback_collector.py` | Pull v1.x telemetry (failures, quality, coverage, heals) for v2.0 consumption (D-GEN-11) |
| reporter | `tools/genesis/reporter.py` | Weekly autonomous markdown report: reflex activity, promotions, circuit breakers (D-GEN-12) |
| pattern_detector | `tools/genesis/pattern_detector.py` | Deterministic tool-chain mining from telemetry; scans hook_events and ai_telemetry for recurring multi-tool sequences with frequency/diversity/failure-avoidance scoring | --json, --lookback-days, --min-frequency | Detected patterns |

### 15 Reflexes (tools/genesis/reflexes/)

| Reflex | Risk Tier | Schedule | Purpose |
|--------|-----------|----------|---------|
| research | GREEN | every 6h | Scrape NIST/CISA/OWASP feeds, export GKP research signals |
| scout | GREEN | daily 07:00 | Monitor 16 GitHub repos (autoresearch, trivy, ollama, etc.), intel briefs |
| audit | GREEN | daily 06:00 | Self-scan: code quality + SAST via existing tools |
| coherence_sweep | GREEN | every 6h | Full-tier coherence sweep on main — runs the whole-app checks the per-task fast-tier gate defers, and refreshes the baseline that gate diffs against |
| awareness | GREEN | every 3h | Internal self-observation cycle: component graph refresh, health probe, drift detection, gap detection, kanban card promotion |
| self_monitor | GREEN | every 30m | Project Internal Awareness health snapshots into operator alerts + failure_log so /monitoring reflects real platform health |
| report | GREEN | weekly Sun 20:00 | Generate weekly status report with promotions/circuit breakers |
| comply | GREEN | daily 09:00 | cATO evidence freshness, crosswalk sync, SbD assessment |
| ingest | GREEN | every 4h | RSS feeds → innovation_signals for knowledge enrichment |
| market | GREEN | daily 10:00 | Marketplace module usage analytics, improvement suggestions |
| publish | YELLOW | daily 08:00 | Demand topic → draft → WriteGuard → staging (never production) |
| test | YELLOW | nightly 03:00 | Find untested modules → generate test stubs → run → keep passing |
| learn | YELLOW | nightly 04:00 | Generate training pairs from approved outputs for Ollama fine-tuning |
| heal | YELLOW | continuous/5min | Pattern-match audit trail errors → auto-remediation (log-only v2.0) |
| evolve | ORANGE | nightly 02:00 | Pick worst-quality file → LLM analysis → propose GKP code_patch for human review |
| docs | GREEN | daily 06:00 | Documentation drift detection → GKP report |
| experiment | ORANGE | nightly 01:00 | Bayesian Autoresearch — Karpathy-loop autonomous experiments (D-AR-9) |
| govcon_scan | GREEN | daily 06:30 | SAM.gov incremental scan + pain-point extraction + Pulse demand bridge |
| kanban | YELLOW | continuous/5min | Kanban Executor — polls kanban_tasks, promotes due cards, dispatches to Claude Code CLI |
| quality | GREEN | daily 07:30 | Self-Learning QA/QC: runs QDC gates, tracks trends, auto-fixes safe lint/deprecation issues, emits GKP improvement proposals |
| synthesize | YELLOW | nightly 01:30 | Auto-generate FORGE goal drafts from observed tool-chain patterns (D-SYN-1; human review required) |

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Research Reflex | tools/genesis/reflexes/research.py | Scrape NIST/CISA/OWASP feeds, export GKP research signals | config dict | GKP signals |
| Scout Reflex | tools/genesis/reflexes/scout.py | Monitor GitHub repos for new tools/CVEs, produce intel briefs | config dict | Intel briefs |
| Audit Reflex | tools/genesis/reflexes/audit.py | Self-scan: code quality + SAST via existing tools | config dict | Audit findings |
| Coherence Sweep Reflex | tools/genesis/reflexes/coherence_sweep.py | Full-tier coherence sweep on the main checkout: runs all 49 checks (including the whole-app heavies `blueprint_imports`, `openapi_parity`, `llm_router_api` that the per-task fast-tier gate defers) and refreshes the cached `.tmp/coherence_baseline_full_<sha>.json` the gate diffs new failures against. GREEN tier — read-only, no `--fix`. CLI: `python tools/genesis/reflexes/coherence_sweep.py`; ctx accepts `dry_run` and `cwd` | config dict | {failing_checks, failed_count, warned_count, total_checks, elapsed_sec} |
| Awareness Reflex | tools/genesis/reflexes/awareness.py | Internal self-observation: component graph refresh, health probe, drift, gap, kanban card promotion | config dict | Awareness report |
| Self-Monitor Reflex | tools/genesis/reflexes/self_monitor.py | Projection layer: reads latest awareness_component_health snapshots, refreshes the cheap http_head probe live, then writes aggregated rows to `alerts` (one per failing category, deduped + auto-resolved) and `failure_log` (one per failing component, deduped) so the operator-facing /monitoring page reflects real platform health. Also carries the kax-stall-01 **board throughput stall** rule (`_check_board_throughput`): opens one `board_throughput:done_flatline` alert when nothing reached `done` inside `window_hours` while tasks were scheduled/in_progress, refreshes it in place on later cycles, honours a cooldown so a human resolving it mid-stall doesn't cause a re-fire, and auto-resolves on recovery. That alert deliberately sits OUTSIDE the `self_monitor:` source prefix — `_sync_alerts` auto-resolves every firing `self_monitor:*` alert absent from the probe results and would otherwise clear a live stall each cycle. Config: `genesis_config.yaml` `self_monitor.board_throughput` + `ICDEV_BOARD_STALL_*` env overrides. GREEN tier. CLI: `--json [--no-refresh] [--min-fail N]` | config dict | {alerts_opened, alerts_updated, alerts_resolved, alerts_firing, failures_logged, board_throughput} |
| Report Reflex | tools/genesis/reflexes/report.py | Weekly status report with reflex activity, promotions, circuit breakers | config dict | Markdown report |
| Comply Reflex | tools/genesis/reflexes/comply.py | cATO evidence freshness, crosswalk sync, SbD assessment | config dict | Compliance status |
| Ingest Reflex | tools/genesis/reflexes/ingest.py | RSS/Atom feeds (NIST NVD, CISA KEV, FedRAMP) → innovation_signals for KG enrichment. GREEN tier, air-gap safe | config dict | nodes_added count |
| Market Reflex | tools/genesis/reflexes/market.py | Marketplace module usage analytics, improvement suggestions | config dict | Usage analytics |
| Publish Reflex | tools/genesis/reflexes/publish.py | Demand topic → draft → WriteGuard → staging (never production). YELLOW tier | config dict | Staged draft |
| Test Reflex | tools/genesis/reflexes/test.py | Find untested modules → generate test stubs → run → keep passing. YELLOW tier | config dict | Test results |
| Learn Reflex | tools/genesis/reflexes/learn.py | Generate fine-tuning pairs from approved outputs for Ollama fine-tuning. YELLOW tier | config dict | Training pairs |
| Heal Reflex | tools/genesis/reflexes/heal.py | Pattern-match audit trail errors → auto-remediation (log-only in v2.0). YELLOW tier | config dict | Heal actions |
| Evolve Reflex | tools/genesis/reflexes/evolve.py | Pick worst-quality file → LLM analysis → GKP code_patch proposal for human review. ORANGE tier | config dict | GKP code_patch |
| Docs Reflex | tools/genesis/reflexes/docs.py | Documentation drift detection → GKP report | config dict | Drift report |
| Experiment Reflex | tools/genesis/reflexes/experiment.py | Bayesian Autoresearch — Karpathy-loop autonomous experiments (D-AR-9). ORANGE tier | config dict | Experiment results |
| GovCon Scan Reflex | tools/genesis/reflexes/govcon_scan.py | SAM.gov incremental scan + pain-point extraction + Pulse demand bridge | config dict | Demand signals |
| Kanban Reflex | tools/genesis/reflexes/kanban.py | Kanban Executor — polls kanban_tasks, promotes due scheduled cards, dispatches via Claude Code CLI | config dict | Dispatch results |
| AlphaDesk News Patterns Reflex | tools/genesis/reflexes/alphadesk_news_patterns.py | Genesis reflex that runs NewsPatternAnalyzer each cycle, emits regime_shift and crackdown patterns, and promotes detections to GKP artifacts. GREEN tier, air-gap safe, no LLM | config dict | Pattern count + GKP export |
| Quality Reflex | tools/genesis/reflexes/quality.py | Self-Learning QA/QC: QDC gates, trend tracking, auto-fix lint/deprecation, GKP improvement proposals | config dict | Quality report |
| Synthesize Reflex | tools/genesis/reflexes/synthesize.py | Auto-generate FORGE goal drafts from telemetry patterns; stages as GKP for human review. YELLOW, confidence 0.55 (D-SYN-1) | config dict | GKP goal drafts |
| Cost Optimizer Reflex | tools/genesis/reflexes/cost_optimizer.py | Weekly LLM token spend audit; Haiku-eligible task detection; bloated prompt flagging. Hard rule: never flags Risk or Execution agents. GREEN tier, weekly cadence (168h) | config dict | recommendations_generated count |
| daemon | `tools/genesis/daemon.py` | Always-on daemon: 14 Reflexes, Trust Kernel, circuit breakers, schedule engine (D-GEN-1). Subclass of DaemonBase |

