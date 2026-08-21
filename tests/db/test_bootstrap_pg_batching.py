# CUI // SP-CTI
"""tools.db.bootstrap_pg loads the snapshot in batches, and its splitter respects pg_dump's syntax.

The regenerated snapshot (1,818 tables, 2,732 indexes, 3,032 ALTERs) loaded as
ONE transaction overflowed a stock server's lock table on CI
(``out of shared memory / increase max_locks_per_transaction``) while loading
fine on a developer instance with max_connections=300. Red-first: at the merge
base ``split_statements`` / ``LOAD_BATCH`` / ``PARTIAL_SENTINEL`` do not exist.
"""
from __future__ import annotations

import re

from tools.db import bootstrap_pg

FUNCTION_BODY = """--
-- Name: touch(); Type: FUNCTION
--

CREATE FUNCTION public.touch() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE public.a (
    id text NOT NULL,
    note text DEFAULT 'semi;colon; inside'::text,
    CONSTRAINT a_check CHECK ((note = ANY (ARRAY['x;y'::text, 'it''s;'::text])))
);

CREATE FUNCTION public.tagged() RETURNS text
    LANGUAGE sql
    AS $body$ SELECT 'a;b'; $body$;

-- a comment with a ; in it
CREATE INDEX idx_a ON public.a USING btree (id);
"""


def test_splitter_respects_dollar_quotes_strings_parens_and_comments():
    stmts = bootstrap_pg.split_statements(FUNCTION_BODY)
    kinds = [re.search(r"^(CREATE \w+)", s, re.M).group(1) for s in stmts]
    assert kinds == ["CREATE FUNCTION", "CREATE TABLE", "CREATE FUNCTION", "CREATE INDEX"], kinds
    assert "RETURN NEW;" in stmts[0] and stmts[0].rstrip().endswith("$$;")
    assert "it''s;" in stmts[1]
    assert "$body$ SELECT 'a;b'; $body$;" in stmts[2]
    assert stmts[3].startswith("-- a comment with a ; in it")


def test_the_real_snapshot_splits_into_whole_statements():
    sql = bootstrap_pg._strip_psql_meta(bootstrap_pg.SCHEMA_FILE.read_text(encoding="utf-8-sig"))
    stmts = bootstrap_pg.split_statements(sql)
    assert len(stmts) > 5000
    # every CREATE TABLE in the file is the head of exactly one statement
    creates_in_file = len(re.findall(r"^CREATE TABLE ", sql, re.M))
    creates_in_stmts = sum(1 for s in stmts if re.search(r"^CREATE TABLE ", s, re.M))
    assert creates_in_stmts == creates_in_file
    # no statement is a dangling fragment: each ends with ';' and has balanced parens
    for s in stmts:
        assert s.rstrip().endswith(";"), s[:120]
        assert s.count("(") == s.count(")"), s[:120]
    # function bodies stayed whole
    bodies = [s for s in stmts if "$$" in s]
    assert bodies and all(s.count("$$") % 2 == 0 for s in bodies)
    # and the batching bounds what one transaction touches
    assert 0 < bootstrap_pg.LOAD_BATCH <= 500


def test_check_reports_a_partial_load_as_not_bootstrapped(monkeypatch):
    class _Cur:
        def __init__(self, tables, sentinel):
            self._tables, self._sentinel, self._last = tables, sentinel, None

        def execute(self, sql, *a):
            self._last = sql

        def fetchone(self):
            if "information_schema.tables" in self._last:
                return (self._tables,)
            if bootstrap_pg.PARTIAL_SENTINEL in self._last:
                return ("public." + bootstrap_pg.PARTIAL_SENTINEL if self._sentinel else None,)
            return (None,)

    class _Conn:
        def __init__(self, tables, sentinel):
            self.cur = _Cur(tables, sentinel)

        def cursor(self):
            return self.cur

        def close(self):
            pass

    monkeypatch.setattr(bootstrap_pg, "_raw_pg_conn", lambda *a, **k: _Conn(900, sentinel=True))
    partial = bootstrap_pg.check()
    assert partial["partial"] is True and partial["bootstrapped"] is False
    monkeypatch.setattr(bootstrap_pg, "_raw_pg_conn", lambda *a, **k: _Conn(900, sentinel=False))
    whole = bootstrap_pg.check()
    assert whole["partial"] is False and whole["bootstrapped"] is True
