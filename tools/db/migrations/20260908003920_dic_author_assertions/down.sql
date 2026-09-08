-- Rollback: 20260908003920_dic_author_assertions
-- CUI // SP-CTI
-- Not append-only: an author's statement is re-derivable from the upload that
-- carried it, and the entity_currency rows derived from it are dropped by the
-- next backfill of that source. Not an audit record.
DROP TABLE IF EXISTS dic_author_assertions;
