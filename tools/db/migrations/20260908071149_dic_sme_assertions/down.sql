-- Rollback: 20260908071149_dic_sme_assertions
-- CUI // SP-CTI
-- Not append-only: the immutable record of a promotion is the fail-closed
-- dic.hitl_decision row in audit_trail, written BEFORE the promotion. This
-- table is the derived evidence the entity_currency store reads, and the rows
-- derived from it are dropped by the next backfill of that source.
DROP TABLE IF EXISTS dic_sme_assertions;
