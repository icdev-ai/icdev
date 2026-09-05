-- Migration: 20260814192722_cortex_rls_composite_indexes
-- CUI // SP-CTI
--
-- ctx-perf-05. The Cortex tables are indexed for the queries the source code
-- WRITES, not for the queries the database actually RUNS.
--
-- get_connection() attaches flask.g.security_context, and StorageCursor._inject_rls
-- then rewrites every statement to carry the row predicate
--
--     AND tenant_id = ? AND classification IN (<dominated set>)
--
-- (tools/security/row_security.py::inject_row_predicate). So there is no such
-- thing as a Cortex read that filters on created_at alone — the tenant equality
-- is ALWAYS there, prepended by the connection layer rather than by the call
-- site, which is why it is invisible when you read the SQL string in the module
-- and why the indexes were chosen as if it did not exist.
--
-- With single-column indexes on tenant_id and on created_at, the planner can use
-- exactly ONE of them: pick the tenant index and it reads every row that tenant
-- ever wrote and discards the ones outside the window; pick the created_at index
-- and it reads the whole window across every tenant and discards the other
-- tenants'. Both degrade linearly with the wrong dimension. A composite
-- (tenant_id, created_at) makes the equality the leading column and the window a
-- range scan inside it, which is the exact shape of these queries.
--
-- Column order and what is deliberately NOT in these indexes:
--
--   * tenant_id FIRST because it is an equality; created_at SECOND because it is
--     a range/sort. The reverse order cannot serve the equality.
--   * classification is left OUT even though the RLS predicate also carries it.
--     It arrives as an IN-list (Bell-LaPadula read-down over the dominated set),
--     so placing it between tenant_id and created_at turns one ordered range scan
--     into a multi-range scan and forfeits the ordering that serves
--     ORDER BY created_at DESC LIMIT n. Left as a residual filter it costs a
--     cheap recheck on rows already narrowed to one tenant and one window.
--   * No DESC. PostgreSQL scans a b-tree backwards at the same cost, so a DESC
--     index would buy nothing the ascending one does not already offer.
--
-- What this does and does not claim. Measured on PG 400k rows / 40 tenants,
-- 90-day spread (see the plans quoted in the ctx-perf-05 PR): the composite is
-- chosen as the ACCESS PATH for both _scan reads, replacing a BitmapAnd that
-- read 10,000 tenant rows plus 44,953 window rows to return 1,126 — startup cost
-- 1033.89 -> 27.98. It is a filter improvement, not an ordering one: on a window
-- whose matches EXCEED the detail LIMIT, the planner still prefers a bitmap scan
-- plus an explicit Sort over an ordered index walk, because the classification
-- IN-list is a residual filter and the heap fetches would be random. That is a
-- legitimate cost decision, not a missing index — do not add classification or a
-- DESC variant chasing it.
--
-- The single-column indexes from migrations 262/263 are intentionally KEPT:
-- (tenant_id) is prefix-redundant with the new composites, but dropping it here
-- would fight tools/cortex/db/init_db.py, which recreates it on every fresh
-- bootstrap. Index churn is the worse defect; a redundant index is not.
--
-- Idempotent: CREATE INDEX IF NOT EXISTS on both backends. Ordering is safe on a
-- fresh DB — MigrationRunner.discover_migrations sorts on (digit-count, digits),
-- so every 3-digit legacy migration (262, 263, which CREATE these tables) runs
-- before every 14-digit timestamp migration, whatever the digits are.

-- cortex_audit — the metrics window query (tools/cortex/metrics.py::_scan) is
-- literally `WHERE created_at >= ? [AND tenant_id = ?]`, run twice per call: once
-- as a GROUP BY rollup over the full window, once as ORDER BY created_at DESC
-- LIMIT for the gates_json detail sample. Both are (tenant_id, created_at).
-- The IQE cortex.audit adapter's ORDER BY created_at DESC LIMIT is the same shape.
CREATE INDEX IF NOT EXISTS idx_cortex_audit_tenant_created
    ON cortex_audit(tenant_id, created_at);

-- cortex_search_history — had session_id ONLY, so any usage-over-time read was a
-- full scan with the tenant predicate applied as a filter.
-- (tools/iqe/adapters/cortex.py::search_history_adapter)
CREATE INDEX IF NOT EXISTS idx_cortex_search_history_tenant_created
    ON cortex_search_history(tenant_id, created_at);

-- cortex_chat_sessions — had tenant_id and user_id but no created_at, while the
-- IQE adapter sorts by created_at DESC.
CREATE INDEX IF NOT EXISTS idx_cortex_chat_sessions_tenant_created
    ON cortex_chat_sessions(tenant_id, created_at);

-- cortex_messages — session_id is selective enough that (session_id, turn_number)
-- was never a full scan, but the tenant predicate stayed a residual filter on
-- every row of the session. Leading with tenant_id lets the RLS equality, the
-- session lookup and the turn ordering all come from one index for both
-- chat_session.py reads (MAX(turn_number) and ORDER BY turn_number).
CREATE INDEX IF NOT EXISTS idx_cortex_messages_tenant_session_turn
    ON cortex_messages(tenant_id, session_id, turn_number);
