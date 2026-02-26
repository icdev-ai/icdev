# CUI // SP-CTI
# /innovate — Innovation Engine: Comprehensive Analysis & Recommendations

Run the full ICDEV Innovation Engine pipeline to discover capability gaps, competitive threats, compliance changes, code quality trends, and enhancement opportunities across all phases. Produces a prioritized, tenet-aligned recommendation report.

This is a repeatable process. Run periodically (weekly/monthly) to keep ICDEV ahead of the curve.

## Variables

SCOPE: Comma-separated analysis scopes to run. Default: `all`. Options: `introspective`, `code_quality`, `audit`, `competitive`, `trends`, `standards`, `score`, `report`

## Workflow

### Phase 1: Internal Introspection (Air-Gap Safe)

1. **Run introspective analysis** across all 8 analysis types:
   - failed_self_heals, gate_failures, unused_tools, slow_pipelines, nlq_gaps, knowledge_gaps, cli_harmonization, code_quality

   ```bash
   python tools/innovation/introspective_analyzer.py --analyze --all --json
   ```

   Review output: count of findings per type, top recommendations, signals queued.

2. **Run code quality scan** on the tools/ directory:

   ```bash
   python tools/analysis/code_analyzer.py --project-dir tools/ --store --json
   ```

   Review: total files, total functions, avg cyclomatic complexity, smell count, maintainability score.

3. **Run production audit** across all categories:

   ```bash
   python tools/testing/production_audit.py --json
   ```

   Review: pass/fail/warn/skip counts per category, list all blockers and warnings. Report any new failures since last run.

### Phase 2: External Intelligence (Requires Network)

4. **Run competitive intelligence** scan:

   ```bash
   python tools/innovation/competitive_intel.py --scan --all --json
   ```

   Review: competitor feature gaps, new releases, capability comparisons. If air-gapped, skip this step and note it was skipped.

5. **Run standards monitoring** across all standards bodies:

   ```bash
   python tools/innovation/standards_monitor.py --check --all --json
   ```

   Review: new/updated standards (NIST, CISA, DoD CIO, FedRAMP, ISO), compliance impact. If air-gapped, skip and note.

6. **Run trend detection** on accumulated signals:

   ```bash
   python tools/innovation/trend_detector.py --detect --json
   ```

   Review: active trends (keyword co-occurrence patterns), trend velocity, signal clusters.

### Phase 3: Signal Processing

7. **Score all unscored signals** using 5-dimension weighted average:

   ```bash
   python tools/innovation/signal_ranker.py --score-all --json
   ```

   Dimensions: community_demand (0.30), impact_breadth (0.25), feasibility (0.20), compliance_alignment (0.15), novelty (0.10).

8. **Triage all scored signals** through 5-gate safety pipeline:

   ```bash
   python tools/innovation/triage_engine.py --triage-all --json
   ```

   Gates: classify, GOTCHA fit, boundary impact, compliance pre-check, duplicate/license.

### Phase 4: Synthesis & Recommendations

9. **Generate pipeline report**:

   ```bash
   python tools/innovation/innovation_manager.py --pipeline-report --json
   ```

10. **Present findings to user** with structured synthesis:

    a. **Executive Summary** — Total signals discovered, scored, triaged. Active trends. Pipeline health.

    b. **Top Signals** — List top 10 signals by score with: title, source, score, dimensions, triage result, GOTCHA layer, boundary tier.

    c. **Active Trends** — Group related signals into themes. Show velocity (accelerating/stable/declining).

    d. **Competitive Gaps** — Features competitors have that ICDEV lacks. Prioritize by feasibility + compliance alignment.

    e. **Standards Updates** — New/changed compliance requirements with deadline and ICDEV impact assessment.

    f. **Code Quality Trends** — Compare current scan to previous: complexity trend, smell density, maintainability direction.

    g. **Production Audit Delta** — New failures or warnings since last audit run. Highlight regressions.

    h. **Prioritized Recommendations** — Synthesize all findings into a ranked list:
       - CRITICAL: deadline-driven (compliance mandates with dates)
       - HIGH: competitive advantage (features that differentiate)
       - MEDIUM: operational improvement (internal quality, efficiency)
       - LOW: nice-to-have (polish, minor gaps)

    i. **Tenet Alignment Check** — For each recommendation, verify:
       - Deterministic (no probabilistic business logic)
       - Read-only / advisory-only (no autonomous code modification)
       - GOTCHA framework fit (maps to Goal/Tool/Arg/Context/HardPrompt)
       - Air-gap safe (works without internet)
       - Compliance-first (strengthens, never weakens posture)

    Filter out any recommendation that violates ICDEV tenets.

## Notes

- **Air-gapped mode**: Steps 4-5 (competitive intel, standards monitoring) require network. If unavailable, skip gracefully and note in report.
- **First run**: If no previous signals exist, introspective + code quality + audit will still produce actionable findings.
- **Database**: All signals stored in `innovation_signals` table (append-only). Trends in `innovation_trends`. Solutions in `innovation_solutions`.
- **Config**: `args/innovation_config.yaml` controls sources, scoring weights, triage gates, scheduling.
- **Daemon mode**: For continuous monitoring, use `python tools/innovation/innovation_manager.py --daemon --json` (respects quiet hours).
- **Budget**: Max 10 auto-generated solutions per PI (D201). Override in config.
- **Chaining**: After review, chain to `/icdev-build` for approved solutions, or `/audit` to verify production readiness.

## Dashboard Pages

- No dedicated innovation dashboard page yet — results are CLI-based
- Code quality trends visible at `/code-quality`
- Production audit results at `/prod-audit`

## MCP Tools

Available via `icdev-innovation` MCP server:
- `introspect` — Run introspective analysis
- `competitive_scan` — Run competitive intelligence
- `standards_check` — Run standards monitoring
- `detect_trends` — Run trend detection
- `score_signals` — Score innovation signals
- `triage_signals` — Triage scored signals
- `run_pipeline` — Run full innovation pipeline
- `get_status` — Pipeline status and health
