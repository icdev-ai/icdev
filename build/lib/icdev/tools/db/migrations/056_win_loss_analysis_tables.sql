CREATE TABLE IF NOT EXISTS win_loss_analysis_runs (
    id                  TEXT PRIMARY KEY,
    run_at              TEXT,
    outcomes_analyzed   INTEGER,
    patterns_found      INTEGER,
    top_win_features    TEXT,
    top_loss_features   TEXT,
    result_json         TEXT,
    classification      TEXT DEFAULT 'CUI // SP-CTI'
);

CREATE TABLE IF NOT EXISTS win_loss_feature_impacts (
    id                      TEXT PRIMARY KEY,
    run_id                  TEXT,
    feature_tag             TEXT,
    win_count               INTEGER,
    loss_count              INTEGER,
    win_rate                REAL,
    impact_score            REAL,
    innovation_signal_id    TEXT,
    analyzed_at             TEXT
);

CREATE INDEX IF NOT EXISTS idx_wl_feature_impacts_run ON win_loss_feature_impacts(run_id);
