#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed: Product Intelligence — 5 new engines + Universal Orchestrator.

Project key: pint   Task prefix: pint-
Epics:
  regfore  — Regulatory Foresight Engine         (migrations 066)
  usage    — Usage Analytics Engine              (migration 067)
  winloss  — Win/Loss Intelligence Engine        (migration 068)
  voc      — Voice of Customer Engine            (migration 069)
  techrad  — Technology Radar Engine             (migration 070)
  univ     — Universal Product Intel Orchestrator (migration 071)

Run: python tools/db/seeds/seed_pint_plan.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

INSERT_SQL = """
INSERT INTO kanban_tasks
    (id, title, description, task_type, priority, status,
     scheduled_at, executor_type, dispatch_source,
     depends_on_task_id, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO NOTHING
"""


def _task(
    task_id: str,
    title: str,
    description: str,
    task_type: str,
    priority: str = "medium",
    depends_on: str | None = None,
) -> tuple:
    return (
        task_id, title, description, task_type, priority,
        "scheduled", NOW, "claude_cli", "pint_plan",
        depends_on, NOW, NOW,
    )


TASKS: list[tuple] = [

    # ══════════════════════════════════════════════════════════════════════════
    # EPIC REGFORE — Regulatory Foresight Engine
    # Scans proposed legislation BEFORE it becomes mandate (congress.gov,
    # regulations.gov, NIST CSRC drafts, FedRAMP GitHub, DoD DARS).
    # ══════════════════════════════════════════════════════════════════════════
    _task(
        "pint-regfore-01",
        "Create args/regulatory_foresight_config.yaml",
        (
            "New file args/regulatory_foresight_config.yaml. Define sources: "
            "congress_gov (bill search API, keywords: cybersecurity, AI, federal IT, "
            "CMMC, FedRAMP, zero-trust, supply chain), "
            "regulations_gov (docket search, agencies: DoD, DHS, NIST, OMB, GSA), "
            "nist_csrc (draft SP endpoint, tags: 800-53, 800-171, 800-218, AI RMF), "
            "fedramp_github (repo: FedRAMP/fedramp-gov, track: issues + releases), "
            "dod_dars (Defense Acquisition Regulations System, open comment periods). "
            "Also define: score_weights (time_to_mandate 0.40, icdev_impact 0.35, "
            "blast_radius 0.25), auto_signal_threshold: 0.70, quiet_hours (matching "
            "research_config.yaml pattern), scan_interval_hours: 24. "
            "Only create this YAML — no Python in this task."
        ),
        task_type="build", priority="critical",
    ),
    _task(
        "pint-regfore-02",
        "Write migration 066_regulatory_foresight_signals/up.py",
        (
            "Create tools/db/migrations/066_regulatory_foresight_signals/up.py. "
            "Creates table regulatory_foresight_signals: "
            "id TEXT PK, source TEXT, doc_id TEXT, title TEXT, url TEXT, "
            "proposed_at TEXT, comment_deadline TEXT, estimated_mandate_date TEXT, "
            "affected_frameworks TEXT, icdev_impact_areas TEXT, "
            "time_to_mandate_days INTEGER, icdev_impact_score REAL, "
            "blast_radius_score REAL, composite_score REAL, "
            "status TEXT DEFAULT 'new', innovation_signal_id TEXT, "
            "scanned_at TEXT NOT NULL, classification TEXT DEFAULT 'CUI // SP-CTI'. "
            "Index: idx_regfore_score (composite_score DESC), idx_regfore_source. "
            "Guard: return skipped if table already exists. Follow migration 065 pattern."
        ),
        task_type="build", priority="critical",
        depends_on="pint-regfore-01",
    ),
    _task(
        "pint-regfore-03",
        "Create tools/regulatory_foresight/__init__.py and source_scanner.py",
        (
            "Create tools/regulatory_foresight/__init__.py (empty). "
            "Create tools/regulatory_foresight/source_scanner.py. "
            "SOURCE_SCANNERS dict (D352/D-RES-3 pattern) mapping source name → scan function. "
            "Each scan function: fetches proposed rules/bills via HTTP (degrades gracefully "
            "if offline), returns list of raw signal dicts with fields matching migration 066. "
            "Use urllib.request (no new deps). Air-gap safe: catch all network errors, "
            "return empty list with warning log. "
            "CLI: --scan --all --source <name> --json. Only touch these two new files."
        ),
        task_type="build", priority="high",
        depends_on="pint-regfore-02",
    ),
    _task(
        "pint-regfore-04",
        "Create tools/regulatory_foresight/impact_scorer.py",
        (
            "New file tools/regulatory_foresight/impact_scorer.py. "
            "ImpactScorer class with score(signal: dict) -> dict method. "
            "Computes three sub-scores deterministically (no LLM): "
            "time_to_mandate: 1.0 if <180 days, 0.7 if <365, 0.4 if <730, 0.1 if >730; "
            "icdev_impact: keyword overlap between affected_frameworks and ICDEV capability "
            "catalog keywords (reuse icdev_capability_catalog.json pattern from D-RES-7); "
            "blast_radius: count of ICDEV modules touched by the regulation keyword set. "
            "Weighted composite per config weights. Returns signal dict with scores appended. "
            "No DB access. No LLM. Only touch this new file."
        ),
        task_type="build", priority="high",
        depends_on="pint-regfore-01",
    ),
    _task(
        "pint-regfore-05",
        "Create tools/regulatory_foresight/foresight_engine.py (main entry point)",
        (
            "New file tools/regulatory_foresight/foresight_engine.py. "
            "ForesightEngine class with run() method: "
            "1. Load config from regulatory_foresight_config.yaml. "
            "2. Call source_scanner for each enabled source. "
            "3. Score each signal via ImpactScorer. "
            "4. Deduplicate by doc_id hash. "
            "5. INSERT new signals to regulatory_foresight_signals via get_connection(). "
            "6. Return {scanned, new, high_score_count, signals[]} JSON. "
            "CLI: --run, --status, --signals --min-score 0.5, --daemon, --json. "
            "Respects quiet_hours from config. Only touch this new file."
        ),
        task_type="build", priority="critical",
        depends_on="pint-regfore-03",
    ),
    _task(
        "pint-regfore-06",
        "Cross-register high-score regulatory signals to innovation_signals",
        (
            "In foresight_engine.py, after INSERT, for any signal with "
            "composite_score >= auto_signal_threshold (default 0.70): "
            "INSERT a corresponding row into innovation_signals with "
            "category='regulatory_foresight', source='regfore_engine', "
            "title=signal.title, score=composite_score, url=signal.url, "
            "details_json=json.dumps(signal). "
            "Store returned innovation_signal_id back on the foresight signal row. "
            "Use get_connection(), append-only. Only modify foresight_engine.py."
        ),
        task_type="build", priority="high",
        depends_on="pint-regfore-05",
    ),
    _task(
        "pint-regfore-07",
        "Write pytest tests for regulatory_foresight — 7 tests",
        (
            "Create tests/regulatory_foresight/test_foresight_engine.py. "
            "(1) source_scanner returns list on network error (graceful degrade); "
            "(2) ImpactScorer.score() with <180 day deadline → time_to_mandate=1.0; "
            "(3) ImpactScorer.score() with >730 days → time_to_mandate=0.1; "
            "(4) foresight_engine.run() with mock scanner inserts to regulatory_foresight_signals; "
            "(5) signal with composite >= 0.70 cross-registers to innovation_signals; "
            "(6) duplicate doc_id not inserted twice; "
            "(7) CLI --json output has 'scanned', 'new', 'high_score_count' keys. "
            "Use conftest MINIMAL_ICDEV_SCHEMA (needs regfore + innovation_signals tables). "
            "Mock HTTP calls with unittest.mock. 7 tests."
        ),
        task_type="test", priority="high",
        depends_on="pint-regfore-05",
    ),
    _task(
        "pint-regfore-08",
        "Manifest + companion sync chore — regfore epic",
        (
            "1. Create tools/manifest/regulatory-foresight-engine.md with entries for "
            "foresight_engine.py, source_scanner.py, impact_scorer.py: "
            "name, path, purpose, CLI flags, DB tables, air-gap behavior. "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore", priority="medium",
        depends_on="pint-regfore-06",
    ),
    _task(
        "pint-regfore-09",
        "V&V gate — regfore epic sign-off",
        (
            "1. python -m tools.regulatory_foresight.foresight_engine --run --json "
            "   (verify returns valid JSON, no exceptions, degrades if offline) "
            "2. pytest tests/regulatory_foresight/ -v  (all 7 pass) "
            "3. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "4. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "5. python tools/dx/companion.py --sync --json  (in sync) "
            "All five must pass before marking done."
        ),
        task_type="run", priority="critical",
        depends_on="pint-regfore-08",
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # EPIC USAGE — Usage Analytics Engine
    # Instruments dashboard routes + CLI invocations to surface feature adoption.
    # ══════════════════════════════════════════════════════════════════════════
    _task(
        "pint-usage-01",
        "Create args/usage_analytics_config.yaml",
        (
            "New file args/usage_analytics_config.yaml. Define: "
            "collection.enabled: true, collection.exclude_routes: ['/health', '/static', "
            "'/metrics'], collection.exclude_ips: ['127.0.0.1'] (internal health checks). "
            "aggregation.rollup_interval_hours: 24, aggregation.retention_days: 90. "
            "scoring.low_adoption_threshold: 0.05 (< 5% of sessions = low adoption), "
            "scoring.high_error_threshold: 0.10, scoring.signal_threshold: 0.65. "
            "quiet_hours matching innovation_config.yaml pattern. "
            "Only create this YAML."
        ),
        task_type="build", priority="high",
    ),
    _task(
        "pint-usage-02",
        "Write migration 067_usage_events/up.py",
        (
            "Create tools/db/migrations/067_usage_events/up.py. "
            "Table usage_events (append-only): "
            "id TEXT PK, route TEXT, method TEXT, status_code INTEGER, "
            "duration_ms INTEGER, skill_invoked TEXT, user_session TEXT, "
            "ip_hash TEXT, feature_tag TEXT, occurred_at TEXT NOT NULL, "
            "classification TEXT DEFAULT 'CUI // SP-CTI'. "
            "Table usage_aggregates (updatable — daily rollup): "
            "id TEXT PK, feature_tag TEXT, date TEXT, hit_count INTEGER, "
            "unique_sessions INTEGER, error_count INTEGER, avg_duration_ms REAL, "
            "adoption_score REAL, updated_at TEXT. "
            "Indexes: idx_usage_route, idx_usage_occurred_at, idx_agg_feature_date. "
            "Guard: skip if table exists. Follow migration 065 pattern."
        ),
        task_type="build", priority="critical",
        depends_on="pint-usage-01",
    ),
    _task(
        "pint-usage-03",
        "Create tools/usage_analytics/event_collector.py",
        (
            "New file tools/usage_analytics/event_collector.py. "
            "EventCollector class with record(route, method, status_code, duration_ms, "
            "skill_invoked=None, user_session=None, ip=None, feature_tag=None) -> str. "
            "Hashes IP with sha256 before storing (PII protection). "
            "Applies exclude_routes and exclude_ips from config before INSERT. "
            "Uses get_connection(), appends to usage_events only. "
            "Degrades silently (try/except, log warning) if table missing. "
            "Also: Flask middleware function track_request(app) that registers "
            "before_request / after_request hooks to auto-record all dashboard routes. "
            "Only touch this new file."
        ),
        task_type="build", priority="high",
        depends_on="pint-usage-02",
    ),
    _task(
        "pint-usage-04",
        "Create tools/usage_analytics/analytics_engine.py (aggregation + scoring + CLI)",
        (
            "New file tools/usage_analytics/analytics_engine.py. "
            "AnalyticsEngine class with: "
            "aggregate(days=1) — rolls up usage_events into usage_aggregates for each "
            "  feature_tag × date, computing hit_count, unique_sessions, error_count, "
            "  avg_duration_ms, adoption_score (hit_count / total_session_count). "
            "score() — returns list of FeatureAdoptionResult: feature_tag, adoption_score, "
            "  error_rate, avg_duration_ms, trend (rising/flat/declining). "
            "surface_signals() — for features with adoption_score < low_adoption_threshold "
            "  OR error_rate > high_error_threshold: cross-register to innovation_signals. "
            "CLI: --aggregate, --score, --surface-signals, --report, --daemon, --json. "
            "Only touch this new file."
        ),
        task_type="build", priority="high",
        depends_on="pint-usage-03",
    ),
    _task(
        "pint-usage-05",
        "Wire EventCollector middleware into tools/dashboard/app.py",
        (
            "In tools/dashboard/app.py, import track_request from "
            "tools.usage_analytics.event_collector. "
            "After create_app() assembles the Flask app, call track_request(app). "
            "Wrap in try/except ImportError so missing module degrades gracefully. "
            "Only touch app.py — do not modify any blueprint or route file."
        ),
        task_type="build", priority="high",
        depends_on="pint-usage-03",
    ),
    _task(
        "pint-usage-06",
        "Write pytest tests for usage analytics — 6 tests",
        (
            "Create tests/usage_analytics/test_analytics_engine.py. "
            "(1) EventCollector.record() inserts row to usage_events with hashed IP; "
            "(2) excluded route not recorded; "
            "(3) aggregate() produces usage_aggregates row with correct hit_count; "
            "(4) score() returns FeatureAdoptionResult with adoption_score field; "
            "(5) surface_signals() cross-registers low-adoption feature to innovation_signals; "
            "(6) EventCollector degrades silently when usage_events table missing. "
            "Use conftest MINIMAL_ICDEV_SCHEMA (add usage_events, usage_aggregates, "
            "innovation_signals tables). 6 tests."
        ),
        task_type="test", priority="high",
        depends_on="pint-usage-04",
    ),
    _task(
        "pint-usage-07",
        "Manifest + companion sync chore — usage epic",
        (
            "1. Create tools/manifest/usage-analytics-engine.md with entries for "
            "event_collector.py, analytics_engine.py: "
            "name, path, purpose, CLI flags, DB tables, middleware hook. "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore", priority="medium",
        depends_on="pint-usage-05",
    ),
    _task(
        "pint-usage-08",
        "V&V gate — usage epic sign-off",
        (
            "1. python -m tools.usage_analytics.analytics_engine --aggregate --score --json "
            "   (returns valid JSON, no exceptions) "
            "2. pytest tests/usage_analytics/ -v  (all 6 pass) "
            "3. Start dashboard, make 3 page requests, verify usage_events rows written "
            "4. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "5. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "All must pass before marking done."
        ),
        task_type="run", priority="critical",
        depends_on="pint-usage-07",
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # EPIC WINLOSS — Win/Loss Intelligence Engine
    # Correlates GovCon proposal outcomes with ICDEV feature presence.
    # Reads existing win_loss_records from Proposal Genesis.
    # ══════════════════════════════════════════════════════════════════════════
    _task(
        "pint-winloss-01",
        "Create args/win_loss_config.yaml",
        (
            "New file args/win_loss_config.yaml. Define: "
            "sources: [win_loss_records (Proposal Genesis DB table), "
            "debrief_uploads (tools/voc/uploads/ dir, shared with VOC engine)]. "
            "analysis.min_outcomes_for_pattern: 5 (need ≥5 wins or losses to trust a pattern), "
            "analysis.feature_correlation_threshold: 0.60, "
            "analysis.auto_signal_threshold: 0.70. "
            "output.top_k_features: 10. "
            "quiet_hours matching research_config.yaml pattern. "
            "Only create this YAML."
        ),
        task_type="build", priority="high",
    ),
    _task(
        "pint-winloss-02",
        "Write migration 068_win_loss_analysis/up.py",
        (
            "Create tools/db/migrations/068_win_loss_analysis/up.py. "
            "Table win_loss_analysis_runs (append-only): "
            "id TEXT PK, run_at TEXT, outcomes_analyzed INTEGER, "
            "patterns_found INTEGER, top_win_features TEXT, top_loss_features TEXT, "
            "result_json TEXT, classification TEXT DEFAULT 'CUI // SP-CTI'. "
            "Table win_loss_feature_impacts (append-only): "
            "id TEXT PK, run_id TEXT, feature_tag TEXT, win_count INTEGER, "
            "loss_count INTEGER, win_rate REAL, impact_score REAL, "
            "innovation_signal_id TEXT, analyzed_at TEXT. "
            "Index: idx_wl_feature_tag, idx_wl_run_id. "
            "Guard: skip if win_loss_records table missing (Proposal Genesis not installed). "
            "Follow migration 065 pattern."
        ),
        task_type="build", priority="critical",
        depends_on="pint-winloss-01",
    ),
    _task(
        "pint-winloss-03",
        "Create tools/win_loss/pattern_analyzer.py",
        (
            "New file tools/win_loss/pattern_analyzer.py. "
            "PatternAnalyzer class with analyze() -> list[FeatureImpact]. "
            "Reads win_loss_records from DB (outcome, opportunity_id, features_demonstrated). "
            "For each feature_tag found in winning vs losing proposals: "
            "computes win_rate = wins_with_feature / total_with_feature. "
            "impact_score = (win_rate - baseline_win_rate) normalized to [0,1]. "
            "Filters: min_outcomes_for_pattern from config. "
            "Returns sorted list of FeatureImpact(feature_tag, win_rate, "
            "impact_score, win_count, loss_count). "
            "Degrades gracefully if win_loss_records table missing (returns []). "
            "No LLM. Only touch this new file."
        ),
        task_type="build", priority="high",
        depends_on="pint-winloss-02",
    ),
    _task(
        "pint-winloss-04",
        "Create tools/win_loss/__init__.py and win_loss_engine.py (main entry)",
        (
            "Create tools/win_loss/__init__.py (empty). "
            "New file tools/win_loss/win_loss_engine.py. "
            "WinLossEngine with run(): "
            "1. Call PatternAnalyzer.analyze(). "
            "2. INSERT run record to win_loss_analysis_runs. "
            "3. INSERT FeatureImpact rows to win_loss_feature_impacts. "
            "4. For features with impact_score >= auto_signal_threshold: "
            "   cross-register to creative_feature_gaps (D360 pattern) with "
            "   gap_type='win_loss_signal', score=impact_score. "
            "5. Return {outcomes_analyzed, patterns_found, top_win_features} JSON. "
            "CLI: --run, --status, --top-features --limit 10, --daemon, --json. "
            "Only touch this new file and __init__.py."
        ),
        task_type="build", priority="critical",
        depends_on="pint-winloss-03",
    ),
    _task(
        "pint-winloss-05",
        "Write pytest tests for win_loss engine — 6 tests",
        (
            "Create tests/win_loss/test_win_loss_engine.py. "
            "(1) PatternAnalyzer returns [] when win_loss_records missing (graceful degrade); "
            "(2) PatternAnalyzer correctly computes win_rate from fixture data; "
            "(3) feature below min_outcomes_for_pattern excluded from results; "
            "(4) WinLossEngine.run() inserts to win_loss_analysis_runs; "
            "(5) high-impact feature cross-registers to creative_feature_gaps; "
            "(6) CLI --json output has 'patterns_found' key. "
            "Use conftest MINIMAL_ICDEV_SCHEMA (add win_loss_records stub, "
            "win_loss_analysis_runs, win_loss_feature_impacts, creative_feature_gaps). "
            "6 tests."
        ),
        task_type="test", priority="high",
        depends_on="pint-winloss-04",
    ),
    _task(
        "pint-winloss-06",
        "Manifest + companion sync chore — winloss epic",
        (
            "1. Create tools/manifest/win-loss-intelligence-engine.md with entries for "
            "pattern_analyzer.py, win_loss_engine.py: "
            "name, path, purpose, CLI flags, DB tables, Proposal Genesis dependency note. "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore", priority="medium",
        depends_on="pint-winloss-04",
    ),
    _task(
        "pint-winloss-07",
        "V&V gate — winloss epic sign-off",
        (
            "1. python -m tools.win_loss.win_loss_engine --run --json "
            "   (must return valid JSON; gracefully returns 0 patterns if no win_loss_records) "
            "2. pytest tests/win_loss/ -v  (all 6 pass) "
            "3. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "4. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "5. python tools/dx/companion.py --sync --json  (in sync) "
            "All must pass before marking done."
        ),
        task_type="run", priority="critical",
        depends_on="pint-winloss-06",
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # EPIC VOC — Voice of Customer Engine
    # JTBD synthesis from first-party support tickets, NPS verbatims, debriefs.
    # ══════════════════════════════════════════════════════════════════════════
    _task(
        "pint-voc-01",
        "Create args/voc_config.yaml",
        (
            "New file args/voc_config.yaml. Define: "
            "ingestion.upload_dir: tools/voc/uploads/ "
            "  (text/pdf/docx files dropped here are auto-processed), "
            "ingestion.supported_types: [txt, md, pdf, docx]. "
            "extraction.job_statement_keywords: [when I, I want, so that, need to, "
            "  frustrated by, unable to, wish it could, pain point, issue, bug]. "
            "scoring.frequency_weight: 0.40, severity_weight: 0.35, "
            "  strategic_fit_weight: 0.25, auto_signal_threshold: 0.65. "
            "quiet_hours matching research_config.yaml pattern. "
            "Only create this YAML."
        ),
        task_type="build", priority="high",
    ),
    _task(
        "pint-voc-02",
        "Write migration 069_voc_signals/up.py",
        (
            "Create tools/db/migrations/069_voc_signals/up.py. "
            "Table voc_documents (append-only): "
            "id TEXT PK, filename TEXT, source_type TEXT, ingested_at TEXT, "
            "word_count INTEGER, job_statement_count INTEGER, "
            "classification TEXT DEFAULT 'CUI // SP-CTI'. "
            "Table voc_job_statements (append-only): "
            "id TEXT PK, document_id TEXT, raw_text TEXT, "
            "job_category TEXT, frequency INTEGER, severity_score REAL, "
            "strategic_fit_score REAL, composite_score REAL, "
            "creative_gap_id TEXT, analyzed_at TEXT. "
            "Indexes: idx_voc_score, idx_voc_document_id, idx_voc_category. "
            "Follow migration 065 pattern."
        ),
        task_type="build", priority="critical",
        depends_on="pint-voc-01",
    ),
    _task(
        "pint-voc-03",
        "Create tools/voc/transcript_ingestor.py",
        (
            "New file tools/voc/transcript_ingestor.py. "
            "TranscriptIngestor class with ingest(file_path: Path) -> dict. "
            "Supports .txt and .md natively (read + split sentences). "
            "PDF: extract text via pdfminer.six if installed, else skip with warning. "
            "Docx: extract via python-docx if installed, else skip. "
            "After text extraction: scan for job statement keywords from config, "
            "extract surrounding sentence as raw job statement. "
            "INSERT to voc_documents + voc_job_statements via get_connection(). "
            "Returns {document_id, job_statement_count, skipped_reason}. "
            "CLI: --ingest <path>, --scan-dir <dir>, --json. "
            "Only touch this new file."
        ),
        task_type="build", priority="high",
        depends_on="pint-voc-02",
    ),
    _task(
        "pint-voc-04",
        "Create tools/voc/__init__.py and voc_engine.py (main entry)",
        (
            "Create tools/voc/__init__.py (empty). "
            "New file tools/voc/voc_engine.py. "
            "VOCEngine with run(): "
            "1. Scan upload_dir for unprocessed files, call TranscriptIngestor for each. "
            "2. Cluster voc_job_statements by job_category using keyword overlap. "
            "3. Score each cluster: composite = frequency*0.40 + severity*0.35 + fit*0.25. "
            "4. For clusters with composite >= auto_signal_threshold: "
            "   INSERT to creative_feature_gaps with gap_type='voc_signal', "
            "   store creative_gap_id back on voc_job_statements rows. "
            "5. Return {documents_processed, job_statements, signals_created} JSON. "
            "CLI: --run, --status, --top-jobs --limit 10, --daemon, --json. "
            "Only touch this new file and __init__.py."
        ),
        task_type="build", priority="critical",
        depends_on="pint-voc-03",
    ),
    _task(
        "pint-voc-05",
        "Write pytest tests for VOC engine — 6 tests",
        (
            "Create tests/voc/test_voc_engine.py. "
            "(1) TranscriptIngestor extracts job statements from .txt fixture; "
            "(2) PDF/docx gracefully skipped when deps missing; "
            "(3) VOCEngine clusters statements by keyword overlap; "
            "(4) cluster above threshold cross-registers to creative_feature_gaps; "
            "(5) empty upload_dir produces {documents_processed:0} with no exception; "
            "(6) CLI --json output has 'documents_processed', 'signals_created' keys. "
            "Use tmp_path for upload_dir and conftest MINIMAL_ICDEV_SCHEMA "
            "(add voc_documents, voc_job_statements, creative_feature_gaps). 6 tests."
        ),
        task_type="test", priority="high",
        depends_on="pint-voc-04",
    ),
    _task(
        "pint-voc-06",
        "Manifest + companion sync chore — voc epic",
        (
            "1. Create tools/manifest/voc-engine.md with entries for "
            "transcript_ingestor.py, voc_engine.py: "
            "name, path, purpose, CLI flags, DB tables, supported file types, "
            "optional dep note (pdfminer.six, python-docx). "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore", priority="medium",
        depends_on="pint-voc-04",
    ),
    _task(
        "pint-voc-07",
        "V&V gate — voc epic sign-off",
        (
            "1. python -m tools.voc.voc_engine --run --json "
            "   (must return valid JSON, 0 docs processed if upload_dir empty — no error) "
            "2. pytest tests/voc/ -v  (all 6 pass) "
            "3. Drop a .txt test transcript into tools/voc/uploads/, re-run --run, "
            "   verify voc_job_statements rows inserted. "
            "4. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "5. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "All must pass before marking done."
        ),
        task_type="run", priority="critical",
        depends_on="pint-voc-06",
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # EPIC TECHRAD — Technology Radar Engine
    # Tracks adopt/trial/assess/hold ring movements for frameworks and tools.
    # ══════════════════════════════════════════════════════════════════════════
    _task(
        "pint-techrad-01",
        "Create args/tech_radar_config.yaml and context/tech_radar/rings.yaml",
        (
            "New file args/tech_radar_config.yaml. Sources: "
            "thoughtworks_radar (public JSON feed URL), cncf_landscape "
            "(github.com/cncf/landscape JSON), github_trending (REST API, "
            "languages: python/go/rust/typescript). "
            "Scoring weights: ecosystem_maturity 0.30, icdev_fit 0.25, "
            "airgap_compat 0.25, il_compliance 0.20. "
            "ring_change_signal_threshold: 0.70. scan_interval_hours: 168 (weekly). "
            "New file context/tech_radar/rings.yaml. Define adopt/trial/assess/hold "
            "with entry criteria (maturity score range, compat requirements). "
            "Seed 10 initial entries (rspack, ast-grep, Hypothesis, uv, "
            "ruff, Playwright, Trivy, OpenTelemetry, Terraform, Ansible) "
            "with current ring classification. Only create these two files."
        ),
        task_type="build", priority="medium",
    ),
    _task(
        "pint-techrad-02",
        "Write migration 070_tech_radar_entries/up.py",
        (
            "Create tools/db/migrations/070_tech_radar_entries/up.py. "
            "Table tech_radar_entries (updatable — ring can change): "
            "id TEXT PK, name TEXT UNIQUE, category TEXT, "
            "current_ring TEXT CHECK(current_ring IN ('adopt','trial','assess','hold')), "
            "previous_ring TEXT, ecosystem_maturity REAL, icdev_fit REAL, "
            "airgap_compat REAL, il_compliance REAL, composite_score REAL, "
            "rationale TEXT, last_assessed TEXT, classification TEXT DEFAULT 'CUI // SP-CTI'. "
            "Table tech_radar_history (append-only): "
            "id TEXT PK, entry_id TEXT, from_ring TEXT, to_ring TEXT, "
            "composite_score REAL, innovation_signal_id TEXT, changed_at TEXT. "
            "Indexes: idx_techrad_ring, idx_techrad_history_entry. "
            "Seed 10 initial entries from context/tech_radar/rings.yaml. "
            "Follow migration 065 pattern."
        ),
        task_type="build", priority="critical",
        depends_on="pint-techrad-01",
    ),
    _task(
        "pint-techrad-03",
        "Create tools/tech_radar/source_scanner.py",
        (
            "New file tools/tech_radar/source_scanner.py. "
            "SOURCE_SCANNERS dict (D352 pattern) with scan functions for: "
            "thoughtworks_radar — fetch public JSON, parse blips into name+ring+description; "
            "cncf_landscape — fetch landscape.yml, extract maturity field; "
            "github_trending — fetch trending repos, compute adoption proxy from star velocity. "
            "All degrade gracefully (catch network errors, return []). "
            "Returns list of dicts: {name, source, ecosystem_maturity_signal, description}. "
            "CLI: --scan --all --source <name> --json. Air-gap safe. Only touch this new file."
        ),
        task_type="build", priority="medium",
        depends_on="pint-techrad-02",
    ),
    _task(
        "pint-techrad-04",
        "Create tools/tech_radar/__init__.py and radar_engine.py (main entry)",
        (
            "Create tools/tech_radar/__init__.py (empty). "
            "New file tools/tech_radar/radar_engine.py. "
            "RadarEngine with run(): "
            "1. Call SOURCE_SCANNERS for each enabled source. "
            "2. For each known tech_radar_entries row: recompute composite_score "
            "   using external signals + ICDEV fit keywords + airgap_compat flag. "
            "3. If composite_score warrants ring change: UPDATE tech_radar_entries, "
            "   INSERT history row to tech_radar_history (append-only). "
            "4. For ring movements to 'adopt': cross-register to innovation_signals. "
            "5. Return {entries_assessed, ring_changes, new_signals} JSON. "
            "CLI: --run, --status, --list --ring adopt, --daemon, --json. "
            "Only touch this new file and __init__.py."
        ),
        task_type="build", priority="high",
        depends_on="pint-techrad-03",
    ),
    _task(
        "pint-techrad-05",
        "Write pytest tests for tech radar — 6 tests",
        (
            "Create tests/tech_radar/test_radar_engine.py. "
            "(1) source_scanner returns [] on network error (graceful degrade); "
            "(2) RadarEngine.run() assesses entries and returns valid JSON; "
            "(3) ring movement from assess→adopt inserts history row; "
            "(4) ring movement to adopt cross-registers to innovation_signals; "
            "(5) no ring change if composite_score unchanged; "
            "(6) CLI --list --ring adopt returns only adopt entries. "
            "Use conftest MINIMAL_ICDEV_SCHEMA (add tech_radar_entries, "
            "tech_radar_history, innovation_signals). 6 tests."
        ),
        task_type="test", priority="high",
        depends_on="pint-techrad-04",
    ),
    _task(
        "pint-techrad-06",
        "Manifest + companion sync chore — techrad epic",
        (
            "1. Create tools/manifest/tech-radar-engine.md with entries for "
            "source_scanner.py, radar_engine.py: "
            "name, path, purpose, CLI flags, DB tables, ring definitions, "
            "seeded technologies list. "
            "2. python tools/dx/companion.py --sync --write --json "
            "3. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore", priority="medium",
        depends_on="pint-techrad-04",
    ),
    _task(
        "pint-techrad-07",
        "V&V gate — techrad epic sign-off",
        (
            "1. python -m tools.tech_radar.radar_engine --run --json "
            "   (returns valid JSON, 10 entries assessed, degrades if offline) "
            "2. pytest tests/tech_radar/ -v  (all 6 pass) "
            "3. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "4. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "5. python tools/dx/companion.py --sync --json  (in sync) "
            "All must pass before marking done."
        ),
        task_type="run", priority="critical",
        depends_on="pint-techrad-06",
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # EPIC UNIV — Universal Product Intelligence Orchestrator
    # Invokes all 8 engines in sequence, runs federation router,
    # stores consolidated run record. --run-all and --daemon modes.
    # ══════════════════════════════════════════════════════════════════════════
    _task(
        "pint-univ-01",
        "Create args/product_intel_config.yaml",
        (
            "New file args/product_intel_config.yaml. Define the engine registry: "
            "engines list with 8 entries, each: name, module, cli_flags, enabled bool, "
            "timeout_seconds, order (1–8). Order: "
            "1=innovation, 2=regulatory_foresight, 3=creative, 4=usage, "
            "5=win_loss, 6=voc, 7=tech_radar, 8=research. "
            "Also: federation.run_after_engines: true, "
            "federation.min_signal_score: 0.70. "
            "scheduling.run_interval_hours: 24, quiet_hours (matching research pattern). "
            "output.store_run_record: true, output.notify_on_signals: true. "
            "Only create this YAML."
        ),
        task_type="build", priority="critical",
    ),
    _task(
        "pint-univ-02",
        "Write migration 071_product_intel_runs/up.py",
        (
            "Create tools/db/migrations/071_product_intel_runs/up.py. "
            "Table product_intel_runs (append-only): "
            "id TEXT PK, started_at TEXT, completed_at TEXT, "
            "engines_run TEXT, engines_failed TEXT, "
            "total_signals INTEGER, total_gaps INTEGER, total_dossiers INTEGER, "
            "federation_routes INTEGER, "
            "result_json TEXT, status TEXT, "
            "classification TEXT DEFAULT 'CUI // SP-CTI'. "
            "Index: idx_pi_runs_started_at. "
            "Guard: skip if table exists. Follow migration 065 pattern."
        ),
        task_type="build", priority="critical",
        depends_on="pint-univ-01",
    ),
    _task(
        "pint-univ-03",
        "Create tools/product_intel/engine_registry.py",
        (
            "New file tools/product_intel/engine_registry.py. "
            "EngineRegistry class that loads args/product_intel_config.yaml "
            "and exposes: "
            "list_engines() -> list[EngineConfig]; "
            "get_engine(name) -> EngineConfig; "
            "invoke(engine_config, extra_args=[]) -> EngineResult "
            "  (subprocess call with --json, captures stdout/stderr, "
            "   parses JSON result, times execution, returns EngineResult "
            "   with name, status, duration_ms, signals_count, output dict). "
            "EngineResult dataclass: name, status (ok|failed|skipped), "
            "duration_ms, signals_count, output. "
            "invoke() never raises — catches all exceptions, returns status='failed'. "
            "Only touch this new file."
        ),
        task_type="build", priority="critical",
        depends_on="pint-univ-02",
    ),
    _task(
        "pint-univ-04",
        "Create tools/product_intel/__init__.py and orchestrator.py (main entry)",
        (
            "Create tools/product_intel/__init__.py (empty). "
            "New file tools/product_intel/orchestrator.py. "
            "ProductIntelOrchestrator with run_all(): "
            "1. Load EngineRegistry, iterate engines in order. "
            "2. For each enabled engine: call registry.invoke(), collect EngineResult. "
            "3. After all engines: call federation router "
            "   (python -m tools.autonomy.federation --route --json) if enabled. "
            "4. Aggregate totals (signals, gaps, dossiers, routes). "
            "5. INSERT run record to product_intel_runs via get_connection(). "
            "6. Return consolidated JSON: {run_id, engines_run[], total_signals, "
            "   total_gaps, federation_routes, duration_ms, status}. "
            "CLI: --run-all, --run-engine <name>, --status, --history, "
            "--daemon, --dry-run, --json. "
            "Only touch this new file and __init__.py."
        ),
        task_type="build", priority="critical",
        depends_on="pint-univ-03",
    ),
    _task(
        "pint-univ-05",
        "Add product_intel_runs to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py",
        (
            "In .claude/hooks/pre_tool_use.py, find APPEND_ONLY_TABLES list and add "
            "'product_intel_runs' to it. "
            "This blocks UPDATE/DELETE on the orchestrator run audit table. "
            "Only touch pre_tool_use.py."
        ),
        task_type="build", priority="high",
        depends_on="pint-univ-02",
    ),
    _task(
        "pint-univ-06",
        "Add product_intel_runs to MINIMAL_ICDEV_SCHEMA in tests/conftest.py",
        (
            "In tests/conftest.py, find MINIMAL_ICDEV_SCHEMA and add "
            "product_intel_runs CREATE TABLE IF NOT EXISTS statement "
            "(same DDL as migration 071, without FK constraints). "
            "Only touch conftest.py."
        ),
        task_type="build", priority="high",
        depends_on="pint-univ-02",
    ),
    _task(
        "pint-univ-07",
        "Write pytest tests for universal orchestrator — 7 tests",
        (
            "Create tests/product_intel/test_orchestrator.py. "
            "(1) EngineRegistry loads config and returns 8 engines; "
            "(2) registry.invoke() with mock subprocess returns EngineResult(status='ok'); "
            "(3) registry.invoke() never raises on subprocess failure (returns status='failed'); "
            "(4) orchestrator.run_all() inserts row to product_intel_runs; "
            "(5) orchestrator returns consolidated JSON with all required keys; "
            "(6) --dry-run mode does not INSERT to DB; "
            "(7) disabled engine in config is skipped (not invoked). "
            "Use conftest MINIMAL_ICDEV_SCHEMA (add product_intel_runs). "
            "Mock all subprocess.run calls. 7 tests."
        ),
        task_type="test", priority="high",
        depends_on="pint-univ-04",
    ),
    _task(
        "pint-univ-08",
        "Run all migrations 066–071 + manifest + companion sync chore — univ epic",
        (
            "1. python tools/db/migrate.py --up "
            "   (verify migrations 066, 067, 068, 069, 070, 071 all show 'applied') "
            "2. Create tools/manifest/universal-product-intel-orchestrator.md: "
            "   entries for orchestrator.py, engine_registry.py — name, path, purpose, "
            "   8-engine list, CLI flags, DB tables, federation router integration. "
            "3. python tools/dx/companion.py --sync --write --json "
            "4. python tools/workflow/coherence_checker.py --all --fix --gate "
            "Fix all coherence errors before marking done."
        ),
        task_type="chore", priority="high",
        depends_on="pint-univ-05",
    ),
    _task(
        "pint-univ-09",
        "V&V gate — univ epic and full pint project sign-off",
        (
            "1. python -m tools.product_intel.orchestrator --dry-run --json "
            "   (lists all 8 engines, no DB writes, no exceptions) "
            "2. python -m tools.product_intel.orchestrator --run-all --json "
            "   (all 8 engines invoked, product_intel_runs row written, "
            "   federation router called) "
            "3. pytest tests/product_intel/ -v  (all 7 pass) "
            "4. python tools/testing/e2e_runner.py --run-all  (no regressions) "
            "5. python tools/workflow/coherence_checker.py --all --gate  (clean) "
            "6. python tools/dx/companion.py --sync --json  (in sync) "
            "All six must pass — this is the full pint project sign-off."
        ),
        task_type="run", priority="critical",
        depends_on="pint-univ-08",
    ),
]


def main() -> None:
    conn = get_connection()
    cur = conn.cursor()

    inserted = skipped = 0
    for task in TASKS:
        cur.execute(INSERT_SQL, task)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    epics = {
        "regfore": "Regulatory Foresight",
        "usage":   "Usage Analytics",
        "winloss": "Win/Loss Intelligence",
        "voc":     "Voice of Customer",
        "techrad": "Technology Radar",
        "univ":    "Universal Orchestrator",
    }
    print(f"[seed_pint_plan] done — {inserted} inserted, {skipped} skipped")
    for key, label in epics.items():
        count = sum(1 for t in TASKS if t[0].startswith(f"pint-{key}"))
        ids = [t[0] for t in TASKS if t[0].startswith(f"pint-{key}")]
        print(f"  pint-{key:<10} {label:<30} {ids[0]} .. {ids[-1]}  ({count} tasks)")
    print()
    print("View at: http://localhost:5050/kanban")


if __name__ == "__main__":
    main()
