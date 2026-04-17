# CUI // SP-CTI
# Chore: I2 — 8-point Registration Checklist Sweep (Modules A–H)

## Metadata
issue_number: `14`
run_id: `89b9c8ed`

## Chore Description
Walk the 8-point new-tool registration checklist from CLAUDE.md for every module
shipped in the AlphaDeskNews (ADN) pipeline phases A–H:

- **A1** `args/news_feeds.yaml` — RSS feed config
- **A2** `tools/trading/news/__init__.py` — package marker
- **A3** DB schema — ad_news_items, ad_news_scenario_links, ad_news_clusters tables
- **A4** APPEND_ONLY_TABLES hook entry
- **B2** `tools/trading/news/rss_ingestor.py` — RSS poller + HTML sanitizer
- **C2** `tools/trading/news/classifier.py` — rule-based classifier
- **D/E** `tools/trading/news/scenario_matcher.py` + `meta_scenarios.yaml`
- **E2** `tools/trading/news/aggregator.py` — cluster/promote engine
- **F1** Nav link + dashboard wiring
- **G1** daemon reflex `news_poller`
- **H1** `perspective_scorer.py` bearish `net_direction` wiring
- Supporting: `tools/trading/news/db.py`, `tools/trading/news/news_reasoner.py`

## Status of Each Checklist Point

| # | Checklist Item | Status | Action |
|---|---|---|---|
| 1 | tools/manifest shard entry | ✅ Done | All 6 news tools in alphadesk-trading-engine.md (lines 24-29) |
| 2 | docs/reference/commands.md | ❌ Missing | Add AlphaDeskNews Pipeline section |
| 3 | args/security_gates.yaml | ❌ Missing | Add alphadesk_news_pipeline gate |
| 4 | MCP tool_registry + gap_handlers | ❌ Missing | Add 6 news tool entries + handlers |
| 5 | pre_tool_use.py APPEND_ONLY_TABLES | ✅ Done | ad_news_items, ad_news_scenario_links, ad_news_clusters present |
| 6 | tests/conftest.py MINIMAL_ICDEV_SCHEMA | ✅ Done | All 4 ad_news_* tables present (lines 1161-1216) |
| 7 | companion --sync --write | 🔲 Run | Execute after code changes |
| 8 | coherence --all --fix --gate | 🔲 Run | Execute last |

## ATO Impact Assessment
- **Boundary Impact**: GREEN
- **Affected NIST Controls**: CM-8 (component inventory via manifest), SA-4 (tool registration)
- **SSP Impact**: None — administrative registration of existing tools, no new data flows

## Relevant Files

### To Modify
- `docs/reference/commands.md` — add AlphaDeskNews Pipeline section after Industry Research Engine
- `args/security_gates.yaml` — add `alphadesk_news_pipeline` gate block at end
- `tools/mcp/tool_registry.py` — add 6 news tool entries under new `alphadesk_news` category
- `tools/mcp/gap_handlers.py` — add 6 handler functions + `_run_cli` helper (if not present)

## Step by Step Tasks

### Step 1: Add AlphaDeskNews Pipeline section to commands.md
- Insert after the `## Industry Research Engine` section
- Include CLI commands for: rss_ingestor, classifier, scenario_matcher, aggregator, news_reasoner, news db

### Step 2: Add alphadesk_news_pipeline gate to security_gates.yaml
- Add gate block with warning conditions for: ingestor_feed_parse_failure, classifier_backlog_overflow
- Blocking condition: news_db_schema_mismatch (if migration not run)

### Step 3: Add MCP handler functions to gap_handlers.py
- Add 6 handler functions: handle_news_ingest_once, handle_news_classify, handle_news_scenario_match, handle_news_aggregate, handle_news_reason, handle_news_db_migrate
- Use `_run_cli` pattern matching existing oracle handlers

### Step 4: Register news tools in tool_registry.py
- Add `alphadesk_news` category header comment (update count in docstring)
- Add 6 tool entries referencing gap_handlers module + new handlers

### Step 5: Run companion sync
- `python tools/dx/companion.py --sync --write --json`

### Step 6: Run coherence check
- `python tools/workflow/coherence_checker.py --all --fix --gate`

## Validation Commands
- `python -m py_compile tools/mcp/gap_handlers.py tools/mcp/tool_registry.py`
- `ruff check . --fix`
- `python -m pytest tests/ -v --tb=short`
- `python tools/security/sast_runner.py --project-path . --json`
- `python tools/security/secret_detector.py --project-path . --json`
- `python tools/security/dependency_auditor.py --project-path . --json`
- `python tools/compliance/sbom_generator.py --project icdev`
- `python tools/compliance/control_mapper.py --activity "code.commit" --project-id "icdev"`
- `python tools/workflow/coherence_checker.py --all --fix --gate`

## Notes
- No new APPEND_ONLY_TABLES needed — A4 already handled
- No new conftest.py tables needed — A3 already handled
- Manifest shard already fully populated — only 4 out of 8 points needed code changes
- All ADN tools follow deterministic CLI pattern (no LLM required) — air-gap safe

# CUI // SP-CTI
