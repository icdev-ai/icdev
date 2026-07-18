# CUI // SP-CTI
"""ODC-NDC-001 real cross-canvas check + engine input-validation (obx-fix-04).

Covers the obx-fix-04 fixes to
``tools.observability_canvas.observability_engine``:

1. ``assess_observability_design`` dispatches a rule's ``check_function`` (here
   ODC-NDC-001) instead of silently skipping it.
2. ``check_nc_audit_to_siem_forwarder`` is FAIL-CLOSED: when data is
   unavailable (missing ids, NDC/ODC store error, no topologies) it returns
   ``status="unknown"`` with NO score — never a fabricated pass.
3. With seeded ``odc_sdc_verifications`` rows read through a translating
   ``StorageConnection`` on a temp SQLite DB, the check computes a REAL
   pass/fail from audit-forwarding coverage.
4. Robustness: a node missing ``id``, an edge missing ``source``/``target``,
   and a graph passed as a JSON string no longer raise.
5. Rules WITHOUT a ``check_function`` score exactly as before (node-presence
   scoring path unchanged).

Patching is shim-aware (``importlib.import_module`` + ``setattr`` on the module
object), so ``tools.*`` vs ``icdev.tools.*`` resolve to the same object the
engine dispatches against.
"""

import importlib
import sqlite3

import pytest

_ENGINE = importlib.import_module("tools.observability_canvas.observability_engine")
_INITDB = importlib.import_module("tools.observability_canvas.db.init_db")
_STORAGE = importlib.import_module("tools.db.storage")

from tools.observability_canvas.constants import OBSERVABILITY_COMPLIANCE_RULES

_RULES_BY_ID = {r["id"]: r for r in OBSERVABILITY_COMPLIANCE_RULES}
_NDC_RULE = _RULES_BY_ID["ODC-NDC-001"]


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _topo(tid, name="", nodes=None):
    return {"id": tid, "name": name or tid, "graph": {"nodes": nodes or [], "edges": []}}


@pytest.fixture
def sdcv_storage_conn(tmp_path):
    """A translating StorageConnection over a temp SQLite odc_sdc_verifications.

    Includes the obx-fix-04 additive columns (topology_id, forward_status,
    siem_node_id). Returns a factory that seeds rows and yields a StorageConnection.
    """
    db_path = tmp_path / "odc_temp.db"
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    raw.execute(
        """
        CREATE TABLE odc_sdc_verifications (
            id             TEXT PRIMARY KEY,
            design_id      TEXT NOT NULL,
            ttp_list       TEXT NOT NULL DEFAULT '[]',
            covered_ttps   TEXT NOT NULL DEFAULT '[]',
            partial_ttps   TEXT NOT NULL DEFAULT '[]',
            gap_ttps       TEXT NOT NULL DEFAULT '[]',
            coverage_pct   REAL NOT NULL DEFAULT 0.0,
            verified_at    TEXT NOT NULL DEFAULT '',
            topology_id    TEXT DEFAULT '',
            siem_node_id   TEXT DEFAULT '',
            forward_status TEXT DEFAULT ''
        )
        """
    )
    raw.commit()
    conn = _STORAGE.StorageConnection(raw, "sqlite")
    conn.row_factory = sqlite3.Row

    def _seed(design_id, topology_id, forward_status):
        raw.execute(
            "INSERT INTO odc_sdc_verifications "
            "(id, design_id, topology_id, forward_status, verified_at) "
            "VALUES (?, ?, ?, ?, '2026-07-18')",
            (f"v-{topology_id}", design_id, topology_id, forward_status),
        )
        raw.commit()

    yield conn, _seed
    raw.close()


# ── (1) dispatcher invokes check_function during assess ──────────────────────


def test_assess_dispatches_check_function(monkeypatch):
    calls = {}

    def _spy(canvas_project_id=None, odc_design_id=None):
        calls["args"] = (canvas_project_id, odc_design_id)
        return {"rule_id": "ODC-NDC-001", "status": "unknown", "violations": []}

    monkeypatch.setattr(_ENGINE, "check_nc_audit_to_siem_forwarder", _spy)

    result = _ENGINE.assess_observability_design(
        {"nodes": [], "edges": []},
        canvas_project_id="proj-1",
        odc_design_id="design-1",
    )

    # The spy was invoked with the threaded ids (proves generic dispatch fired).
    assert calls["args"] == ("proj-1", "design-1")
    # Surfaced under cross_canvas_checks, not silently dropped.
    cc = {c["rule_id"]: c for c in result["cross_canvas_checks"]}
    assert "ODC-NDC-001" in cc
    assert cc["ODC-NDC-001"]["status"] == "unknown"


def test_missing_check_function_is_unknown(monkeypatch):
    """A rule whose check_function cannot be resolved -> unknown, not a crash."""
    bogus = dict(_NDC_RULE)
    bogus["check_function"] = "no_such_function_xyz"
    result = _ENGINE.assess_observability_design({"nodes": [], "edges": []}, rules=[bogus])
    cc = result["cross_canvas_checks"]
    assert len(cc) == 1
    assert cc[0]["status"] == "unknown"
    # unknown adds no finding.
    assert result["total_findings"] == 0


# ── (2) fail-closed: unknown (never fabricated pass) ─────────────────────────


def test_unknown_on_missing_identifiers():
    res = _ENGINE.check_nc_audit_to_siem_forwarder("", "")
    assert res["status"] == "unknown"
    assert "score" not in res  # NO score on unknown


def test_unknown_on_ndc_store_error(monkeypatch):
    def _boom(_pid):
        raise RuntimeError("no such table: ndc_topologies")

    monkeypatch.setattr(_ENGINE, "_load_ndc_topologies", _boom)
    res = _ENGINE.check_nc_audit_to_siem_forwarder("proj-1", "design-1")
    assert res["status"] == "unknown"
    assert "score" not in res


def test_unknown_on_no_topologies(monkeypatch):
    monkeypatch.setattr(_ENGINE, "_load_ndc_topologies", lambda _pid: [])
    res = _ENGINE.check_nc_audit_to_siem_forwarder("proj-1", "design-1")
    assert res["status"] == "unknown"
    assert "score" not in res


def test_unknown_on_odc_connection_error(monkeypatch):
    monkeypatch.setattr(
        _ENGINE, "_load_ndc_topologies", lambda _pid: [_topo("t1")]
    )

    def _boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(_INITDB, "get_connection", _boom)
    res = _ENGINE.check_nc_audit_to_siem_forwarder("proj-1", "design-1")
    assert res["status"] == "unknown"
    assert "score" not in res


# ── (3) real pass/fail from seeded odc_sdc_verifications ─────────────────────


def test_real_fail_from_seeded_rows(monkeypatch, sdcv_storage_conn):
    conn, seed = sdcv_storage_conn
    # design d1 forwards audit for t-covered only.
    seed("d1", "t-covered", "forwarded")
    seed("d1", "t-other", "unverified")  # NOT counted (status not forwarded/verified)

    topologies = [
        _topo("t-covered"),  # covered by ODC row -> compliant
        _topo("t-other"),    # no coverage, no SIEM node -> VIOLATION
        _topo("t-self", nodes=[{"id": "s", "type": "plt-splunk", "label": "Splunk SIEM"}]),  # self-forwards
    ]
    monkeypatch.setattr(_ENGINE, "_load_ndc_topologies", lambda _pid: topologies)
    monkeypatch.setattr(_INITDB, "get_connection", lambda: conn)

    res = _ENGINE.check_nc_audit_to_siem_forwarder("proj-1", "d1")
    assert res["status"] == "fail"
    assert res["score"] == 80  # 100 - 1*20
    viol_ids = {v["topology_id"] for v in res["violations"]}
    assert viol_ids == {"t-other"}


def test_real_pass_from_seeded_rows(monkeypatch, sdcv_storage_conn):
    conn, seed = sdcv_storage_conn
    seed("d1", "t-covered", "verified")
    topologies = [_topo("t-covered")]
    monkeypatch.setattr(_ENGINE, "_load_ndc_topologies", lambda _pid: topologies)
    monkeypatch.setattr(_INITDB, "get_connection", lambda: conn)

    res = _ENGINE.check_nc_audit_to_siem_forwarder("proj-1", "d1")
    assert res["status"] == "pass"
    assert res["score"] == 100
    assert res["violations"] == []


def test_unknown_when_column_missing(monkeypatch, tmp_path):
    """Old-schema odc_sdc_verifications (no forward_status) -> unknown, not crash."""
    raw = sqlite3.connect(str(tmp_path / "old.db"))
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE odc_sdc_verifications (id TEXT, design_id TEXT)")
    raw.commit()
    conn = _STORAGE.StorageConnection(raw, "sqlite")
    monkeypatch.setattr(_ENGINE, "_load_ndc_topologies", lambda _pid: [_topo("t1")])
    monkeypatch.setattr(_INITDB, "get_connection", lambda: conn)
    res = _ENGINE.check_nc_audit_to_siem_forwarder("proj-1", "d1")
    assert res["status"] == "unknown"
    assert "score" not in res
    raw.close()


# ── (4) robustness: malformed graph input does not raise ─────────────────────


class TestRobustness:
    def _patch_check(self, monkeypatch):
        # Keep robustness tests off the DB; the check itself is covered above.
        monkeypatch.setattr(
            _ENGINE,
            "check_nc_audit_to_siem_forwarder",
            lambda canvas_project_id=None, odc_design_id=None: {
                "rule_id": "ODC-NDC-001",
                "status": "unknown",
                "violations": [],
            },
        )

    def test_node_without_id_no_raise(self, monkeypatch):
        self._patch_check(monkeypatch)
        res = _ENGINE.assess_observability_design(
            {"nodes": [{"type": "src-app-log"}], "edges": []}
        )
        assert isinstance(res, dict)
        assert res["total_findings"] >= 0

    def test_edge_without_source_target_no_raise(self, monkeypatch):
        self._patch_check(monkeypatch)
        design = {"nodes": [{"id": "a", "type": "src-app-log"}], "edges": [{"foo": "bar"}]}
        res = _ENGINE.assess_observability_design(design)
        assert isinstance(res, dict)

    def test_string_graph_data_no_raise(self, monkeypatch):
        self._patch_check(monkeypatch)
        res = _ENGINE.assess_observability_design('{"nodes": [], "edges": []}')
        assert isinstance(res, dict)

    def test_invalid_string_graph_data_no_raise(self, monkeypatch):
        self._patch_check(monkeypatch)
        res = _ENGINE.assess_observability_design("{not valid json")
        assert isinstance(res, dict)

    def test_compute_coverage_string_no_raise(self):
        cov = _ENGINE.compute_coverage_score('{"nodes": []}')
        assert cov["coverage_pct"] == 0.0

    def test_compute_mitre_string_no_raise(self):
        res = _ENGINE.compute_mitre_detection_coverage('{"nodes": []}')
        assert res["has_baseline"] is False


# ── (5) rules WITHOUT check_function score exactly as before ──────────────────


class TestNodePresenceScoringUnchanged:
    def test_single_cat2_rule_exact_score(self):
        """Missing col-s3 fires ODC-LOG-003 (CAT2, weight 5).

        max_penalty = 5*2 = 10 ; penalty = 5 ; score = 100*(1-5/10) = 50.0.
        No check_function rule in the list -> cross_canvas_checks empty.
        """
        design = {"nodes": [], "edges": []}
        res = _ENGINE.assess_observability_design(design, rules=[_RULES_BY_ID["ODC-LOG-003"]])
        assert res["total_findings"] == 1
        assert res["findings"][0]["rule_id"] == "ODC-LOG-003"
        assert res["score"] == 50.0
        assert res["grade"] == "F"
        assert res["cross_canvas_checks"] == []

    def test_no_findings_full_marks(self):
        design = {"nodes": [{"id": "s3", "type": "col-s3"}], "edges": []}
        res = _ENGINE.assess_observability_design(design, rules=[_RULES_BY_ID["ODC-LOG-003"]])
        assert res["total_findings"] == 0
        assert res["score"] == 100.0
        assert res["grade"] == "A"

    def test_unknown_check_function_adds_no_penalty(self, monkeypatch):
        """An unknown ODC-NDC-001 leaves a node-only design's score untouched."""
        monkeypatch.setattr(
            _ENGINE,
            "check_nc_audit_to_siem_forwarder",
            lambda canvas_project_id=None, odc_design_id=None: {
                "rule_id": "ODC-NDC-001",
                "status": "unknown",
                "violations": [],
            },
        )
        design = {"nodes": [{"id": "s3", "type": "col-s3"}], "edges": []}
        rules = [_RULES_BY_ID["ODC-LOG-003"], _NDC_RULE]
        res = _ENGINE.assess_observability_design(design, rules=rules)
        # ODC-LOG-003 suppressed (col-s3 present); ODC-NDC-001 unknown -> no penalty.
        assert res["total_findings"] == 0
        assert res["score"] == 100.0
        assert res["grade"] == "A"
