# CUI // SP-CTI
"""Truthful-scoring tests for pdx-fix-02.

Four defects previously made the Pipeline Design Canvas scorecard/heatmap lie:

  1. STAGE COVERAGE — computed from ``n['stage']`` but the save path never sends
     ``stage``, so Stage Coverage was permanently 0/12. Fixed by deriving the
     stage server-side from the node type (``stage_from_type``).
  2. GOVERNANCE PREDICATES — checked src-/bld-/tst-/sast-/reg-/dep- prefixes from
     a DIFFERENT canvas taxonomy that never match PDC types (scm-/build-/scan-/
     registry-/deploy- …). Rewritten against the real type keys.
  3. FINDINGS HEATMAP — read ``config.findings_count`` (never set → permanent 0).
     Now queries pc_compliance_findings and reports honest pipeline-level counts.
  4. QDC ATTRIBUTION — attached findings to the most-recently-updated pipeline.
     Now resolves the pipeline from a payload correlation id, else stores NULL.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(**kw):
    return kw


def _mock_conn(fetchone=None, fetchall=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall if fetchall is not None else []
    conn.execute.return_value = cur
    return conn


def _graph(nodes):
    return json.dumps({"nodes": nodes, "edges": []})


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("ICDEV_PIPELINE_ENABLED", "true")
    with patch("tools.pipeline.blueprint.init_db"):
        from tools.pipeline.blueprint import create_pipeline_blueprint

        flask_app = Flask(__name__)
        flask_app.secret_key = "test-secret-key"
        flask_app.config["TESTING"] = True

        @flask_app.context_processor
        def _inject_base_ctx():
            return {"ROLE_VIEWS": {}, "current_role": None, "current_user": None}

        bp = create_pipeline_blueprint()
        assert bp is not None, "ICDEV_PIPELINE_ENABLED must be true"
        flask_app.register_blueprint(bp, url_prefix="/devops")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user_id="test-user", role="developer"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        if role is not None:
            sess["role"] = role
        else:
            sess.pop("role", None)


# ══════════════════════════════════════════════════════════════════════════════
# 0. stage_from_type unit behaviour (server-side derivation)
# ══════════════════════════════════════════════════════════════════════════════

def test_stage_from_type_explicit_wins():
    from tools.pipeline.constants import stage_from_type
    # Explicit stage on the node beats derivation.
    assert stage_from_type("scm-gitlab", "monitor") == "monitor"


def test_stage_from_type_curated_mapping():
    from tools.pipeline.constants import stage_from_type
    assert stage_from_type("scm-gitlab") == "source"
    assert stage_from_type("build-docker") == "build"
    assert stage_from_type("scan-bandit") == "test"
    assert stage_from_type("registry-harbor") == "package"
    assert stage_from_type("deploy-canary") == "deploy_prod"


def test_stage_from_type_prefix_fallback_and_none():
    from tools.pipeline.constants import stage_from_type
    # An unknown type that still matches a prefix resolves via the fallback.
    assert stage_from_type("scan-brandnew-scanner") == "test"
    # infrastructure types (not one of the 12 stages) resolve to None.
    assert stage_from_type("ndc-router") is None
    assert stage_from_type("") is None


# ══════════════════════════════════════════════════════════════════════════════
# 1. STAGE COVERAGE — non-zero for typed nodes lacking a persisted 'stage'
# ══════════════════════════════════════════════════════════════════════════════

def test_scorecard_stage_coverage_nonzero_without_stage_field(client):
    """A saved graph whose nodes carry only 'type' (no 'stage') must still show
    non-zero stage coverage derived from the type taxonomy."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    nodes = [
        {"id": "n1", "type": "scm-gitlab"},       # source
        {"id": "n2", "type": "build-docker"},     # build
        {"id": "n3", "type": "scan-bandit"},      # test
        {"id": "n4", "type": "registry-harbor"},  # package
        {"id": "n5", "type": "deploy-canary"},    # deploy_prod
    ]
    conn = _mock_conn(fetchone=_row(graph_json=_graph(nodes)))
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint.assess_slsa", return_value={"level": 0}), \
         patch("tools.pipeline.blueprint.run_compliance_check",
               return_value={"passed": 0, "failed": 0, "findings": []}):
        resp = client.get(f"/devops/api/pipelines/{pipe_id}/scorecard")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["stages_covered"] == 5, body
    assert body["total_stages"] == 12


def test_scorecard_stage_coverage_zero_for_untyped_nodes(client):
    """Nodes whose type maps to no canonical stage contribute 0 (honest)."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    nodes = [{"id": "n1", "type": "ndc-router"}, {"id": "n2", "type": ""}]
    conn = _mock_conn(fetchone=_row(graph_json=_graph(nodes)))
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint.assess_slsa", return_value={"level": 0}), \
         patch("tools.pipeline.blueprint.run_compliance_check",
               return_value={"passed": 0, "failed": 0, "findings": []}):
        resp = client.get(f"/devops/api/pipelines/{pipe_id}/scorecard")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["stages_covered"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# 2. GOVERNANCE PREDICATES — real PDC types (one positive + one negative each)
# ══════════════════════════════════════════════════════════════════════════════

# Representative real PDC type key that should make each governance check PASS
# purely from the type (no reliance on the label-keyword fallback).
_GOV_POSITIVE = {
    "Source Control Defined": "scm-gitlab",
    "Automated Build Stage": "build-docker",
    "Automated Test Stage": "scan-bandit",
    "SAST Integration": "scan-bandit",
    "Container Image Scanning": "scan-trivy",
    "SCA / Dependency Scanning": "scan-grype",
    "Secrets Detection": "scan-gitleaks",
    "Artifact Registry Defined": "registry-harbor",
    "Deployment Stage": "deploy-canary",
    "Environment Promotion Gates": "gate-manual",
    "SLSA L2 or Higher": "sign-cosign",
    "SBOM Generation": "sbom-syft",
    "IaC Scanning / Policy": "scan-checkov",
    "Pipeline Execution Monitoring": "mon-prometheus",
    "Failure Alerting": "mon-pagerduty",
    "Rollback / Blue-Green / Canary": "deploy-bluegreen",
    "Compliance Gate (FedRAMP/CMMC)": "comp-oscal",
    "DoD DevSecOps Ref Arch Aligned": "deploy-bigbang",
}


def _run_governance(client, nodes):
    """POST the governance analysis and return {check_title: status}."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    conn = _mock_conn(fetchone=_row(graph_json=_graph(nodes)))
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.pipeline.blueprint.record_canvas_decision"):
        resp = client.post(
            f"/devops/api/pipelines/{pipe_id}/analyze",
            data=json.dumps({"analysis_type": "governance"}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()["result"]
    return {c["title"]: c["status"] for c in result["checks"]}


@pytest.mark.parametrize("title,node_type", list(_GOV_POSITIVE.items()))
def test_governance_predicate_positive(client, title, node_type):
    """A single real PDC type makes exactly its target governance check pass."""
    statuses = _run_governance(client, [{"id": "n1", "type": node_type}])
    assert statuses[title] == "pass", (title, node_type, statuses)


def test_governance_all_fail_on_empty_graph(client):
    """The negative case for every predicate: no nodes → every check fails."""
    statuses = _run_governance(client, [])
    assert set(statuses.values()) == {"fail"}, statuses
    # And the full check vocabulary is present.
    assert set(statuses) == set(_GOV_POSITIVE)


def test_governance_wrong_taxonomy_prefix_does_not_score(client):
    """Legacy src-/bld-/tst- types (wrong taxonomy) must NOT satisfy checks —
    proving the predicates now key off real PDC types, not the old prefixes."""
    nodes = [
        {"id": "n1", "type": "src-repo"},
        {"id": "n2", "type": "bld-compile"},
        {"id": "n3", "type": "tst-unit"},
    ]
    statuses = _run_governance(client, nodes)
    assert statuses["Source Control Defined"] == "fail", statuses
    assert statuses["Automated Build Stage"] == "fail", statuses


# ══════════════════════════════════════════════════════════════════════════════
# 3. FINDINGS HEATMAP — pc_compliance_findings-derived, NOT config zeros
# ══════════════════════════════════════════════════════════════════════════════

def test_heatmap_findings_from_db_not_config(client):
    """type=findings must reflect pc_compliance_findings rows, not node config."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    # Nodes carry a bogus large config.findings_count — it must be IGNORED.
    nodes = [
        {"id": "n1", "type": "scan-bandit", "config": {"findings_count": 999}},
        {"id": "n2", "type": "scm-gitlab", "config": {"findings_count": 999}},
    ]
    graph_conn = _mock_conn(fetchone=_row(graph_json=_graph(nodes)))
    findings_conn = _mock_conn(fetchall=[
        _row(severity="CAT2", status="open"),
        _row(severity="CAT1", status="open"),
        _row(severity="CAT2", status="resolved"),
    ])
    # First get_connection() → graph fetch; second → findings query.
    gc = MagicMock(side_effect=[graph_conn, findings_conn])
    with patch("tools.pipeline.blueprint.get_connection", gc):
        resp = client.get(f"/devops/api/heatmap/{pipe_id}?type=findings")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["scope"] == "pipeline"
    assert body["total"] == 3, body           # from DB, not config (would be 999s)
    assert body["open"] == 2, body
    assert body["by_severity"] == {"CAT2": 2, "CAT1": 1}, body
    assert body["data"] == {}, "must NOT fabricate a per-node distribution"


def test_heatmap_findings_zero_when_no_rows(client):
    """No findings rows → honest zero (not a fabricated per-node overlay)."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    nodes = [{"id": "n1", "type": "scan-bandit", "config": {"findings_count": 5}}]
    graph_conn = _mock_conn(fetchone=_row(graph_json=_graph(nodes)))
    findings_conn = _mock_conn(fetchall=[])
    gc = MagicMock(side_effect=[graph_conn, findings_conn])
    with patch("tools.pipeline.blueprint.get_connection", gc):
        resp = client.get(f"/devops/api/heatmap/{pipe_id}?type=findings")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["total"] == 0 and body["open"] == 0, body


def test_pipeline_findings_endpoint_returns_rows(client):
    """GET /api/pipelines/<id>/findings returns the raw findings list."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    rows = [
        _row(id="f1", pipeline_id=pipe_id, rule_id="qdc.g1", severity="CAT2",
             status="open", title="Quality gate failed: g1"),
    ]
    conn = _mock_conn(fetchall=rows)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        resp = client.get(f"/devops/api/pipelines/{pipe_id}/findings")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["count"] == 1
    assert body["findings"][0]["rule_id"] == "qdc.g1"
    # LIMIT 100 pagination clause is present in the executed SQL.
    sql = conn.execute.call_args_list[0].args[0]
    assert "LIMIT 100" in sql and "OFFSET" in sql


def test_pipeline_findings_endpoint_requires_login(client):
    """Unauthenticated access is rejected (login required)."""
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get(f"/devops/api/pipelines/{uuid.uuid4()}/findings")
    assert resp.status_code in (302, 401, 403), resp.status_code


# ══════════════════════════════════════════════════════════════════════════════
# 4. QDC ATTRIBUTION — correlation id, else NULL (no most-recent guess)
# ══════════════════════════════════════════════════════════════════════════════

def _executed_sql(conn):
    return [c.args[0] for c in conn.execute.call_args_list]


def _insert_params(conn):
    for c in conn.execute.call_args_list:
        if c.args[0].startswith("INSERT INTO pc_compliance_findings"):
            return c.args[1]
    return None


def test_qdc_attribution_with_pipeline_id_correlation():
    """A failing gate whose payload carries a resolvable pipeline_id attributes
    the finding to THAT pipeline."""
    from tools.pipeline import bus_subscriber

    conn = MagicMock()
    cur = MagicMock()
    # 1) SELECT id FROM pipelines WHERE id='pipe-abc' -> found
    # 2) SELECT id FROM pc_compliance_findings ... status='open' -> none
    cur.fetchone.side_effect = [_row(id="pipe-abc"), None]
    conn.execute.return_value = cur

    payload = {"gate_id": "g1", "pipeline_id": "pipe-abc",
               "passed": False, "status": "fail", "score": 10}
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        bus_subscriber._on_qdc_gate_executed("evt-1", "qdc", "qdc.gate.executed", payload)

    params = _insert_params(conn)
    assert params is not None, "a finding must be inserted"
    assert params[1] == "pipe-abc", "finding attributed to the correlated pipeline"
    # Must NOT fall back to most-recent.
    assert not any("ORDER BY updated_at" in s for s in _executed_sql(conn))


def test_qdc_attribution_without_correlation_stores_null():
    """A failing gate with no usable correlation stores pipeline_id=NULL and
    NEVER guesses the most-recently-updated pipeline."""
    from tools.pipeline import bus_subscriber

    conn = MagicMock()
    cur = MagicMock()
    # Only the existing-finding lookup runs -> none.
    cur.fetchone.side_effect = [None]
    conn.execute.return_value = cur

    payload = {"gate_id": "g2", "passed": False, "status": "fail", "score": 0}
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        bus_subscriber._on_qdc_gate_executed("evt-2", "qdc", "qdc.gate.executed", payload)

    params = _insert_params(conn)
    assert params is not None, "a finding must still be inserted"
    assert params[1] is None, "unattributed finding stores NULL pipeline_id"
    sql = _executed_sql(conn)
    assert not any("ORDER BY updated_at" in s for s in sql), "no most-recent guess"
    assert not any("SELECT id FROM pipelines" in s for s in sql), \
        "no pipeline lookup when payload carries no correlation id"


def test_qdc_attribution_design_id_only_when_it_resolves():
    """design_id is honored only when it resolves to a real pipeline; an
    unresolvable design_id yields NULL (not a wrong guess)."""
    from tools.pipeline import bus_subscriber

    # design_id does NOT resolve -> pipeline lookup returns None, then existing None.
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = [None, None]
    conn.execute.return_value = cur

    payload = {"gate_id": "g3", "design_id": "qdc-design-xyz",
               "passed": False, "status": "fail", "score": 0}
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        bus_subscriber._on_qdc_gate_executed("evt-3", "qdc", "qdc.gate.executed", payload)

    params = _insert_params(conn)
    assert params is not None
    assert params[1] is None, "unresolvable design_id -> NULL attribution"
    assert not any("ORDER BY updated_at" in s for s in _executed_sql(conn))
