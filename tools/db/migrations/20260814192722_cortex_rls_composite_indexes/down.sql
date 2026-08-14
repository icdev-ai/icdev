-- Rollback: 20260814192722_cortex_rls_composite_indexes
-- CUI // SP-CTI
--
-- Index-only migration: dropping these loses read performance, never data. The
-- single-column indexes from migrations 262/263 were never touched, so a
-- rollback returns the tables to exactly their pre-migration index set.

DROP INDEX IF EXISTS idx_cortex_audit_tenant_created;
DROP INDEX IF EXISTS idx_cortex_search_history_tenant_created;
DROP INDEX IF EXISTS idx_cortex_chat_sessions_tenant_created;
DROP INDEX IF EXISTS idx_cortex_messages_tenant_session_turn;
