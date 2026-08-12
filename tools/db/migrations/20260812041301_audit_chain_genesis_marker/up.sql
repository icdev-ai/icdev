-- Migration: 20260812041301_audit_chain_genesis_marker
-- CUI // SP-CTI
--
-- exa-audit-03 -- where the audit_trail hash chain starts.
--
-- WHY A ROW AND NOT A DERIVED MIN()
--
-- Migration 149 added hash / previous_hash / signature to audit_trail, and for
-- its whole life nothing wrote them. Every row carried NULL, so
-- provenance_verifier reported hash_valid=False and chain_valid=False on all of
-- them. Backfilling is out of scope and would be dishonest anyway: hashing a
-- row today proves only that it looks like this today, which is exactly the
-- claim a hash chain is supposed to NOT let anyone make retroactively.
--
-- So the chain starts at a cutover instead, and the cutover has to be written
-- down. Without it, "this row predates the writer" and "this row was chained
-- and someone broke it" are the same observation -- a NULL hash -- and an
-- auditor looking at a table of failures cannot tell an un-instrumented
-- history from a compromised one.
--
-- The obvious shortcut, deriving the boundary as MIN(id) WHERE hash IS NOT
-- NULL, fails the one case the marker exists for. Remove the first chained row
-- and the derived boundary simply slides up to the second, reporting a clean
-- chain over a table that just lost its head. A recorded id does not move, and
-- the mismatch becomes visible.
--
-- ONE ROW, AND THE ID IS THE KEY
--
-- chain_start_id is the PRIMARY KEY rather than a surrogate: there is exactly
-- one cutover per database, and making the id itself the key means a race
-- between two first-writers resolves to one row by constraint instead of by
-- the read-then-write check in chain.py::record_chain_start. Should a database
-- ever be re-cut over -- restored from a backup taken before the cutover, say
-- -- a second row can be added and chain_start_id(conn) takes the MIN, so the
-- earliest boundary wins and no already-chained row is retroactively declared
-- to predate the chain.
--
-- APPEND-ONLY
--
-- This is evidence about evidence: it states when the audit trail became
-- verifiable. It is registered in APPEND_ONLY_TABLES in
-- .claude/hooks/pre_tool_use.py alongside audit_trail itself. Nothing in the
-- writer ever updates it -- record_chain_start inserts once and then returns
-- early forever after.
--
-- tenant_id and classification are present so the global RLS predicate has the
-- columns it injects against. chain.py reads the table with RLS disabled, but a
-- dashboard reader on a request-scoped connection does not, and a table lacking
-- these columns raises UndefinedColumn on every such read.

CREATE TABLE IF NOT EXISTS audit_chain_genesis (
    chain_start_id INTEGER PRIMARY KEY,     -- first audit_trail.id written WITH a hash
    hash_algorithm TEXT NOT NULL DEFAULT 'sha256',
    note           TEXT,
    tenant_id      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    recorded_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
