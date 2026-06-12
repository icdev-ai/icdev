-- Migration: 085_aisg_training_labels
-- CUI // SP-CTI
-- Table: aisg_training_labels — human-labeled training examples replacing BLEU-score quality gate

-- @sqlite-only
CREATE TABLE IF NOT EXISTS aisg_training_labels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id    TEXT,
    input_text    TEXT,
    output_text   TEXT,
    label         TEXT CHECK (label IN ('good', 'bad')),
    business_goal TEXT,
    labeled_by    TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- @pg-only
CREATE TABLE IF NOT EXISTS aisg_training_labels (
    id            SERIAL PRIMARY KEY,
    example_id    TEXT,
    input_text    TEXT,
    output_text   TEXT,
    label         TEXT CHECK (label IN ('good', 'bad')),
    business_goal TEXT,
    labeled_by    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
