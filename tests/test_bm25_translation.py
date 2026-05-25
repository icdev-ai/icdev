# CUI // SP-CTI
"""Tests for FTS5/BM25 → PostgreSQL text search translation in translate_sql()."""
import re

from tools.db.storage import FTS5_TABLES, get_fts5_tables, is_fts5_query, translate_sql


class TestFts5Detection:
    def test_is_fts5_query_literal(self):
        sql = "SELECT * FROM memory_fts WHERE memory_fts MATCH 'bgp routing'"
        assert is_fts5_query(sql) is True

    def test_is_fts5_query_param(self):
        assert is_fts5_query("SELECT * FROM memory_fts WHERE memory_fts MATCH %s") is True

    def test_is_fts5_query_false(self):
        assert is_fts5_query("SELECT * FROM memory_entries WHERE content LIKE '%bgp%'") is False

    def test_is_fts5_query_case_insensitive(self):
        assert is_fts5_query("SELECT * FROM t WHERE t match 'foo'") is True

    def test_get_fts5_tables_returns_copy(self):
        tables = get_fts5_tables()
        assert isinstance(tables, dict)
        assert "memory_fts" in tables
        # Modifying returned dict must not mutate the registry
        tables["injected"] = ["x"]
        assert "injected" not in FTS5_TABLES

    def test_get_fts5_tables_columns(self):
        tables = get_fts5_tables()
        assert "content" in tables["memory_fts"]


class TestFts5MatchTranslation:
    def test_match_literal_produces_plainto_tsquery(self):
        sql = "SELECT * FROM memory_fts WHERE memory_fts MATCH 'bgp routing'"
        result = translate_sql(sql)
        assert "MATCH" not in result
        assert "plainto_tsquery('english', 'bgp routing')" in result
        assert "ts_doc @@" in result

    def test_match_param_produces_plainto_tsquery(self):
        result = translate_sql("SELECT * FROM memory_fts WHERE memory_fts MATCH %s")
        assert "ts_doc @@ plainto_tsquery('english', %s)" in result

    def test_match_no_injection_uses_plainto(self):
        """Embedded MATCH value goes to plainto_tsquery, not bare to_tsquery — safe."""
        sql = "SELECT * FROM memory_fts WHERE memory_fts MATCH 'OR 1=1'"
        result = translate_sql(sql)
        assert "plainto_tsquery" in result
        # Must not contain a bare to_tsquery() call (which requires tsquery syntax)
        assert not re.search(r"(?<!plain)to_tsquery", result)

    def test_sqlite_passthrough(self):
        sql = "SELECT * FROM memory_fts WHERE memory_fts MATCH 'test'"
        assert translate_sql(sql, backend="sqlite") == sql

    def test_match_uppercase(self):
        sql = "SELECT * FROM memory_fts WHERE memory_fts MATCH 'alpha'"
        result = translate_sql(sql)
        assert "plainto_tsquery" in result


class TestFts5CreateVirtualTable:
    def test_creates_regular_table(self):
        sql = "CREATE VIRTUAL TABLE memory_fts USING fts5(content, type, tags)"
        result = translate_sql(sql)
        assert "CREATE VIRTUAL TABLE" not in result
        assert "VIRTUAL" not in result
        assert "CREATE TABLE IF NOT EXISTS memory_fts" in result

    def test_tsvector_column_added(self):
        sql = "CREATE VIRTUAL TABLE memory_fts USING fts5(content, type, tags)"
        result = translate_sql(sql)
        assert "ts_doc TSVECTOR" in result

    def test_fts5_columns_become_text(self):
        sql = "CREATE VIRTUAL TABLE memory_fts USING fts5(content, type, tags)"
        result = translate_sql(sql)
        assert "content TEXT" in result
        assert "type TEXT" in result
        assert "tags TEXT" in result

    def test_create_if_not_exists(self):
        sql = "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content)"
        result = translate_sql(sql)
        assert "fts5" not in result.lower()
        assert "CREATE TABLE IF NOT EXISTS" in result

    def test_fts5_options_excluded(self):
        """FTS5 options like tokenize='porter' must not become column names."""
        sql = "CREATE VIRTUAL TABLE t USING fts5(body, tokenize='porter')"
        result = translate_sql(sql)
        assert "tokenize" not in result
        assert "body TEXT" in result

    def test_sqlite_passthrough(self):
        sql = "CREATE VIRTUAL TABLE memory_fts USING fts5(content)"
        assert translate_sql(sql, backend="sqlite") == sql


class TestFts5SnippetHighlight:
    def test_snippet_to_ts_headline(self):
        sql = "SELECT snippet(memory_fts, 0, '<b>', '</b>', '...', 10) FROM memory_fts WHERE memory_fts MATCH %s"
        result = translate_sql(sql)
        assert "snippet(" not in result
        assert "ts_headline('english'" in result

    def test_snippet_preserves_selectors(self):
        sql = "SELECT snippet(memory_fts, 0, '<mark>', '</mark>', '...', 5) FROM memory_fts WHERE memory_fts MATCH %s"
        result = translate_sql(sql)
        assert "<mark>" in result
        assert "</mark>" in result

    def test_highlight_to_ts_headline(self):
        sql = "SELECT highlight(memory_fts, 0, '<b>', '</b>') FROM memory_fts WHERE memory_fts MATCH %s"
        result = translate_sql(sql)
        assert "highlight(" not in result
        assert "ts_headline('english'" in result

    def test_snippet_sqlite_passthrough(self):
        sql = "SELECT snippet(t, 0, '<b>', '</b>', '...', 10) FROM t WHERE t MATCH %s"
        assert translate_sql(sql, backend="sqlite") == sql


class TestFts5RankOrdering:
    def test_order_by_rank_flipped(self):
        """FTS5 rank is negative-best; ts_rank() is positive-best — flip direction."""
        sql = "SELECT * FROM memory_fts WHERE memory_fts MATCH %s ORDER BY rank"
        result = translate_sql(sql)
        assert "ORDER BY rank DESC" in result

    def test_order_by_rank_desc_unchanged(self):
        sql = "SELECT * FROM memory_fts WHERE memory_fts MATCH %s ORDER BY rank DESC"
        result = translate_sql(sql)
        assert result.count("DESC") == 1

    def test_non_fts5_rank_not_flipped(self):
        sql = "SELECT * FROM scores ORDER BY rank"
        result = translate_sql(sql)
        assert "ORDER BY rank DESC" not in result
