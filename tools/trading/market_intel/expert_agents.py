"""Expert advisory agents for AlphaDesk — CIS recommendation engine."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.db.storage import get_connection

_ADVISOR_TABLES = [
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
        ticker TEXT NOT NULL,
        final_direction TEXT NOT NULL,
        final_conviction INTEGER NOT NULL,
        expert_votes TEXT,
        synthesis TEXT,
        narrative TEXT,
        auto_trade INTEGER DEFAULT 0,
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
    """CREATE TABLE IF NOT EXISTS ad_expert_recommendations (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        direction TEXT NOT NULL,
        conviction INTEGER NOT NULL,
        source TEXT DEFAULT 'cis',
        narrative TEXT,
        risk_profile TEXT,
        expert_votes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]


def _ensure_tables() -> None:
    conn = get_connection()
    for ddl in _ADVISOR_TABLES:
        conn.execute(ddl)
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_latest_recommendations(limit: int = 10) -> list[dict]:
    """Return the most recent expert recommendations."""
    _ensure_tables()
    conn = get_connection()
    rows = conn.execute(
        "SELECT ticker, direction, conviction, source, narrative, risk_profile, created_at "
        "FROM ad_expert_recommendations ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    if rows:
        return [dict(r) for r in rows]

    # Fall back to ad_cis_recommendations if no rows yet
    conn = get_connection()
    rows = conn.execute(
        "SELECT ticker, final_direction AS direction, final_conviction AS conviction, "
        "'cis' AS source, narrative, created_at "
        "FROM ad_cis_recommendations ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
