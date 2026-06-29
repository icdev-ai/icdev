"""FathomDesk database layer — persistent storage for the trading engine.

All tables use the ad_ prefix in the main ICDEV database (icdev.db or PostgreSQL).
Provides get_conn() and helpers for analysis runs, signals, debates, etc.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection, StorageConnection

# ---------------------------------------------------------------------------
# Table DDL (same as apps/fathomdesk/tools/trading/db_init.py)
# ---------------------------------------------------------------------------
_TABLES = [
    """CREATE TABLE IF NOT EXISTS ad_portfolios (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        name TEXT NOT NULL DEFAULT 'Default',
        cash_balance REAL NOT NULL DEFAULT 100000.0,
        strategy TEXT DEFAULT 'balanced',
        risk_profile TEXT DEFAULT 'moderate',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_positions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        portfolio_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        qty REAL NOT NULL,
        avg_cost REAL NOT NULL,
        market_value REAL DEFAULT 0.0,
        unrealized_pnl REAL DEFAULT 0.0,
        asset_class TEXT DEFAULT 'equity',
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_orders (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        portfolio_id TEXT,
        ticker TEXT NOT NULL,
        side TEXT NOT NULL,
        qty REAL NOT NULL,
        order_type TEXT NOT NULL DEFAULT 'market',
        status TEXT NOT NULL DEFAULT 'pending',
        fill_price REAL,
        signal_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        filled_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_signals (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        direction TEXT NOT NULL,
        composite_score REAL NOT NULL,
        confidence REAL NOT NULL,
        component_scores TEXT,
        run_id TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        actioned_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_analysis_runs (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        ticker TEXT NOT NULL,
        workflow_name TEXT DEFAULT 'analysis',
        status TEXT DEFAULT 'running',
        duration_ms INTEGER,
        result_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_analyst_reports (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        analyst_type TEXT NOT NULL,
        findings_json TEXT NOT NULL,
        score REAL,
        confidence REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_debate_records (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        bull_thesis TEXT,
        bear_thesis TEXT,
        bull_conviction REAL,
        bear_conviction REAL,
        net_score REAL,
        consensus INTEGER DEFAULT 0,
        synthesis TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_risk_assessments (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        passed INTEGER NOT NULL DEFAULT 1,
        checks_json TEXT,
        warnings TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_trade_decisions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        action TEXT NOT NULL,
        approved INTEGER DEFAULT 0,
        reason TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_pulse_articles (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        pulse_post_id TEXT,
        article_topic TEXT,
        status TEXT DEFAULT 'draft',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_macro_context (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        macro_score INTEGER NOT NULL,
        regime TEXT NOT NULL,
        supply_chain_risk INTEGER NOT NULL DEFAULT 0,
        geopolitical_risk INTEGER NOT NULL DEFAULT 0,
        data_source TEXT DEFAULT 'sample',
        summary TEXT,
        context_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_macro_indicators (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        name TEXT NOT NULL,
        value REAL,
        score INTEGER,
        signal TEXT,
        label TEXT,
        weight REAL DEFAULT 0.1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_macro_sector_impact (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        sector TEXT NOT NULL,
        impact_score INTEGER DEFAULT 0,
        direction TEXT DEFAULT 'neutral',
        rate_effect REAL DEFAULT 0,
        oil_effect REAL DEFAULT 0,
        dxy_effect REAL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Portfolio Manager time-series tables ---
    """CREATE TABLE IF NOT EXISTS ad_portfolio_snapshots (
        id TEXT PRIMARY KEY,
        run_id TEXT,
        total_value REAL NOT NULL,
        cash_balance REAL NOT NULL,
        position_count INTEGER DEFAULT 0,
        total_unrealized_pnl REAL DEFAULT 0.0,
        macro_regime TEXT,
        macro_score INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_position_history (
        id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        qty REAL NOT NULL,
        avg_cost REAL NOT NULL,
        market_value REAL DEFAULT 0.0,
        unrealized_pnl REAL DEFAULT 0.0,
        weight_pct REAL DEFAULT 0.0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Expert Advisory + Scenarios (migrated from PG to SQLite) ---
    """CREATE TABLE IF NOT EXISTS ad_expert_opinions (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        expert_key TEXT NOT NULL,
        expert_name TEXT NOT NULL,
        direction TEXT NOT NULL,
        conviction INTEGER NOT NULL,
        reasoning TEXT,
        risk_profile TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_cis_recommendations (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        ticker TEXT NOT NULL,
        final_direction TEXT NOT NULL,
        final_conviction INTEGER NOT NULL,
        expert_votes TEXT,
        synthesis TEXT,
        narrative TEXT,
        auto_trade INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_expert_consensus (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        run_id TEXT,
        bull_votes INTEGER NOT NULL DEFAULT 0,
        bear_votes INTEGER NOT NULL DEFAULT 0,
        neutral_votes INTEGER NOT NULL DEFAULT 0,
        total_votes INTEGER NOT NULL DEFAULT 6,
        bull_pct REAL,
        expert_keys TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_daily_briefs (
        id TEXT PRIMARY KEY,
        brief_date TEXT NOT NULL,
        market_summary TEXT,
        top_ideas TEXT,
        watchlist TEXT,
        risk_alerts TEXT,
        expert_highlights TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_risk_profiles (
        id TEXT PRIMARY KEY,
        profile_name TEXT NOT NULL UNIQUE,
        max_position_pct REAL DEFAULT 0.15,
        max_daily_budget REAL DEFAULT 10000,
        min_confidence_to_trade REAL DEFAULT 0.60,
        preferred_directions TEXT DEFAULT '["BUY","HOLD"]',
        max_portfolio_beta REAL DEFAULT 1.2,
        is_active INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_scenario_runs (
        id TEXT PRIMARY KEY,
        scenario_key TEXT NOT NULL,
        scenario_name TEXT NOT NULL,
        scenario_type TEXT NOT NULL,
        parameters TEXT,
        total_tickers_impacted INTEGER DEFAULT 0,
        portfolio_impact_pct REAL DEFAULT 0.0,
        summary TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_scenario_impacts (
        id TEXT PRIMARY KEY,
        scenario_run_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_name TEXT NOT NULL,
        impact_1m_pct REAL DEFAULT 0.0,
        impact_1q_pct REAL DEFAULT 0.0,
        impact_1y_pct REAL DEFAULT 0.0,
        confidence REAL DEFAULT 0.5,
        rationale TEXT,
        price_at_run REAL,
        current_price REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_scenario_signal_adjustments (
        id TEXT PRIMARY KEY,
        scenario_run_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        original_confidence REAL,
        adjusted_confidence REAL,
        adjustment_reason TEXT,
        notification_label TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_cascade_watchlists (
        id TEXT PRIMARY KEY,
        trigger_key TEXT NOT NULL,
        trigger_name TEXT NOT NULL,
        tickers TEXT NOT NULL,
        ticker_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_graph_cache (
        id TEXT PRIMARY KEY,
        kg_fingerprint TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ad_transaction_costs (
        id TEXT PRIMARY KEY,
        order_id TEXT,
        ticker TEXT NOT NULL,
        direction TEXT NOT NULL,
        qty REAL NOT NULL,
        estimated_price REAL,
        round_trip_cost_pct REAL NOT NULL,
        expected_alpha_pct REAL,
        net_alpha_pct REAL,
        cost_exceeds_alpha INTEGER DEFAULT 0,
        avg_daily_volume INTEGER,
        bid_ask_spread REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Strategist tables (autonomous portfolio strategy engine) ---
    """CREATE TABLE IF NOT EXISTS ad_strategy_runs (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        run_type TEXT NOT NULL DEFAULT 'full',
        macro_regime TEXT,
        macro_score REAL,
        total_tickers_scored INTEGER DEFAULT 0,
        strategy_json TEXT,
        core_count INTEGER DEFAULT 0,
        tactical_count INTEGER DEFAULT 0,
        opportunistic_count INTEGER DEFAULT 0,
        hedge_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_strategy_holdings (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        tenant_id TEXT NOT NULL DEFAULT 'default',
        run_id TEXT NOT NULL,
        tier TEXT NOT NULL,
        ticker TEXT NOT NULL,
        sector TEXT,
        direction TEXT NOT NULL DEFAULT 'LONG',
        weight_pct REAL NOT NULL DEFAULT 0.0,
        conviction REAL NOT NULL DEFAULT 50.0,
        composite_rank INTEGER,
        momentum_score REAL,
        consistency_score REAL,
        mean_reversion_flag INTEGER DEFAULT 0,
        macro_alignment REAL,
        scenario_resilience REAL,
        kg_centrality REAL,
        expert_alignment REAL,
        rationale TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_strategy_sector_allocation (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        sector TEXT NOT NULL,
        target_weight_pct REAL NOT NULL DEFAULT 0.0,
        current_weight_pct REAL DEFAULT 0.0,
        macro_alignment REAL,
        momentum_rank INTEGER,
        avg_scenario_resilience REAL,
        tilt_direction TEXT DEFAULT 'NEUTRAL',
        rationale TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_strategy_signals (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        severity TEXT DEFAULT 'info',
        ticker TEXT,
        sector TEXT,
        message TEXT NOT NULL,
        data_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_fundamental_metrics (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        as_of_date TEXT NOT NULL,
        pe_ratio REAL,
        pb_ratio REAL,
        ps_ratio REAL,
        ev_ebitda REAL,
        roe REAL,
        roa REAL,
        roic REAL,
        gross_margin REAL,
        operating_margin REAL,
        net_margin REAL,
        fcf_yield REAL,
        fcf_per_share REAL,
        debt_to_equity REAL,
        current_ratio REAL,
        dividend_yield REAL,
        payout_ratio REAL,
        eps_growth_3y REAL,
        revenue_growth_3y REAL,
        dividend_growth_5y REAL,
        accruals_ratio REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(ticker, as_of_date)
    )""",
    """CREATE TABLE IF NOT EXISTS ad_quality_scores (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        as_of_date TEXT NOT NULL,
        composite_quality_score REAL,
        value_quality REAL,
        profitability_quality REAL,
        growth_quality REAL,
        balance_sheet_quality REAL,
        capital_allocation_quality REAL,
        moat_score REAL,
        stagflation_quality_adjustment REAL DEFAULT 0.0,
        quality_label TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(ticker, as_of_date)
    )""",
    # --- Factor model tables ---
    """CREATE TABLE IF NOT EXISTS ad_factor_returns (
        date TEXT PRIMARY KEY,
        market_rf REAL,
        smb REAL,
        hml REAL,
        rmw REAL,
        cma REAL,
        risk_free_rate REAL,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_factor_loadings (
        ticker TEXT PRIMARY KEY,
        beta_market REAL,
        beta_smb REAL,
        beta_hml REAL,
        beta_rmw REAL,
        beta_cma REAL,
        r_squared REAL,
        alpha_annualized REAL,
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_regime_premiums (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        regime TEXT,
        expected_return_annual_pct REAL,
        market_contrib REAL,
        smb_contrib REAL,
        hml_contrib REAL,
        rmw_contrib REAL,
        cma_contrib REAL,
        risk_free_rate REAL,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_alpha_decomposition (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        actual_return_pct REAL,
        expected_return_capm REAL,
        jensens_alpha REAL,
        ff5_alpha REAL,
        risk_free_contrib REAL,
        market_contrib REAL,
        smb_contrib REAL,
        hml_contrib REAL,
        rmw_contrib REAL,
        cma_contrib REAL,
        r_squared REAL,
        period_days INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_signal_validation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        analyst_type TEXT,
        predicted_direction TEXT,
        actual_direction TEXT,
        is_hit INTEGER,
        regime TEXT DEFAULT 'neutral',
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_dynamic_weights (
        analyst_type TEXT PRIMARY KEY,
        weight REAL,
        hit_rate REAL,
        t_statistic REAL,
        passes_harvey INTEGER,
        sample_size INTEGER,
        regime TEXT DEFAULT 'all',
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_economic_events (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        event_type TEXT NOT NULL,
        scheduled_date TEXT,
        actual_value REAL,
        expected_value REAL,
        previous_value REAL,
        impact_level TEXT DEFAULT 'medium',
        source TEXT DEFAULT 'manual',
        description TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Knowledge graph tables ---
    """CREATE TABLE IF NOT EXISTS kg_graphs (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        name TEXT NOT NULL,
        description TEXT,
        entity_count INTEGER DEFAULT 0,
        edge_count INTEGER DEFAULT 0,
        metadata TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS kg_nodes (
        id TEXT PRIMARY KEY,
        graph_id TEXT NOT NULL,
        label TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        properties TEXT DEFAULT '{}',
        embedding BLOB,
        centrality REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS kg_edges (
        id TEXT PRIMARY KEY,
        graph_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relationship TEXT NOT NULL,
        weight REAL DEFAULT 1.0,
        properties TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    # --- Event-stack pillar tables (used by confluence_pillars/event_stack.py) ---
    """CREATE TABLE IF NOT EXISTS ad_earnings_history (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        report_date TEXT NOT NULL,
        surprise_pct REAL,
        eps_actual REAL,
        eps_estimate REAL,
        revenue_actual REAL,
        revenue_estimate REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_analyst_actions (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        action TEXT DEFAULT '',
        analyst TEXT DEFAULT '',
        firm TEXT DEFAULT '',
        price_target REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_analyst_ratings (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        change TEXT DEFAULT '',
        rating TEXT DEFAULT '',
        analyst TEXT DEFAULT '',
        firm TEXT DEFAULT '',
        price_target REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_insider_transactions (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        transaction_type TEXT DEFAULT '',
        insider_name TEXT DEFAULT '',
        insider_title TEXT DEFAULT '',
        shares REAL,
        price REAL,
        value REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Perspective scores (ticker-indexed for confluence_scorer lookups) ---
    """CREATE TABLE IF NOT EXISTS ad_perspective_scores (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        run_id TEXT,
        net_score REAL NOT NULL DEFAULT 0.0,
        consensus INTEGER NOT NULL DEFAULT 0,
        signal TEXT DEFAULT '',
        bull_conviction REAL DEFAULT 0.0,
        bear_conviction REAL DEFAULT 0.0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Trading daemon tables ---
    """CREATE TABLE IF NOT EXISTS trading_daemon_audit (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        reflex_name TEXT,
        risk_tier TEXT,
        details TEXT,
        success INTEGER,
        duration_ms INTEGER,
        metric_name TEXT,
        metric_value REAL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trading_daemon_reflex_state (
        reflex_name TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 1,
        last_run_at TEXT,
        next_run_at TEXT,
        consecutive_failures INTEGER DEFAULT 0,
        circuit_breaker_open INTEGER DEFAULT 0,
        circuit_breaker_tripped_at TEXT,
        total_runs INTEGER DEFAULT 0,
        total_successes INTEGER DEFAULT 0,
        total_failures INTEGER DEFAULT 0,
        last_metric_value REAL,
        last_error TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_market_snapshot (
        ticker TEXT PRIMARY KEY,
        sector TEXT,
        direction TEXT,
        composite_score REAL,
        confidence REAL,
        signal_created_at TEXT,
        signal_run_id TEXT,
        regime TEXT,
        kg_centrality REAL DEFAULT 0,
        kg_supply_chain INTEGER DEFAULT 0,
        kg_competitors INTEGER DEFAULT 0,
        refreshed_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_ticker_performance (
        ticker TEXT PRIMARY KEY,
        p1y REAL,
        p5y REAL,
        p10y REAL,
        p20y REAL,
        refreshed_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Systemic Risk & Opportunity Radar (SROR) tables ---
    """CREATE TABLE IF NOT EXISTS ad_systemic_radar_snapshots (
        id TEXT PRIMARY KEY,
        danger_score_immediate REAL NOT NULL DEFAULT 50.0,
        danger_score_near_term REAL NOT NULL DEFAULT 50.0,
        danger_score_medium_term REAL NOT NULL DEFAULT 50.0,
        opportunity_score_immediate REAL NOT NULL DEFAULT 50.0,
        opportunity_score_near_term REAL NOT NULL DEFAULT 50.0,
        opportunity_score_medium_term REAL NOT NULL DEFAULT 50.0,
        composite_danger REAL NOT NULL DEFAULT 50.0,
        composite_opportunity REAL NOT NULL DEFAULT 50.0,
        regime_label TEXT DEFAULT 'NEUTRAL',
        family_scores_json TEXT,
        indicator_details_json TEXT,
        active_divergences_json TEXT,
        recession_precursor_count INTEGER DEFAULT 0,
        crisis_similarity_json TEXT,
        hmm_transition_json TEXT,
        data_source TEXT DEFAULT 'sample',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_systemic_radar_indicators (
        id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        family TEXT NOT NULL,
        indicator_name TEXT NOT NULL,
        raw_value REAL,
        normalized_score REAL,
        signal TEXT DEFAULT 'NEUTRAL',
        time_horizon TEXT DEFAULT 'immediate',
        weight REAL DEFAULT 1.0,
        delta_from_prior REAL,
        trend_direction TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS ad_systemic_radar_alerts (
        id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        alert_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'medium',
        family TEXT,
        indicator_name TEXT,
        message TEXT NOT NULL,
        threshold_breached REAL,
        current_value REAL,
        acknowledged INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # --- Watchlist (user-curated ticker list) ---
    """CREATE TABLE IF NOT EXISTS ad_watchlists (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        ticker TEXT NOT NULL,
        notes TEXT,
        added_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_user_ticker
       ON ad_watchlists(user_id, ticker)""",
    # --- Options chain snapshots (IV Rank cache) ---
    """CREATE TABLE IF NOT EXISTS ad_option_chain_snapshots (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        spot_price REAL,
        atm_iv REAL,
        ivr_pct REAL,
        iv_percentile REAL,
        iv_52w_high REAL,
        iv_52w_low REAL,
        expiration_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_ad_chain_snap_ticker_created
       ON ad_option_chain_snapshots(ticker, created_at DESC)""",
]


_SCHEMA_READY = False


def _ensure_schema(conn: StorageConnection) -> None:
    """Run DDL + safe migrations once per process.

    Previously ran on every get_conn() call — ~51 DDL + per-DDL commits =
    ~2s per request on PostgreSQL, which became the floor for every
    dashboard route.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    for ddl in _TABLES:
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            conn.rollback()
    _safe_migrations = [
        ("ad_signals", "price_at_add", "REAL"),
        ("ad_signals", "current_price", "REAL"),
        ("ad_signals", "signal_decay_weight", "REAL NOT NULL DEFAULT 1.0"),
        ("ad_strategy_holdings", "macro_alignment", "REAL"),
        ("ad_option_chain_snapshots", "ivr_pct", "REAL"),
        ("ad_option_chain_snapshots", "iv_percentile", "REAL"),
        ("ad_option_chain_snapshots", "iv_52w_high", "REAL"),
        ("ad_option_chain_snapshots", "iv_52w_low", "REAL"),
    ]
    for table, col, col_type in _safe_migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()
        except Exception:
            conn.rollback()
    _SCHEMA_READY = True


def get_conn() -> StorageConnection:
    """Get a connection to the main ICDEV database.

    Schema is ensured on the first call per process only — set the env var
    ICDEV_FATHOMDESK_FORCE_SCHEMA_CHECK=1 to re-run DDL (e.g. during tests).
    """
    conn = get_connection()
    import os as _os
    if _os.environ.get("ICDEV_FATHOMDESK_FORCE_SCHEMA_CHECK") == "1":
        global _SCHEMA_READY
        _SCHEMA_READY = False
    _ensure_schema(conn)
    return conn


def _uid() -> str:
    return str(uuid.uuid4())[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Analysis Runs
# ---------------------------------------------------------------------------
def create_run(ticker: str, conn: StorageConnection | None = None) -> str:
    """Create a new analysis run, return run_id."""
    c = conn or get_conn()
    run_id = f"run-{_uid()}"
    c.execute(
        "INSERT INTO ad_analysis_runs (id, ticker, status, created_at) VALUES (%s, %s, 'running', %s)",
        (run_id, ticker, _now()),
    )
    c.commit()
    return run_id


def complete_run(
    run_id: str,
    result: dict,
    duration_ms: int,
    conn: StorageConnection | None = None,
):
    """Mark a run as completed and store result JSON."""
    c = conn or get_conn()
    c.execute(
        "UPDATE ad_analysis_runs SET status='completed', result_json=%s, duration_ms=%s, completed_at=%s WHERE id=%s",
        (json.dumps(result, default=str), duration_ms, _now(), run_id),
    )
    c.commit()


def get_runs(
    limit: int = 20,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get recent analysis runs."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT * FROM ad_analysis_runs ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Analyst Reports
# ---------------------------------------------------------------------------
def save_analyst_report(
    run_id: str,
    analyst_type: str,
    findings: dict,
    conn: StorageConnection | None = None,
):
    """Persist an analyst's findings."""
    c = conn or get_conn()
    score = findings.get("score", 0)
    c.execute(
        "INSERT INTO ad_analyst_reports "
        "(id, run_id, analyst_type, findings_json, score, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            f"rpt-{_uid()}",
            run_id,
            analyst_type,
            json.dumps(findings, default=str),
            score,
            _now(),
        ),
    )
    c.commit()


# ---------------------------------------------------------------------------
# Debate Records
# ---------------------------------------------------------------------------
def save_debate(
    run_id: str,
    perspective: dict,
    conn: StorageConnection | None = None,
):
    """Persist bull/bear debate results."""
    c = conn or get_conn()
    bull = perspective.get("bull_thesis", {})
    bear = perspective.get("bear_thesis", {})
    c.execute(
        "INSERT INTO ad_debate_records "
        "(id, run_id, bull_thesis, bear_thesis, bull_conviction, "
        "bear_conviction, net_score, consensus, synthesis, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            f"dbt-{_uid()}",
            run_id,
            json.dumps(bull, default=str),
            json.dumps(bear, default=str),
            bull.get("conviction", 0),
            bear.get("conviction", 0),
            perspective.get("net_score", 0),
            1 if perspective.get("consensus") else 0,
            perspective.get("signal", ""),
            _now(),
        ),
    )
    c.commit()


def save_perspective_score(
    run_id: str,
    perspective: dict,
    conn: StorageConnection | None = None,
) -> None:
    """Persist perspective score indexed by ticker for confluence_scorer lookups."""
    c = conn or get_conn()
    bull = perspective.get("bull_thesis", {})
    bear = perspective.get("bear_thesis", {})
    c.execute(
        "INSERT INTO ad_perspective_scores "
        "(id, ticker, run_id, net_score, consensus, signal, "
        "bull_conviction, bear_conviction, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            f"ps-{_uid()}",
            perspective.get("ticker", ""),
            run_id,
            perspective.get("net_score", 0.0),
            1 if perspective.get("consensus") else 0,
            perspective.get("signal", ""),
            bull.get("conviction", 0.0),
            bear.get("conviction", 0.0),
            _now(),
        ),
    )
    c.commit()


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
def save_signal(
    run_id: str,
    signal: dict,
    conn: StorageConnection | None = None,
) -> str:
    """Persist a generated signal, return signal_id."""
    c = conn or get_conn()
    sig_id = f"sig-{_uid()}"

    # Capture price at signal creation for watchlist tracking
    price_at_add = _get_sample_price(signal.get("ticker", ""))

    c.execute(
        "INSERT INTO ad_signals "
        "(id, ticker, direction, composite_score, confidence, "
        "component_scores, run_id, status, price_at_add, current_price, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s)",
        (
            sig_id,
            signal.get("ticker", ""),
            signal.get("direction", "HOLD"),
            signal.get("composite_score", 0),
            signal.get("confidence", 0),
            json.dumps(
                signal.get("component_scores", {}),
                default=str,
            ),
            run_id,
            price_at_add,
            price_at_add,  # current = add price initially
            _now(),
        ),
    )
    c.commit()
    return sig_id


def _get_sample_price(ticker: str) -> float:
    """Get a sample price for a ticker (deterministic for paper trading)."""
    import hashlib

    if not ticker:
        return 100.0
    seed = int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)
    return round(50 + (seed % 400) + (seed % 100) / 100, 2)


def refresh_signal_prices(conn: StorageConnection | None = None) -> int:
    """Update current_price for all signals (for watchlist performance tracking).

    Returns number of signals updated.
    """
    c = conn or get_conn()
    signals = c.execute("SELECT id, ticker FROM ad_signals WHERE status IN ('approved', 'executed')").fetchall()

    updated = 0
    for sig in signals:
        price = _get_sample_price(sig["ticker"])
        # Add small random drift from original price for realism
        import hashlib

        day_seed = int(hashlib.sha256(f"{sig['ticker']}:{_now()[:10]}".encode()).hexdigest()[:6], 16)
        drift = (day_seed % 200 - 100) / 1000  # -10% to +10%
        adjusted = round(price * (1 + drift), 2)
        c.execute(
            "UPDATE ad_signals SET current_price = %s WHERE id = %s",
            (adjusted, sig["id"]),
        )
        updated += 1

    c.commit()
    return updated


def get_signals(
    status: str | None = None,
    limit: int = 50,
    ranked: bool = False,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get signals, optionally filtered by status.

    ranked=True sorts by (composite_score * signal_decay_weight) DESC.
    """
    c = conn or get_conn()
    if ranked:
        if status:
            rows = c.execute(  # nosec B608 — column names hardcoded, status/limit parameterized
                "SELECT * FROM ad_signals WHERE status=%s ORDER BY (composite_score * signal_decay_weight) DESC LIMIT %s",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(  # nosec B608 — column names hardcoded, limit parameterized
                "SELECT * FROM ad_signals ORDER BY (composite_score * signal_decay_weight) DESC LIMIT %s",
                (limit,),
            ).fetchall()
    else:
        if status:
            rows = c.execute(
                "SELECT * FROM ad_signals WHERE status=%s ORDER BY created_at DESC LIMIT %s",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM ad_signals ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def update_signal_status(
    signal_id: str,
    status: str,
    conn: StorageConnection | None = None,
):
    """Update signal status (approve/reject/execute)."""
    c = conn or get_conn()
    c.execute(
        "UPDATE ad_signals SET status=%s, actioned_at=%s WHERE id=%s",
        (status, _now(), signal_id),
    )
    c.commit()


# ---------------------------------------------------------------------------
# Risk Assessments
# ---------------------------------------------------------------------------
def save_risk(
    run_id: str,
    risk: dict,
    conn: StorageConnection | None = None,
):
    """Persist risk assessment."""
    c = conn or get_conn()
    c.execute(
        "INSERT INTO ad_risk_assessments "
        "(id, run_id, passed, checks_json, warnings, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            f"rsk-{_uid()}",
            run_id,
            1 if risk.get("passed") else 0,
            json.dumps(risk.get("checks", []), default=str),
            json.dumps(risk.get("warnings", []), default=str),
            _now(),
        ),
    )
    c.commit()


# ---------------------------------------------------------------------------
# Trade Decisions
# ---------------------------------------------------------------------------
def save_decision(
    run_id: str,
    ticker: str,
    direction: str,
    approved: bool,
    reason: str,
    conn: StorageConnection | None = None,
):
    """Persist final trade decision."""
    c = conn or get_conn()
    c.execute(
        "INSERT INTO ad_trade_decisions "
        "(id, run_id, ticker, action, approved, reason, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            f"dec-{_uid()}",
            run_id,
            ticker,
            direction,
            1 if approved else 0,
            reason,
            _now(),
        ),
    )
    c.commit()


# ---------------------------------------------------------------------------
# Pulse Articles
# ---------------------------------------------------------------------------
def save_article_link(
    run_id: str,
    ticker: str,
    topic: str,
    conn: StorageConnection | None = None,
) -> str:
    """Record a Pulse article generation."""
    c = conn or get_conn()
    art_id = f"art-{_uid()}"
    c.execute(
        "INSERT INTO ad_pulse_articles "
        "(id, run_id, ticker, article_topic, status, created_at) "
        "VALUES (%s, %s, %s, %s, 'draft', %s)",
        (art_id, run_id, ticker, topic, _now()),
    )
    c.commit()
    return art_id


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Macro Context (Layer 0)
# ---------------------------------------------------------------------------
def save_macro_context(
    run_id: str,
    macro: dict,
    conn: StorageConnection | None = None,
):
    """Persist macro overlay context for a run."""
    c = conn or get_conn()
    c.execute(
        "INSERT INTO ad_macro_context "
        "(id, run_id, macro_score, regime, supply_chain_risk, "
        "geopolitical_risk, data_source, summary, context_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            f"mac-{_uid()}",
            run_id,
            macro.get("macro_score", 50),
            macro.get("regime", "YELLOW"),
            macro.get("supply_chain_risk", {}).get("score", 0),
            macro.get("geopolitical_risk", {}).get("score", 0),
            macro.get("data_source", "sample"),
            macro.get("summary", ""),
            json.dumps(macro, default=str),
            _now(),
        ),
    )
    c.commit()


def save_macro_indicators(
    run_id: str,
    indicators: list[dict],
    conn: StorageConnection | None = None,
):
    """Persist individual macro indicator values for a run.

    Note: per-run indicators are already stored in ad_macro_context.context_json
    via save_macro_context(). This function is kept for backward compatibility
    but gracefully skips if the table schema doesn't support per-run inserts
    (ad_macro_indicators is a global reference table in PostgreSQL).
    """
    c = conn or get_conn()
    try:
        for ind in indicators:
            c.execute(
                "INSERT INTO ad_macro_indicators "
                "(id, run_id, name, value, score, signal, label, weight, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"mi-{_uid()}",
                    run_id,
                    ind.get("name", ""),
                    ind.get("value", 0),
                    ind.get("score", 50),
                    ind.get("signal", "YELLOW"),
                    ind.get("label", ""),
                    ind.get("weight", 0.1),
                    _now(),
                ),
            )
        c.commit()
    except Exception:
        # PG table schema differs (global reference, no run_id column).
        # Per-run data is already in ad_macro_context.context_json — safe to skip.
        try:
            c.rollback()
        except Exception:
            pass


def save_macro_sector_impact(
    run_id: str,
    sector_impacts: dict,
    conn: StorageConnection | None = None,
):
    """Persist per-sector macro impact scores for a run."""
    c = conn or get_conn()
    for sector, impact in sector_impacts.items():
        c.execute(
            "INSERT INTO ad_macro_sector_impact "
            "(id, run_id, sector, impact_score, direction, "
            "rate_effect, oil_effect, dxy_effect, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                f"ms-{_uid()}",
                run_id,
                sector,
                impact.get("impact_score", 0),
                impact.get("direction", "neutral"),
                impact.get("rate_effect", 0),
                impact.get("oil_effect", 0),
                impact.get("dxy_effect", 0),
                _now(),
            ),
        )
    c.commit()


# ---------------------------------------------------------------------------
# Transaction Costs
# ---------------------------------------------------------------------------
def save_transaction_cost(
    ticker: str,
    direction: str,
    qty: float,
    round_trip_cost_pct: float,
    expected_alpha_pct: float | None = None,
    net_alpha_pct: float | None = None,
    cost_exceeds_alpha: bool = False,
    estimated_price: float | None = None,
    avg_daily_volume: int | None = None,
    bid_ask_spread: float | None = None,
    order_id: str | None = None,
    conn: StorageConnection | None = None,
) -> str:
    """Log a transaction cost estimate, return record id."""
    c = conn or get_conn()
    rec_id = f"tc-{_uid()}"
    c.execute(
        "INSERT INTO ad_transaction_costs "
        "(id, order_id, ticker, direction, qty, estimated_price, "
        "round_trip_cost_pct, expected_alpha_pct, net_alpha_pct, "
        "cost_exceeds_alpha, avg_daily_volume, bid_ask_spread, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            rec_id,
            order_id,
            ticker,
            direction,
            qty,
            estimated_price,
            round_trip_cost_pct,
            expected_alpha_pct,
            net_alpha_pct,
            1 if cost_exceeds_alpha else 0,
            avg_daily_volume,
            bid_ask_spread,
            _now(),
        ),
    )
    c.commit()
    return rec_id


def get_monthly_turnover(conn: StorageConnection | None = None) -> float:
    """Sum absolute order values for the current month from ad_orders."""
    c = conn or get_conn()
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    row = c.execute(
        "SELECT COALESCE(SUM(qty * COALESCE(fill_price, 100)), 0) FROM ad_orders WHERE created_at LIKE %s",
        (f"{month_prefix}%",),
    ).fetchone()
    return float(row[0]) if row else 0.0


def get_macro_context(
    run_id: str,
    conn: StorageConnection | None = None,
) -> dict | None:
    """Get macro context for a run, including indicators and sector impacts."""
    c = conn or get_conn()
    row = c.execute(
        "SELECT * FROM ad_macro_context WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    if not row:
        return None

    result = dict(row)
    if result.get("context_json"):
        try:
            result["context"] = json.loads(result["context_json"])
        except (json.JSONDecodeError, TypeError):
            result["context"] = None

    indicators = c.execute(
        "SELECT * FROM ad_macro_indicators WHERE run_id=%s ORDER BY weight DESC",
        (run_id,),
    ).fetchall()
    result["indicators"] = [dict(i) for i in indicators]

    sectors = c.execute(
        "SELECT * FROM ad_macro_sector_impact WHERE run_id=%s",
        (run_id,),
    ).fetchall()
    result["sector_impacts"] = [dict(s) for s in sectors]

    return result


def get_portfolio(conn: StorageConnection | None = None) -> dict:
    """Get or create default portfolio."""
    c = conn or get_conn()
    row = c.execute("SELECT * FROM ad_portfolios LIMIT 1").fetchone()
    if not row:
        pid = f"pf-{_uid()}"
        c.execute(
            "INSERT INTO ad_portfolios (id, name, cash_balance) VALUES (%s, 'Default', 100000.0)",
            (pid,),
        )
        c.commit()
        row = c.execute(
            "SELECT * FROM ad_portfolios WHERE id=%s",
            (pid,),
        ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# Portfolio Snapshots (time-series)
# ---------------------------------------------------------------------------
def save_portfolio_snapshot(
    run_id: str,
    macro_regime: str | None = None,
    macro_score: int | None = None,
    conn: StorageConnection | None = None,
) -> str:
    """Snapshot current portfolio state at a point in time, return snapshot_id."""
    c = conn or get_conn()
    portfolio = get_portfolio(c)
    positions = c.execute(
        "SELECT * FROM ad_positions WHERE portfolio_id=%s",
        (portfolio["id"],),
    ).fetchall()

    total_value = portfolio["cash_balance"] + sum(p["market_value"] for p in positions)
    total_unrealized = sum(p["unrealized_pnl"] for p in positions)

    snap_id = f"snap-{_uid()}"
    c.execute(
        "INSERT INTO ad_portfolio_snapshots "
        "(id, run_id, total_value, cash_balance, position_count, "
        "total_unrealized_pnl, macro_regime, macro_score, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            snap_id,
            run_id,
            total_value,
            portfolio["cash_balance"],
            len(positions),
            total_unrealized,
            macro_regime,
            macro_score,
            _now(),
        ),
    )

    # Snapshot each position
    for p in positions:
        weight = p["market_value"] / total_value * 100 if total_value > 0 else 0
        c.execute(
            "INSERT INTO ad_position_history "
            "(id, snapshot_id, ticker, qty, avg_cost, market_value, "
            "unrealized_pnl, weight_pct, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                f"ph-{_uid()}",
                snap_id,
                p["ticker"],
                p["qty"],
                p["avg_cost"],
                p["market_value"],
                p["unrealized_pnl"],
                round(weight, 2),
                _now(),
            ),
        )
    c.commit()
    return snap_id


def get_portfolio_timeline(
    limit: int = 50,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get portfolio value snapshots over time."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT * FROM ad_portfolio_snapshots ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Time-Series Query Helpers (PM Evolution Views)
# ---------------------------------------------------------------------------
def get_signal_history(
    ticker: str,
    limit: int = 30,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get signal evolution for a specific ticker, with regime joined per run_id."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT s.*, r.created_at as run_date, m.regime as regime "
        "FROM ad_signals s "
        "JOIN ad_analysis_runs r ON s.run_id = r.id "
        "LEFT JOIN ad_macro_context m ON m.run_id = s.run_id "
        "WHERE s.ticker = %s "
        "ORDER BY s.created_at DESC LIMIT %s",
        (ticker, limit),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        if d.get("component_scores"):
            try:
                d["component_scores"] = json.loads(d["component_scores"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(d)
    return results


def get_analyst_contribution_history(
    ticker: str,
    limit: int = 20,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get analyst scores across runs for a ticker (contribution tracking)."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT ar.run_id, ar.analyst_type, ar.score, ar.created_at, "
        "r.ticker "
        "FROM ad_analyst_reports ar "
        "JOIN ad_analysis_runs r ON ar.run_id = r.id "
        "WHERE r.ticker = %s "
        "ORDER BY ar.created_at DESC LIMIT %s",
        (ticker, limit * 6),  # 6 analysts per run
    ).fetchall()
    return [dict(r) for r in rows]


def get_macro_regime_history(
    limit: int = 30,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get macro regime transitions over time."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT id, run_id, macro_score, regime, supply_chain_risk, "
        "geopolitical_risk, data_source, created_at "
        "FROM ad_macro_context ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_runs_for_ticker(
    ticker: str,
    limit: int = 20,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get all analysis runs for a specific ticker."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT * FROM ad_analysis_runs WHERE ticker = %s ORDER BY created_at DESC LIMIT %s",
        (ticker, limit),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        if d.get("result_json"):
            try:
                d["result"] = json.loads(d["result_json"])
            except (json.JSONDecodeError, TypeError):
                d["result"] = None
            del d["result_json"]
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Option Chain Snapshots (IVR cache)
# ---------------------------------------------------------------------------
def save_chain_snapshot(
    ticker: str,
    ivr: dict,
    spot_price: float | None = None,
    expiration_count: int = 0,
    conn: StorageConnection | None = None,
) -> str:
    """Persist an option chain IVR snapshot, return snapshot_id.

    *ivr* is the dict returned by compute_ivr() — must contain iv_rank
    (stored as ivr_pct), iv_percentile, iv_52w_high, iv_52w_low, current_iv.
    """
    c = conn or get_conn()
    snap_id = f"ocs-{_uid()}"
    c.execute(
        "INSERT INTO ad_option_chain_snapshots "
        "(id, ticker, spot_price, atm_iv, ivr_pct, iv_percentile, "
        "iv_52w_high, iv_52w_low, expiration_count, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            snap_id,
            ticker.upper(),
            spot_price,
            ivr.get("current_iv"),
            ivr.get("iv_rank"),
            ivr.get("iv_percentile"),
            ivr.get("iv_52w_high"),
            ivr.get("iv_52w_low"),
            expiration_count,
            _now(),
        ),
    )
    c.commit()
    return snap_id


def get_chain_snapshots(
    ticker: str,
    limit: int = 50,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Return recent IVR snapshots for *ticker*, newest first."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT * FROM ad_option_chain_snapshots "
        "WHERE ticker = %s ORDER BY created_at DESC LIMIT %s",
        (ticker.upper(), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_debate_history(
    ticker: str,
    limit: int = 20,
    conn: StorageConnection | None = None,
) -> list[dict]:
    """Get bull/bear debate evolution for a ticker."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT d.*, r.ticker "
        "FROM ad_debate_records d "
        "JOIN ad_analysis_runs r ON d.run_id = r.id "
        "WHERE r.ticker = %s "
        "ORDER BY d.created_at DESC LIMIT %s",
        (ticker, limit),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        for field in ("bull_thesis", "bear_thesis"):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)
    return results
