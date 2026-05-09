from __future__ import annotations
# CUI // SP-CTI
"""Helm chart emitter for IDC graph nodes (K8s scope).

emit_manifest(node)                        -> str  — single K8s manifest YAML string
emit_chart(nodes, chart_name, ...)         -> dict — filename -> YAML-string mapping

Supported node types (Kubernetes workloads):
  k8s-deployment   apps/v1 Deployment  — STIG-hardened security context + resource limits
  k8s-service      v1 Service          — ClusterIP/NodePort/LoadBalancer
  k8s-configmap    v1 ConfigMap        — key/value environment configuration
  k8s-hpa          autoscaling/v2 HPA  — CPU-based horizontal pod autoscaler

STIG-hardened defaults applied to every Deployment container:
  runAsNonRoot: true            DISA K8s STIG V-242415
  readOnlyRootFilesystem: true  DISA K8s STIG V-242424
  allowPrivilegeEscalation: false  DISA K8s STIG V-242425
  resource limits required      DISA K8s STIG V-242397 (DoS prevention)
"""

from typing import Any

import yaml

# ── Type alias ────────────────────────────────────────────────────────────────
Node = dict[str, Any]

# ── Constants ─────────────────────────────────────────────────────────────────
_MANAGED_BY = "icdev-helm-emitter"
_SUPPORTED_TYPES = frozenset({"k8s-deployment", "k8s-service", "k8s-configmap", "k8s-secret", "k8s-hpa"})
_CUI_VALUES = {"CUI", "CUI//SP-CTI", "SECRET", "CUI//SP-CTI/IL4", "CUI//SP-CTI/IL5"}

# STIG-hardened security context — applied to every Deployment container
_STIG_SECURITY_CONTEXT: dict[str, Any] = {
    "runAsNonRoot": True,
    "readOnlyRootFilesystem": True,
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
}

# Default resource envelope when caller omits resource fields
_DEFAULT_RESOURCES: dict[str, Any] = {
    "requests": {"cpu": "100m", "memory": "128Mi"},
    "limits": {"cpu": "250m", "memory": "256Mi"},
}


class UnsupportedResourceError(ValueError):
    """Raised when a node type has no Helm manifest emitter."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta(node: Node) -> dict[str, Any]:
    return node.get("metadata") or {}


def _base_labels(name: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": name,
        "app.kubernetes.io/managed-by": _MANAGED_BY,
    }


def _cui_labels(meta: dict[str, Any]) -> dict[str, str]:
    """Return classification labels when node carries a CUI/SECRET marking."""
    classification = str(meta.get("classification", "")).strip()
    if classification in _CUI_VALUES:
        return {
            "icdev.io/classification": classification,
            "icdev.io/data-handling": "CUI//SP-CTI",
        }
    return {}


def _resource_block(meta: dict[str, Any]) -> dict[str, Any]:
    """Build K8s resources block from node metadata with safe defaults."""
    cpu_req = meta.get("cpu_request", _DEFAULT_RESOURCES["requests"]["cpu"])
    cpu_lim = meta.get("cpu_limit", _DEFAULT_RESOURCES["limits"]["cpu"])
    mem_req = meta.get("memory_request", _DEFAULT_RESOURCES["requests"]["memory"])
    mem_lim = meta.get("memory_limit", _DEFAULT_RESOURCES["limits"]["memory"])
    return {
        "requests": {"cpu": cpu_req, "memory": mem_req},
        "limits": {"cpu": cpu_lim, "memory": mem_lim},
    }


def _yaml_str(doc: dict[str, Any]) -> str:
    """Dump a dict to a clean YAML string (no flow style, unicode preserved)."""
    return yaml.dump(
        doc,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        indent=2,
    )


# ── Per-type manifest builders ────────────────────────────────────────────────

def _build_deployment(node: Node) -> str:
    meta = _meta(node)
    name = node.get("label", "app")
    image = meta.get("image", "nginx:latest")
    replicas = int(meta.get("replicas", 1))
    port = int(meta.get("port", 8080))

    labels = {**_base_labels(name), **_cui_labels(meta)}
    annotations: dict[str, str] = {}
    if _cui_labels(meta):
        annotations["icdev.io/classification"] = str(meta.get("classification", "")).strip()

    container: dict[str, Any] = {
        "name": name,
        "image": image,
        "ports": [{"containerPort": port}],
        "securityContext": _STIG_SECURITY_CONTEXT,
        "resources": _resource_block(meta),
    }

    doc: dict[str, Any] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "labels": labels,
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "securityContext": {"runAsNonRoot": True},
                    "containers": [container],
                },
            },
        },
    }
    if annotations:
        doc["metadata"]["annotations"] = annotations

    return _yaml_str(doc)


def _build_service(node: Node) -> str:
    meta = _meta(node)
    name = node.get("label", "app")
    port = int(meta.get("port", 80))
    target_port = int(meta.get("target_port", 8080))
    svc_type = meta.get("service_type", "ClusterIP")

    labels = {**_base_labels(name), **_cui_labels(meta)}

    doc: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "labels": labels,
        },
        "spec": {
            "type": svc_type,
            "selector": {"app.kubernetes.io/name": name},
            "ports": [
                {
                    "name": "http",
                    "port": port,
                    "targetPort": target_port,
                    "protocol": "TCP",
                }
            ],
        },
    }
    return _yaml_str(doc)


def _build_configmap(node: Node) -> str:
    meta = _meta(node)
    name = node.get("label", "app-config")
    data = meta.get("data", {})

    labels = {**_base_labels(name), **_cui_labels(meta)}

    doc: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "labels": labels,
        },
        "data": {str(k): str(v) for k, v in data.items()},
    }
    return _yaml_str(doc)


def _build_secret(node: Node) -> str:
    meta = _meta(node)
    name = node.get("label", "app-secret")
    # STIG note: secret data must never be plaintext in IaC — emit placeholder refs only.
    # Callers are expected to supply values via external-secrets or vault injection.
    keys = meta.get("keys", [])

    labels = {**_base_labels(name), **_cui_labels(meta)}
    annotations: dict[str, str] = {
        "icdev.io/secret-management": "external-secrets-required",
    }
    if _cui_labels(meta):
        annotations["icdev.io/classification"] = str(meta.get("classification", "")).strip()

    # Emit empty stringData placeholders — never real secret values
    string_data = {str(k): f"REPLACE_WITH_{str(k).upper()}" for k in keys}

    doc: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "labels": labels,
            "annotations": annotations,
        },
        "type": meta.get("secret_type", "Opaque"),
        "stringData": string_data,
    }
    return _yaml_str(doc)


def _build_hpa(node: Node) -> str:
    meta = _meta(node)
    name = node.get("label", "app")
    min_replicas = int(meta.get("min_replicas", 2))
    max_replicas = int(meta.get("max_replicas", 10))
    cpu_utilization = int(meta.get("cpu_utilization", 70))

    labels = {**_base_labels(name), **_cui_labels(meta)}

    doc: dict[str, Any] = {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": name,
            "labels": labels,
        },
        "spec": {
            "scaleTargetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": name,
            },
            "minReplicas": min_replicas,
            "maxReplicas": max_replicas,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": cpu_utilization,
                        },
                    },
                }
            ],
        },
    }
    return _yaml_str(doc)


# ── Dispatch ──────────────────────────────────────────────────────────────────

_MANIFEST_BUILDERS: dict[str, Any] = {
    "k8s-deployment": _build_deployment,
    "k8s-service": _build_service,
    "k8s-configmap": _build_configmap,
    "k8s-secret": _build_secret,
    "k8s-hpa": _build_hpa,
}

# Template filename stems per node type (Helm conventional naming)
_TEMPLATE_STEMS: dict[str, str] = {
    "k8s-deployment": "deployment",
    "k8s-service": "service",
    "k8s-configmap": "configmap",
    "k8s-secret": "secret",
    "k8s-hpa": "hpa",
}


# ── Values derivation ─────────────────────────────────────────────────────────

def _derive_values(nodes: list[Node]) -> dict[str, Any]:
    """Derive a values.yaml dict from IDC node metadata attributes.

    Each node contributes a keyed block under its label so templates can
    reference ``{{ .Values.<label>.<attr> }}`` without duplication.
    """
    values: dict[str, Any] = {}
    for node in nodes:
        meta = _meta(node)
        label = node.get("label", node.get("id", "unknown"))
        # Collect scalar attrs; skip internal/private keys
        block: dict[str, Any] = {}
        for k, v in meta.items():
            if k in {"classification", "data", "keys"}:
                continue
            if isinstance(v, (str, int, float, bool)):
                block[k] = v
        if block:
            values[label] = block
    return values


# ── Public API ────────────────────────────────────────────────────────────────

def emit_manifest(node: Node) -> str:
    """Return a Kubernetes manifest YAML string for a single IDC graph node.

    STIG-hardened defaults are applied automatically to Deployment containers.
    CUI classification labels/annotations are injected when node metadata carries
    a recognised classification value.

    Args:
        node: IDC graph node with keys ``id``, ``type``, ``label``, ``metadata``.

    Returns:
        YAML string for one Kubernetes manifest document.

    Raises:
        UnsupportedResourceError: Node type is not one of the 4 supported K8s types.
    """
    node_type = node.get("type", "")
    builder = _MANIFEST_BUILDERS.get(node_type)
    if builder is None:
        raise UnsupportedResourceError(
            f"Node type {node_type!r} not supported. "
            f"Supported: {sorted(_SUPPORTED_TYPES)}"
        )
    return builder(node)


def emit_chart(
    nodes: list[Node],
    chart_name: str = "icdev-chart",
    chart_version: str = "0.1.0",
    app_version: str = "1.0.0",
    classification: str | None = None,
) -> dict[str, str]:
    """Emit a complete Helm chart as a dict mapping filename -> YAML content.

    The returned dict contains:
      - ``Chart.yaml``           — chart metadata
      - ``templates/<stem>.yaml`` — one manifest per input node

    CUI classification is auto-detected from node metadata when ``classification``
    is ``None``. When detected, it is injected into ``Chart.yaml`` annotations
    and into each manifest's labels/annotations.

    Args:
        nodes: List of IDC graph node dicts.
        chart_name: Helm chart name (default ``"icdev-chart"``).
        chart_version: SemVer chart version (default ``"0.1.0"``).
        app_version: Application version string (default ``"1.0.0"``).
        classification: CUI/classification marking. Auto-detected when ``None``.

    Returns:
        Dict of ``{filename: yaml_string}`` for all chart files.

    Raises:
        UnsupportedResourceError: Any node has an unsupported type.
    """
    # Auto-detect classification from node metadata
    if classification is None:
        for n in nodes:
            cls = str(_meta(n).get("classification", "")).strip()
            if cls in _CUI_VALUES:
                classification = cls
                break

    # Build Chart.yaml
    chart_doc: dict[str, Any] = {
        "apiVersion": "v2",
        "name": chart_name,
        "version": chart_version,
        "appVersion": app_version,
        "description": f"ICDEV-generated Helm chart — {chart_name}",
        "type": "application",
    }
    if classification:
        chart_doc["annotations"] = {
            "icdev.io/classification": classification,
            "icdev.io/data-handling": "CUI//SP-CTI",
            "icdev.io/managed-by": _MANAGED_BY,
        }
    else:
        chart_doc["annotations"] = {"icdev.io/managed-by": _MANAGED_BY}

    result: dict[str, str] = {
        "Chart.yaml": _yaml_str(chart_doc),
        "values.yaml": _yaml_str(_derive_values(nodes)),
    }

    # Build one template file per node
    seen_stems: dict[str, int] = {}
    for node in nodes:
        node_type = node.get("type", "")
        stem = _TEMPLATE_STEMS.get(node_type, "resource")
        # Deduplicate stems when multiple nodes share the same type
        count = seen_stems.get(stem, 0)
        seen_stems[stem] = count + 1
        filename = f"templates/{stem}.yaml" if count == 0 else f"templates/{stem}-{count}.yaml"
        result[filename] = emit_manifest(node)

    return result
