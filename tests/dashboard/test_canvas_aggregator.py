# CUI // SP-CTI
"""Tests for tools/dashboard/canvas_aggregator.py.

Covers all four public aggregator functions:
  - get_canvas_compliance_summary()
  - get_canvas_finding_counts()
  - get_recent_canvas_findings()
  - get_canvas_activity_trend()

Uses in-memory SQLite databases so that no real canvas DB file is required.
Three of the four functions still read canvases through
``agg._get_canvas_conn`` and are injected via a patch on it. The fourth,
``get_canvas_compliance_summary``, was re-pointed at the canonical posture
aggregator by cnr-cc-02 and now reaches the canvases through
``tools.canvas_compliance.posture._open_canvas_connection`` plus the main
``get_connection()`` — patching only ``_get_canvas_conn`` no longer intercepts
it, so those tests silently read whatever ``data/*.db`` the checkout happens to
carry (9 canvas rows on a freshly seeded worktree, 11 on the shared checkout).
``_patch_posture`` closes that hole; see ``_patch_conns`` for the other three.

Each test class also exercises the 60-second result cache independently.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._sql_compat import translating  # noqa: E402

import tools.canvas_compliance.posture as posture  # noqa: E402
import tools.dashboard.canvas_aggregator as agg  # noqa: E402
from tools.dashboard.canvas_aggregator import (  # noqa: E402
    get_canvas_activity_trend,
    get_canvas_compliance_summary,
    get_canvas_finding_counts,
    get_recent_canvas_findings,
    invalidate_cache,
)


# ---------------------------------------------------------------------------
# In-memory SQLite helpers
# ---------------------------------------------------------------------------

def _make_conn() -> Any:
    """Create an in-memory SQLite connection with dict-like row access.

    Wrapped in a translating connection because the code under test authors
    PostgreSQL SQL: ``get_canvas_activity_trend`` binds its cutoff with ``%s``,
    which a bare ``sqlite3`` connection rejects with ``near "%": syntax
    error``.  The aggregator catches that inside a best-effort ``except``, so
    the test used to assert against a no-op it had caused itself.

    ``unclosable=True`` because ``posture.compute_canvas_posture`` closes every
    canvas connection in a ``finally`` block, and an in-memory database dies
    with its connection.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return translating(conn, unclosable=True)


def _json_canvas_conn(
    atbl: str,
    score_col: str,
    grade_col: str,
    time_col: str,
    audit_tbl: str,
    audit_time_col: str,
    *,
    assessments: list[dict] | None = None,
    audit_rows: list[dict] | None = None,
) -> sqlite3.Connection:
    """Return a populated in-memory connection for a JSON-based canvas."""
    conn = _make_conn()
    extra_col = f"{grade_col} TEXT DEFAULT 'N/A'," if grade_col else ""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {atbl} ("
        f"  id TEXT PRIMARY KEY,"
        f"  design_id TEXT,"
        f"  {score_col} REAL DEFAULT 0,"
        f"  {extra_col}"
        f"  cat1_findings INTEGER DEFAULT 0,"
        f"  cat2_findings INTEGER DEFAULT 0,"
        f"  cat3_findings INTEGER DEFAULT 0,"
        f"  findings_json TEXT DEFAULT '[]',"
        f"  {time_col} TEXT DEFAULT CURRENT_TIMESTAMP"
        f")"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {audit_tbl} ("
        f"  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        f"  action TEXT,"
        f"  {audit_time_col} TEXT DEFAULT CURRENT_TIMESTAMP"
        f")"
    )
    for i, row in enumerate(assessments or []):
        # posture scores JSON canvases with a latest-per-design_id correlated
        # subquery; a NULL design_id never equals itself, so a row without one
        # is invisible to the aggregate and the canvas scores 0.
        row = {"design_id": f"design-{i}", **row}
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        conn.execute(f"INSERT INTO {atbl} ({cols}) VALUES ({ph})", list(row.values()))
    for row in audit_rows or []:
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        conn.execute(f"INSERT INTO {audit_tbl} ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()
    return conn


def _direct_canvas_conn(
    findings_tbl: str,
    checks_tbl: str,
    audit_tbl: str,
    audit_time_col: str,
    *,
    findings: list[dict] | None = None,
    checks: list[dict] | None = None,
    audit_rows: list[dict] | None = None,
) -> sqlite3.Connection:
    """Return a populated in-memory connection for a direct-row canvas."""
    conn = _make_conn()
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {findings_tbl} ("
        f"  id TEXT PRIMARY KEY,"
        f"  rule_id TEXT,"
        f"  title TEXT,"
        f"  description TEXT,"
        f"  severity TEXT,"
        f"  status TEXT DEFAULT 'open',"
        f"  affected_entity TEXT,"
        f"  created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        f")"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {checks_tbl} ("
        f"  id TEXT PRIMARY KEY,"
        f"  passed INTEGER DEFAULT 0,"
        f"  failed INTEGER DEFAULT 0,"
        f"  ran_at TEXT DEFAULT CURRENT_TIMESTAMP"
        f")"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {audit_tbl} ("
        f"  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        f"  action TEXT,"
        f"  {audit_time_col} TEXT DEFAULT CURRENT_TIMESTAMP"
        f")"
    )
    for tbl, rows in [
        (findings_tbl, findings or []),
        (checks_tbl, checks or []),
        (audit_tbl, audit_rows or []),
    ]:
        for row in rows:
            cols = ", ".join(row.keys())
            ph = ", ".join("?" * len(row))
            conn.execute(f"INSERT INTO {tbl} ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Connection-map builder
# ---------------------------------------------------------------------------

def _build_all_conns(
    extra_assessments: dict[str, list[dict]] | None = None,
    extra_findings: dict[str, list[dict]] | None = None,
    extra_checks: dict[str, list[dict]] | None = None,
    extra_audit: dict[str, list[dict]] | None = None,
    skip_canvases: set[str] | None = None,
) -> dict[str, Any]:
    """Return a {db_filename: connection|None} map for all 7 canvases."""
    ea = extra_assessments or {}
    ef = extra_findings or {}
    ec = extra_checks or {}
    eau = extra_audit or {}
    skip = skip_canvases or set()
    conns: dict[str, Any] = {}

    for (db, _label, slug, atbl, score_col, grade_col, time_col, audit_tbl, audit_tc) in agg._JSON_CANVASES:
        if slug in skip:
            conns[db] = None
            continue
        conns[db] = _json_canvas_conn(
            atbl, score_col, grade_col, time_col, audit_tbl, audit_tc,
            assessments=ea.get(slug),
            audit_rows=eau.get(slug),
        )

    for (db, _label, slug, findings_tbl, checks_tbl, audit_tbl, audit_tc) in agg._DIRECT_CANVASES:
        if slug in skip:
            conns[db] = None
            continue
        conns[db] = _direct_canvas_conn(
            findings_tbl, checks_tbl, audit_tbl, audit_tc,
            findings=ef.get(slug),
            checks=ec.get(slug),
            audit_rows=eau.get(slug),
        )

    return conns


def _patch_conns(conns: dict[str, Any]):
    """Return a patch context that routes _get_canvas_conn to in-memory DBs.

    Covers get_canvas_finding_counts, get_recent_canvas_findings and
    get_canvas_activity_trend.  It does **not** cover
    get_canvas_compliance_summary — use :func:`_patch_posture` for that.
    """

    def _fake(path: Path) -> Any | None:
        s = str(path)
        for db, conn in conns.items():
            if s.endswith(db):
                return conn
        return None

    return patch.object(agg, "_get_canvas_conn", side_effect=_fake)


# posture identifies canvases by display name; the aggregator's own registry
# keys them by db filename.  Only these seven are modelled by _build_all_conns;
# posture's other four (Agentic AI, AI/ML, QDC, Migration) resolve to None and
# are skipped, which is what keeps the summary at a deterministic seven rows.
_CANVAS_DB_BY_NAME: dict[str, str] = {
    "Security": "security_canvas.db",
    "Infra": "infra_canvas.db",
    "Observability": "observability_canvas.db",
    "Boundary": "boundary_canvas.db",
    "Data": "data_canvas.db",
    "Network": "network_canvas.db",
    "Pipeline": "pipeline_canvas.db",
}


@contextmanager
def _patch_posture(conns: dict[str, Any]):
    """Route get_canvas_compliance_summary at the in-memory canvas DBs.

    Two seams, because the summary reaches the data two ways:

    * ``posture._open_canvas_connection`` — one connection per canvas.
    * ``agg.get_connection`` — the main icdev DB, used only for the GovLift
      STIG rollup.  An empty in-memory DB has no ``govlift_stig_checks``, so
      posture skips that row, as it does the ZIG "Zero Trust" row whose
      ``zig_*`` tables the fake Security connection does not carry.
    """

    def _fake_open(canvas_name: str) -> Any | None:
        db = _CANVAS_DB_BY_NAME.get(canvas_name)
        return conns.get(db) if db else None

    main = _make_conn()
    with patch.object(posture, "_open_canvas_connection", side_effect=_fake_open), \
            patch.object(agg, "get_connection", return_value=main):
        yield


# ---------------------------------------------------------------------------
# Autouse fixture — clear module-level caches between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_caches():
    agg._result_cache.clear()
    agg._conn_cache.clear()
    yield
    agg._result_cache.clear()
    agg._conn_cache.clear()


# ---------------------------------------------------------------------------
# get_canvas_compliance_summary
# ---------------------------------------------------------------------------

class TestGetCanvasComplianceSummary:
    """The summary delegates to posture.compute_canvas_posture (cnr-cc-02).

    Two contract changes came with that delegation and are asserted here rather
    than papered over: ``grade`` and ``last_assessed`` are now always empty
    (posture is a numeric view), and a canvas whose DB will not open is
    *omitted* from the result instead of appearing with ``available=False``.
    """

    def test_returns_seven_rows(self):
        conns = _build_all_conns()
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        assert len(result) == 7
        assert {r["slug"] for r in result} == {
            "security", "infra", "observability", "boundary",
            "data", "network", "pipeline",
        }

    def test_row_has_required_keys(self):
        conns = _build_all_conns()
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        required = {"name", "slug", "latest_score", "grade", "open_findings", "closed_findings", "last_assessed", "available"}
        for row in result:
            assert required <= set(row.keys()), f"Missing keys in row: {row}"

    def test_missing_db_canvas_is_omitted(self):
        conns = _build_all_conns(skip_canvases={"security", "network"})
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        slugs = {r["slug"]: r for r in result}
        assert "security" not in slugs
        assert "network" not in slugs
        assert len(slugs) == 5
        for slug, row in slugs.items():
            assert row["available"], f"{slug} should be available"

    def test_json_canvas_score_and_counts(self):
        conns = _build_all_conns(
            extra_assessments={
                "boundary": [
                    {
                        "id": "a1",
                        "score": 75.0,
                        "grade": "C",
                        "cat1_findings": 1,
                        "cat2_findings": 2,
                        "created_at": "2026-04-10T12:00:00",
                    }
                ]
            }
        )
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        row = next(r for r in result if r["slug"] == "boundary")
        assert row["latest_score"] == 75.0
        assert row["open_findings"] == 3      # cat1 + cat2 + cat3
        assert row["closed_findings"] == 0
        # posture is a numeric view: no grade, no assessment timestamp.
        assert row["grade"] == ""
        assert row["last_assessed"] == ""

    def test_direct_canvas_score_from_checks(self):
        conns = _build_all_conns(
            extra_checks={
                "network": [
                    {"id": "c1", "passed": 8, "failed": 2, "ran_at": "2026-04-11T09:00:00"}
                ]
            },
        )
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        row = next(r for r in result if r["slug"] == "network")
        assert row["latest_score"] == 80.0    # 8 / (8+2) * 100
        assert row["open_findings"] == 2      # failed checks
        assert row["closed_findings"] == 8    # passed checks

    def test_direct_canvas_falls_back_to_findings_without_checks(self):
        conns = _build_all_conns(
            extra_findings={
                "network": [
                    {"id": "f1", "rule_id": "R1", "title": "Open port", "severity": "CAT2",
                     "status": "open", "affected_entity": "FW", "created_at": "2026-04-11"},
                    {"id": "f2", "rule_id": "R2", "title": "Weak cipher", "severity": "CAT3",
                     "status": "remediated", "affected_entity": "VPN", "created_at": "2026-04-10"},
                ]
            },
        )
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        row = next(r for r in result if r["slug"] == "network")
        assert row["open_findings"] == 1
        assert row["closed_findings"] == 1
        assert row["latest_score"] == 50.0    # 1 closed / 2 total * 100

    def test_empty_tables_yield_defaults(self):
        conns = _build_all_conns()
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        scores = {r["slug"]: r["latest_score"] for r in result}
        # An empty canvas is not "unknown" under posture — it scores either a
        # clean 100 (no risk recorded, no check failed) or 0 (no assessment to
        # average). Pinning the exact split keeps a silent rule change visible.
        assert scores == {
            "security": 100.0,      # 100 - avg(risk_score=0)
            "network": 100.0,       # no checks and no findings
            "pipeline": 100.0,
            "infra": 0.0,           # avg(score) over no rows
            "data": 0.0,
            "boundary": 0.0,
            "observability": 0.0,
        }
        for row in result:
            assert row["open_findings"] == 0
            assert row["closed_findings"] == 0
            assert row["grade"] == ""
            assert row["last_assessed"] == ""

    def test_result_is_cached_on_second_call(self):
        conns = _build_all_conns()
        call_count = [0]
        real = posture.compute_canvas_posture

        def counting_posture(conn):
            call_count[0] += 1
            return real(conn)

        with _patch_posture(conns), \
                patch.object(posture, "compute_canvas_posture", side_effect=counting_posture):
            get_canvas_compliance_summary()
            assert call_count[0] == 1
            get_canvas_compliance_summary()
            assert call_count[0] == 1, "Second call should use cached result"

    def test_malformed_findings_json_is_skipped(self):
        conns = _build_all_conns(
            extra_assessments={
                "infra": [
                    {"id": "a1", "score": 50.0, "created_at": "2026-04-01", "findings_json": "NOT_JSON"},
                ]
            }
        )
        with _patch_posture(conns):
            result = get_canvas_compliance_summary()
        row = next(r for r in result if r["slug"] == "infra")
        # Must not raise; the score still comes through and counts stay at zero.
        assert row["latest_score"] == 50.0
        assert row["open_findings"] == 0
        assert row["closed_findings"] == 0


# ---------------------------------------------------------------------------
# get_canvas_finding_counts
# ---------------------------------------------------------------------------

class TestGetCanvasFindingCounts:

    def test_empty_returns_zero_counts(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            result = get_canvas_finding_counts()
        assert result == {"CAT1": 0, "CAT2": 0, "CAT3": 0, "total": 0}

    def test_json_canvas_open_findings_only(self):
        findings = [
            {"rule_id": "A", "title": "A", "severity": "CAT1", "status": "open"},
            {"rule_id": "B", "title": "B", "severity": "CAT2", "status": "open"},
            {"rule_id": "C", "title": "C", "severity": "CAT1", "status": "remediated"},  # closed
            {"rule_id": "D", "title": "D", "severity": "CAT3", "status": "open"},
        ]
        conns = _build_all_conns(
            extra_assessments={
                "observability": [
                    {"id": "a1", "score": 60.0, "grade": "D", "created_at": "2026-04-10",
                     "findings_json": json.dumps(findings)},
                ]
            }
        )
        with _patch_conns(conns):
            result = get_canvas_finding_counts()
        assert result["CAT1"] == 1
        assert result["CAT2"] == 1
        assert result["CAT3"] == 1
        assert result["total"] == 3

    def test_direct_canvas_open_findings_only(self):
        conns = _build_all_conns(
            extra_findings={
                "pipeline": [
                    {"id": "f1", "rule_id": "R1", "title": "T1", "severity": "CAT1",
                     "status": "open", "affected_entity": "", "created_at": "2026-04-10"},
                    {"id": "f2", "rule_id": "R2", "title": "T2", "severity": "CAT1",
                     "status": "accepted_risk", "affected_entity": "", "created_at": "2026-04-10"},
                    {"id": "f3", "rule_id": "R3", "title": "T3", "severity": "CAT2",
                     "status": "open", "affected_entity": "", "created_at": "2026-04-10"},
                ]
            }
        )
        with _patch_conns(conns):
            result = get_canvas_finding_counts()
        assert result["CAT1"] == 1
        assert result["CAT2"] == 1
        assert result["total"] == 2

    def test_aggregates_across_multiple_canvases(self):
        conns = _build_all_conns(
            extra_assessments={
                "boundary": [
                    {"id": "a1", "score": 80.0, "grade": "B", "created_at": "2026-04-10",
                     "findings_json": json.dumps([{"rule_id": "X", "title": "X", "severity": "CAT1", "status": "open"}])},
                ],
                "data": [
                    {"id": "a2", "score": 70.0, "created_at": "2026-04-10",
                     "findings_json": json.dumps([{"rule_id": "Y", "title": "Y", "severity": "CAT1", "status": "open"}])},
                ],
            },
            extra_findings={
                "network": [
                    {"id": "f1", "rule_id": "Z", "title": "Z", "severity": "CAT1",
                     "status": "open", "affected_entity": "", "created_at": "2026-04-10"},
                ]
            },
        )
        with _patch_conns(conns):
            result = get_canvas_finding_counts()
        assert result["CAT1"] == 3
        assert result["total"] == 3

    def test_accepted_risk_and_false_positive_are_not_open(self):
        # This rule used to be asserted through get_canvas_compliance_summary;
        # cnr-cc-02 moved that function off findings_json, and this is now the
        # only implementation of it, so the assertion lives here.
        findings = [
            {"rule_id": "X1", "title": "X1", "severity": "CAT1", "status": "accepted_risk", "affected_entity": ""},
            {"rule_id": "X2", "title": "X2", "severity": "CAT2", "status": "false_positive", "affected_entity": ""},
            {"rule_id": "X3", "title": "X3", "severity": "CAT3", "status": "open", "affected_entity": ""},
        ]
        conns = _build_all_conns(
            extra_assessments={
                "data": [
                    {"id": "a1", "score": 60.0, "created_at": "2026-04-10",
                     "findings_json": json.dumps(findings)},
                ]
            }
        )
        with _patch_conns(conns):
            result = get_canvas_finding_counts()
        assert result["CAT1"] == 0
        assert result["CAT2"] == 0
        assert result["CAT3"] == 1
        assert result["total"] == 1

    def test_unknown_severity_mapped_to_cat3(self):
        findings = [
            {"rule_id": "U1", "title": "U1", "severity": "CRITICAL", "status": "open"},
        ]
        conns = _build_all_conns(
            extra_assessments={
                "security": [
                    {"id": "a1", "risk_score": 40.0, "posture_grade": "F",
                     "ran_at": "2026-04-10", "findings_json": json.dumps(findings)},
                ]
            }
        )
        with _patch_conns(conns):
            result = get_canvas_finding_counts()
        # Unknown severity is bucketed into CAT3
        assert result["CAT3"] >= 1
        assert result["total"] >= 1

    def test_result_is_cached(self):
        call_count = [0]

        def counting_conn(path: Path) -> None:
            call_count[0] += 1
            return None

        with patch.object(agg, "_get_canvas_conn", side_effect=counting_conn):
            get_canvas_finding_counts()
            first = call_count[0]
            get_canvas_finding_counts()
            assert call_count[0] == first


# ---------------------------------------------------------------------------
# get_recent_canvas_findings
# ---------------------------------------------------------------------------

class TestGetRecentCanvasFindings:

    def test_empty_returns_empty_list(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            result = get_recent_canvas_findings()
        assert result == []

    def test_respects_limit(self):
        # 20 findings in one canvas
        findings = [
            {"rule_id": f"R{i}", "title": f"T{i}", "severity": "CAT2",
             "status": "open", "created_at": f"2026-04-{i+1:02d}"}
            for i in range(20)
        ]
        conns = _build_all_conns(
            extra_assessments={
                "boundary": [
                    {"id": "a1", "score": 50.0, "grade": "D",
                     "created_at": "2026-04-10", "findings_json": json.dumps(findings)},
                ]
            }
        )
        with _patch_conns(conns):
            result = get_recent_canvas_findings(limit=5)
        assert len(result) <= 5

    def test_cat1_before_cat3_regardless_of_date(self):
        conns = _build_all_conns(
            extra_assessments={
                "boundary": [
                    {"id": "a1", "score": 50.0, "grade": "D",
                     "created_at": "2026-04-01",
                     "findings_json": json.dumps([
                         {"rule_id": "R1", "title": "CAT1 old", "severity": "CAT1",
                          "status": "open", "created_at": "2026-04-01"},
                     ])},
                ],
                "data": [
                    {"id": "a2", "score": 60.0, "created_at": "2026-04-11",
                     "findings_json": json.dumps([
                         {"rule_id": "R2", "title": "CAT3 new", "severity": "CAT3",
                          "status": "open", "created_at": "2026-04-11"},
                     ])},
                ],
            }
        )
        with _patch_conns(conns):
            result = get_recent_canvas_findings(limit=10)
        severities = [r["severity"] for r in result]
        assert severities.index("CAT1") < severities.index("CAT3")

    def test_row_has_required_keys(self):
        findings = [{"rule_id": "R1", "title": "T1", "severity": "CAT2",
                     "status": "open", "affected_entity": "S3", "created_at": "2026-04-10"}]
        conns = _build_all_conns(
            extra_assessments={
                "infra": [{"id": "a1", "score": 70.0, "created_at": "2026-04-10",
                           "findings_json": json.dumps(findings)}],
            }
        )
        with _patch_conns(conns):
            result = get_recent_canvas_findings()
        assert len(result) == 1
        required = {"canvas_label", "canvas_source", "rule_id", "title",
                    "severity", "status", "affected_entity", "discovered_at"}
        assert required <= set(result[0].keys())
        assert result[0]["canvas_source"] == "infra"
        assert result[0]["severity"] == "CAT2"

    def test_direct_canvas_findings_included(self):
        conns = _build_all_conns(
            extra_findings={
                "network": [
                    {"id": "f1", "rule_id": "R99", "title": "Missing VLAN",
                     "severity": "CAT1", "status": "open",
                     "affected_entity": "SW01", "created_at": "2026-04-11"},
                ]
            }
        )
        with _patch_conns(conns):
            result = get_recent_canvas_findings(limit=10)
        nc = [r for r in result if r["canvas_source"] == "network"]
        assert len(nc) == 1
        assert nc[0]["rule_id"] == "R99"

    def test_dedup_same_finding_across_assessments(self):
        f = {"rule_id": "DUP", "title": "Dup", "severity": "CAT2",
             "status": "open", "affected_entity": "X"}
        conns = _build_all_conns(
            extra_assessments={
                "boundary": [
                    {"id": "a1", "score": 50.0, "grade": "D",
                     "created_at": "2026-04-11", "findings_json": json.dumps([f])},
                    {"id": "a2", "score": 55.0, "grade": "D",
                     "created_at": "2026-04-10", "findings_json": json.dumps([f])},
                ]
            }
        )
        with _patch_conns(conns):
            result = get_recent_canvas_findings(limit=50)
        dup_hits = [r for r in result if r["rule_id"] == "DUP"]
        assert len(dup_hits) == 1

    def test_separate_cache_key_per_limit(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            r5 = get_recent_canvas_findings(limit=5)
            r10 = get_recent_canvas_findings(limit=10)
        # Both calls succeed independently
        assert isinstance(r5, list)
        assert isinstance(r10, list)
        # Verify both are cached separately
        assert "recent_findings_5" in agg._result_cache
        assert "recent_findings_10" in agg._result_cache

    def test_missing_canvas_db_is_skipped_gracefully(self):
        conns = _build_all_conns(skip_canvases={"security", "boundary", "infra"})
        with _patch_conns(conns):
            result = get_recent_canvas_findings(limit=10)
        # Should not raise; only available canvases contribute findings
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_canvas_activity_trend
# ---------------------------------------------------------------------------

class TestGetCanvasActivityTrend:

    def test_returns_exactly_days_rows(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            result = get_canvas_activity_trend(days=7)
        assert len(result) == 7

    def test_row_has_date_and_count(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            result = get_canvas_activity_trend(days=3)
        for row in result:
            assert "date" in row
            assert "count" in row
            assert isinstance(row["count"], int)

    def test_rows_in_chronological_order(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            result = get_canvas_activity_trend(days=7)
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)

    def test_empty_tables_yield_zero_counts(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            result = get_canvas_activity_trend(days=7)
        assert all(r["count"] == 0 for r in result)

    def test_counts_events_across_canvases(self):
        today = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        conns = _build_all_conns(
            extra_audit={
                # boundary uses created_at
                "boundary": [
                    {"action": "update", "created_at": f"{today}T10:00:00"},
                    {"action": "create", "created_at": f"{today}T11:00:00"},
                    {"action": "view",   "created_at": f"{yesterday}T09:00:00"},
                ],
                # network uses ts
                "network": [
                    {"action": "assess", "ts": f"{today}T14:00:00"},
                ],
            }
        )
        with _patch_conns(conns):
            result = get_canvas_activity_trend(days=7)
        by_date = {r["date"]: r["count"] for r in result}
        assert by_date.get(today, 0) == 3       # 2 boundary + 1 network
        assert by_date.get(yesterday, 0) == 1   # 1 boundary

    def test_events_outside_window_excluded(self):
        old = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
        conns = _build_all_conns(
            extra_audit={
                "boundary": [
                    {"action": "old_event", "created_at": f"{old}T10:00:00"},
                ],
            }
        )
        with _patch_conns(conns):
            result = get_canvas_activity_trend(days=7)
        assert sum(r["count"] for r in result) == 0

    def test_different_day_window_sizes(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            r3 = get_canvas_activity_trend(days=3)
            r14 = get_canvas_activity_trend(days=14)
        assert len(r3) == 3
        assert len(r14) == 14

    def test_result_cached_by_days_key(self):
        call_count = [0]

        def counting_conn(path: Path) -> None:
            call_count[0] += 1
            return None

        with patch.object(agg, "_get_canvas_conn", side_effect=counting_conn):
            get_canvas_activity_trend(days=7)
            first = call_count[0]
            get_canvas_activity_trend(days=7)
            assert call_count[0] == first

    def test_different_day_keys_cached_separately(self):
        conns = _build_all_conns()
        with _patch_conns(conns):
            get_canvas_activity_trend(days=7)
            get_canvas_activity_trend(days=14)
        assert "activity_trend_7" in agg._result_cache
        assert "activity_trend_14" in agg._result_cache


# ---------------------------------------------------------------------------
# invalidate_cache
# ---------------------------------------------------------------------------

class TestInvalidateCache:

    def test_clears_all_entries(self):
        conns = _build_all_conns()
        with _patch_posture(conns), _patch_conns(conns):
            get_canvas_compliance_summary()
            get_canvas_finding_counts()
            get_canvas_activity_trend(days=7)
        assert len(agg._result_cache) > 0
        invalidate_cache()
        assert len(agg._result_cache) == 0

    def test_forces_recomputation_after_invalidation(self):
        call_count = [0]

        def counting_conn(path: Path) -> None:
            call_count[0] += 1
            return None

        with patch.object(agg, "_get_canvas_conn", side_effect=counting_conn):
            get_canvas_finding_counts()
            first = call_count[0]
            invalidate_cache()
            get_canvas_finding_counts()
            assert call_count[0] > first, "Should re-query after cache invalidation"
