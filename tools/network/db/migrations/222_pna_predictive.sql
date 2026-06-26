-- CUI // SP-CTI
-- Migration 222: PNA — Predictive Network Analytics
-- 6 new predictive models: EOL, BGP Instability, Compliance Drift,
-- Capacity Exhaustion, Change Failure Probability, Supply Chain Risk
--
-- APPEND-ONLY tables: nc_eol_predictions, nc_bgp_predictions,
--                     nc_compliance_drift, nc_capacity_predictions,
--                     nc_change_risk, nc_supply_chain_risk
-- Mutable tables:     nc_bgp_events

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. nc_eol_predictions  (APPEND-ONLY — device end-of-life/support risk)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_eol_predictions (
    id                  SERIAL PRIMARY KEY,
    device_name         VARCHAR(200) NOT NULL,
    vendor              VARCHAR(100),
    model               VARCHAR(200),
    os_version          VARCHAR(100),
    eos_date            DATE,                   -- end-of-support date (NULL = unknown)
    eol_date            DATE,                   -- end-of-life date (NULL = unknown)
    days_remaining      INTEGER,                -- days until EOS (negative = already EOS)
    has_active_cves     BOOLEAN NOT NULL DEFAULT FALSE,
    active_cve_count    INTEGER NOT NULL DEFAULT 0,
    risk_score          FLOAT NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    risk_tier           VARCHAR(20) NOT NULL DEFAULT 'medium'
                            CHECK(risk_tier IN ('critical','high','medium','low')),
    nqe_source          VARCHAR(30) NOT NULL DEFAULT 'local_mapping'
                            CHECK(nqe_source IN ('nqe_api','local_mapping','local_heuristic','static_registry')),
    model_version       VARCHAR(20) NOT NULL DEFAULT '1.0',
    predicted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_device
    ON nc_eol_predictions(device_name);
CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_risk
    ON nc_eol_predictions(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_eos_date
    ON nc_eol_predictions(eos_date ASC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_vendor
    ON nc_eol_predictions(vendor);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. nc_bgp_events  (MUTABLE — rolling event log, pruned after 90 days)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_bgp_events (
    id              SERIAL PRIMARY KEY,
    session_key     VARCHAR(300) NOT NULL,      -- "{peer_ip}|{device_name}"
    device_name     VARCHAR(200) NOT NULL,
    peer_ip         VARCHAR(45) NOT NULL,
    peer_asn        INTEGER,
    event_type      VARCHAR(20) NOT NULL CHECK(event_type IN ('up','down','flap','reset','timeout')),
    event_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_bgp_events_session
    ON nc_bgp_events(session_key, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_events_device
    ON nc_bgp_events(device_name, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_events_type
    ON nc_bgp_events(event_type, event_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. nc_bgp_predictions  (APPEND-ONLY — BGP session instability forecast)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_bgp_predictions (
    id                      SERIAL PRIMARY KEY,
    session_key             VARCHAR(300) NOT NULL,
    device_name             VARCHAR(200) NOT NULL,
    peer_ip                 VARCHAR(45) NOT NULL,
    peer_asn                INTEGER,
    stability_score         FLOAT NOT NULL CHECK(stability_score BETWEEN 0.0 AND 1.0),
    flap_count_24h          INTEGER NOT NULL DEFAULT 0,
    flap_count_7d           INTEGER NOT NULL DEFAULT 0,
    flap_risk               VARCHAR(20) NOT NULL DEFAULT 'low'
                                CHECK(flap_risk IN ('critical','high','medium','low')),
    route_count             INTEGER,
    session_state           VARCHAR(20),        -- Established, Idle, Active, etc.
    predicted_outage_hrs    FLOAT,              -- estimated hours until likely outage (NULL = stable)
    confidence              FLOAT NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    model_version           VARCHAR(20) NOT NULL DEFAULT '1.0',
    predicted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_bgp_predictions_session
    ON nc_bgp_predictions(session_key, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_predictions_device
    ON nc_bgp_predictions(device_name);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_predictions_risk
    ON nc_bgp_predictions(stability_score ASC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. nc_compliance_drift  (APPEND-ONLY — STIG baseline deviation prediction)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_compliance_drift (
    id                          SERIAL PRIMARY KEY,
    device_name                 VARCHAR(200) NOT NULL,
    framework                   VARCHAR(100) NOT NULL,  -- e.g. DISA_STIG_IOS-XE, NIST_800-53
    last_compliant_score        FLOAT CHECK(last_compliant_score BETWEEN 0.0 AND 1.0),
    current_score               FLOAT NOT NULL CHECK(current_score BETWEEN 0.0 AND 1.0),
    drift_delta                 FLOAT NOT NULL,         -- current - last_compliant (negative = degrading)
    drift_rate_per_day          FLOAT,                  -- average score change per day
    failing_controls            INTEGER NOT NULL DEFAULT 0,
    critical_controls_failing   INTEGER NOT NULL DEFAULT 0,
    predicted_fail_date         DATE,                   -- projected date score drops below threshold
    days_to_failure             INTEGER,                -- negative = already failed
    risk_score                  FLOAT NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    risk_tier                   VARCHAR(20) NOT NULL DEFAULT 'medium'
                                    CHECK(risk_tier IN ('critical','high','medium','low')),
    assessed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_compliance_drift_device
    ON nc_compliance_drift(device_name, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_compliance_drift_framework
    ON nc_compliance_drift(framework);
CREATE INDEX IF NOT EXISTS idx_nc_compliance_drift_risk
    ON nc_compliance_drift(risk_score DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. nc_capacity_predictions  (APPEND-ONLY — bandwidth saturation forecast)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_capacity_predictions (
    id                  SERIAL PRIMARY KEY,
    device_name         VARCHAR(200) NOT NULL,
    interface_name      VARCHAR(200) NOT NULL,
    interface_id        VARCHAR(300),
    current_util_pct    FLOAT NOT NULL CHECK(current_util_pct BETWEEN 0.0 AND 100.0),
    peak_util_pct       FLOAT CHECK(peak_util_pct BETWEEN 0.0 AND 100.0),
    avg_util_pct_7d     FLOAT,
    trend_slope         FLOAT NOT NULL,         -- pct/day (positive = growing utilization)
    days_to_saturation  INTEGER,                -- NULL if trend is flat/declining
    saturation_date     DATE,
    confidence          FLOAT NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    risk_score          FLOAT NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    risk_tier           VARCHAR(20) NOT NULL DEFAULT 'low'
                            CHECK(risk_tier IN ('critical','high','medium','low')),
    nqe_source          VARCHAR(30) NOT NULL DEFAULT 'local_mapping',
    model_version       VARCHAR(20) NOT NULL DEFAULT '1.0',
    predicted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_capacity_predictions_device
    ON nc_capacity_predictions(device_name, interface_name, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_capacity_predictions_saturation
    ON nc_capacity_predictions(days_to_saturation ASC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_nc_capacity_predictions_risk
    ON nc_capacity_predictions(risk_score DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. nc_change_risk  (APPEND-ONLY — pre-change failure probability scoring)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_change_risk (
    id                              SERIAL PRIMARY KEY,
    change_request_id               VARCHAR(200) NOT NULL,
    device_name                     VARCHAR(200) NOT NULL,
    action_type                     VARCHAR(100),
    failure_probability             FLOAT NOT NULL CHECK(failure_probability BETWEEN 0.0 AND 1.0),
    blast_radius_size               INTEGER NOT NULL DEFAULT 0,
    concurrent_change_count         INTEGER NOT NULL DEFAULT 0,
    maintenance_window_compliant    BOOLEAN NOT NULL DEFAULT TRUE,
    device_criticality              SMALLINT NOT NULL DEFAULT 3
                                        CHECK(device_criticality BETWEEN 1 AND 5),
    risk_factors_json               JSONB,
    risk_tier                       VARCHAR(20) NOT NULL DEFAULT 'medium'
                                        CHECK(risk_tier IN ('critical','high','medium','low')),
    simulation_verdict              VARCHAR(20),    -- pass/warn/fail/skipped from twin
    model_version                   VARCHAR(20) NOT NULL DEFAULT '1.0',
    predicted_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_change_risk_change_request
    ON nc_change_risk(change_request_id);
CREATE INDEX IF NOT EXISTS idx_nc_change_risk_device
    ON nc_change_risk(device_name, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_change_risk_probability
    ON nc_change_risk(failure_probability DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. nc_supply_chain_risk  (APPEND-ONLY — vendor/model risk aggregation)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_supply_chain_risk (
    id                      SERIAL PRIMARY KEY,
    vendor                  VARCHAR(100) NOT NULL,
    device_count            INTEGER NOT NULL DEFAULT 0,
    model_count             INTEGER NOT NULL DEFAULT 0,
    cve_count               INTEGER NOT NULL DEFAULT 0,
    kev_count               INTEGER NOT NULL DEFAULT 0,     -- CISA KEV matches
    critical_cve_count      INTEGER NOT NULL DEFAULT 0,
    high_cve_count          INTEGER NOT NULL DEFAULT 0,
    risk_score              FLOAT NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    vendor_risk_rating      VARCHAR(20) NOT NULL DEFAULT 'medium'
                                CHECK(vendor_risk_rating IN ('critical','high','medium','low')),
    top_cves_json           JSONB,                          -- [{cve_id, cvss, kev}]
    nqe_device_sample_json  JSONB,                          -- [{name, model, os_version}] first 5
    model_version           VARCHAR(20) NOT NULL DEFAULT '1.0',
    assessed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_supply_chain_risk_vendor
    ON nc_supply_chain_risk(vendor, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_supply_chain_risk_score
    ON nc_supply_chain_risk(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_nc_supply_chain_risk_kev
    ON nc_supply_chain_risk(kev_count DESC);
