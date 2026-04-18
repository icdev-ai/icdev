# CUI // SP-CTI
"""Parse + execute 5 ODC observability seed .iqe queries against a fixture coverage table."""
from __future__ import annotations

import pathlib
import sqlite3

from tools.iqe.ast_nodes import AttrRef, ForeachNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_QUERY_DIR = pathlib.Path(__file__).parent.parent / "context" / "iqe" / "queries" / "observability"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS odc_coverage (
    technique_id   TEXT NOT NULL,
    tactic_id      TEXT NOT NULL,
    signal_source  TEXT,
    coverage_state TEXT NOT NULL,
    fp_rate        REAL NOT NULL DEFAULT 0.0,
    severity       TEXT NOT NULL
);
"""

_ROWS = [
    ("T1059", "execution",         "src-os-log",      "none",    0.0,  "critical"),
    ("T1078", "defense-evasion",   "src-iam",         "partial", 0.05, "high"),
    ("T1003", "credential-access", None,              "none",    0.0,  "critical"),
    ("T1110", "credential-access", "src-iam",         "full",    0.15, "high"),
    ("T1566", "initial-access",    "src-endpoint",    "none",    0.0,  "critical"),
    ("T1083", "discovery",         "src-os-log",      "partial", 0.08, "medium"),
    ("T1021", "lateral-movement",  None,              "none",    0.0,  "high"),
    ("T1190", "initial-access",    "src-network-log", "full",    0.25, "critical"),
]


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO odc_coverage "
        "(technique_id, tactic_id, signal_source, coverage_state, fp_rate, severity) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        _ROWS,
    )
    conn.commit()
    return conn


def _executor() -> Executor:
    return Executor()


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


# 1 — uncovered_techniques -------------------------------------------------------

def test_uncovered_techniques() -> None:
    q = parse(_read("uncovered_techniques.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "t"
    assert q.collection == AttrRef(["odc_coverage"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # T1059, T1003, T1566, T1021 all have coverage_state == "none"
    assert len(rows) == 4
    tids = {r["technique_id"] for r in rows}
    assert "T1059" in tids
    assert "T1003" in tids
    assert "T1566" in tids
    assert "T1021" in tids


# 2 — weak_coverage_by_tactic ----------------------------------------------------

def test_weak_coverage_by_tactic() -> None:
    q = parse(_read("weak_coverage_by_tactic.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "t"
    assert q.collection == AttrRef(["odc_coverage"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # T1078 (defense-evasion/partial) and T1083 (discovery/partial)
    assert len(rows) == 2
    tactics = {r["tactic_id"] for r in rows}
    assert "defense-evasion" in tactics
    assert "discovery" in tactics


# 3 — signal_source_gaps ---------------------------------------------------------

def test_signal_source_gaps() -> None:
    q = parse(_read("signal_source_gaps.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "t"
    assert q.collection == AttrRef(["odc_coverage"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # T1003 and T1021 have signal_source == NULL
    assert len(rows) == 2
    tids = {r["technique_id"] for r in rows}
    assert "T1003" in tids
    assert "T1021" in tids
    for r in rows:
        assert r["signal_source"] is None


# 4 — missing_critical_tids ------------------------------------------------------

def test_missing_critical_tids() -> None:
    q = parse(_read("missing_critical_tids.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "t"
    assert q.collection == AttrRef(["odc_coverage"])
    assert len(q.where_clauses) == 2

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # T1059, T1003, T1566 — critical + none; T1190 is critical but full — excluded
    assert len(rows) == 3
    tids = {r["technique_id"] for r in rows}
    assert "T1059" in tids
    assert "T1003" in tids
    assert "T1566" in tids
    assert "T1190" not in tids
    for r in rows:
        assert r["severity"] == "critical"
        assert r["coverage_state"] == "none"


# 5 — fp_rate_check --------------------------------------------------------------

def test_fp_rate_check() -> None:
    q = parse(_read("fp_rate_check.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "t"
    assert q.collection == AttrRef(["odc_coverage"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # T1110 (fp=0.15) and T1190 (fp=0.25) exceed the 0.1 threshold
    assert len(rows) == 2
    for r in rows:
        assert r["fp_rate"] > 0.1
    tids = {r["technique_id"] for r in rows}
    assert "T1110" in tids
    assert "T1190" in tids
