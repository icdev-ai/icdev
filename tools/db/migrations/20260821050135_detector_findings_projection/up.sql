-- Migration: 20260821050135_detector_findings_projection
-- CUI // SP-CTI
--
-- autonomy-act-02 — the projection three hand-run detectors write their
-- findings into, so each finding becomes ONE card carrying its evidence.
--
-- status_churn (kpr-watch-11), born_red_survey (rem-hyg-14) and
-- recovery_summary (rem-hyg-16) were each built because a human found the
-- defect by hand, and each then sat waiting for a human to run it by hand.
-- tools/kanban/detector_findings.py runs them on the Genesis cadence; this
-- table is how it knows a finding from a run.
--
-- Deliberately NOT append-only: one row per (detector, subject, fingerprint),
-- upserted on re-observation. A task seen oscillating on forty cycles is ONE
-- finding with seen_count 40, not forty cards. The cef-ui-02 shape.

CREATE TABLE IF NOT EXISTS detector_findings (
    finding_id      TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    -- The RLS LABEL ('CUI'), never a banner — a banner matches no clearance.
    classification  TEXT NOT NULL DEFAULT 'CUI',
    -- status_churn | born_red | recovery
    detector        TEXT NOT NULL,
    -- What the finding is ABOUT: a task id, a test path.
    subject         TEXT NOT NULL DEFAULT '',
    -- What makes two observations the SAME finding (the churn cycle, the
    -- recovery outcome). A changed fingerprint is a NEW finding.
    fingerprint     TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    priority        TEXT NOT NULL DEFAULT 'medium',
    -- The detector's own row, verbatim. The card is rendered FROM this, so a
    -- card and its projection row can never disagree about the evidence.
    evidence_json   TEXT NOT NULL DEFAULT '{}',
    -- The command that re-derives the finding. A finding without its
    -- derivation cannot be acted on and gets dismissed.
    derivation      TEXT NOT NULL DEFAULT '',
    -- active: reported by the detector's latest MEASURABLE run.
    -- cleared: a later measurable run no longer reported it. An unmeasurable
    -- or failed run clears NOTHING.
    status          TEXT NOT NULL DEFAULT 'active',
    seen_count      INTEGER NOT NULL DEFAULT 1,
    -- How many cards this finding has produced. >1 means it recurred after
    -- its card was closed.
    card_count      INTEGER NOT NULL DEFAULT 0,
    -- The newest card, seeded through task_factory.create_tasks.
    task_id         TEXT,
    first_seen_at   TIMESTAMP,
    last_seen_at    TIMESTAMP,
    cleared_at      TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_detector_findings_browse
    ON detector_findings (detector, status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_detector_findings_task
    ON detector_findings (task_id);

-- The DENOMINATOR. An empty findings table has three causes that read
-- identically without it: the reflex never ran, it ran and the detector was
-- UNMEASURABLE (idle board, unmigrated baseline), or it ran and found nothing.
-- One row per detector, upserted on every run.
CREATE TABLE IF NOT EXISTS detector_runs (
    detector            TEXT PRIMARY KEY,
    classification      TEXT NOT NULL DEFAULT 'CUI',
    runs                INTEGER NOT NULL DEFAULT 0,
    measurable_runs     INTEGER NOT NULL DEFAULT 0,
    -- findings | clean | unmeasurable | error
    last_state          TEXT NOT NULL DEFAULT '',
    last_reason         TEXT NOT NULL DEFAULT '',
    -- NULL, never 0, when the last run was not measurable.
    last_findings       INTEGER,
    last_summary_json   TEXT NOT NULL DEFAULT '{}',
    last_run_at         TIMESTAMP,
    last_measurable_at  TIMESTAMP
);
