# CUI // SP-CTI
"""Tests for tools/infra_canvas/emitters/helm.py — 8 deterministic cases.

Cases:
  1.  emit_manifest — k8s-deployment node produces valid Deployment YAML
  2.  emit_manifest — k8s-service node produces valid Service YAML
  3.  emit_manifest — k8s-configmap node produces valid ConfigMap YAML
  4.  emit_manifest — k8s-hpa node produces valid HPA YAML
  5.  emit_manifest — CUI classification label injected
  6.  emit_manifest — unsupported node type raises UnsupportedResourceError
  7.  emit_chart — multi-node chart returns all expected template files
  8.  emit_chart — Chart.yaml contains chart name + version + CUI annotation
"""

import sys
import pathlib

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from tools.infra_canvas.emitters.helm import (
    UnsupportedResourceError,
    emit_chart,
    emit_manifest,
    _derive_values,
)

# ── Node fixtures ─────────────────────────────────────────────────────────────

_DEPLOYMENT = {
    "id": "deploy-api",
    "type": "k8s-deployment",
    "label": "api-server",
    "metadata": {
        "image": "registry.example.com/api:1.0.0",
        "replicas": 2,
        "port": 8080,
        "cpu_request": "250m",
        "cpu_limit": "500m",
        "memory_request": "256Mi",
        "memory_limit": "512Mi",
    },
}

_SERVICE = {
    "id": "svc-api",
    "type": "k8s-service",
    "label": "api-server",
    "metadata": {
        "port": 80,
        "target_port": 8080,
        "service_type": "ClusterIP",
    },
}

_CONFIGMAP = {
    "id": "cm-api",
    "type": "k8s-configmap",
    "label": "api-config",
    "metadata": {
        "data": {
            "LOG_LEVEL": "INFO",
            "APP_ENV": "production",
        },
    },
}

_HPA = {
    "id": "hpa-api",
    "type": "k8s-hpa",
    "label": "api-server",
    "metadata": {
        "min_replicas": 2,
        "max_replicas": 10,
        "cpu_utilization": 70,
    },
}

_SECRET = {
    "id": "secret-api",
    "type": "k8s-secret",
    "label": "api-secret",
    "metadata": {
        "keys": ["DB_PASSWORD", "API_KEY"],
        "secret_type": "Opaque",
    },
}

_CUI_DEPLOY = {
    "id": "deploy-secure",
    "type": "k8s-deployment",
    "label": "secure-api",
    "metadata": {
        "image": "registry.example.com/secure-api:2.0.0",
        "replicas": 3,
        "port": 8443,
        "classification": "CUI//SP-CTI",
    },
}


# ── Case 1: Deployment YAML ───────────────────────────────────────────────────

def test_emit_manifest_deployment():
    manifest_str = emit_manifest(_DEPLOYMENT)

    # Must parse as valid YAML
    doc = yaml.safe_load(manifest_str)
    assert doc["apiVersion"] == "apps/v1"
    assert doc["kind"] == "Deployment"
    assert doc["metadata"]["name"] == "api-server"

    spec = doc["spec"]
    assert spec["replicas"] == 2

    container = spec["template"]["spec"]["containers"][0]
    assert container["image"] == "registry.example.com/api:1.0.0"
    assert container["name"] == "api-server"

    # STIG hardening: securityContext required
    sc = container["securityContext"]
    assert sc["runAsNonRoot"] is True
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False

    # Resource limits must be present (STIG CAT2 — denial-of-service prevention)
    resources = container["resources"]
    assert resources["requests"]["cpu"] == "250m"
    assert resources["limits"]["cpu"] == "500m"
    assert resources["requests"]["memory"] == "256Mi"
    assert resources["limits"]["memory"] == "512Mi"


# ── Case 2: Service YAML ──────────────────────────────────────────────────────

def test_emit_manifest_service():
    manifest_str = emit_manifest(_SERVICE)

    doc = yaml.safe_load(manifest_str)
    assert doc["apiVersion"] == "v1"
    assert doc["kind"] == "Service"
    assert doc["metadata"]["name"] == "api-server"

    spec = doc["spec"]
    assert spec["type"] == "ClusterIP"
    assert spec["ports"][0]["port"] == 80
    assert spec["ports"][0]["targetPort"] == 8080


# ── Case 3: ConfigMap YAML ────────────────────────────────────────────────────

def test_emit_manifest_configmap():
    manifest_str = emit_manifest(_CONFIGMAP)

    doc = yaml.safe_load(manifest_str)
    assert doc["apiVersion"] == "v1"
    assert doc["kind"] == "ConfigMap"
    assert doc["metadata"]["name"] == "api-config"

    data = doc["data"]
    assert data["LOG_LEVEL"] == "INFO"
    assert data["APP_ENV"] == "production"


# ── Case 4: HPA YAML ──────────────────────────────────────────────────────────

def test_emit_manifest_hpa():
    manifest_str = emit_manifest(_HPA)

    doc = yaml.safe_load(manifest_str)
    assert doc["apiVersion"] == "autoscaling/v2"
    assert doc["kind"] == "HorizontalPodAutoscaler"
    assert doc["metadata"]["name"] == "api-server"

    spec = doc["spec"]
    assert spec["minReplicas"] == 2
    assert spec["maxReplicas"] == 10

    # CPU metric must be present
    metrics = spec["metrics"]
    cpu_metric = next(m for m in metrics if m.get("type") == "Resource")
    assert cpu_metric["resource"]["name"] == "cpu"
    assert cpu_metric["resource"]["target"]["averageUtilization"] == 70


# ── Case 5: CUI label injection ───────────────────────────────────────────────

def test_emit_manifest_cui_labels():
    manifest_str = emit_manifest(_CUI_DEPLOY)

    doc = yaml.safe_load(manifest_str)
    labels = doc["metadata"].get("labels", {})
    annotations = doc["metadata"].get("annotations", {})

    # Either labels or annotations must carry classification
    cui_present = (
        any("classification" in k.lower() for k in labels)
        or any("classification" in k.lower() for k in annotations)
    )
    assert cui_present, "CUI classification must appear in labels or annotations"

    # CUI header must appear in the raw YAML string
    assert "CUI" in manifest_str


# ── Case 5b: Secret YAML ─────────────────────────────────────────────────────

def test_emit_manifest_secret():
    manifest_str = emit_manifest(_SECRET)

    doc = yaml.safe_load(manifest_str)
    assert doc["apiVersion"] == "v1"
    assert doc["kind"] == "Secret"
    assert doc["metadata"]["name"] == "api-secret"
    assert doc["type"] == "Opaque"

    # Placeholder refs only — no real secret values
    string_data = doc.get("stringData", {})
    assert "DB_PASSWORD" in string_data
    assert string_data["DB_PASSWORD"].startswith("REPLACE_WITH_")

    # External-secrets annotation must be present
    annotations = doc["metadata"].get("annotations", {})
    assert any("external-secrets" in v for v in annotations.values())


# ── Case 5c: values.yaml derived from node attrs ──────────────────────────────

def test_derive_values_from_nodes():
    # Use configmap (unique label) to avoid collision with deployment/service "api-server"
    deploy_unique = {
        "id": "deploy-backend",
        "type": "k8s-deployment",
        "label": "backend",
        "metadata": {"image": "backend:1.0", "replicas": 3, "port": 9090},
    }
    nodes = [deploy_unique, _CONFIGMAP]
    values = _derive_values(nodes)

    # Deployment node attributes should appear under its label
    assert "backend" in values
    deploy_vals = values["backend"]
    assert deploy_vals["replicas"] == 3
    assert deploy_vals["port"] == 9090

    # ConfigMap data dict is excluded; label key exists only if other scalars present
    # api-config has no scalar attrs outside 'data' — key may be absent
    if "api-config" in values:
        assert "data" not in values["api-config"]


# ── Case 6: Unsupported type raises ──────────────────────────────────────────

def test_emit_manifest_unsupported_raises():
    bad_node = {"id": "x", "type": "aws-ec2", "label": "VM", "metadata": {}}
    with pytest.raises(UnsupportedResourceError, match="aws-ec2"):
        emit_manifest(bad_node)


# ── Case 7: emit_chart returns all template files ─────────────────────────────

def test_emit_chart_returns_all_templates():
    nodes = [_DEPLOYMENT, _SERVICE, _CONFIGMAP, _SECRET]
    chart = emit_chart(nodes, chart_name="api-chart", chart_version="1.0.0")

    # Must contain Chart.yaml and values.yaml
    assert "Chart.yaml" in chart
    assert "values.yaml" in chart

    # Must contain one template file per node
    template_keys = [k for k in chart if k.startswith("templates/")]
    assert len(template_keys) == 4

    # Each template must be non-empty valid YAML
    for key in template_keys:
        doc = yaml.safe_load(chart[key])
        assert doc is not None
        assert "kind" in doc


# ── Case 8: Chart.yaml content ────────────────────────────────────────────────

def test_emit_chart_yaml_content():
    nodes = [_DEPLOYMENT, _CUI_DEPLOY]
    chart = emit_chart(
        nodes,
        chart_name="secure-chart",
        chart_version="2.1.0",
        classification="CUI//SP-CTI",
    )

    chart_yaml = yaml.safe_load(chart["Chart.yaml"])
    assert chart_yaml["name"] == "secure-chart"
    assert chart_yaml["version"] == "2.1.0"
    assert chart_yaml["apiVersion"] == "v2"

    # CUI annotation must be present in Chart.yaml
    annotations = chart_yaml.get("annotations", {})
    assert any("classification" in k.lower() for k in annotations), (
        "Chart.yaml must carry classification annotation for CUI charts"
    )
