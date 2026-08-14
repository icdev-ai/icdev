# CUI // SP-CTI
"""A capped IQE scan must never be answered at confidence_score 1.0.

The Cortex IQE adapters issue ``ORDER BY created_at DESC LIMIT <cap>`` with no
WHERE clause, and ``Executor.run`` applies the query's where clauses in PYTHON
afterwards. So a question whose window matches more rows than the cap was
answered from the newest ``cap`` rows only — and ``analyst._label_rows_result``
stamped that undercount ``grounding="rows_by_construction"``,
``confidence="include"``, ``confidence_score: 1.0``.

Not a crash: a confident falsehood, on the exact surface an operator uses to
audit Cortex itself (ctx-trust-04).

Everything below runs against a REAL seeded ``cortex_audit`` table holding 700
rows — the real adapter, the real SQL, the real executor. Nothing is mocked;
mocking the executor is precisely how a cap-vs-filter ordering bug hides.

Seed shape, chosen so the cap is *load-bearing* rather than incidental:

    600 rows dated 2026-01-05   (the answer to "before 2026-06-01")
    100 rows dated 2026-08-10   (newer, and NOT part of the answer)

``ORDER BY created_at DESC LIMIT 500`` therefore returns the 100 new rows plus
only 400 of the 600 that match — filtering in Python then yields 400. The true
answer is 600.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.cortex.analyst import ask
from tools.cortex.schemas import CortexContext
from tools.iqe import executor as _executor
from tools.iqe.adapters import cortex as _adapters
from tools.iqe.executor import execute_query_with_meta, register_collection
from tools.iqe.parser import parse
from tools.quality.citation_grounding import CONF_INCLUDE

_OLD_ROWS = 600          # match the question's window
_NEW_ROWS = 100          # newer, outside the window
_TOTAL = _OLD_ROWS + _NEW_ROWS
_LOW_CAP = 500           # < _OLD_ROWS, so the cap truncates the answer

_QUESTION = "cortex audit created_at before 2026-06-01"
_IQE_MATCHING = 'foreach a in {coll}{call} where a.created_at < "2026-06-01" select *'

_SCHEMA = """
CREATE TABLE cortex_audit (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    function        TEXT NOT NULL DEFAULT 'cortex',
    agent_id        TEXT,
    user_id         TEXT,
    gates_json      TEXT,
    outcome         TEXT NOT NULL DEFAULT 'pass',
    blocked         INTEGER NOT NULL DEFAULT 0,
    provenance_id   TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """A real SQLite ``cortex_audit`` holding 700 rows; ICDEV_DB_PATH points at it."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO cortex_audit (id, function, outcome, blocked, created_at) "
        "VALUES (?, 'cortex.ask', 'blocked', 1, ?)",
        [(f"old-{i:04d}", f"2026-01-05T00:{i // 60:02d}:{i % 60:02d}") for i in range(_OLD_ROWS)],
    )
    conn.executemany(
        "INSERT INTO cortex_audit (id, function, outcome, blocked, created_at) "
        "VALUES (?, 'cortex.ask', 'pass', 0, ?)",
        [(f"new-{i:04d}", f"2026-08-10T00:{i // 60:02d}:{i % 60:02d}") for i in range(_NEW_ROWS)],
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    return db_path


@pytest.fixture
def open_conn(seeded_db):
    from tools.db.storage import get_connection

    conn = get_connection()
    yield conn
    conn.close()


def _run(conn, cap=None, coll="cortex.audit"):
    call = f"({cap})" if cap is not None else ""
    return execute_query_with_meta(parse(_IQE_MATCHING.format(coll=coll, call=call)), conn)


# ---------------------------------------------------------------------------
# The seed actually reproduces the defect
# ---------------------------------------------------------------------------


def test_the_seed_puts_more_matching_rows_than_the_cap_behind_newer_rows(open_conn):
    """Guards the fixture itself: without this shape the cap proves nothing."""
    cur = open_conn.execute("SELECT COUNT(*) FROM cortex_audit")
    assert cur.fetchall()[0][0] == _TOTAL
    assert _OLD_ROWS > _LOW_CAP, "the cap must cut into the matching rows"


# ---------------------------------------------------------------------------
# Executor level — the row cap is reported, and the count it produces is a
# LOWER BOUND rather than an answer
# ---------------------------------------------------------------------------


def test_an_uncapped_scan_returns_the_correct_count(open_conn):
    result = _run(open_conn)

    assert len(result.rows) == _OLD_ROWS
    assert result.complete
    assert result.incomplete == []


def test_a_capped_scan_undercounts_and_says_so(open_conn):
    """The historical wrong answer — now carrying its own contradiction."""
    result = _run(open_conn, cap=_LOW_CAP)

    # This IS the wrong number the bug produced: the newest 500 rows hold only
    # 400 of the 600 that match, because the cap ran before the filter.
    assert len(result.rows) == _LOW_CAP - _NEW_ROWS == 400
    assert len(result.rows) != _OLD_ROWS

    assert not result.complete, (
        "a scan cut short by the row cap reported itself as the complete answer"
    )
    assert result.incomplete == [
        {"collection": "cortex.audit", "reason": "row_cap", "limit": _LOW_CAP}
    ]


def test_a_cap_that_exactly_equals_the_row_count_is_not_truncated(open_conn):
    """Off-by-one guard: exactly-cap rows is a complete collection, not a cut one.

    ``len(rows) >= limit`` would flag every honest full-table answer, and a
    truncation warning that fires on correct answers is one nobody reads.
    """
    result = _run(open_conn, cap=_TOTAL)

    assert len(result.rows) == _OLD_ROWS
    assert result.complete


def test_the_adapter_returns_at_most_the_cap(open_conn):
    """The +1 probe row must not leak into the result set."""
    rows = _adapters.audit_adapter(open_conn, limit=_LOW_CAP)

    assert len(rows) == _LOW_CAP
    assert rows.incomplete[0]["reason"] == "row_cap"


# ---------------------------------------------------------------------------
# ask() level — the label, which is what the operator actually sees
# ---------------------------------------------------------------------------


@pytest.fixture
def capped_collection():
    """Bind cortex.audit to a cap smaller than the answer.

    The REAL adapter over the REAL table, just with the cap lowered — the
    default is 10000, which this seed would have to grow twentyfold to reach.
    """
    saved = dict(_executor._default._registry)
    register_collection("cortex.audit", lambda conn: _adapters.audit_adapter(conn, limit=_LOW_CAP))
    yield
    _executor._default._registry.clear()
    _executor._default._registry.update(saved)


def test_ask_returns_the_correct_count_under_the_default_cap(open_conn):
    result = ask(_QUESTION, collections=["cortex.audit"], conn=open_conn, ctx=CortexContext())

    assert result.data["row_count"] == _OLD_ROWS
    assert result.data["truncated"] is False
    assert result.metadata["confidence_score"] == 1.0
    assert result.metadata["grounding"] == "rows_by_construction"


def test_ask_never_labels_a_truncated_answer_maximally_confident(open_conn, capped_collection):
    result = ask(_QUESTION, collections=["cortex.audit"], conn=open_conn, ctx=CortexContext())

    # The count is still the undercount — truncation cannot be un-truncated
    # after the fact. What must not happen is presenting it as certain.
    assert result.data["row_count"] < _OLD_ROWS
    assert result.data["truncated"] is True
    assert result.metadata["confidence"] != "include"
    assert result.metadata["confidence_score"] < CONF_INCLUDE
    assert result.metadata["grounding"] == "rows_by_construction_truncated"
    assert result.metadata["truncated"] is True
    assert result.metadata["incomplete"][0]["reason"] == "row_cap"


def test_a_truncated_answer_says_so_in_the_text(open_conn, capped_collection):
    """metadata alone is not enough — the prose is what a human reads."""
    result = ask(_QUESTION, collections=["cortex.audit"], conn=open_conn, ctx=CortexContext())

    assert "truncated" in result.text.lower()
    assert "lower bound" in result.text.lower()


def test_an_untruncated_answer_carries_no_truncation_caveat(open_conn):
    result = ask(_QUESTION, collections=["cortex.audit"], conn=open_conn, ctx=CortexContext())

    assert "truncated" not in result.text.lower()
    assert "truncated" not in result.metadata


# ---------------------------------------------------------------------------
# The related unbounded path: an unregistered collection used to be scanned
# with no LIMIT at all, into Python memory, and then filtered
# ---------------------------------------------------------------------------


def test_an_unregistered_collection_scan_is_bounded_and_reports_its_cap(open_conn, monkeypatch):
    """``cortex_audit`` is a real table with no registered adapter — the fallback."""
    monkeypatch.setattr(_executor, "FALLBACK_ROW_CAP", _LOW_CAP)

    result = _run(open_conn, coll="cortex_audit")

    assert len(result.rows) <= _LOW_CAP
    assert not result.complete
    assert result.incomplete[0]["reason"] == "row_cap"
    assert result.incomplete[0]["limit"] == _LOW_CAP


def test_an_unregistered_collection_under_the_cap_is_complete(open_conn, monkeypatch):
    monkeypatch.setattr(_executor, "FALLBACK_ROW_CAP", _TOTAL + 1)

    result = _run(open_conn, coll="cortex_audit")

    assert len(result.rows) == _OLD_ROWS
    assert result.complete


# ---------------------------------------------------------------------------
# union: a collection that failed to fetch is a SHORT answer, not a complete one
# ---------------------------------------------------------------------------


def test_a_union_that_lost_a_collection_is_reported_incomplete():
    """Same defect as the cap: fewer rows than asked for, labelled certain.

    No DB here on purpose — ``_fetch_union`` fans out onto a thread pool and a
    sqlite3 connection is thread-bound, so a real connection would fail for a
    reason that has nothing to do with what this asserts (the propagation).
    """
    saved = dict(_executor._default._registry)

    def _boom(conn):
        raise RuntimeError("relation does not exist")

    register_collection("cortex.fake_ok", lambda conn: [{"id": 1}, {"id": 2}])
    register_collection("cortex.fake_broken", _boom)
    try:
        result = execute_query_with_meta(
            parse('foreach a in union("cortex.fake_ok", "cortex.fake_broken") select *'), None
        )
    finally:
        _executor._default._registry.clear()
        _executor._default._registry.update(saved)

    assert len(result.rows) == 2
    assert not result.complete
    assert result.incomplete[0]["reason"] == "fetch_failed"
    assert result.incomplete[0]["collection"] == "cortex.fake_broken"
