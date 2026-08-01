-- CUI // SP-CTI
-- Migration 317: record the evidence a certificate relied on (aca-int-07, part 2).
--
-- fa_certificates stores a user, a tier, a label, a token and a timestamp. That is
-- an assertion with nothing behind it: check_cert_eligibility computes the gates,
-- issue_certificate reads only the boolean, and every detail of WHICH missions and
-- WHICH verified steps satisfied them is discarded. /academy/verify/<token> could
-- therefore only repeat the label back.
--
-- A CERTIFICATE IS A STATEMENT ABOUT A MOMENT, so the evidence is snapshotted at
-- issue time rather than recomputed on the verify page. Recomputing would let the
-- claim drift with the data underneath it: retire a mission, re-seed a step, or let
-- a learner's progress change, and a certificate issued last year would silently
-- start describing something else — or fail to verify at all. The snapshot is what
-- makes the token checkable by someone who was not there.
--
-- Append-only, registered in APPEND_ONLY_TABLES: revoking a certificate means
-- recording a revocation, not deleting the evidence that it was once issued.
--
-- No backfill. Zero certificates have been issued on this board, and there is no
-- honest way to reconstruct what a past issuance relied on — that is the whole
-- defect. Certificates issued from here carry evidence; there are none that do not.
--
-- Portable across PostgreSQL and the SQLite test/fallback backend. No semicolon
-- appears inside any string literal here: statement splitters that are not
-- string-aware cut an INSERT in half at the first one.

CREATE TABLE IF NOT EXISTS fa_certificate_evidence (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_id        INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    -- gate    — one requirement from the cert definition, with the figures that
    --           satisfied it, captured verbatim from check_cert_eligibility
    -- mission — a specific completed mission that counted toward a gate
    -- step    — a specific verified step submission underneath one of those missions
    evidence_type  TEXT    NOT NULL,
    ref_id         INTEGER,
    label          TEXT    NOT NULL,
    detail         TEXT,
    -- When the evidence itself happened, not when the certificate was issued. A
    -- mission completed in March, cited by a certificate issued in July, should
    -- verify as March work.
    demonstrated_at TEXT,
    score          INTEGER,
    classification TEXT    DEFAULT 'CUI',
    tenant_id      TEXT,
    created_at     TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fa_cert_evidence_cert ON fa_certificate_evidence(cert_id);
CREATE INDEX IF NOT EXISTS idx_fa_cert_evidence_user ON fa_certificate_evidence(user_id);
