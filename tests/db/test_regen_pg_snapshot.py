# CUI // SP-CTI
"""compose() keeps what pg_dump cannot see: carried tables, carried columns, the tail."""
import re

from tools.db import regen_pg_snapshot as regen

FRESH = """--
-- PostgreSQL database dump
--

SET statement_timeout = 0;

CREATE TABLE public.alpha (
    id text NOT NULL,
    name text
);

CREATE TABLE public.dd_field_mappings (
    id text NOT NULL,
    session_id text NOT NULL
);

ALTER TABLE ONLY public.dd_field_mappings
    ADD CONSTRAINT dd_field_mappings_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.dd_mapping_sessions(id);
"""

PREVIOUS = """--
-- PostgreSQL database dump (older)
--

CREATE TABLE public.alpha (
    id text NOT NULL
);

CREATE TABLE public.rag_queries (
    id text NOT NULL,
    query text
);

CREATE SEQUENCE public.rag_queries_id_seq
    START WITH 1;

CREATE INDEX idx_rag_queries_query ON public.rag_queries USING btree (query);

ALTER TABLE ONLY public.rag_queries
    ADD CONSTRAINT rag_queries_pkey PRIMARY KEY (id);

CREATE TABLE public.unrelated (
    id text NOT NULL
);

-- ============================================================================
-- ICDEV ADDITIVE SECTION (post-dump, hand-maintained) — APPEND ONLY
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.fedramp_controls (
    id text NOT NULL,
    tenant_id text DEFAULT 'default'
);
"""

CARRY = "ALTER TABLE public.alpha ADD COLUMN IF NOT EXISTS name text;\n"


def _compose():
    return regen.compose(FRESH, PREVIOUS, CARRY, generated="2026-08-21")


def test_fresh_dump_comes_first_and_unchanged():
    out = _compose()["text"]
    assert out.startswith(FRESH.rstrip("\n"))


def test_tables_absent_from_the_fresh_dump_are_carried_in_order_and_idempotent():
    res = _compose()
    out = res["text"]
    assert res["carried_tables"] == ["rag_queries", "unrelated"]
    assert "CREATE TABLE IF NOT EXISTS public.rag_queries (" in out
    assert "CREATE SEQUENCE IF NOT EXISTS public.rag_queries_id_seq" in out
    assert "CREATE INDEX IF NOT EXISTS idx_rag_queries_query ON public.rag_queries" in out
    assert "ADD CONSTRAINT rag_queries_pkey" in out
    # previous order preserved: rag_queries before unrelated
    assert out.index("public.rag_queries (") < out.index("public.unrelated (")
    # a table the fresh dump HAS is not duplicated from the previous region
    assert out.count("CREATE TABLE public.alpha (") == 1
    assert "CREATE TABLE IF NOT EXISTS public.alpha" not in out


def test_carried_columns_land_before_the_tail_and_the_tail_is_verbatim():
    out = _compose()["text"]
    tail_start = out.index("-- ICDEV ADDITIVE SECTION")
    assert out.index(CARRY.strip()) < tail_start
    _, tail = regen.split_previous(PREVIOUS)
    assert out.endswith(tail)
    assert "CREATE TABLE IF NOT EXISTS public.fedramp_controls" in out


def test_a_snapshot_without_a_tail_still_composes():
    dump_only, tail = regen.split_previous(FRESH)
    assert tail == "" and dump_only == FRESH
    res = regen.compose(FRESH, FRESH)
    assert res["carried_tables"] == []
    assert res["text"].startswith(FRESH.rstrip("\n"))


def test_statement_splitter_respects_parentheses():
    stmts = regen.statements(FRESH)
    bodies = [s for s in stmts if s.startswith("CREATE TABLE")]
    assert len(bodies) == 2
    assert all(s.rstrip().endswith(");") for s in bodies)
    assert any(s.startswith("ALTER TABLE ONLY public.dd_field_mappings") for s in stmts)


def test_table_regex_accepts_both_dump_and_hand_forms():
    assert regen.tables_in('CREATE TABLE public.a (\nCREATE TABLE IF NOT EXISTS "b" (\nCREATE TABLE c (') == ["a", "b", "c"]
    assert re.search(regen.CREATE_TABLE_RE, "CREATE TABLE public.kanban_tasks (")
