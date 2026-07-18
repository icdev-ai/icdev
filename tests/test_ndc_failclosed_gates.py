# CUI // SP-CTI
"""Fail-closed intent/compliance gate evaluation (ndc-gov-01).

Before this change, an evaluation error inside the NDC governance/analysis
gates was swallowed and defaulted to a benign result — a compliance gate that
FAILED OPEN (reported "pass"/"healthy" on error). These tests pin the new
contract:

  (a) governance._evaluate_intent_rule: when the underlying evaluation
      (twin.blast_radius) raises, the rule result is the explicit "error"
      state whose detail names the exception, and the aggregate verdict
      (_verdict_is_pass) is NOT pass.
  (b) analysis._parse_topology_graph: when the inner json.loads raises, the
      helper reports an explicit error string instead of a silent benign
      empty-graph default that heuristics would read as "healthy".
  (c) healthy path: when nothing raises, results are identical to the prior
      behavior ("pass"/"fail" as appropriate).

Uses a temp SQLite DB opened via the canvas init_db get_connection() (same
pattern as tests/test_ndc_backend_helpers.py) for the DB-backed rule.
"""

from tools.network.routes import analysis as analysis_routes
from tools.network.routes.governance import _evaluate_intent_rule, _verdict_is_pass


# ── shared temp-DB helper (mirrors test_ndc_backend_helpers.py) ─────────────────

def _canvas_sqlite_conn(tmp_path, monkeypatch):
    from tools.network.db import init_db

    db_file = tmp_path / "nc_failclosed_test.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db_file)
    return init_db.get_connection()


# ── (a) governance: evaluation error → "error", not "pass" ─────────────────────

def test_intent_rule_error_when_blast_radius_raises(monkeypatch):
    """twin.blast_radius raising must yield result=='error' (never 'pass')."""

    def _boom(*_a, **_k):
        raise RuntimeError("blast_radius exploded")

    # _evaluate_intent_rule imports blast_radius from tools.network.twin at call
    # time; patch it at that source module.
    import tools.network.twin as twin
    monkeypatch.setattr(twin, "blast_radius", _boom)

    result, detail = _evaluate_intent_rule(
        None, "topo-x", {"type": "no_single_points_of_failure"}
    )

    assert result == "error"
    # reason/detail must carry a summary of the exception
    assert "blast_radius exploded" in detail["reason"]
    assert detail["error"] == "RuntimeError"
    # aggregate verdict must NOT read as pass (fail-closed)
    assert _verdict_is_pass(result) is False


def test_verdict_is_pass_only_for_pass():
    """The fail-closed verdict: only the explicit 'pass' passes."""
    assert _verdict_is_pass("pass") is True
    assert _verdict_is_pass("fail") is False
    assert _verdict_is_pass("error") is False
    assert _verdict_is_pass("unknown") is False


# ── (b) analysis: inner parse error → explicit error, not benign default ────────

def test_parse_topology_graph_reports_error_on_raise(monkeypatch):
    """json.loads raising must surface an error string, not a silent {} default."""

    def _boom(*_a, **_k):
        raise ValueError("corrupt graph json")

    # analysis references the module-level `json`; patch its loads attribute.
    monkeypatch.setattr(analysis_routes.json, "loads", _boom)

    graph, error = analysis_routes._parse_topology_graph('{"nodes":[]}')

    assert error is not None
    assert "corrupt graph json" in error
    # a benign fallback graph is returned so callers don't crash, but the error
    # string is what forces the failure to be visible (not read as healthy)
    assert graph == {"nodes": [], "edges": []}


def test_parse_topology_graph_reports_error_on_non_object():
    """A JSON payload that is not an object is a fail-closed error, not benign."""
    graph, error = analysis_routes._parse_topology_graph("[1, 2, 3]")
    assert error is not None
    assert "expected object" in error
    assert graph == {"nodes": [], "edges": []}


# ── (c) healthy path: unchanged pass/fail behavior ─────────────────────────────

def test_intent_rule_healthy_pass_and_fail(monkeypatch):
    """With no exception, SPOF rule returns 'pass'/'fail' exactly as before."""
    import tools.network.twin as twin

    # No SPOFs → pass
    monkeypatch.setattr(
        twin, "blast_radius",
        lambda *_a, **_k: {"nodes": {"a": {"is_spof": False}, "b": {"is_spof": False}}},
    )
    result, detail = _evaluate_intent_rule(None, "t", {"type": "no_single_points_of_failure"})
    assert result == "pass"
    assert detail["spof_devices"] == []

    # A SPOF present → fail
    monkeypatch.setattr(
        twin, "blast_radius",
        lambda *_a, **_k: {"nodes": {"a": {"is_spof": True}, "b": {"is_spof": False}}},
    )
    result, detail = _evaluate_intent_rule(None, "t", {"type": "no_single_points_of_failure"})
    assert result == "fail"
    assert detail["spof_devices"] == ["a"]


def test_intent_rule_cat1_findings_healthy_db(tmp_path, monkeypatch):
    """DB-backed rule keeps pass/fail semantics on the healthy path."""
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)
    conn.execute(
        "CREATE TABLE nc_compliance_findings ("
        "id TEXT PRIMARY KEY, topology_id TEXT, severity TEXT, status TEXT)"
    )
    conn.commit()

    # No open cat1 findings → pass
    result, detail = _evaluate_intent_rule(conn, "topo-1", {"type": "no_open_cat1_findings"})
    assert result == "pass"
    assert detail["cat1_open_count"] == 0

    # One open cat1 finding → fail
    conn.execute(
        "INSERT INTO nc_compliance_findings (id, topology_id, severity, status) "
        "VALUES (%s,%s,%s,%s)",
        ("f1", "topo-1", "cat1", "open"),
    )
    conn.commit()
    result, detail = _evaluate_intent_rule(conn, "topo-1", {"type": "no_open_cat1_findings"})
    assert result == "fail"
    assert detail["cat1_open_count"] == 1


def test_intent_rule_cat1_findings_db_error_is_fail_closed(tmp_path, monkeypatch):
    """If the DB read raises (e.g. missing table), the rule reports 'error'."""
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)
    # nc_compliance_findings intentionally NOT created → execute() raises.
    result, detail = _evaluate_intent_rule(conn, "topo-1", {"type": "no_open_cat1_findings"})
    assert result == "error"
    assert _verdict_is_pass(result) is False
    assert "reason" in detail


def test_parse_topology_graph_healthy():
    """Valid JSON object parses cleanly with no error."""
    graph, error = analysis_routes._parse_topology_graph(
        '{"nodes":[{"id":"a"}],"edges":[]}'
    )
    assert error is None
    assert graph["nodes"] == [{"id": "a"}]


def test_parse_topology_graph_empty_default():
    """None/empty input yields the documented empty graph, no error."""
    graph, error = analysis_routes._parse_topology_graph(None)
    assert error is None
    assert graph == {"nodes": [], "edges": []}
