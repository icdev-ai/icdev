# CUI // SP-CTI
"""Seed script — FathomDesk TradingAgents Adaptation (fdt-).

Phases A–E from the TradingAgents engine run (2026-04-30).
Reuses: data_gateway.py, backtester.py, signal_generator.py, broker_adapter.py,
        constants.py, blueprint.py, INTaaS reasoner, existing trap detector,
        tax-lot FIFO, Trading Oracle, ICDEV LLM router.

Run:
    cd /path/to/ICDev && PYTHONPATH=. python tools/kanban/seed_fdt_tradingagents.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()
SCHEDULED = NOW  # all tasks are immediately schedulable if deps are met


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS: list[dict] = [
    # ── PHASE A: Quick Fixes ────────────────────────────────────────────────
    {
        "id": "fdt-qfix-01",
        "title": "Rate-limit retry + exponential backoff for yfinance news in data_gateway.py",
        "description": (
            "TradingAgents v0.2.3 fixed yfinance news endpoint rate-limiting. "
            "Replicate in tools/fathomdesk/data_gateway.py: wrap the yfinance "
            "news fetch call with a retry loop (max 3 attempts, backoff 2^n seconds). "
            "Reuse the existing _yfinance guard pattern already in data_gateway.py. "
            "No new file needed — edit the fetch_news() method only. "
            "Add a RATE_LIMIT_RETRY_MAX constant to tools/fathomdesk/constants.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "fdt-qfix-02",
        "title": "Audit and fix date-aware data fetching — unified date anchor in data_gateway.py",
        "description": (
            "TradingAgents v0.2.3 fixed inconsistent date handling across OHLCV, "
            "fundamentals, and news. Audit tools/fathomdesk/data_gateway.py: "
            "ensure fetch_ohlcv(), fetch_fundamentals(), and fetch_news() all accept "
            "and honour the same `as_of_date` parameter so callers can request "
            "data at a consistent point in time. Add as_of_date=None default to each "
            "method; pass it through to yfinance period/start args. "
            "No new file — edit data_gateway.py only."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-qfix-01",
    },
    {
        "id": "fdt-qfix-03",
        "title": "Fix creative_config.yaml — add 'trading' as alias for 'trading platforms' domain",
        "description": (
            "Creative engine domain discovery failed because the key 'trading' does not "
            "match the configured key 'trading platforms'. "
            "Edit args/creative_config.yaml: under the trading domain section add "
            "an `aliases` list containing 'trading'. "
            "Update creative_engine.py domain resolution to check aliases when the "
            "primary key doesn't match — single elif block, no new file."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": None,
    },
    {
        "id": "fdt-qfix-04",
        "title": "V&V Phase A — tests + coherence + companion sync",
        "description": (
            "Validation gate for Phase A quick fixes. "
            "Run: pytest tests/ -k fathomdesk -v; "
            "python tools/workflow/coherence_checker.py --all --fix --gate; "
            "python tools/dx/companion.py --sync --write --json. "
            "Confirm: (1) rate-limit retry is tested; (2) date-aware fetch is tested; "
            "(3) creative domain alias resolves correctly. "
            "No code changes — gate task only."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-qfix-02",
    },
    # ── PHASE B: LLM Factory + International Tickers ────────────────────────
    {
        "id": "fdt-llmfac-01",
        "title": "Create tools/fathomdesk/llm_factory.py — per-agent LLM provider override",
        "description": (
            "Thin wrapper over the existing ICDEV LLM router that allows each FathomDesk "
            "analyst agent to declare its preferred provider. "
            "Expose: get_llm(agent_name: str) -> callable. "
            "Resolution order: (1) args/fathomdesk_config.yaml agent_llm_overrides[agent_name]; "
            "(2) LLMRouter default. "
            "Reuse: from tools.llm.router import LLMRouter. "
            "New file only — no edits to LLMRouter itself."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-qfix-04",
    },
    {
        "id": "fdt-llmfac-02",
        "title": "Add agent_llm_overrides section to args/fathomdesk_config.yaml",
        "description": (
            "Add config block to args/fathomdesk_config.yaml (create file if absent): "
            "  agent_llm_overrides:\n"
            "    fundamentals_agent: claude-sonnet-4-6\n"
            "    technical_agent: ollama/llama3\n"
            "    sentiment_agent: ollama/llama3\n"
            "    macro_agent: claude-sonnet-4-6\n"
            "    debate_engine: claude-opus-4-7\n"
            "    risk_manager: claude-opus-4-7\n"
            "Document each choice in inline comments. "
            "Ollama agents are air-gap safe; Opus agents handle high-stakes reasoning."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-llmfac-01",
    },
    {
        "id": "fdt-intltick-01",
        "title": "Add exchange-qualified ticker support to data_gateway.py (CNC.TO, 7203.T, 0700.HK)",
        "description": (
            "TradingAgents v0.2.3 added exchange-qualified tickers. "
            "Edit tools/fathomdesk/data_gateway.py: "
            "In fetch_ohlcv() and fetch_news(), detect exchange-qualified format "
            "(TICKER.EXCHANGE) and route to yfinance directly instead of Alpaca "
            "(Alpaca covers US equities only). "
            "Add _is_intl_ticker(symbol: str) -> bool helper in the same file. "
            "Reuse existing _yfinance guard. No new file."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-qfix-04",
    },
    {
        "id": "fdt-intltick-02",
        "title": "Extend FathomDesk universe YAML with TSX, Tokyo, HK tickers",
        "description": (
            "Extend args/fathomdesk_universe.yaml (or equivalent ticker list) with "
            "a new international_tickers section. "
            "Add representative tickers: TSX (CNC.TO, SU.TO, RY.TO), "
            "Tokyo (7203.T, 6758.T, 9984.T), HK (0700.HK, 9988.HK, 0005.HK). "
            "Tag each with exchange, currency, timezone. "
            "FathomDesk KG coverage report should include these. "
            "No Python changes — config only."
        ),
        "task_type": "chore",
        "priority": "low",
        "status": "scheduled",
        "depends_on_task_id": "fdt-intltick-01",
    },
    {
        "id": "fdt-llmfac-03",
        "title": "Manifest entry + companion sync — Phase B (LLM factory + intl tickers)",
        "description": (
            "Register new/modified files from Phase B in tools/manifest/ shard. "
            "Run: python tools/dx/companion.py --sync --write --json. "
            "Check no missing entries in tools/manifest/dashboard.md or fathomdesk shard. "
            "Gate: companion sync must exit 0."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-llmfac-02",
    },
    {
        "id": "fdt-llmfac-04",
        "title": "V&V Phase B — tests + coherence + companion sync",
        "description": (
            "Validation gate for Phase B. "
            "Run: pytest tests/ -k 'llm_factory or intl_ticker' -v; "
            "python tools/workflow/coherence_checker.py --all --fix --gate; "
            "python tools/dx/companion.py --sync --write --json. "
            "Confirm: (1) get_llm('fundamentals_agent') returns claude-sonnet-4-6; "
            "(2) _is_intl_ticker('CNC.TO') returns True; "
            "(3) fathomdesk_config.yaml loads without error."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-llmfac-03",
    },
    # ── PHASE C: Multi-Agent Analyst Panel + Decision Audit Trail ──────────
    {
        "id": "fdt-magent-01",
        "title": "Create tools/fathomdesk/agents/base_analyst.py — BaseAnalystAgent ABC",
        "description": (
            "Abstract base class for all FathomDesk analyst agents. "
            "Fields: agent_name: str, ticker: str, as_of_date: str. "
            "Abstract method: analyze() -> dict (returns structured lens output). "
            "In __init__: call llm_factory.get_llm(self.agent_name) and store as self._llm. "
            "Reuse: tools/fathomdesk/llm_factory.py (Phase B). "
            "New file: tools/fathomdesk/agents/__init__.py + base_analyst.py."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-llmfac-04",
    },
    {
        "id": "fdt-magent-02",
        "title": "Create tools/fathomdesk/agents/fundamentals_agent.py",
        "description": (
            "Concrete analyst agent: fetches fundamentals via data_gateway.fetch_fundamentals() "
            "and prompts self._llm for a structured fundamental analysis. "
            "Output schema: {score: float, signals: list[str], reasoning: str, confidence: float}. "
            "Reuse: FathomDeskDataGateway (existing), BaseAnalystAgent (fdt-magent-01). "
            "New file only."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-01",
    },
    {
        "id": "fdt-magent-03",
        "title": "Create tools/fathomdesk/agents/technical_agent.py",
        "description": (
            "Concrete analyst agent: reads existing signal_generator outputs "
            "(load_thresholds + filtered signals) and prompts self._llm for technical analysis. "
            "Reuse: tools/fathomdesk/signal_generator.py load_thresholds(), "
            "BaseAnalystAgent (fdt-magent-01). "
            "New file only — do not duplicate signal logic."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-01",
    },
    {
        "id": "fdt-magent-04",
        "title": "Create tools/fathomdesk/agents/sentiment_agent.py",
        "description": (
            "Concrete analyst agent: fetches news via data_gateway.fetch_news() "
            "(which now has rate-limit retry from Phase A) and feeds headlines to self._llm "
            "for sentiment scoring. If INTaaS reasoner is available, use its pre-computed "
            "sentiment score directly instead of re-running LLM. "
            "Reuse: FathomDeskDataGateway, BaseAnalystAgent."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-01",
    },
    {
        "id": "fdt-magent-05",
        "title": "Create tools/fathomdesk/agents/macro_agent.py",
        "description": (
            "Concrete analyst agent: reads macro regime signals (existing fathomdesk"
            "regime detector / liquidity trap flags from ad_trap_events) and prompts "
            "self._llm for macro context. "
            "Reuse: get_connection() to query ad_trap_events, BaseAnalystAgent. "
            "New file only."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-01",
    },
    {
        "id": "fdt-magent-06",
        "title": "Create tools/fathomdesk/agents/debate_engine.py — Bull/Bear debate + arbitration",
        "description": (
            "Orchestrates the multi-agent analyst panel. "
            "Steps: (1) run all 4 analyst agents concurrently (asyncio.gather or ThreadPoolExecutor); "
            "(2) synthesize their outputs into a bull_case and bear_case summary; "
            "(3) spawn two opposing LLM calls (bull advocate, bear advocate) using Opus model; "
            "(4) score each position; return structured debate result. "
            "Reuse: all 4 agents (magent-02..05), llm_factory. "
            "New file only."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-05",
    },
    {
        "id": "fdt-magent-07",
        "title": "Create tools/fathomdesk/agents/risk_manager.py — arbitrate + enforce position constraints",
        "description": (
            "Reads debate_engine output and applies portfolio-level risk constraints: "
            "(1) check existing liquidity trap regime (ad_trap_events); "
            "(2) check tax-lot FIFO wash-sale flag before sizing short recommendations; "
            "(3) apply signal_thresholds.yaml min_confidence gate; "
            "(4) emit final recommendation: {direction, confidence, size_modifier, reasoning}. "
            "Reuse: get_connection(), existing preflight Gate 0 logic in options/preflight.py, "
            "signal_generator load_thresholds()."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-06",
    },
    {
        "id": "fdt-magent-08",
        "title": "Wire analyst panel into existing Trading Oracle — replace heuristic lens outputs",
        "description": (
            "Locate the Trading Oracle lens scoring code (tools/trading/ or fathomdesk). "
            "Replace the 4 heuristic lens computations with calls to debate_engine.py: "
            "invoke DebateEngine(ticker, as_of_date).run() and map its output to the "
            "existing lens field names so downstream Oracle consumers are unchanged. "
            "Add a feature flag ANALYST_PANEL_ENABLED in fathomdesk_config.yaml: "
            "when False, fall back to original heuristics. "
            "Reuse: existing Oracle route, debate_engine (magent-06)."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-07",
    },
    {
        "id": "fdt-decaudit-01",
        "title": "DB migration — create ad_decision_audit table (SEC Rule 17a-4)",
        "description": (
            "Create migration file tools/db/migrations/NNN_ad_decision_audit.sql. "
            "Table: ad_decision_audit (id TEXT PK, ticker TEXT, as_of_date TEXT, "
            "fundamentals_score REAL, technical_score REAL, sentiment_score REAL, "
            "macro_score REAL, bull_confidence REAL, bear_confidence REAL, "
            "final_direction TEXT, final_confidence REAL, reasoning TEXT, "
            "venue TEXT, instrument_type TEXT, mifid_timestamp TEXT, "
            "created_at TIMESTAMPTZ DEFAULT now()). "
            "Append-only (no UPDATE/DELETE). Add to APPEND_ONLY_TABLES in pre_tool_use.py. "
            "Add to MINIMAL_ICDEV_SCHEMA in tests/conftest.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-llmfac-04",
    },
    {
        "id": "fdt-decaudit-02",
        "title": "Wire ad_decision_audit writes into debate_engine.py and risk_manager.py",
        "description": (
            "After risk_manager.py emits its final recommendation, write one row to "
            "ad_decision_audit with all 4 agent scores, bull/bear confidence, "
            "final direction, venue (exchange from intl ticker support), "
            "and mifid_timestamp. "
            "Use get_connection() (never sqlite3.connect()). "
            "Reuse: existing _audit() helper pattern from other FathomDesk modules."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-07",
    },
    {
        "id": "fdt-decaudit-03",
        "title": "Add CFTC/MiFID II metadata fields — venue, instrument_type, mifid_timestamp to audit write",
        "description": (
            "Extend the ad_decision_audit write in fdt-decaudit-02: "
            "populate venue from the ticker's exchange (from intl ticker config), "
            "instrument_type from STRATEGY_TYPES (reuse constants.py), "
            "and mifid_timestamp as ISO-8601 UTC (datetime.now(timezone.utc)). "
            "These three fields satisfy CFTC recordkeeping (3 regulatory mappings) "
            "and MiFID II timestamp requirements (1 mapping) from the research scan. "
            "No schema change needed — fields already exist from fdt-decaudit-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-decaudit-02",
    },
    {
        "id": "fdt-magent-09",
        "title": "Manifest + companion sync — Phase C (analyst panel + audit trail)",
        "description": (
            "Register all new files from Phase C in the appropriate tools/manifest/ shard. "
            "Add ad_decision_audit to docs/reference/databases.md. "
            "Run: python tools/dx/companion.py --sync --write --json. "
            "Gate: companion sync exits 0."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-decaudit-03",
    },
    {
        "id": "fdt-magent-10",
        "title": "V&V Phase C — tests + E2E + coherence + companion sync",
        "description": (
            "Validation gate for Phase C. "
            "Run: pytest tests/ -k 'analyst or debate or risk_manager or decaudit' -v; "
            "python tools/workflow/coherence_checker.py --all --fix --gate; "
            "python tools/dx/companion.py --sync --write --json. "
            "Confirm: (1) DebateEngine returns structured output; "
            "(2) RiskManager applies Gate 0 floors; "
            "(3) ad_decision_audit has a row after a full run; "
            "(4) ANALYST_PANEL_ENABLED=False falls back to heuristics without error."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-09",
    },
    # ── PHASE D: Backtesting Engine + News Sentiment Signal ────────────────
    {
        "id": "fdt-bte-01",
        "title": "Extend backtester.py — event-driven OHLCV replay + position tracker + FIFO P&L",
        "description": (
            "tools/fathomdesk/backtester.py currently only computes precision/recall/F1 "
            "on a pre-labelled signal list. Extend (do not replace) it: "
            "Add BacktestSession class with run(ticker, strategy_id, start, end) method: "
            "(1) fetch historical OHLCV via data_gateway.fetch_ohlcv(as_of_date); "
            "(2) replay signals bar-by-bar; (3) track positions with FIFO lot accounting "
            "(reuse tax-lot logic already in FathomDesk); (4) compute realized P&L per close. "
            "Keep existing score() function unchanged."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-10",
    },
    {
        "id": "fdt-bte-02",
        "title": "Add Sharpe, Calmar, and max-drawdown metrics to BacktestSession",
        "description": (
            "Extend BacktestSession.run() to compute and return: "
            "sharpe_ratio (annualized, risk-free rate from fathomdesk_config.yaml), "
            "calmar_ratio (annualized return / max_drawdown_pct), "
            "max_drawdown_pct (peak-to-trough on equity curve). "
            "Populate BacktestResult dataclass (reuse existing from constants.py). "
            "No new file — extend backtester.py only."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-bte-01",
    },
    {
        "id": "fdt-bte-03",
        "title": "DB migration — create ad_backtest_runs table (append-only)",
        "description": (
            "Create migration tools/db/migrations/NNN_ad_backtest_runs.sql. "
            "Table: ad_backtest_runs (id TEXT PK, ticker TEXT, strategy_id TEXT, "
            "backtest_start TEXT, backtest_end TEXT, sharpe_ratio REAL, "
            "calmar_ratio REAL, max_drawdown_pct REAL, win_rate REAL, "
            "trade_count INT, triggered_by TEXT, created_at TIMESTAMPTZ DEFAULT now()). "
            "Append-only. Add to APPEND_ONLY_TABLES in pre_tool_use.py. "
            "Add to MINIMAL_ICDEV_SCHEMA in tests/conftest.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-10",
    },
    {
        "id": "fdt-bte-04",
        "title": "Wire BacktestSession into FathomDesk strategy coach page",
        "description": (
            "Add a /fathomdesk/backtest route to tools/fathomdesk/blueprint.py: "
            "POST {ticker, strategy_id, start, end} → runs BacktestSession, "
            "writes to ad_backtest_runs, returns BacktestResult as JSON. "
            "Reuse: existing fathomdesk_api Blueprint, BacktestSession (bte-01/02), "
            "ad_backtest_runs table (bte-03). "
            "No new template unless a strategy coach page already exists — if so, "
            "add a Backtest tab to the existing page only."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-bte-02",
    },
    {
        "id": "fdt-newssig-01",
        "title": "Add sentiment_weight field to Oracle lens output schema",
        "description": (
            "Locate the Trading Oracle lens output dict (tools/trading/ or fathomdesk). "
            "Add sentiment_weight: float (0.0–1.0) to the existing lens output schema "
            "in the appropriate constants or dataclass. "
            "Default: 0.5 (neutral) so existing callers are unaffected. "
            "No logic change — schema addition only."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-10",
    },
    {
        "id": "fdt-newssig-02",
        "title": "Wire news sentiment score into Trading Oracle confidence band",
        "description": (
            "In the Trading Oracle aggregation step: "
            "read SentimentAgent score (from analyst panel output or INTaaS reasoner "
            "pre-computed value), compute sentiment_weight = sigmoid(score - 0.5), "
            "multiply final Oracle confidence by (0.7 + 0.3 * sentiment_weight). "
            "Reuse: sentiment_agent.py (magent-04), existing Oracle aggregation logic. "
            "No new file — edit Oracle aggregation only."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-newssig-01",
    },
    {
        "id": "fdt-newssig-03",
        "title": "Wire sentiment weight into existing bull/bear trap detector",
        "description": (
            "In tools/trading/trap_detector.py (or equivalent): "
            "read sentiment_weight from Oracle lens output; "
            "if sentiment_weight < 0.4 AND price near resistance → elevate trap "
            "probability by 0.15 (additive). "
            "This reproduces the Phase 7.10 trap detector's news-signal integration "
            "with the new per-agent sentiment score. "
            "Reuse: existing trap_detector logic, ad_trap_events write path."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-newssig-02",
    },
    {
        "id": "fdt-bte-05",
        "title": "Manifest + companion sync — Phase D (backtesting + news sentiment)",
        "description": (
            "Register new/modified files from Phase D in tools/manifest/ shards. "
            "Add ad_backtest_runs to docs/reference/databases.md. "
            "Run: python tools/dx/companion.py --sync --write --json. "
            "Gate: exits 0."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-bte-04",
    },
    {
        "id": "fdt-bte-06",
        "title": "V&V Phase D — tests + coherence + companion sync",
        "description": (
            "Validation gate for Phase D. "
            "Run: pytest tests/ -k 'backtest or newssig or sentiment_weight' -v; "
            "python tools/workflow/coherence_checker.py --all --fix --gate; "
            "python tools/dx/companion.py --sync --write --json. "
            "Confirm: (1) BacktestSession.run('SPY','covered_call','2024-01-01','2024-06-01') "
            "returns BacktestResult with sharpe > -99; "
            "(2) ad_backtest_runs has a row; "
            "(3) sentinel_weight modulates Oracle confidence; "
            "(4) trap probability elevated when sentiment < 0.4."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-bte-05",
    },
    # ── PHASE E: Factor Modeling + DeFi Coverage ────────────────────────────
    {
        "id": "fdt-factor-01",
        "title": "Create tools/fathomdesk/factor_model.py — Fama-French 3-factor exposure",
        "description": (
            "New module computing Fama-French 3-factor exposures for a given ticker. "
            "Factors: market (Rm-Rf), size (SMB), value (HML). "
            "Data: download FF3 daily CSV from Kenneth French data library (public, free). "
            "Compute: OLS regression of ticker excess returns on 3 factors over trailing 252 days. "
            "Return: {beta_market, beta_smb, beta_hml, r_squared, as_of_date}. "
            "Use only stdlib + numpy (already a dep). New file only."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-bte-06",
    },
    {
        "id": "fdt-factor-02",
        "title": "Add momentum and mean-reversion signals to signal_generator.py",
        "description": (
            "Extend tools/fathomdesk/signal_generator.py (do not replace load_thresholds): "
            "Add compute_momentum(prices: list[float], window: int = 20) -> float "
            "  (return (prices[-1] / prices[-window] - 1)). "
            "Add compute_mean_reversion(prices: list[float], window: int = 20) -> float "
            "  (z-score of last price vs rolling mean/std). "
            "No new file. Add to DEFAULTS dict in signal_generator."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-factor-01",
    },
    {
        "id": "fdt-factor-03",
        "title": "Wire factor signals into TechnicalAgent as supplementary data source",
        "description": (
            "In tools/fathomdesk/agents/technical_agent.py: "
            "before calling self._llm, compute factor_model.get_exposures(ticker) "
            "and momentum/mean-reversion from signal_generator. "
            "Include factor exposures in the prompt context so the LLM can reference "
            "systematic risk. "
            "Reuse: factor_model.py (fdt-factor-01), signal_generator (fdt-factor-02), "
            "TechnicalAgent (fdt-magent-03). Edit technical_agent.py only."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-factor-02",
    },
    {
        "id": "fdt-defi-01",
        "title": "Extend data_gateway.py — DeFi/on-chain asset coverage via public APIs",
        "description": (
            "Add fetch_defi_yield(protocol: str) -> dict to FathomDeskDataGateway. "
            "Sources (all free/public, no auth): "
            "  DefiLlama APY API (https://yields.llama.fi/pools) for protocol TVL and APY; "
            "  Coingecko free tier for on-chain token price. "
            "Target protocols: Aave, Compound, Uniswap, Jupiter (Solana). "
            "Graceful degradation: if offline, return empty dict without raising. "
            "Reuse: existing urlopen pattern from data_gateway.py. No new file."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "scheduled",
        "depends_on_task_id": "fdt-bte-06",
    },
    {
        "id": "fdt-defi-02",
        "title": "Add DeFi protocols to FathomDesk universe YAML",
        "description": (
            "Extend args/fathomdesk_universe.yaml with a defi_protocols section. "
            "Include: Aave, Compound, Uniswap, Jupiter. "
            "Fields per entry: protocol_id, chain, coingecko_id, defillama_pool_id, "
            "category (lending/dex/yield). "
            "Tag with liquidity_risk: high. "
            "Config only — no Python changes."
        ),
        "task_type": "chore",
        "priority": "low",
        "status": "scheduled",
        "depends_on_task_id": "fdt-defi-01",
    },
    {
        "id": "fdt-factor-04",
        "title": "Manifest + companion sync — Phase E (factor modeling + DeFi coverage)",
        "description": (
            "Register new/modified files from Phase E in tools/manifest/ shards. "
            "Run: python tools/dx/companion.py --sync --write --json. "
            "Gate: exits 0."
        ),
        "task_type": "chore",
        "priority": "low",
        "status": "scheduled",
        "depends_on_task_id": "fdt-factor-03",
    },
    {
        "id": "fdt-factor-05",
        "title": "V&V Phase E — tests + coherence + companion sync",
        "description": (
            "Validation gate for Phase E. "
            "Run: pytest tests/ -k 'factor_model or momentum or defi' -v; "
            "python tools/workflow/coherence_checker.py --all --fix --gate; "
            "python tools/dx/companion.py --sync --write --json. "
            "Confirm: (1) factor_model.get_exposures('AAPL') returns dict with beta_market; "
            "(2) compute_momentum([...]) returns float; "
            "(3) TechnicalAgent prompt includes factor context; "
            "(4) fetch_defi_yield('aave') returns dict (may be empty if offline)."
        ),
        "task_type": "test",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-factor-04",
    },
]

# Multi-parent deps (kanban_task_deps junction table)
# Format: (task_id, depends_on_id)
EXTRA_DEPS: list[tuple[str, str]] = [
    # fdt-qfix-04 V&V also waits for qfix-03 (config fix, parallel track)
    ("fdt-qfix-04", "fdt-qfix-03"),
    # fdt-llmfac-03 sync waits for BOTH llmfac-02 and intltick-02
    ("fdt-llmfac-03", "fdt-intltick-02"),
    # fdt-magent-06 debate waits for all 4 agents (02,03,04,05)
    ("fdt-magent-06", "fdt-magent-02"),
    ("fdt-magent-06", "fdt-magent-03"),
    ("fdt-magent-06", "fdt-magent-04"),
    # fdt-decaudit-02 write path waits for both magent-07 and decaudit-01
    ("fdt-decaudit-02", "fdt-decaudit-01"),
    # fdt-magent-09 sync waits for both magent-08 and decaudit-03
    ("fdt-magent-09", "fdt-magent-08"),
    # fdt-bte-04 coach wire waits for both bte-02 (metrics) and bte-03 (migration)
    ("fdt-bte-04", "fdt-bte-03"),
    # fdt-bte-05 sync waits for both bte-04 and newssig-03
    ("fdt-bte-05", "fdt-newssig-03"),
    # fdt-factor-04 sync waits for both factor-03 and defi-02
    ("fdt-factor-04", "fdt-defi-02"),
]


def seed() -> None:
    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    for t in TASKS:
        cur.execute("SELECT id FROM kanban_tasks WHERE id = %s", (t["id"],))
        if cur.fetchone():
            print(f"  SKIP (exists): {t['id']}")
            skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO kanban_tasks
              (id, title, description, task_type, priority, status,
               scheduled_at, depends_on_task_id, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                t["id"],
                t["title"],
                t["description"],
                t["task_type"],
                t["priority"],
                t["status"],
                NOW,  # scheduled_at — required for scheduler to pick up
                t["depends_on_task_id"],
                NOW,
                NOW,
            ),
        )
        print(f"  INSERT: {t['id']}")
        inserted += 1

    # Extra multi-parent deps
    dep_inserted = 0
    dep_skipped = 0
    for task_id, dep_id in EXTRA_DEPS:
        cur.execute(
            "SELECT task_id FROM kanban_task_deps WHERE task_id=%s AND depends_on_id=%s",
            (task_id, dep_id),
        )
        if cur.fetchone():
            dep_skipped += 1
            continue
        cur.execute(
            "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) VALUES (%s,%s,%s)",
            (task_id, dep_id, NOW),
        )
        dep_inserted += 1

    conn.commit()
    print(
        f"\nDone — tasks: {inserted} inserted / {skipped} skipped | "
        f"extra deps: {dep_inserted} inserted / {dep_skipped} skipped"
    )


if __name__ == "__main__":
    seed()
