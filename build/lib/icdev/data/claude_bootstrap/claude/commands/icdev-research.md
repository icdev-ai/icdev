# CUI // SP-CTI
# /icdev-research — Industry Research Engine: Vertical Market Intelligence

[TEMPLATE: CUI // SP-CTI]

Run the ICDEV™ Industry Research Engine to perform deep, structured research into a target industry vertical. Produces a scored, ranked dossier of industry challenges, regulatory landscape, community pain points, competitive analysis, and build-vs-buy recommendations — feeding directly into the agentic child app generation pipeline.

This is a session-based process. Sessions persist across runs and can be resumed at any stage.

## Variables

VERTICAL: Target industry vertical. Options: `trading`, `healthcare`, `defense`, `fintech`, `cybersecurity`, `logistics`, or a custom vertical slug defined in config. Default: prompt user to select.
SESSION_ID: Existing session ID to resume. Default: create new session.
STAGE: Run a specific pipeline stage. Options: `scope`, `landscape`, `regulate`, `community`, `academic`, `build_buy`, `synthesize`, `dossier`, `all`. Default: `all`.

## Workflow

### Step 1: Verify Database

1. **Check ICDEV™ database is initialized** and research tables exist:

   ```bash
   python tools/research/research_manager.py --status --json
   ```

   If database is missing or tables don't exist, initialize:

   ```bash
   python tools/db/init_icdev_db.py
   ```

### Step 2: Load Verticals

2. **Load available vertical definitions** from config:

   ```bash
   python tools/research/vertical_loader.py --list --json
   ```

   Review output: available verticals with key regulations, pain point categories, and data stream configuration. If the user requested a vertical not in the list, check if custom verticals can be defined:

   ```bash
   python tools/research/vertical_loader.py --detail --vertical $VERTICAL --json
   ```

### Step 3: Create or Resume Session

3. **Create a new research session** (if no SESSION_ID provided):

   ```bash
   python tools/research/research_manager.py --create --vertical $VERTICAL --json
   ```

   Or **resume an existing session**:

   ```bash
   python tools/research/research_manager.py --resume --session-id $SESSION_ID --json
   ```

   Review: session state, completed stages, pending stages, vertical definition.

### Step 4: Run Pipeline

4. **Run full pipeline** (all 8 stages sequentially):

   ```bash
   python tools/research/research_manager.py --run --session-id $SESSION_ID --json
   ```

   Or **run individual stages** (for partial/resumable execution):

   **Stage 1 — SCOPE** (define vertical + constraints):
   ```bash
   python tools/research/research_manager.py --stage scope --session-id $SESSION_ID --json
   ```

   **Stage 2 — LANDSCAPE** (competitive + commercial mapping):
   ```bash
   python tools/research/landscape_scanner.py --session-id $SESSION_ID --json
   ```
   Review: direct competitors, adjacent competitors, commercial SaaS products, market size indicators. If air-gapped, this stage returns cached/partial results and notes reduced coverage.

   **Stage 3 — REGULATE** (regulatory body + compliance requirements):
   ```bash
   python tools/research/regulatory_scanner.py --session-id $SESSION_ID --json
   ```
   Review: primary regulations, ICDEV™ crosswalk coverage, enforcement actions, upcoming changes, compliance gaps.

   **Stage 4 — COMMUNITY** (forums + review sites + pain points):
   ```bash
   python tools/research/community_scanner.py --session-id $SESSION_ID --json
   ```
   Review: pain points by category (compliance_burden, integration_difficulty, cost_concern, feature_gap, security_worry, performance_issue, usability_problem), frequency counts, severity.

   **Stage 5 — ACADEMIC** (papers + patents + emerging tech):
   ```bash
   python tools/research/academic_scanner.py --session-id $SESSION_ID --json
   ```
   Review: relevant papers (Semantic Scholar, arXiv), patent filings (USPTO), emerging tech trends, ICDEV™ applicability. If air-gapped, skip and note.

   **Stage 6 — BUILD_BUY** (open-source + SaaS + gap analysis):
   ```bash
   python tools/research/build_buy_analyzer.py --session-id $SESSION_ID --json
   ```
   Review: per-challenge build-vs-buy matrix (compliance_control 0.30, integration_effort 0.25, cost 0.20, time_to_market 0.15, competitive_advantage 0.10), license checks, ICDEV™ capability gaps.

   **Stage 7 — SYNTHESIZE** (challenge scoring + ranking):
   ```bash
   python tools/research/challenge_scorer.py --session-id $SESSION_ID --json
   ```
   Review: 6-dimension scores per challenge (market_demand 0.25, regulatory_pressure 0.20, technical_complexity 0.15, competitive_saturation 0.15, icdev_readiness 0.15, compliance_alignment 0.10). Challenges >= 0.75 are high-priority, 0.50-0.74 medium, < 0.50 low.

   **Stage 8 — DOSSIER** (template-based report generation):
   ```bash
   python tools/research/dossier_generator.py --session-id $SESSION_ID --json
   ```
   Review: 11-section dossier (executive summary, market landscape, regulatory environment, community pain points, academic landscape, build-vs-buy analysis, challenge ranking, ICDEV™ capability map, recommended child app scope, risk assessment, appendix).

### Step 5: Review Dossier

5. **Present dossier for human review**:

   ```bash
   python tools/research/research_manager.py --dossier --session-id $SESSION_ID --json
   ```

   Present findings to user with structured synthesis:

   a. **Executive Summary** — Vertical overview, top 5 challenges, recommended approach.

   b. **Top Challenges** — List top 10 challenges by composite score with: title, source streams, 6-dimension breakdown, build-vs-buy recommendation.

   c. **Regulatory Landscape** — Key regulations, ICDEV™ coverage percentage, gaps requiring new framework catalogs.

   d. **Community Voice** — Most frequent pain points, representative quotes/descriptions, severity distribution.

   e. **Competitive Position** — How ICDEV™ compares to existing solutions, differentiation opportunities.

   f. **Build-vs-Buy Summary** — Challenges where ICDEV™ should build (compliance control + competitive advantage) vs. integrate (commodity features).

   g. **Recommended Child App Scope** — Suggested feature set, compliance frameworks, agent configuration, estimated effort.

   h. **Risk Matrix** — Technical, regulatory, market, and compliance risks with mitigation strategies.

   **Approve or reject** the dossier:

   ```bash
   python tools/research/research_manager.py --review --session-id $SESSION_ID --decision approved --reviewer "user@org" --json
   ```

   Or reject with notes:

   ```bash
   python tools/research/research_manager.py --review --session-id $SESSION_ID --decision rejected --reviewer "user@org" --notes "Need deeper regulatory analysis for FINRA" --json
   ```

### Step 6: Trigger Fitness Assessment

6. **Cross-register findings** to Innovation + Creative engines (only for reviewed sessions):

   ```bash
   python tools/research/cross_engine_bridge.py --session-id $SESSION_ID --json
   ```

   **Trigger child app fitness assessment** using dossier as input:

   ```bash
   python tools/builder/agentic_fitness.py --spec-from-dossier --session-id $SESSION_ID --json
   ```

   Review fitness scorecard. If fit, proceed to blueprint and generation:

   ```bash
   python tools/builder/app_blueprint.py --fitness-scorecard scorecard.json --user-decisions '{}' --app-name "vertical-app" --json
   ```

   Then chain to `/icdev-agentic` for full child app generation.

## Status & Monitoring

**Check session status:**
```bash
python tools/research/research_manager.py --status --session-id $SESSION_ID --json
```

**List all sessions:**
```bash
python tools/research/research_manager.py --list --json
```

**List sessions by state:**
```bash
python tools/research/research_manager.py --list --state dossier_ready --json
```

**View session history (state transitions):**
```bash
python tools/research/research_manager.py --history --session-id $SESSION_ID --json
```

**Archive old sessions:**
```bash
python tools/research/research_manager.py --archive --session-id $SESSION_ID --json
```

## Notes

- **Air-gapped mode:** Stages 2-5 (landscape, regulate, community, academic) require network for full coverage. If unavailable, skip web sources gracefully and note reduced coverage in dossier. Build-vs-buy (stage 6) can still analyze ICDEV™ internal capabilities.
- **First run:** If no previous research sessions exist, the pipeline will create a fresh session. All 8 stages run sequentially.
- **Resumability:** Sessions persist across runs. Re-running a stage overwrites that stage's output without corrupting other stages (D-RES-4). Use `--stage` to run individual stages.
- **Database:** All findings stored in `research_*` tables (append-only except `research_sessions.state`). State transitions audited in `research_session_log`.
- **Config:** `args/research_config.yaml` controls verticals, data stream sources, scoring weights, cache TTL, rate limits, session timeout.
- **HITL Review:** Dossier MUST be human-reviewed before triggering child app generation (D-RES-10). No auto-trigger.
- **Cross-engine bridge:** High-scoring challenges (>= 0.75) register to Innovation Engine (`innovation_signals`) and Creative Engine (`creative_pain_points`) for cross-pollination.
- **Budget:** Max 5 active research sessions per tenant (configurable in `args/research_config.yaml`).
- **Chaining:** After review, chain to `/icdev-agentic` for child app generation, or `/icdev-innovate` to cross-reference with innovation signals, or `/audit` to verify production readiness.
- **Vertical customization:** Add custom verticals to `args/research_config.yaml` without code changes. Define key regulations, pain point categories, data stream overrides.

## Dashboard Pages

- No dedicated research dashboard page yet — results are CLI-based
- Cross-registered signals visible in Innovation Engine output
- Cross-registered pain points visible in Creative Engine output

## MCP Tools

Available via `icdev-unified` MCP server:
- `research_create` — Create new research session
- `research_resume` — Resume existing session
- `research_run` — Run full pipeline or individual stage
- `research_status` — Session status and pipeline progress
- `research_dossier` — Retrieve generated dossier
- `research_review` — Submit HITL review decision
- `research_list` — List all sessions with filters
- `research_cross_register` — Cross-register findings to Innovation + Creative engines
