-- Rollback: 20260817014226_docmod_nist_pubs
-- CUI // SP-CTI
--
-- docmod_nist_pubs is a rebuildable evidence CACHE, not a system of record:
-- every row is re-derivable from the NIST CSRC feed, the static seed
-- (args/docmod/nist_pubs.yaml) or an operator import, so dropping it loses no
-- authored data. It is not append-only and carries no audit rows.

DROP INDEX IF EXISTS idx_docmod_nist_pubs_pub;
DROP TABLE IF EXISTS docmod_nist_pubs;
