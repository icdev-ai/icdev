# CUI // SP-CTI
"""pdx-sec-06 — Generated-output integrity.

Covers three defects in the Pipeline Design Canvas generators:
  1. Secrets: default/weak passwords must not ship, and the IaC validator gate
     must FAIL on inline (HCL and YAML) secrets.
  2. Escaping: user-controlled node labels must not break the emitted GitLab CI,
     GitHub Actions, draw.io, or SVG documents, nor inject XSS into the SVG.
  3. Swallowed CI layer: a CI-generation failure must be visible in the manifest
     instead of the bundle silently shipping without a .gitlab-ci.yml.
"""

import csv
import io
import xml.etree.ElementTree as ET

import pytest
import yaml

from tools.pipeline.export import export_pipeline
from tools.pipeline.deploy_generator import generate_deploy_bundle, _safe_name
from tools.pipeline.iac_validator import (
    validate_bundle,
    _check_no_secrets,
    _check_helm_security,
)

# A malicious/awkward graph: labels carry YAML/XML/HTML-breaking characters, two
# nodes share a label (duplicate-key risk), and coordinates are strings.
HOSTILE_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "scm-gitlab", "label": "Deploy: prod <script>alert(1)</script>",
         "stage": "build", "x": "10", "y": "20"},
        {"id": "n2", "type": "scan-trivy", "label": "Deploy: prod <script>alert(1)</script>",
         "stage": "test", "x": 30, "y": 40},
    ],
    "edges": [{"source": "n1", "target": "n2", "label": "on: fail # boom"}],
}
HOSTILE_NAME = "My Pipe: <danger> & \"co\""


# ── (2) Escaping ────────────────────────────────────────────────────────────


def test_gitlab_ci_is_yaml_parseable_and_dedupes_jobs():
    content = export_pipeline(HOSTILE_GRAPH, HOSTILE_NAME, "gitlab_ci")["content"]
    doc = yaml.safe_load(content)  # must not raise
    assert isinstance(doc, dict)
    assert doc["stages"] == ["build", "test"]
    job_keys = [k for k in doc if k != "stages"]
    # Two nodes with identical labels must yield two distinct job keys.
    assert len(job_keys) == 2
    assert len(set(job_keys)) == 2
    for k in job_keys:
        assert all(c.isalnum() or c in "-_" for c in k), k


def test_github_actions_is_yaml_parseable_and_preserves_on_key():
    content = export_pipeline(HOSTILE_GRAPH, HOSTILE_NAME, "github_actions")["content"]
    doc = yaml.safe_load(content)  # must not raise
    # The `on` trigger key must survive as a string, not YAML-1.1 boolean True.
    assert "on" in doc and True not in doc
    assert set(doc["jobs"]) == {"build", "test"}
    assert doc["name"] == HOSTILE_NAME


def test_drawio_is_xml_parseable_with_escaped_labels():
    content = export_pipeline(HOSTILE_GRAPH, HOSTILE_NAME, "drawio")["content"]
    ET.fromstring(content)  # must not raise
    assert "<script>alert(1)</script>" not in content


def test_svg_escapes_labels_to_prevent_stored_xss():
    content = export_pipeline(HOSTILE_GRAPH, HOSTILE_NAME, "svg")["content"]
    ET.fromstring(content)  # must not raise
    # Raw script tag must be neutralized.
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content


def test_csv_quotes_fields_and_preserves_columns():
    graph = {"nodes": [{"id": "n1", "label": "a,b\"c", "type": "t", "x": 1, "y": 2}], "edges": []}
    content = export_pipeline(graph, "x", "csv")["content"]
    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0] == ["id", "label", "type", "stage", "x", "y"]
    assert rows[1][1] == 'a,b"c'  # comma + quote preserved, columns intact
    assert len(rows[1]) == 6


def test_safe_name_allowlists_and_never_empty():
    assert _safe_name('Bad: "name"\n rm -rf /') == "bad-name-rm-rf"
    assert all(c.isalnum() or c == "-" for c in _safe_name("A_B C!@#"))
    assert _safe_name("!!!") == "pipeline"  # fallback when nothing survives


# ── (1) Secrets ─────────────────────────────────────────────────────────────


def test_bundle_with_default_password_fails_gate():
    bad = [{"path": "values/grafana.yaml", "content": "grafana:\n  adminPassword: changeme\n"}]
    result = validate_bundle(bad)
    assert result["gate"] == "fail"


def test_yaml_literal_secret_flagged_but_secret_ref_is_not():
    literal = [{"path": "v.yaml", "content": "apiKey: sk-abcdef0123456789\n"}]
    assert _check_no_secrets(literal).status == "fail"

    ref = [{"path": "v.yaml", "content": (
        "admin:\n  existingSecret: grafana-admin-credentials\n  passwordKey: admin-password\n")}]
    assert _check_no_secrets(ref).status == "pass"

    env_var = [{"path": "v.yaml", "content": "password: ${VAULT_DB_PASSWORD}\n"}]
    assert _check_no_secrets(env_var).status == "pass"


def test_helm_security_default_password_is_fail_not_warn():
    hf = {"path": "values/x.yaml", "content": "grafana:\n  adminPassword: changeme\n"}
    assert _check_helm_security(hf).status == "fail"


def test_generated_bundle_ships_no_default_password():
    graph = {"nodes": [
        {"type": "mon-prometheus", "label": "Prometheus"},
        {"type": "aws-eks", "label": "EKS"},
    ], "edges": []}
    bundle = generate_deploy_bundle(graph, "Secure Test")
    all_content = "\n".join(f["content"] for f in bundle["files"])
    assert "adminPassword: changeme" not in all_content
    assert "password: changeme" not in all_content
    # The clean bundle must pass the secrets/helm gate.
    assert validate_bundle(bundle["files"])["gate"] == "pass"


# ── (3) Swallowed CI layer ──────────────────────────────────────────────────


def test_ci_generation_failure_is_visible_in_manifest(monkeypatch):
    import tools.pipeline.export as exp_mod

    def boom(*_a, **_k):
        raise RuntimeError("simulated CI failure")

    monkeypatch.setattr(exp_mod, "export_pipeline", boom)

    graph = {"nodes": [
        {"type": "scm-gitlab", "label": "GitLab"},
        {"type": "aws-eks", "label": "EKS"},
    ], "edges": []}
    bundle = generate_deploy_bundle(graph, "CI Fail Test")
    manifest = bundle["manifest"]

    assert manifest.get("layers", {}).get("ci_config_error") is True
    assert manifest.get("errors")
    assert manifest["errors"][0]["stage"] == "ci_config"

    paths = [f["path"] for f in bundle["files"]]
    assert "05-ci-config/CI_GENERATION_ERROR.txt" in paths
    assert "05-ci-config/.gitlab-ci.yml" not in paths


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
