-- Migration 282: Insider-Risk UBA (lite) — derived baseline/score tables.
-- CUI // SP-CTI
--
-- Card crx-sec-01. Backing store for the deterministic user-behavior anomaly
-- engine at tools/security/insider_risk.py. These tables hold DERIVED data
-- (recomputable) — they are NOT audit records and are intentionally NOT part
-- of APPEND_ONLY_TABLES. The engine reads audit_trail / usage_events /
-- hook_events strictly read-only and never mutates those tables.
--
-- Both tables carry tenant_id + classification for row-level security. DDL is
-- idempotent (CREATE ... IF NOT EXISTS) and mirrors the runtime _ensure_tables
-- in tools/security/insider_risk.py, so running either first is safe. On
-- PostgreSQL the runner translates INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL.

CREATE TABLE IF NOT EXISTS insider_risk_baselines (
    account_id      TEXT PRIMARY KEY,
    typical_hours   TEXT DEFAULT '[]',
    event_count     INTEGER DEFAULT 0,
    distinct_events INTEGER DEFAULT 0,
    export_count    INTEGER DEFAULT 0,
    first_seen      TEXT,
    last_seen       TEXT,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS insider_risk_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL,
    risk_score      REAL NOT NULL,
    risk_band       TEXT NOT NULL,
    rules_fired     TEXT DEFAULT '[]',
    details_json    TEXT DEFAULT '{}',
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_insider_scores_acct
    ON insider_risk_scores(account_id, created_at);
