-- Migration: 20260817024050_docmod_nist_pubs (down)
-- CUI // SP-CTI
--
-- Deliberately a NO-OP. docmod_nist_pubs predates this migration on every existing
-- database (created by the flat legacy 282_docmod_nist_pubs.sql, applied 2026-07-29),
-- so dropping the table here would destroy a cache this migration did not create.
-- The up is an idempotent CREATE ... IF NOT EXISTS; the correct inverse of "ensure it
-- exists" is nothing, not DROP.

SELECT 1;
