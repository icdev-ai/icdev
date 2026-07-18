# CUI // SP-CTI
"""Correctness + input-validation tests for the Pipeline Design Canvas (pdx-fix-03).

Covers:
  * iac_validator network-policy: no false positive on `enable_dns_support = true`,
    true positive on `cluster_endpoint_public_access = true`.
  * iac_validator HCL brace check: brace-count imbalance is WARN, not FAIL.
  * iac_validator terraform layer: `init -backend=false` runs BEFORE `validate`
    (argv order asserted with a mocked subprocess; no terraform binary required).
  * deploy_generator helm ordering: releases emitted in deploy_catalog install_order.
  * deploy_generator ICDEV_HELM_MIRROR air-gap override is respected.
  * blueprint export route: ImportError -> HTTP 501 (not a placeholder 200).
  * blueprint garbage query params -> HTTP 400 (not 500).
  * blueprint snippet/template load: classification propagates onto the pipeline.
  * export SLA block: string `target` ("99.9") does not crash (coerced to float).
  * blueprint feature-flag truth table (registry-aligned OFF default).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# iac_validator — network policy
# ══════════════════════════════════════════════════════════════════════════════

def test_network_policy_no_false_positive_on_dns_support_true():
    from tools.pipeline.iac_validator import _check_network_policy

    tf = {
        "path": "02-compute/eks.tf",
        "content": (
            'module "eks" {\n'
            "  enable_dns_support = true\n"
            "  cluster_endpoint_public_access = false\n"
            "}\n"
        ),
    }
    res = _check_network_policy([tf])
    assert res.status == "pass", res.to_dict()


def test_network_policy_true_positive_on_public_access_true():
    from tools.pipeline.iac_validator import _check_network_policy

    tf = {
        "path": "02-compute/eks.tf",
        "content": (
            'module "eks" {\n'
            "  cluster_endpoint_public_access = true\n"
            "}\n"
        ),
    }
    res = _check_network_policy([tf])
    assert res.status == "warn", res.to_dict()
    assert any("public" in d.lower() for d in res.details)


# ══════════════════════════════════════════════════════════════════════════════
# iac_validator — HCL brace check (warn, not fail)
# ══════════════════════════════════════════════════════════════════════════════

def test_brace_imbalance_is_warn_not_fail():
    """An unbalanced brace inside a string literal must NOT hard-fail."""
    from tools.pipeline.iac_validator import _check_hcl_syntax

    # 2 real block pairs, plus one stray '{' inside a string literal -> open>close.
    content = (
        'resource "aws_s3_bucket" "b" {\n'
        "  tags = {\n"
        '    Note = "has a { brace in a string"\n'
        "  }\n"
        "}\n"
    )
    res = _check_hcl_syntax("01-network/main.tf", content)
    assert res.status == "warn", res.to_dict()
    # The stray-brace note is present but classified advisory.
    assert any("brace" in d.lower() for d in res.details)


def test_missing_opening_brace_still_fails():
    """A genuinely malformed block declaration remains a hard fail."""
    from tools.pipeline.iac_validator import _check_hcl_syntax

    # A block declaration line that uses '=' instead of an opening brace is a
    # genuine structural error the narrow heuristic catches.
    content = 'variable "region" = "us-east-1"\n'
    res = _check_hcl_syntax("main.tf", content)
    assert res.status == "fail", res.to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# iac_validator — terraform layer runs init before validate
# ══════════════════════════════════════════════════════════════════════════════

def test_terraform_layer_runs_init_before_validate(monkeypatch):
    """`terraform init -backend=false` must run BEFORE `terraform validate`."""
    from tools.pipeline import iac_validator

    monkeypatch.setattr(iac_validator.shutil, "which", lambda _: "/usr/bin/terraform")

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(iac_validator.subprocess, "run", fake_run)

    files = [{"path": "01-network/main.tf", "content": 'module "vpc" { source = "x" }\n'}]
    results = iac_validator._layer4_plan(files)

    # First subprocess call must be `terraform init -backend=false ...`
    assert calls, "expected terraform to be invoked"
    assert calls[0][:3] == ["terraform", "init", "-backend=false"], calls
    # A subsequent `validate` call must appear after init.
    validate_idx = next((i for i, c in enumerate(calls) if c[:2] == ["terraform", "validate"]), None)
    assert validate_idx is not None and validate_idx > 0, calls
    assert results and any(r.check_name == "terraform_validate" for r in results)


def test_terraform_layer_init_failure_is_skip_not_fail(monkeypatch):
    """If init fails (offline module download), the layer reports skip, not fail."""
    from tools.pipeline import iac_validator

    monkeypatch.setattr(iac_validator.shutil, "which", lambda _: "/usr/bin/terraform")

    def fake_run(argv, **kwargs):
        if argv[:2] == ["terraform", "init"]:
            return MagicMock(returncode=1, stdout="", stderr="could not download module")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(iac_validator.subprocess, "run", fake_run)

    files = [{"path": "01-network/main.tf", "content": 'module "vpc" { source = "x" }\n'}]
    results = iac_validator._layer4_plan(files)
    assert results and all(r.status == "skip" for r in results), [r.to_dict() for r in results]


# ══════════════════════════════════════════════════════════════════════════════
# deploy_generator — helm ordering + mirror override
# ══════════════════════════════════════════════════════════════════════════════

def _release_order(main_tf_content):
    """Return the ordered list of helm_release resource names from main.tf."""
    import re

    return re.findall(r'resource "helm_release" "([^"]+)"', main_tf_content)


def test_helm_releases_emitted_in_install_order():
    from tools.pipeline.deploy_generator import _gen_helm_releases

    # vault install_order=30 (via vault-hashicorp), kube-prometheus-stack=60
    # (via mon-prometheus). Version-string sort would order them differently;
    # install_order must place vault before kube-prometheus-stack.
    files = _gen_helm_releases(["kube-prometheus-stack", "vault"], "proj", {})
    main_tf = next(f["content"] for f in files if f["path"] == "04-tools/main.tf")
    order = _release_order(main_tf)
    assert order.index("vault") < order.index("kube_prometheus_stack"), order
    # Each release after the first chains to the previous via depends_on.
    assert "depends_on = [helm_release.vault]" in main_tf, main_tf


def test_helm_mirror_override_respected(monkeypatch):
    """ICDEV_HELM_MIRROR (captured at import) must override the chart repo URL."""
    from tools.pipeline import deploy_catalog
    from tools.pipeline.deploy_generator import _gen_helm_releases

    monkeypatch.setattr(deploy_catalog, "_HELM_MIRROR", "https://charts.internal.local")
    files = _gen_helm_releases(["vault"], "proj", {})
    main_tf = next(f["content"] for f in files if f["path"] == "04-tools/main.tf")
    assert 'repository = "https://charts.internal.local"' in main_tf, main_tf
    assert "helm.releases.hashicorp.com" not in main_tf, main_tf


# ══════════════════════════════════════════════════════════════════════════════
# export — SLA target coercion
# ══════════════════════════════════════════════════════════════════════════════

def test_openslo_string_target_does_not_crash():
    from tools.pipeline.export import _openslo_service_block

    # UI supplies target as a string; target/100 must not raise TypeError.
    block = _openslo_service_block("svc", target="99.9")
    text = "\n".join(block)
    assert "target: 0.999" in text, text


def test_openslo_garbage_target_falls_back():
    from tools.pipeline.export import _openslo_service_block

    block = _openslo_service_block("svc", target="not-a-number")
    text = "\n".join(block)
    assert "target: 0.999" in text, text  # falls back to 99.9/100


def test_runbook_trigger_pattern_is_yaml_safe():
    from tools.pipeline.export import _to_runbook_manifest

    nodes = [{
        "type": "sre-runbook",
        "label": "custom rb",
        "config": {"trigger_pattern": "it's a 'quoted' pattern"},
    }]
    out = _to_runbook_manifest(nodes, [], "p")
    # Single quotes are doubled in YAML single-quoted style; parses cleanly.
    import yaml

    parsed = yaml.safe_load(out)
    rb = parsed["runbooks"][0]
    assert rb["trigger_pattern"] == "it's a 'quoted' pattern", rb


# ══════════════════════════════════════════════════════════════════════════════
# blueprint — Flask route tests
# ══════════════════════════════════════════════════════════════════════════════

def _make_app():
    import os

    os.environ["ICDEV_PIPELINE_ENABLED"] = "true"
    from tools.pipeline.blueprint import create_pipeline_blueprint

    with patch("tools.pipeline.blueprint.init_db"):
        bp = create_pipeline_blueprint()
    assert bp is not None
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(bp, url_prefix="/devops")
    return app


def _login(client, role="developer", user_id="test-user"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        if role:
            sess["role"] = role


def _mock_conn(fetchone=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.rowcount = 1
    conn.execute.return_value = cur
    return conn


def test_export_importerror_returns_501():
    app = _make_app()
    pipe_id = str(uuid.uuid4())
    row = {"id": pipe_id, "name": "X", "graph_json": '{"nodes":[],"edges":[]}'}
    conn = _mock_conn(fetchone=row)
    # Force `from tools.pipeline.export import export_pipeline` to raise ImportError.
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch.dict(sys.modules, {"tools.pipeline.export": None}):
        client = app.test_client()
        _login(client)
        resp = client.post(
            f"/devops/api/export/{pipe_id}",
            data=json.dumps({"format": "gitlab_ci"}),
            content_type="application/json",
        )
    assert resp.status_code == 501, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "export module unavailable"


def test_garbage_limit_param_returns_400():
    app = _make_app()
    with patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = client.get("/devops/api/ai-trace?limit=abc")
    assert resp.status_code == 400, resp.get_data(as_text=True)


def test_garbage_since_param_returns_400():
    app = _make_app()
    client = app.test_client()
    _login(client)
    resp = client.get(f"/devops/api/collab/{uuid.uuid4()}/poll?since=xyz")
    assert resp.status_code == 400, resp.get_data(as_text=True)


def test_invalid_classification_on_create_returns_422():
    app = _make_app()
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = client.post(
            "/devops/api/pipelines",
            data=json.dumps({"name": "X", "classification": "TOPSECRET-BOGUS"}),
            content_type="application/json",
        )
    assert resp.status_code == 422, resp.get_data(as_text=True)
    insert_calls = [c for c in conn.execute.call_args_list
                    if "INSERT INTO pipelines" in str(c.args[0])]
    assert not insert_calls, "invalid classification must not reach the INSERT"


def test_invalid_target_csp_on_create_returns_422():
    app = _make_app()
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = client.post(
            "/devops/api/pipelines",
            data=json.dumps({"name": "X", "target_csp": "moon-cloud"}),
            content_type="application/json",
        )
    assert resp.status_code == 422, resp.get_data(as_text=True)


def test_garbage_boundary_pos_returns_400():
    app = _make_app()
    pipe_id = str(uuid.uuid4())
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = client.post(
            f"/devops/api/boundaries/{pipe_id}",
            data=json.dumps({"label": "Zone", "pos_x": "abc"}),
            content_type="application/json",
        )
    assert resp.status_code == 400, resp.get_data(as_text=True)


def test_snippet_load_propagates_classification():
    """Loading a SECRET/IL5 snippet must carry classification onto the pipeline."""
    app = _make_app()
    snip_id = str(uuid.uuid4())
    snip_row = {
        "id": snip_id, "name": "IL5 SCCA", "description": "d",
        "graph_json": '{"nodes":[],"edges":[]}',
        "classification_level": "SECRET", "impact_level": "IL6",
    }
    conn = _mock_conn(fetchone=snip_row)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = client.post(f"/devops/api/snippets/{snip_id}/load")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    insert = next(c for c in conn.execute.call_args_list
                  if "INSERT INTO pipelines" in str(c.args[0]))
    params = insert.args[1]
    assert "SECRET" in params, params  # classification propagated


def test_template_load_propagates_classification():
    """Template load carries the template row's classification/target_csp."""
    app = _make_app()
    tpl_id = str(uuid.uuid4())
    tpl_row = {
        "id": tpl_id, "name": "DoD FedRAMP", "description": "d",
        "graph_json": '{"nodes":[],"edges":[]}',
        "classification": "CUI // SP-CTI", "target_csp": "aws",
    }
    conn = _mock_conn(fetchone=tpl_row)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = client.post(f"/devops/api/templates/{tpl_id}/load")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    insert = next(c for c in conn.execute.call_args_list
                  if "INSERT INTO pipelines" in str(c.args[0]))
    params = insert.args[1]
    assert "CUI // SP-CTI" in params, params
    assert "aws" in params, params


# ══════════════════════════════════════════════════════════════════════════════
# blueprint — feature-flag truth table (pdx-fix-03 / pdx-ops-01)
# ══════════════════════════════════════════════════════════════════════════════

def _factory_enabled(monkeypatch, pdc=None, legacy=None):
    from tools.pipeline.blueprint import create_pipeline_blueprint

    for key in ("ICDEV_PDC_ENABLED", "ICDEV_PIPELINE_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    if pdc is not None:
        monkeypatch.setenv("ICDEV_PDC_ENABLED", pdc)
    if legacy is not None:
        monkeypatch.setenv("ICDEV_PIPELINE_ENABLED", legacy)
    with patch("tools.pipeline.blueprint.init_db"):
        return create_pipeline_blueprint() is not None


def test_flag_truth_table(monkeypatch):
    # (pdc, legacy) -> expected enabled
    cases = [
        (None, None, False),   # both unset -> registry-consistent OFF
        ("true", None, True),  # PDC explicit on
        (None, "true", True),  # legacy explicit on
        ("false", "true", True),   # legacy explicit truthy overrides
        ("true", "false", True),   # PDC explicit on
        ("false", "false", False),
        ("false", None, False),
        (None, "false", False),
    ]
    for pdc, legacy, expected in cases:
        assert _factory_enabled(monkeypatch, pdc, legacy) is expected, (pdc, legacy)
