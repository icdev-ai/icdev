# CUI // SP-CTI
"""Idempotent enqueue for the FathomDesk /news plan.

Decomposes the plan at ~/.claude/plans/recursive-noodling-meteor.md into
atomic subtasks sized for a single agent turn (per memory
feedback_kanban_phantom.md — batch tasks phantom-complete 96-100% of the
time). Every phase ends with an exit-gate subtask that runs the mandatory
5-step validation (codelens, coherence, e2e, regression, companion).

Subtasks inserted with status='scheduled' and linear-chained via
depends_on_task_id. Re-runnable: skips rows whose stable id already exists.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "adn-"  # FathomDesk News

GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) regression pytest: pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json. "
    "If any step fails, stay in_progress and fix within this gate task."
)

# (suffix, title, task_type, priority, description)
SUBTASKS: list[tuple[str, str, str, str, str]] = [
    # ───────── Phase A — DB + package scaffold ─────────
    ("A1-feeds-yaml", "A1: Create args/news_feeds.yaml with ~25 RSS URLs",
     "build", "high",
     "Create args/news_feeds.yaml. Group feeds under keys: macro (Fed, BLS, BEA, FRED blog), "
     "financial (Reuters Markets, CNBC Top News, Seeking Alpha Market Currents), "
     "geopolitical (Reuters World, AP Top, BBC World, Al Jazeera), "
     "sector (Defense News, Oil & Gas Journal, Semi Engineering, Endpoints Bio), "
     "corporate (SEC EDGAR 8-K atom feed). Each entry: {name, url}. "
     "Plus top-level: poll_interval_minutes: 15, max_items_per_feed: 30, retention_days: 90, "
     "promotion_thresholds: {emerging: 3, cluster: 6, regime: 10}. "
     "Verify each URL is reachable (HTTP 200) before committing. Do not embed API keys."),
    ("A2-package-init", "A2: Create tools/trading/news/__init__.py package marker",
     "build", "high",
     "Create tools/trading/news/__init__.py with module docstring referencing the plan "
     "(~/.claude/plans/recursive-noodling-meteor.md) and a __version__ = '0.1.0'. "
     "Empty body otherwise."),
    ("A3-db-schema", "A3: tools/trading/news/db.py — DDL for 3 tables + CRUD helpers",
     "build", "high",
     "Create tools/trading/news/db.py. Tables: ad_news_items (id, source, feed_url, title, link, "
     "published_at, ingested_at, summary, category, impact_level, net_direction, "
     "mentioned_tickers JSON, classifier_version); ad_news_scenario_links (id, news_id, "
     "scenario_run_id, scenario_key, match_confidence, match_reason, created_at); "
     "ad_news_clusters (id, scenario_key, category, window, item_ids JSON, cumulative_score, "
     "first_seen, last_seen, status). All append-only. Use get_connection() — NEVER sqlite3.connect() "
     "(memory feedback_always_use_get_connection.md). SQLite syntax; storage.translate_sql() handles PG. "
     "Add CLI: --migrate (apply DDL), --json (status). Helpers: insert_news_item(), "
     "list_news(filters), get_news_by_id(), insert_scenario_link(), insert_cluster(), "
     "list_active_clusters()."),
    ("A4-appendonly-hook", "A4: Register 3 new tables as append-only in pre_tool_use.py",
     "build", "high",
     "Open .claude/hooks/pre_tool_use.py. Locate APPEND_ONLY_TABLES. Append 'ad_news_items', "
     "'ad_news_scenario_links', 'ad_news_clusters'. Per CLAUDE.md: any append-only/immutable "
     "DB table must be in this list. Add a one-line comment grouping them under '# FathomDesk news (plan adn-)'."),
    ("A5-conftest-schema", "A5: Add 3 new tables to tests/conftest.py MINIMAL_ICDEV_SCHEMA",
     "build", "high",
     "Open tests/conftest.py. Locate MINIMAL_ICDEV_SCHEMA. Add the DDL strings for ad_news_items, "
     "ad_news_scenario_links, ad_news_clusters (copied verbatim from tools/trading/news/db.py). "
     "Required for pytest fixtures that build an ephemeral icdev.db."),
    ("A6-migrate-run", "A6: Apply migration — python -m tools.trading.news.db --migrate",
     "test", "high",
     "Run: python -m tools.trading.news.db --migrate. Verify all 3 tables exist: "
     "sqlite3 data/fathomdesk.db \".tables\" | grep ad_news. Confirm indexes created "
     "(idx_news_published, idx_news_category, idx_news_scenario_news). JSON output captured."),
    ("A-gate", "A-gate: Phase A exit validation", "test", "high", GATE),

    # ───────── Phase B — RSS ingestor ─────────
    ("B1-feedparser-dep", "B1: Verify feedparser wheel available (air-gap compat check)",
     "research", "high",
     "Per memory feedback_airgap_compat.md: verify dependency works BEFORE building. "
     "Run: python -c 'import feedparser; print(feedparser.__version__)'. If missing: "
     "pip install feedparser. Confirm wheel cached for air-gap (vendor/wheels/ if that "
     "dir exists). Add 'feedparser>=6.0' to requirements.txt if not present. "
     "Output: version string + wheel path."),
    ("B2-rss-ingestor", "B2: tools/trading/news/rss_ingestor.py — poll, dedupe, store",
     "build", "high",
     "Create tools/trading/news/rss_ingestor.py. Load args/news_feeds.yaml. For each feed, "
     "use feedparser.parse(url) with 10s timeout per feed. For each entry: compute id = "
     "sha256(source || link).hexdigest()[:16]. Skip if already exists (SELECT id FROM "
     "ad_news_items WHERE id=?). Otherwise insert with published_at = entry.published_parsed "
     "(normalized to ISO-8601 UTC), summary = BeautifulSoup-stripped entry.summary (HTML-strip "
     "for safety — memory feedback), mentioned_tickers=[], category=NULL (Phase C fills), "
     "classifier_version=NULL. CLI: --once (single pass), --start (daemon loop), --json. "
     "Per-feed exceptions caught and logged; one bad feed cannot block others."),
    ("B3-html-sanitize", "B3: HTML sanitizer util for summary bodies",
     "build", "medium",
     "Add _sanitize_html(html: str) -> str helper inside rss_ingestor.py (or a sibling "
     "util.py if cleaner). Uses html.parser stdlib (no new deps) to strip all tags and "
     "decode entities. Truncate at 500 chars. Reject input > 50KB (feed abuse guard). "
     "Per OPT-58 sandbox coverage: RSS content is untrusted, strip before storage."),
    ("B4-sandbox-coverage", "B4: Document sandbox decision in docs/security/sandbox-coverage.md",
     "chore", "high",
     "Append an entry for tools/trading/news/* under category 'sandboxed-on-demand'. "
     "Rationale: ingests third-party RSS. Mitigation: HTML-strip + size cap + no code "
     "execution. Ref: plan adn- Phase B. Per CLAUDE.md OPT-58, enforced by "
     "coherence_checker.py::check_sandbox_coverage."),
    ("B5-ingestor-smoke", "B5: Smoke-run ingestor; verify ≥ 20 items across ≥ 5 sources",
     "test", "high",
     "Run: python -m tools.trading.news.rss_ingestor --once --json. Query: "
     "SELECT source, COUNT(*) FROM ad_news_items GROUP BY source. Must show ≥ 5 distinct "
     "sources and ≥ 20 total items. If a feed is 0, check its URL in args/news_feeds.yaml."),
    ("B-gate", "B-gate: Phase B exit validation", "test", "high", GATE),

    # ───────── Phase C — Classifier ─────────
    ("C1-extend-keywords", "C1: Extend analysts/news.py keyword maps (macro, geopolitical)",
     "build", "high",
     "Open tools/trading/analysts/news.py. Locate HIGH_IMPACT_KEYWORDS and MEDIUM_IMPACT_KEYWORDS. "
     "Add geopolitical tokens (invasion, strike, sanctions, cease-fire, embargo), "
     "monetary tokens (rate hike, rate cut, FOMC, hawkish, dovish, taper, QT, yield curve), "
     "fiscal tokens (CPI, PPI, NFP, GDP, unemployment, retail sales, ISM). DO NOT duplicate "
     "existing entries. Preserve categorization structure."),
    ("C2-classifier-rule", "C2: tools/trading/news/classifier.py — rule-based first pass",
     "build", "high",
     "Create tools/trading/news/classifier.py. classify(news_id) reads row from ad_news_items, "
     "applies keyword maps from analysts/news.py, outputs {category, impact_level, "
     "mentioned_tickers, sentiment, net_direction, classifier_version}. Ticker detection: "
     "regex \\b[A-Z]{1,5}\\b intersected with the FathomDesk universe "
     "(tools/trading/market_intel/universe.py). Net direction: bullish if positive keyword "
     "count > negative by 2+, else neutral. Write result back via UPDATE — wait, per A4 "
     "ad_news_items is append-only; instead, store classifier output in a new row in "
     "ad_news_classifications (another append-only table — ADD to A3 schema). OR: "
     "decision — allow in-place UPDATE for classifier fields only, and carve an exception "
     "in pre_tool_use.py's APPEND_ONLY_TABLES allowlist (classifier fields are metadata, "
     "not the immutable news content). Pick option 2 — simpler; document in commit."),
    ("C3-classifier-llm-fallback", "C3: Tier-1 LLM fallback for ambiguous items",
     "build", "medium",
     "Extend classifier.py: if rule-based confidence < 0.5 (no clear category, no impact "
     "keywords), call LLMRouter.get_provider_for_function('news_classify') with a tight "
     "prompt: title + summary → JSON {category, impact_level, net_direction}. Cache result "
     "by sha256(title). Tier-1 only (local qwen3.5), NO tier-2 escalation (cost control)."),
    ("C4-llm-config-register", "C4: Register news_classify in args/llm_config_trading.yaml",
     "build", "high",
     "Open args/llm_config_trading.yaml. Add 'news_classify' and 'news_scenario_match' to "
     "the scanner_functions list (tier-1 only, no tier-2 escalation). Copy the existing "
     "entry pattern from 'analyze_news'. Document in the comment block at top."),
    ("C5-classifier-backfill", "C5: Backfill classifier across all rows from Phase B smoke",
     "test", "high",
     "Run: python -m tools.trading.news.classifier --backfill-all --json. Must classify "
     "100% of rows from Phase B5. Verify distribution: category counts non-zero across "
     "≥ 3 categories. Any NULLs remaining = bug."),
    ("C-gate", "C-gate: Phase C exit validation", "test", "high", GATE),

    # ───────── Phase D — Scenario matcher ─────────
    ("D1-read-scenario-templates", "D1: Read all SCENARIO_TEMPLATES keys and descriptions",
     "research", "high",
     "Read tools/trading/market_intel/scenario_engine.py starting at line 286. Produce a "
     "table of all 29 scenario keys with (key, name, category, typical trigger phrase). "
     "Save as a comment block at the top of tools/trading/news/scenario_matcher.py. This "
     "is the reference for the keyword map in D2."),
    ("D2-matcher-rule", "D2: scenario_matcher.py — deterministic keyword → scenario_key map",
     "build", "high",
     "Create tools/trading/news/scenario_matcher.py. SCENARIO_KEYWORDS dict: "
     "{'rate_hike_25bps': ['rate hike', 'hikes rates', '+25bps', 'raised rates'], "
     "'war_escalation': ['invasion', 'airstrike', 'military action'], ...} — one entry per "
     "SCENARIO_TEMPLATES key. match(news_id) returns (scenario_key, confidence, reason). "
     "Confidence = (matched_tokens / total_tokens_in_map_entry) × keyword_weight. Return None "
     "if max confidence < 0.3."),
    ("D3-matcher-llm-fallback", "D3: Tier-1 LLM fallback when rule conf < 0.6",
     "build", "medium",
     "Extend scenario_matcher.py: if rule confidence < 0.6, call LLMRouter with prompt: "
     "list of scenario keys + descriptions + news title + summary → single JSON "
     "{scenario_key, confidence 0..1, reason}. Strict schema validation on response. "
     "Tier-1 only."),
    ("D4-run-scenario-wire", "D4: on-demand match_and_run(news_id) wired to scenario_engine",
     "build", "high",
     "Add match_and_run(news_id) to scenario_matcher.py: (1) call match() → scenario_key; "
     "(2) from tools.trading.market_intel.scenario_engine import run_scenario; "
     "(3) run_id = run_scenario(scenario_key); (4) insert row in ad_news_scenario_links. "
     "Returns {run_id, scenario_key, match_confidence, match_reason}. This is the backing "
     "for the 'Run Scenario' UI button (Phase F)."),
    ("D5-matcher-batch-smoke", "D5: Smoke — match across Phase C5 backfill set",
     "test", "high",
     "Run: python -m tools.trading.news.scenario_matcher --batch --json. Success criteria: "
     "≥ 60% of high-impact items match a scenario with confidence ≥ 0.6 (plan success "
     "criterion 5b). Dump mismatches to .tmp/news_matcher_misses.json for review."),
    ("D-gate", "D-gate: Phase D exit validation", "test", "high", GATE),

    # ───────── Phase E — Aggregation (regime detection) ─────────
    ("E1-meta-scenarios-yaml", "E1: tools/trading/news/meta_scenarios.yaml",
     "build", "high",
     "Create tools/trading/news/meta_scenarios.yaml. Each entry: signature (list of "
     "scenario_keys that must co-occur), min_count, max_window_hours, min_category_diversity, "
     "meta_scenario_key (output). Seed examples: hawkish_regime_shift (rate_hike_25bps × 3 "
     "+ cpi_hot_surprise within 48h → rate_hike_50bps); oil_supply_cascade (oil_supply_disruption "
     "× 3 + geopolitical within 72h → geopolitical_crisis); tech_correction_forming "
     "(earnings_miss × 3 in Technology within 7d → tech_correction); banking_contagion "
     "(regional bank stress × 3 within 7d → market_crash)."),
    ("E2-aggregator-cluster", "E2: tools/trading/news/aggregator.py — clustering + scoring",
     "build", "high",
     "Create aggregator.py. cluster(window_hours) reads recent ad_news_items + their "
     "scenario_matcher matches. Groups by (category, scenario_key) AND shared_tickers (≥ 2 "
     "overlap). For each group compute cumulative_score = Σ(impact_weight × source_reliability "
     "× exp(-hours_ago/decay) × directional_alignment). Source reliability map in constants "
     "(Reuters=1.0, SEC=1.0, CNBC=0.7, anonymous RSS=0.5). Opposing directions cancel."),
    ("E3-aggregator-promote", "E3: Promotion thresholds + ad_news_clusters writes",
     "build", "high",
     "Extend aggregator.py with promote() that, given a freshly computed cluster, compares "
     "cumulative_score to promotion_thresholds from args/news_feeds.yaml. On crossing emerging/"
     "cluster/regime, INSERT a new row in ad_news_clusters with new status (never UPDATE — "
     "append-only). Source-diversity guard: reject promotion if ≥ 50% of items share the same "
     "outlet. Regime-level promotion also POSTs to /api/market/alerts (existing pipeline)."),
    ("E4-meta-scenario-match", "E4: Meta-scenario lookup on regime-level clusters",
     "build", "high",
     "When aggregator promotes a cluster to 'regime' status, evaluate meta_scenarios.yaml. "
     "If any signature matches (all required scenario_keys present in cluster.item_ids' "
     "matches, window respected, category diversity satisfied), attach meta_scenario_key to "
     "the cluster row. This is what 'Run meta-scenario' on the UI banner will trigger."),
    ("E5-historical-replay-test", "E5: Historical replay — 2026-04-09..04-14 scout reports",
     "test", "high",
     "Per plan success criterion: load data/genesis/reports/scout-2026-04-09.md..scout-2026-04-14.md "
     "and data/scout/2026-04-10.md..04-11.md, extract headline-like items, ingest into a test "
     "DB, run aggregator. Assert ≥ 2 of the 3 expected clusters promoted to 'cluster' or higher. "
     "Test lives at tests/trading/test_news_aggregator_replay.py."),
    ("E-gate", "E-gate: Phase E exit validation", "test", "high", GATE),

    # ───────── Phase F — Dashboard /news route + template + nav ─────────
    ("F1-nav-link", "F1: Add /news to tools/trading/dashboard/templates/base.html",
     "build", "high",
     "Open tools/trading/dashboard/templates/base.html. After the '/market' <li> and before "
     "'/graph', insert: <li><a href=\"/news\" class=\"{% if request.path == '/news' %}active{% endif %}\">News</a></li>. "
     "Also add to the start.md Pages: line per CLAUDE.md guardrail."),
    ("F2-flask-routes", "F2: Add /news + 5 API endpoints to tools/trading/dashboard/app.py",
     "build", "high",
     "In tools/trading/dashboard/app.py, add: GET /news (render news.html); GET /api/news "
     "(list with filters: category, impact, window_hours); GET /api/news/<id> (item + "
     "matched scenario impacts joined from ad_scenario_impacts); POST /api/news/<id>/run-scenario "
     "(calls scenario_matcher.match_and_run); GET /api/news/clusters (active clusters with "
     "members); POST /api/news/clusters/<id>/run-meta-scenario; POST /api/news/clusters/<id>/suppress. "
     "TTL-cache /api/news 60s (reuse _cached_response helper at top of app.py)."),
    ("F3-news-template", "F3: tools/trading/dashboard/templates/news.html — card view",
     "build", "high",
     "Create news.html extending base.html. Layout: (1) top 'Regime Watch' strip (red border) "
     "pulling from /api/news/clusters status=regime; (2) 'Emerging' strip below (yellow) "
     "status=emerging|cluster; (3) filter chips row (category × impact × time window); "
     "(4) card list from /api/news. Each card shows: category badge, impact badge, direction, "
     "source · relative time, title (link to original), matched scenario name + confidence, "
     "top 5 affected tickers with 1m/1q/1y bars (mini sparkbars), expand-justification, "
     "[Run Scenario] [Send to Signals] [Dismiss] buttons. Jinja: use value|round(0)|int for "
     "percentages (CLAUDE.md guardrail — never '%.0f'|format)."),
    ("F4-news-js", "F4: /news client-side JS (filters, actions, cluster expand)",
     "build", "high",
     "In news.html {% block scripts %}: loadNews() / loadClusters() calling the Phase F2 "
     "endpoints. Filter chip click → refetch with query params. [Run Scenario] → POST "
     "/api/news/<id>/run-scenario → on success, navigate to /scenarios?run_id=<id>. [Send to "
     "Signals] → POST /api/scenarios/<run_id>/send-to-signals (existing endpoint, unchanged). "
     "[Dismiss] → local-only hide (no server state). Cluster expand → accordion of member items."),
    ("F5-e2e-news-selenium", "F5: Selenium E2E test at tests/e2e/fathomdesk/test_news_page.py",
     "test", "high",
     "Per memory e2e_selenium.md: use Selenium, NOT Playwright. Test: open /news, assert page "
     "title, nav link has .active class, ≥ 1 card renders, click first card's 'Run Scenario', "
     "assert redirect to /scenarios?run_id=... AND that run_id is valid. Screenshots to "
     "playwright/screenshots/news_*.png (path name conserved per CLAUDE.md even though we're "
     "using Selenium)."),
    ("F-gate", "F-gate: Phase F exit validation", "test", "high", GATE),

    # ───────── Phase G — Daemon reflexes ─────────
    ("G1-daemon-news-poller", "G1: Register news_poller reflex in market_intel/daemon.py",
     "build", "high",
     "Open tools/trading/market_intel/daemon.py. Add reflex 'news_poller': green tier, every "
     "15m, action = call rss_ingestor.ingest_once() + classifier.backfill_unclassified(). "
     "Follow the existing reflex pattern (market_scanner). DO NOT auto-run scenarios."),
    ("G2-daemon-news-aggregator", "G2: Register news_aggregator reflex — every 5m",
     "build", "high",
     "Add reflex 'news_aggregator': green tier, every 5m, action = aggregator.cluster() + "
     "aggregator.promote(). On regime-level promotion, post to /api/market/alerts via existing "
     "alert_engine.emit_alert() helper. Circuit breaker: pause if ≥ 3 consecutive failures "
     "(base_daemon behavior — no new code)."),
    ("G3-daemon-config", "G3: Declare both reflexes in args/trading_daemon_config.yaml",
     "build", "high",
     "Open args/trading_daemon_config.yaml. Add news_poller (enabled: true, schedule: 'every "
     "15m', risk_tier: green) and news_aggregator (every 5m, green). Copy the structure from "
     "macro_watcher as a template."),
    ("G4-reflex-once-smoke", "G4: --once smoke for both reflexes",
     "test", "high",
     "Run: python tools/trading/market_intel/daemon.py --once news_poller; then --once "
     "news_aggregator. Verify status JSON shows last_run_at updated, total_successes +1, "
     "no circuit breaker trip."),
    ("G-gate", "G-gate: Phase G exit validation", "test", "high", GATE),

    # ───────── Phase H — INTaaS + cascade fallback ─────────
    ("H1-perspective-news-direction", "H1: perspective_scorer.py — consume news.net_direction (~5 LOC)",
     "build", "medium",
     "Open tools/trading/analysis/perspective_scorer.py. In _extract_positive_signals (line "
     "~34), add: if news.get('net_direction') == 'bullish': positives.append(f\"Bullish news "
     "tone ({news.get('catalyst_count',0)} items)\"). Mirror in _extract_negative_signals. "
     "This makes the existing INTaaS pillar react to direction, not just catalyst count."),
    ("H2-perspective-cluster-weight", "H2: perspective_scorer.py — cluster-level weighting (~15 LOC)",
     "build", "medium",
     "Extend perspective_scorer.py: new helper _load_active_clusters(ticker) queries "
     "ad_news_clusters for active (status∈{cluster, regime}) rows whose scenario_impacts include "
     "this ticker. For each, inject a pseudo-signal of magnitude proportional to "
     "cumulative_score into positives/negatives based on cluster direction. This is what "
     "makes aggregation flow into bull/bear conviction automatically."),
    ("H3-cascade-fallback", "H3: scenario_matcher — cascade_engine fallback for trigger keys",
     "build", "medium",
     "Extend match_and_run(news_id): after scenario_engine.run_scenario returns, check if the "
     "scenario_key has a corresponding CASCADE_TRIGGER_KEY in cascade_engine.py. If yes, also "
     "call cascade_engine.propagate_trigger(trigger_key, news_id) to populate KG-level "
     "propagation. Attach cascade run_id to the ad_news_scenario_links row (add column if "
     "needed — or JSON-embed in match_reason)."),
    ("H4-analysis-pulls-news", "H4: runner.py pulls fresh news for a ticker analysis",
     "build", "high",
     "In tools/trading/runner.py, where the news analyst is invoked per ticker, change input "
     "from static/mock headlines to: SELECT * FROM ad_news_items WHERE mentioned_tickers "
     "LIKE '%TICKER%' AND published_at > NOW()-INTERVAL 7d ORDER BY published_at DESC LIMIT 10. "
     "Pass those titles to analyze_news(). This completes the INTaaS auto-flow."),
    ("H5-intaas-regression", "H5: Verify perspective scores shift after news + cluster inject",
     "test", "high",
     "Integration test tests/trading/test_news_intaas_integration.py: (1) run analysis on a "
     "ticker with no news → capture baseline net_score; (2) insert 3 bearish news items + a "
     "bearish cluster; (3) re-run analysis → assert net_score decreased by ≥ 5 points. Proves "
     "news signal flows into INTaaS pillar."),
    ("H-gate", "H-gate: Phase H exit validation", "test", "high", GATE),

    # ───────── Phase I — Post-impl ─────────
    ("I1-feature-doc", "I1: docs/features/phase-news-fathomdesk.md",
     "chore", "high",
     "Write feature doc per CLAUDE.md guardrail. Cover: (a) why /news (manual gap: nothing "
     "feeds scenarios automatically); (b) RSS-only choice rationale (free, air-gap friendly); "
     "(c) reuse of scenario_impacts (no new projection math); (d) aggregation layer — "
     "emergent regime detection with rule-based reproducibility; (e) INTaaS integration via "
     "perspective_scorer. Screenshots to playwright/screenshots/news_*.png per CLAUDE.md."),
    ("I2-registration-checklist", "I2: 8-point registration checklist sweep",
     "chore", "high",
     "Walk CLAUDE.md 8-point new-tool checklist for every module shipped in A..H: "
     "(1) tools/manifest/fathomdesk-trading-engine.md — add rss_ingestor, classifier, "
     "scenario_matcher, aggregator, news/db; (2) docs/reference/commands.md — CLI examples; "
     "(3) args/security_gates.yaml — add gate for regime-level cluster promotion (warning); "
     "(4) MCP registry + gap_handlers if news is queryable via MCP; (5) pre_tool_use.py done "
     "in A4; (6) conftest.py done in A5; (7) companion sync in I5; (8) coherence --all --gate "
     "in I3."),
    ("I3-full-codelens-coherence", "I3: Full CodeLens + coherence across every changed file",
     "test", "high",
     "Run tools/code_intelligence/codelens.py --all --json and tools/workflow/coherence_checker.py "
     "--all --fix --gate across the full repo. Attach JSON to the feature doc. Fix any new "
     "warnings surfaced by adn- files."),
    ("I4-full-e2e-regression", "I4: Full-dashboard E2E + regression pytest suite",
     "test", "critical",
     "Run tools/testing/e2e_full_dashboard.py + pytest tests/ -x --timeout=120 "
     "--ignore=tests/e2e_selenium + pytest tests/e2e_selenium/. Zero new failures vs. pre-A "
     "baseline. Per memory feedback_mandatory_testing_workflow.md this is the SIGN-OFF gate."),
    ("I5-companion-sync-final", "I5: Final companion sync + git status clean check",
     "chore", "high",
     "Run tools/dx/companion.py --sync --write --json to propagate changes to all AI platform "
     "configs. git status: no unexpected files; no marketplace/child-app artifacts committed "
     "(memory feedback_no_marketplace_commit.md). Final rubric: plan done only when I5 passes."),
]


def enqueue() -> dict:
    conn = get_connection(db_path=str(DB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []
    skipped: list[str] = []
    parent: str | None = None
    try:
        cur = conn.cursor()
        for suffix, title, task_type, priority, description in SUBTASKS:
            tid = PREFIX + suffix
            existing = cur.execute(
                "SELECT id, status FROM kanban_tasks WHERE id = %s", (tid,),
            ).fetchone()
            if existing:
                skipped.append(tid)
                parent = tid
                continue
            cur.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "depends_on_task_id, dispatch_source, created_at, updated_at, scheduled_at) "
                "VALUES (%s, %s, %s, %s, %s, 'scheduled', %s, 'manual_plan', %s, %s, %s)",
                (tid, title, description, task_type, priority, parent, now, now, now),
            )
            inserted.append(tid)
            parent = tid
        conn.commit()
    finally:
        conn.close()
    return {
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_subtasks": len(SUBTASKS),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
