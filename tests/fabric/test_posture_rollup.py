# CUI // SP-CTI
"""Cross-fabric posture roll-up (rmf-fab-02).

The three acceptance criteria, each pinned by a test that FAILS if the
criterion is broken:

  1. a fabric never assessed reads ``not_assessed`` — never 0, never 100;
  2. both cATO sources appear with their scope LABELLED;
  3. no blended score exists anywhere in the output.

Plus the read-only guarantee, which is structural: the roll-up must never call
``evaluate_authorization`` (INSERTs a row) or ``check_evidence_freshness``
(UPDATEs evidence status), so a report can never be built on evidence it just
manufactured. That is asserted over the AST of the module, because a
behavioural test passes just as happily when a future edit adds the call behind
a branch the test does not take.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from tools.fabric import posture as P

_MODULE_PATH = Path(P.__file__)

FABRIC_NEVER_ASSESSED = {
    "key": "fab-nowhere",
    "display_name": "Never Assessed Fabric",
    "classification": "CUI",
    "impact_level": "IL5",
}


# ---------------------------------------------------------------------------
# Stand-ins. Each returns EXACTLY the shape the real source returns, so the
# measures are exercised over real reductions rather than over a mock of them.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """A main-DB stand-in keyed by the table named in the SQL."""

    def __init__(self, tables):
        self._tables = tables

    def execute(self, sql, params=None):
        for name, rows in self._tables.items():
            if name in sql:
                if isinstance(rows, Exception):
                    raise rows
                return _FakeCursor(rows)
        raise sqlite3.OperationalError("no such table")

    def close(self):
        pass


# ---------------------------------------------------------------------------
# AC 1 — a fabric never assessed reads not_assessed, never 0, never 100
# ---------------------------------------------------------------------------

def test_unassessed_fabric_reads_not_assessed_and_carries_no_number():
    empty = _FakeConn({"stig_findings": [], "poam_items": []})
    result = P.fabric_posture(FABRIC_NEVER_ASSESSED, conn=empty, canvas_conn=None)

    assert result["fabric_state"] == P.STATE_NOT_ASSESSED
    for key in P.MEASURE_KEYS:
        measure = result["measures"][key]
        assert measure["state"] != P.STATE_MEASURED, key
        # The whole point: not 0, not 100, not any number at all.
        assert measure["value"] is None, key
        assert measure["numerator"] is None, key
        assert measure["reason"], f"{key} must say WHY it could not be measured"


def test_unassessed_fabric_never_renders_a_zero_or_a_hundred_anywhere():
    empty = _FakeConn({"stig_findings": [], "poam_items": []})
    result = P.fabric_posture(FABRIC_NEVER_ASSESSED, conn=empty, canvas_conn=None)

    # A COUNT of things not assessed is a legitimate zero and must keep
    # rendering ("0 of 5 measures resolved"). A MEASURE is what must never
    # carry a number here. The two are told apart by name, not by value.
    count_containers = ("measures_by_state", "counts", "by_ato_state")

    def _is_count(path):
        leaf = path.rsplit(".", 1)[-1]
        return leaf.endswith("_count") or any(c in path for c in count_containers)

    def _walk(node, path="$"):
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            assert _is_count(path), (
                f"bare number {node} at {path} on a fabric nobody assessed"
            )

    _walk(result)
    # And the specific rails, stated directly rather than inferred from the sweep.
    for key in P.MEASURE_KEYS:
        assert result["measures"][key]["value"] is None
        assert result["measures"][key]["value"] != 0
        assert result["measures"][key]["value"] != 100


def test_a_measured_zero_is_kept_apart_from_not_assessed():
    """0 open CAT I findings is a REAL answer; no CAT I findings recorded is not.

    Conflating them is how a project nobody scanned passes the STIG gate, whose
    pass condition is literally ``cat1_open == 0``.
    """
    scanned = _FakeConn({
        "stig_findings": [
            {"severity": "CAT1", "status": "NotAFinding"},
            {"severity": "CAT1", "status": "NotAFinding"},
            {"severity": "CAT2", "status": "Open"},
        ],
    })
    measured = P.measure_open_cat1(scanned, "proj")
    assert measured["state"] == P.STATE_MEASURED
    assert measured["value"] == 0            # a real, measured zero
    assert measured["denominator"] == 2      # over the CAT I findings recorded

    never_scanned = P.measure_open_cat1(_FakeConn({"stig_findings": []}), "proj")
    assert never_scanned["state"] == P.STATE_NOT_ASSESSED
    assert never_scanned["value"] is None

    assert measured["value"] != never_scanned["value"]


def test_missing_table_is_source_unavailable_not_not_assessed():
    """A migration that never ran and a writer that never ran are different fixes."""
    broken = _FakeConn({"stig_findings": sqlite3.OperationalError("no such table")})
    result = P.measure_open_cat1(broken, "proj")
    assert result["state"] == P.STATE_SOURCE_UNAVAILABLE
    assert result["value"] is None
    assert "stig_findings_unavailable" in result["reason"]


def test_registry_absent_reports_unmeasurable_never_a_clean_board(monkeypatch):
    monkeypatch.setattr(P, "load_fabrics", lambda: ([], {"state": "absent", "reason": "no registry"}))
    result = P.roll_up()
    assert result["fabric_count"] == 0
    assert result["unmeasurable"] is True
    assert result["unmeasurable_reason"]
    assert "UNMEASURABLE" in P._format_report(result)
    assert "not a clean bill of health" in P._format_report(result)


# ---------------------------------------------------------------------------
# AC 2 — both cATO sources appear, each with its SCOPE labelled
# ---------------------------------------------------------------------------

def test_both_cato_sources_appear_with_their_scope_labelled():
    empty = _FakeConn({"stig_findings": [], "poam_items": []})
    sources = P.fabric_posture(FABRIC_NEVER_ASSESSED, conn=empty, canvas_conn=None)["cato_sources"]

    assert set(sources) == {P.SCOPE_SYSTEM, P.SCOPE_APPLICATION}

    system = sources[P.SCOPE_SYSTEM]
    assert system["module"] == "tools/compliance/cato_monitor.py"
    assert system["scope"] == P.SCOPE_SYSTEM
    assert "System-level" in system["scope_label"]

    application = sources[P.SCOPE_APPLICATION]
    assert application["module"] == "tools/security_canvas/continuous_authorization.py"
    assert application["scope"] == P.SCOPE_APPLICATION
    assert "Per-application" in application["scope_label"]

    # Two scopes, never one. Same fabric, different populations, different labels.
    assert system["scope_label"] != application["scope_label"]
    assert system["state"] in P.MEASURE_STATES
    assert application["state"] in P.MEASURE_STATES


def test_system_scope_is_measured_over_a_real_seeded_database(tmp_path):
    """Proves the system source is CONSUMED, not merely declared.

    ``cato_monitor._get_connection`` requires a SQLite file to exist before it
    opens anything, so this seeds one and points ``system_db_path`` at it.
    """
    db = tmp_path / "icdev.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute(
        "CREATE TABLE cato_evidence (id INTEGER PRIMARY KEY, project_id TEXT, "
        "control_id TEXT, evidence_type TEXT, status TEXT, is_fresh INTEGER, "
        "automation_frequency TEXT)"
    )
    conn.execute("INSERT INTO projects (id, name) VALUES ('fab-a', 'Fabric A')")
    conn.executemany(
        "INSERT INTO cato_evidence (project_id, control_id, evidence_type, status,"
        " is_fresh, automation_frequency) VALUES (?,?,?,?,?,?)",
        [
            ("fab-a", "AC-2", "config", "current", 1, "daily"),
            ("fab-a", "AU-6", "config", "current", 1, "daily"),
            ("fab-a", "SI-4", "config", "expired", 0, "manual"),
            ("fab-a", "CM-6", "config", "stale", 0, "manual"),
        ],
    )
    conn.commit()
    conn.close()

    source = P.system_cato("fab-a", db_path=str(db))
    assert source["state"] == P.STATE_MEASURED, source["reason"]
    assert source["counts"]["controls_with_evidence"] == 4
    assert source["counts"]["controls_with_fresh_evidence"] == 2

    freshness = P.measure_evidence_freshness(source)
    assert freshness["state"] == P.STATE_MEASURED
    assert freshness["value"] == 50.0
    # The denominator is controls that HAVE evidence — stated in words, so a
    # reader can check the number means what they think it means.
    assert freshness["denominator"] == 4
    assert freshness["denominator_of"] == "controls that have at least one cATO evidence item"


def test_system_scope_project_not_registered_is_not_assessed(tmp_path):
    db = tmp_path / "icdev.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()

    source = P.system_cato("fab-missing", db_path=str(db))
    assert source["state"] == P.STATE_NOT_ASSESSED
    assert "project_not_registered" in source["reason"]
    # And the 0.0 `compute_cato_readiness` would have returned never appears.
    assert P.measure_evidence_freshness(source)["value"] is None


def test_application_scope_reads_stored_rows_and_never_evaluates(monkeypatch):
    rows = [
        {"application": "icdev-api", "ato_state": "authorized",
         "degraded_signals": "[]", "evaluated_at": "2026-09-01T00:00:00+00:00"},
        {"application": "icdev-api", "ato_state": "conditional",
         "degraded_signals": '["dast_gate"]', "evaluated_at": "2026-09-02T00:00:00+00:00"},
        {"application": "other-app", "ato_state": "authorized",
         "degraded_signals": "[]", "evaluated_at": "2026-09-02T00:00:00+00:00"},
    ]
    monkeypatch.setitem(
        __import__("sys").modules,
        "tools.security_canvas.db.init_db",
        type("M", (), {"get_connection": staticmethod(
            lambda: _FakeConn({"zig_continuous_ato": rows})
        )}),
    )

    source = P.application_cato(["icdev-api", "never-deployed"])
    assert source["state"] == P.STATE_MEASURED
    # Latest row per application wins; the non-declared app is filtered out.
    assert [a["application"] for a in source["applications"]] == ["icdev-api"]
    assert source["applications"][0]["ato_state"] == "conditional"
    assert source["applications"][0]["degraded_signals"] == ["dast_gate"]
    # An application declared and never evaluated is NAMED, not silently absent.
    assert source["never_evaluated"] == ["never-deployed"]
    # The six-signal weighted blend those rows carry is deliberately dropped.
    assert "posture_score" not in json.dumps(source)


def test_application_scope_with_no_rows_is_not_assessed(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "tools.security_canvas.db.init_db",
        type("M", (), {"get_connection": staticmethod(
            lambda: _FakeConn({"zig_continuous_ato": []})
        )}),
    )
    source = P.application_cato(["icdev-api"])
    assert source["state"] == P.STATE_NOT_ASSESSED
    assert source["reason"] == "declared_applications_never_evaluated"
    assert source["evaluated_count"] == 0
    assert source["applications"] == []


# ---------------------------------------------------------------------------
# AC 3 — no blended score exists anywhere in the output
# ---------------------------------------------------------------------------

def test_no_blended_score_in_a_fully_measured_rollup(monkeypatch):
    """The strict case: a fabric where EVERY measure resolved."""
    monkeypatch.setattr(P, "_bdc_components", lambda *a, **k: {
        "control_coverage": {"score": 82.5, "scored_controls": 40,
                             "total_controls": 55, "satisfied_controls": 31,
                             "snapshot_id": "snap-1"},
        "isa_expiry": {"score": 75.0, "total_isas": 4, "expired": 1,
                       "expiring_soon": 1, "warn_days": 90},
    })
    monkeypatch.setattr(P, "system_cato", lambda pid, db_path=None: {
        "module": "tools/compliance/cato_monitor.py",
        "scope": P.SCOPE_SYSTEM, "scope_label": P.SCOPE_LABELS[P.SCOPE_SYSTEM],
        "state": P.STATE_MEASURED, "project_id": pid, "reason": None,
        "counts": {"total_controls": 55, "controls_with_evidence": 20,
                   "controls_with_fresh_evidence": 13, "total_evidence_items": 30,
                   "by_frequency": {"daily": 30}},
    })
    monkeypatch.setattr(P, "application_cato", lambda apps=None: {
        "module": "tools/security_canvas/continuous_authorization.py",
        "scope": P.SCOPE_APPLICATION,
        "scope_label": P.SCOPE_LABELS[P.SCOPE_APPLICATION],
        "state": P.STATE_MEASURED, "reason": None,
        "declared_applications": list(apps or []),
        "applications": [{"application": "icdev-api", "ato_state": "conditional",
                          "degraded_signals": ["dast_gate"], "evaluated_at": "2026-09-02"}],
        "by_ato_state": {"conditional": 1}, "evaluated_count": 1, "never_evaluated": [],
    })
    conn = _FakeConn({
        "stig_findings": [{"severity": "CAT1", "status": "Open"},
                          {"severity": "CAT1", "status": "NotAFinding"}],
        "poam_items": [{"status": "open", "created_at": "2026-06-01T00:00:00+00:00",
                        "milestone_date": "2026-07-01T00:00:00+00:00"}],
    })

    result = P.fabric_posture(
        {"key": "fab-a", "display_name": "A", "applications": ["icdev-api"]},
        conn=conn, canvas_conn=None,
    )
    assert result["fabric_state"] == "assessed"
    assert all(result["measures"][k]["state"] == P.STATE_MEASURED for k in P.MEASURE_KEYS)

    # Every measure resolved and STILL no composite exists.
    P.assert_no_blended_score(result)
    for forbidden in P.FORBIDDEN_BLEND_KEYS:
        assert f'"{forbidden}"' not in json.dumps(result), forbidden

    # Each measure carries its OWN denominator, and they genuinely differ.
    denominators = {result["measures"][k]["denominator_of"] for k in P.MEASURE_KEYS}
    assert len(denominators) == len(P.MEASURE_KEYS)


def test_assert_no_blended_score_actually_fires():
    """The guard must be discriminating — a guard that never fires proves nothing."""
    with pytest.raises(AssertionError, match="blended score key"):
        P.assert_no_blended_score({"fabrics": [{"key": "a", "score": 88.0}]})
    with pytest.raises(AssertionError, match="posture_score"):
        P.assert_no_blended_score({"cato_sources": {"application": {"posture_score": 0.89}}})
    # And it passes what it should pass.
    P.assert_no_blended_score({"measures": {"open_cat1": {"value": 0, "denominator": 3}}})


def test_bdc_composite_is_discarded_not_propagated(monkeypatch):
    """``compute_readiness`` returns a weighted 0-100 composite. It must not travel."""
    captured = {}

    def _fake_compute(design_id, project_id, conn=None, canvas_conn=None):
        captured["called"] = True
        return {
            "design_id": design_id, "project_id": project_id,
            "score": 71.4, "readiness_score": 71.4, "band": "amber",
            "weights": {"control_coverage": 0.4},
            "components": {"control_coverage": {"score": 82.5, "scored_controls": 4,
                                                "total_controls": 4,
                                                "satisfied_controls": 3}},
        }

    import tools.boundary_canvas.cato_readiness as cr
    monkeypatch.setattr(cr, "compute_readiness", _fake_compute)

    components = P._bdc_components("d", "p", conn=None, canvas_conn=None)
    assert captured.get("called"), "the BDC scorer must actually be consumed"
    assert "score" not in components
    assert "band" not in components
    assert "weights" not in components
    # The per-component detail — with its own denominator — is what survives.
    measure = P.measure_control_coverage(components)
    assert measure["state"] == P.STATE_MEASURED
    assert measure["denominator"] == 4
    P.assert_no_blended_score({"measures": {"control_coverage": measure}})


# ---------------------------------------------------------------------------
# Read-only, structurally
# ---------------------------------------------------------------------------

_WRITERS = ("evaluate_authorization", "check_evidence_freshness", "auto_reassess",
            "collect_evidence", "expire_old_evidence", "deploy_continuous_authorization")


def test_module_never_references_a_writing_entry_point():
    """Asserted over the AST, not over behaviour.

    ``evaluate_authorization`` INSERTs a ``zig_continuous_ato`` row on every
    call and ``check_evidence_freshness`` UPDATEs evidence status. A roll-up
    calling either would report evidence it had just manufactured — and a
    behavioural test would still pass if a future edit put the call behind a
    branch the test does not take.
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.name for a in node.names}
    for writer in _WRITERS:
        assert writer not in names, f"{writer} is a WRITER and must not be called here"


def test_module_issues_no_write_sql():
    source = _MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [
        n.value.upper() for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    for sql in literals:
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "ALTER TABLE"):
            assert verb not in sql, f"write SQL {verb!r} in a read-only roll-up"


def test_no_sqlite_dialect_json_sql():
    """PostgreSQL is the primary backend; JSON filtering is done in Python."""
    source = _MODULE_PATH.read_text(encoding="utf-8")
    for bad in ("json_extract", "json_array_length", "json_each"):
        assert bad not in source, f"{bad} is SQLite-dialect JSON SQL"


def test_cli_runs_and_refuses_to_emit_a_blend(monkeypatch, capsys):
    monkeypatch.setattr(P, "load_fabrics", lambda: ([], {"state": "absent", "reason": "none"}))
    assert P.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    P.assert_no_blended_score(payload)
    assert payload["scoring"]["blended"] is False
    assert payload["unmeasurable"] is True
