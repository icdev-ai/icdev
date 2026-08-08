-- Rollback: 20260808053058_sbom_conformance_assessments
-- CUI // SP-CTI
--
-- sbx-sig-02. The table is created by this migration and by nothing else, so
-- dropping it returns the schema to its prior state exactly.
--
-- Note this destroys conformance history. That is correct for a rollback of a
-- migration that introduced the table, and is the only sanctioned removal
-- path: the table is append-only at runtime.

DROP TABLE IF EXISTS sbom_conformance_assessments;
