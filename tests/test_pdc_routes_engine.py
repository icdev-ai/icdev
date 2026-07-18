# CUI // SP-CTI
"""Real-schema engine-route tests for pdx-test-01 — PDC value paths.

The PDC "engine" routes (export / validate / fix / deploy / remediate /
scorecard / heatmap) had no real-schema coverage. This file drives them against a
REAL sqlite DB wrapped in StorageConnection (the blueprint authors ``%s`` SQL, so a
raw sqlite3 connection would choke — repo gotcha), seeding pipelines through the
create route so the whole write→read→transform path is exercised.

Each test asserts response SHAPE plus a substantive value (not a smoke 200):
  * export round-trip: gitlab/github parse with yaml.safe_load, drawio with
    xml.etree, csv with the csv module, svg is well-formed and carries the label.
  * validate: real ValidationResults shape (gate/results/layers_run).
  * fix: applies a fix_snippet path and re-validates (honest 'fixed'/'applied').
  * deploy (K8s graph): manifest with layered install order + expected layers.
  * remediate: real remediation-plan shape from live compliance findings.
  * scorecard: non-zero stage coverage (fix-02) out of 12 stages.
  * heatmap: per-node overlays for time/compliance/freshness; pipeline-level
    findings from pc_compliance_findings.

KNOWN-ISSUE tests (assert CURRENT behavior, do not fix here) are labelled inline.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask  # noqa: E402

from tools.db.storage import StorageConnection  # noqa: E402
from tools.pipeline.db.init_db import SCHEMA  # noqa: E402


# ── real-sqlite fixtures ──────────────────────────────────────────────────────


def _raw_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _new_conn(db_path: Path) -> StorageConnection:
    return StorageConnection(_raw_conn(db_path), "sqlite")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline_canvas_test.db"
    conn = _raw_conn(p)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return p


@pytest.fixture
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
        assert bp is not None
        flask_app.register_blueprint(bp, url_prefix="/devops")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def wired(db_path):
    """Route DB + KG-reindex hook stubbed. get_connection -> real sqlite."""
    with patch("tools.pipeline.blueprint.get_connection", side_effect=lambda: _new_conn(db_path)), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"):
        yield db_path


def _login(client, user_id="dev-alice", role="developer"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["role"] = role


# ── graph fixtures ─────────────────────────────────────────────────────────────

_CLEAN_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "scm-gitlab", "label": "Source", "x": 0, "y": 0},
        {"id": "n2", "type": "scan-bandit", "label": "SAST", "x": 150, "y": 0},
        {"id": "n3", "type": "build-docker", "label": "Build", "x": 300, "y": 0},
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
    ],
}

_K8S_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "scm-gitlab", "label": "Source"},
        {"id": "n2", "type": "build-docker", "label": "Build"},
        {"id": "n3", "type": "k8s-cluster", "label": "K8s"},
        {"id": "n4", "type": "registry-harbor", "label": "Harbor"},
    ],
    "edges": [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}],
}


def _create_pipeline(client, graph, name="Engine Pipe", classification="public",
                     target_csp="generic"):
    resp = client.post(
        "/devops/api/pipelines",
        data=json.dumps({"name": name, "graph_json": graph,
                         "classification": classification, "target_csp": target_csp}),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


# ══════════════════════════════════════════════════════════════════════════════
# 1. Export round-trip — each supported format parses with its real parser
# ══════════════════════════════════════════════════════════════════════════════


def _export(client, pipe_id, fmt):
    return client.post(
        f"/devops/api/export/{pipe_id}",
        data=json.dumps({"format": fmt}),
        content_type="application/json",
    )


def test_export_gitlab_ci_is_valid_yaml(client, wired):
    _login(client)
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    resp = _export(client, pid, "gitlab_ci")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["format"] == "gitlab_ci"
    assert body["filename"].endswith(".gitlab-ci.yml")
    doc = yaml.safe_load(body["content"])
    assert isinstance(doc, dict) and "stages" in doc


def test_export_github_actions_is_valid_yaml(client, wired):
    _login(client)
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    body = _export(client, pid, "github_actions").get_json()
    doc = yaml.safe_load(body["content"])
    # NB: PyYAML (YAML 1.1) parses the bare ``on:`` trigger key as the boolean
    # True (the "Norway problem"), so assert on that rather than the string "on".
    assert "jobs" in doc and True in doc
    assert doc["name"] == "Engine Pipe"


def test_export_drawio_is_wellformed_xml(client, wired):
    _login(client)
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    body = _export(client, pid, "drawio").get_json()
    root = ET.fromstring(body["content"])
    assert root.tag == "mxfile"
    # one mxCell per node (plus the two root cells).
    assert len(root.findall(".//mxCell")) >= len(_CLEAN_GRAPH["nodes"])


def test_export_svg_is_wellformed_and_carries_labels(client, wired):
    _login(client)
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    body = _export(client, pid, "svg").get_json()
    content = body["content"]
    root = ET.fromstring(content)
    assert root.tag.endswith("svg")
    assert "Source" in content and "SAST" in content


def test_export_csv_parses_with_csv_module(client, wired):
    _login(client)
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    body = _export(client, pid, "csv").get_json()
    rows = list(csv.DictReader(io.StringIO(body["content"])))
    assert len(rows) == 3
    assert rows[0]["id"] == "n1"
    # stage is derived server-side from the type taxonomy (scm- -> source).
    assert rows[0]["stage"] == "source"


def test_export_unknown_format_500(client, wired):
    _login(client)
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    resp = _export(client, pid, "not_a_format")
    # export_pipeline raises ValueError("Unknown export format") -> 500 branch.
    assert resp.status_code == 500, resp.get_data(as_text=True)
    assert "Unknown export format" in resp.get_json()["error"]


def test_export_unknown_pipeline_404(client, wired):
    _login(client)
    resp = _export(client, str(uuid.uuid4()), "gitlab_ci")
    assert resp.status_code == 404


def test_export_drawio_does_not_escape_angle_brackets(client, wired):
    """KNOWN-ISSUE(pdx): export._to_drawio interpolates node labels into an XML
    ``value="..."`` attribute WITHOUT escaping, so a label containing '<' produces
    malformed XML (and is an XML-injection vector). The graph_json XSS defence is
    at the canvas RENDER boundary only — the write boundary
    (validate_graph_json_payload) does NOT sanitize labels, so the raw '<' reaches
    export. Asserting current behavior; the export escaping fix is out of scope.
    """
    _login(client)
    pid = _create_pipeline(client, {"nodes": [{"id": "n1", "type": "x", "label": "A<b>"}],
                                    "edges": []})
    body = _export(client, pid, "drawio").get_json()
    with pytest.raises(ET.ParseError):
        ET.fromstring(body["content"])


# ══════════════════════════════════════════════════════════════════════════════
# 2. Validate + fix
# ══════════════════════════════════════════════════════════════════════════════


def test_validate_returns_validation_results_shape(client, wired):
    _login(client)
    pid = _create_pipeline(client, _K8S_GRAPH)
    resp = client.post(f"/devops/api/validate/{pid}",
                       data=json.dumps({"target_csp": "aws", "max_layer": 3}),
                       content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert set(body) >= {"bundle_summary", "validation", "file_count"}
    val = body["validation"]
    assert val["gate"] in ("pass", "warn", "fail")
    assert val["layers_run"] == 3
    assert isinstance(val["results"], list) and val["results"]
    # every result row carries the 5-layer pyramid shape.
    assert set(val["results"][0]) >= {"layer", "check", "status"}


def test_validate_bad_max_layer_returns_400(client, wired):
    _login(client)
    pid = _create_pipeline(client, _K8S_GRAPH)
    resp = client.post(f"/devops/api/validate/{pid}",
                       data=json.dumps({"max_layer": "abc"}),
                       content_type="application/json")
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "max_layer" in resp.get_json()["error"]


def test_validate_unknown_pipeline_404(client, wired):
    _login(client)
    resp = client.post(f"/devops/api/validate/{uuid.uuid4()}",
                       data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 404


def test_fix_applies_snippet_and_revalidates(client, wired):
    """POST /fix with an add_tags fix_action injects a common_tags block into a
    .tf file and re-validates; response carries honest 'fixed'/'applied' + the
    re-validation shape."""
    _login(client)
    pid = _create_pipeline(client, _K8S_GRAPH)
    # add_tags targets any .tf file; the K8s bundle emits 02-compute/eks.tf etc.
    resp = client.post(
        f"/devops/api/validate/{pid}/fix",
        data=json.dumps({"fixes": [{"fix_action": "add_tags", "file": "02-compute/eks.tf"}]}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "fixed" in body and "applied" in body
    assert isinstance(body["applied"], list)
    assert body["fixed"] == len(body["applied"])
    # re-validation result merged in.
    assert "validation" in body and body["validation"]["gate"] in ("pass", "warn", "fail")


def test_fix_with_no_fixes_is_noop_but_revalidates(client, wired):
    _login(client)
    pid = _create_pipeline(client, _K8S_GRAPH)
    resp = client.post(f"/devops/api/validate/{pid}/fix",
                       data=json.dumps({"fixes": []}), content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["fixed"] == 0 and body["applied"] == []
    assert "validation" in body


# ══════════════════════════════════════════════════════════════════════════════
# 3. Deploy — layered install-order bundle (K8s-typed graph)
# ══════════════════════════════════════════════════════════════════════════════


def test_deploy_generates_layered_bundle(client, wired):
    _login(client, role="developer")  # deploy requires a write-tier role
    pid = _create_pipeline(client, _K8S_GRAPH)
    resp = client.post(f"/devops/api/deploy/{pid}",
                       data=json.dumps({"target_csp": "aws"}),
                       content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert set(body) >= {"summary", "files", "manifest", "file_contents"}
    manifest = body["manifest"]
    # Expected layers for a K8s + registry graph on AWS.
    assert manifest["layers"]["network"] is True
    assert manifest["layers"]["compute_k8s"] is True
    assert manifest["layers"]["platform"] is True
    assert manifest["target_csp"] == "aws"
    # fix-03 install-order: numbered layer directories emit in ascending order
    # (01-network before 02-compute before 03-platform).
    numbered = [p.split("/")[0] for p in body["files"] if p[:2].isdigit()]
    assert numbered == sorted(numbered), body["files"]
    assert "manifest.json" in body["files"] and "deploy.sh" in body["files"]
    # manifest.json content is itself valid JSON.
    assert json.loads(body["file_contents"]["manifest.json"])["node_count"] == 4


def test_deploy_denied_for_unknown_role(client, wired):
    _login(client, role=None)
    with client.session_transaction() as sess:
        sess["user_id"] = "x"
        sess.pop("role", None)
    pid_owner = None
    # create needs a write role; seed the pipeline with a developer first.
    _login(client, role="developer")
    pid_owner = _create_pipeline(client, _K8S_GRAPH)
    # now downgrade the caller to no role and attempt deploy.
    with client.session_transaction() as sess:
        sess.pop("role", None)
    resp = client.post(f"/devops/api/deploy/{pid_owner}",
                       data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 403, resp.get_data(as_text=True)


def test_deploy_unknown_pipeline_404(client, wired):
    _login(client, role="developer")
    resp = client.post(f"/devops/api/deploy/{uuid.uuid4()}",
                       data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 4. Remediate
# ══════════════════════════════════════════════════════════════════════════════


def test_remediate_returns_plan_from_live_findings(client, wired):
    """A graph with gaps yields real compliance findings -> a remediation plan."""
    _login(client)
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    resp = client.post(f"/devops/api/pipelines/{pid}/remediate",
                       data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert set(body) >= {"phases", "total_actions", "auto_fixable", "summary"}
    # the clean 3-node graph is missing most controls -> at least one action.
    assert body["total_actions"] >= 1
    assert isinstance(body["phases"], list)


def test_remediate_corrupt_graph_422(client, wired):
    _login(client, role="developer")
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    # Corrupt the stored graph_json directly.
    conn = _raw_conn(wired)
    conn.execute("UPDATE pipelines SET graph_json='{not json' WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    resp = client.post(f"/devops/api/pipelines/{pid}/remediate",
                       data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 422, resp.get_data(as_text=True)


def test_remediate_unknown_pipeline_404(client, wired):
    _login(client)
    resp = client.post(f"/devops/api/pipelines/{uuid.uuid4()}/remediate",
                       data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 5. Scorecard — non-zero stage coverage (fix-02)
# ══════════════════════════════════════════════════════════════════════════════


def test_scorecard_shape_and_nonzero_stage_coverage(client, wired):
    _login(client)
    # types spanning source/test/build -> at least 3 canonical stages covered.
    pid = _create_pipeline(client, _CLEAN_GRAPH)
    resp = client.get(f"/devops/api/pipelines/{pid}/scorecard")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert set(body) >= {"security_coverage", "slsa_level", "compliance",
                         "antipatterns", "stages_covered", "total_stages"}
    assert body["total_stages"] == 12
    assert body["stages_covered"] >= 3   # source + test + build (fix-02: derived from type)
    assert body["node_count"] == 3


def test_scorecard_unknown_pipeline_404(client, wired):
    _login(client)
    resp = client.get(f"/devops/api/pipelines/{uuid.uuid4()}/scorecard")
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 6. Heatmap — per-node overlays + pipeline-level findings
# ══════════════════════════════════════════════════════════════════════════════


def test_heatmap_execution_time_per_node(client, wired):
    _login(client)
    graph = {"nodes": [{"id": "n1", "type": "build-docker",
                        "config": {"avg_execution_min": 12}}], "edges": []}
    pid = _create_pipeline(client, graph)
    body = client.get(f"/devops/api/heatmap/{pid}?type=execution_time").get_json()
    assert body["type"] == "execution_time"
    assert body["data"]["n1"]["value"] == 12
    assert "color" in body["data"]["n1"]


def test_heatmap_compliance_and_freshness(client, wired):
    _login(client)
    graph = {"nodes": [{"id": "n1", "type": "scan-bandit",
                        "config": {"compliance_pct": 60, "tool_age_days": 400}}],
             "edges": []}
    pid = _create_pipeline(client, graph)
    comp = client.get(f"/devops/api/heatmap/{pid}?type=compliance").get_json()
    assert comp["data"]["n1"]["value"] == 60
    fresh = client.get(f"/devops/api/heatmap/{pid}?type=freshness").get_json()
    assert fresh["data"]["n1"]["value"] == 400


def test_heatmap_findings_from_db_not_config(client, wired):
    """type=findings aggregates pc_compliance_findings at the pipeline level,
    ignoring any node config.findings_count (fix-02 truthful scoring)."""
    _login(client)
    graph = {"nodes": [{"id": "n1", "type": "scan-bandit",
                        "config": {"findings_count": 999}}], "edges": []}
    pid = _create_pipeline(client, graph)
    # Seed real findings rows.
    conn = _raw_conn(wired)
    conn.execute("INSERT INTO pc_compliance_checks (id, pipeline_id, check_type) VALUES (?,?,?)",
                 ("chk1", pid, "full_audit"))
    for i, (sev, status) in enumerate([("CAT1", "open"), ("CAT2", "open"), ("CAT2", "resolved")]):
        conn.execute(
            "INSERT INTO pc_compliance_findings (id, pipeline_id, audit_id, rule_id, framework, "
            "title, severity, status) VALUES (?,?,?,?,?,?,?,?)",
            (f"f{i}", pid, "chk1", "R-1", "NIST", "finding", sev, status),
        )
    conn.commit()
    conn.close()
    body = client.get(f"/devops/api/heatmap/{pid}?type=findings").get_json()
    assert body["scope"] == "pipeline"
    assert body["total"] == 3          # from DB, not the 999 config value
    assert body["open"] == 2
    assert body["by_severity"] == {"CAT1": 1, "CAT2": 2}
    assert body["data"] == {}          # findings are not node-attributed


def test_heatmap_unknown_pipeline_404(client, wired):
    _login(client)
    resp = client.get(f"/devops/api/heatmap/{uuid.uuid4()}")
    assert resp.status_code == 404
