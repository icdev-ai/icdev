-- Rollback: 20260817011242_docmod_defacto_evidence_provenance
-- CUI // SP-CTI
--
-- Safe: docmod_defacto_standards is recomputed in full on every sweep, so these
-- columns hold no evidence that the next run would not re-derive.
--
-- SQLite before 3.35 cannot DROP COLUMN; on such a build this rollback fails
-- loudly rather than silently leaving the columns in place, which is the correct
-- outcome — a rollback that reports success without doing anything is the shape
-- this repo keeps finding.

ALTER TABLE docmod_defacto_standards DROP COLUMN evidence_kind;
ALTER TABLE docmod_defacto_standards DROP COLUMN source_feed;
