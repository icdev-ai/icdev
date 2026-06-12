CREATE TABLE IF NOT EXISTS ad_cta_scores (
    id TEXT PRIMARY KEY,
    ticker TEXT,
    cta_score REAL,
    signal TEXT,
    crowding_ratio REAL,
    vol_deleveraging_alert INTEGER,
    adx REAL,
    donchian_breakout TEXT,
    computed_at TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ad_cta_scores_ticker_computed ON ad_cta_scores(ticker, computed_at DESC);
