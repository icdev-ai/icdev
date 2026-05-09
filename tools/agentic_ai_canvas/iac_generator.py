# CUI // SP-CTI
"""Agentic AI Design Canvas — IaC generator.

Converts a design graph into a deployable Terraform + Helm bundle.
Output: dict with 'files' list, 'manifest' summary, 'summary' string.
Mirrors the PDC deploy_generator.py signature/pattern.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone

from tools.agentic_ai_canvas.constants import AADC_IAC_NODE_MAP

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^a-z0-9\-]")


def _slug(text: str) -> str:
    return _SAFE.sub("-", (text or "node").lower()).strip("-")[:40]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_deploy_bundle(
    graph: dict,
    name: str,
    target_csp: str = "auto",
    options: dict | None = None,
) -> dict:
    """Generate a Terraform + Helm deployment bundle from a design graph.

    Args:
        graph: Design graph dict with 'nodes' and 'edges'.
        name: Design name (used as Helm chart name and TF module prefix).
        target_csp: 'auto' | 'aws' | 'azure' | 'gcp' | 'oci' | 'on_prem'
        options: Optional overrides — region, k8s_namespace, k8s_version,
                 helm_chart_version, container_registry.

    Returns:
        {files: [{path, content}], manifest: {agents, models, tools, memory},
         summary: str, zip_bytes: bytes}
    """
    opts = options or {}
    namespace = opts.get("k8s_namespace", "agentic-ai")
    region = opts.get("region", "us-east-1")
    registry = opts.get("container_registry", "ghcr.io/icdev")
    chart_version = opts.get("helm_chart_version", "0.1.0")
    chart_name = _slug(name)

    nodes = graph.get("nodes", [])
    if isinstance(nodes, str):
        try:
            nodes = json.loads(nodes)
        except Exception:
            nodes = []

    # Classify nodes
    agents, models, tools, memory, other = [], [], [], [], []
    for node in nodes:
        ntype = node.get("type", "")
        label = node.get("label") or node.get("id", "node")
        node_slug = _slug(label)
        mapping = AADC_IAC_NODE_MAP.get(ntype)
        if not mapping:
            other.append(node)
            continue
        tf_res, helm_tpl = mapping
        enriched = {**node, "_slug": node_slug, "_tf_resource": tf_res, "_helm_tpl": helm_tpl}
        if ntype in ("llm", "llm-local", "embedding-model", "fine-tuned-adapter",
                     "classifier", "reranker", "multimodal"):
            models.append(enriched)
        elif any(ntype.startswith(p) for p in ("mcp-", "tool-", "function-", "code-", "web-")):
            tools.append(enriched)
        elif ntype in ("vector-db", "doc-store", "knowledge-graph", "short-term-mem",
                       "long-term-mem", "episodic-buffer", "semantic-cache",
                       "conversation-history", "working-memory", "embedding-cache"):
            memory.append(enriched)
        else:
            agents.append(enriched)

    files: list[dict] = []

    # ── Terraform ──────────────────────────────────────────────────────────
    files.append({"path": f"{chart_name}-iac/terraform/providers.tf",
                  "content": _tf_providers(target_csp, region)})
    files.append({"path": f"{chart_name}-iac/terraform/variables.tf",
                  "content": _tf_variables(namespace, region, registry)})
    files.append({"path": f"{chart_name}-iac/terraform/main.tf",
                  "content": _tf_main(agents, models, tools, memory, namespace, chart_name)})
    files.append({"path": f"{chart_name}-iac/terraform/outputs.tf",
                  "content": _tf_outputs(agents, tools)})

    # ── Helm chart skeleton ────────────────────────────────────────────────
    files.append({"path": f"{chart_name}-iac/helm/Chart.yaml",
                  "content": _helm_chart_yaml(chart_name, chart_version, name)})
    files.append({"path": f"{chart_name}-iac/helm/values.yaml",
                  "content": _helm_values(namespace, registry, agents, models, tools, memory)})

    # Per-agent deployments
    for node in agents:
        slug = node["_slug"]
        files.append({
            "path": f"{chart_name}-iac/helm/templates/deployment-{slug}.yaml",
            "content": _helm_deployment(node, namespace, registry),
        })

    # Per-tool/MCP services
    for node in tools:
        slug = node["_slug"]
        files.append({
            "path": f"{chart_name}-iac/helm/templates/service-{slug}.yaml",
            "content": _helm_service(node, namespace),
        })

    # Per-model configmaps (managed models) or Ollama values
    for node in models:
        slug = node["_slug"]
        if node.get("_helm_tpl") == "ollama-values":
            files.append({
                "path": f"{chart_name}-iac/helm/templates/ollama-{slug}.yaml",
                "content": _helm_ollama(node, namespace),
            })
        else:
            files.append({
                "path": f"{chart_name}-iac/helm/templates/configmap-{slug}.yaml",
                "content": _helm_configmap_model(node, namespace),
            })

    # Memory/vector-db values
    for node in memory:
        slug = node["_slug"]
        if node.get("_helm_tpl") == "vector-db-values":
            files.append({
                "path": f"{chart_name}-iac/helm/templates/vectordb-{slug}.yaml",
                "content": _helm_vectordb(node, namespace),
            })
        else:
            files.append({
                "path": f"{chart_name}-iac/helm/templates/configmap-{slug}.yaml",
                "content": _helm_configmap_generic(node, namespace),
            })

    # README
    files.append({"path": f"{chart_name}-iac/README.md",
                  "content": _readme(name, chart_name, namespace, agents, models, tools, memory)})

    # Assemble ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f["path"], f["content"])
    zip_bytes = buf.getvalue()

    summary = (
        f"Generated IaC for '{name}': "
        f"{len(agents)} agents, {len(models)} models, {len(tools)} tools, "
        f"{len(memory)} memory stores. "
        f"{len(files)} files in ZIP."
    )

    return {
        "files": files,
        "manifest": {
            "agents": [n.get("label", n["id"]) for n in agents],
            "models": [n.get("label", n["id"]) for n in models],
            "tools": [n.get("label", n["id"]) for n in tools],
            "memory": [n.get("label", n["id"]) for n in memory],
        },
        "summary": summary,
        "zip_bytes": zip_bytes,
    }


# ---------------------------------------------------------------------------
# Terraform emitters
# ---------------------------------------------------------------------------

def _tf_providers(csp: str, region: str) -> str:
    k8s_block = """provider "kubernetes" {
  config_path = "~/.kube/config"
}

provider "helm" {
  kubernetes {
    config_path = "~/.kube/config"
  }
}
"""
    aws_block = """provider "aws" {
  region = var.aws_region
}
""" if csp in ("aws", "auto") else ""

    return f"""# Auto-generated by ICDEV™ AADC IaC Generator
# Classification: CUI // SP-CTI

terraform {{
  required_version = ">= 1.5"
  required_providers {{
    kubernetes = {{
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }}
    helm = {{
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }}{'''
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }''' if csp in ("aws", "auto") else ''}
  }}
}}

{k8s_block}{aws_block}"""


def _tf_variables(namespace: str, region: str, registry: str) -> str:
    return f"""variable "k8s_namespace" {{
  description = "Kubernetes namespace for AADC deployment"
  type        = string
  default     = "{namespace}"
}}

variable "container_registry" {{
  description = "Container image registry"
  type        = string
  default     = "{registry}"
}}

variable "aws_region" {{
  description = "AWS region (used when deploying to AWS)"
  type        = string
  default     = "{region}"
}}

variable "image_tag" {{
  description = "Container image tag"
  type        = string
  default     = "latest"
}}
"""


def _tf_main(agents, models, tools, memory, namespace, chart_name) -> str:
    lines = [
        "# Auto-generated by ICDEV™ AADC IaC Generator\n",
        f'resource "kubernetes_namespace" "{_slug(chart_name)}_ns" {{\n',
        '  metadata {\n    name = var.k8s_namespace\n  }\n}\n\n',
    ]
    for node in agents:
        slug = node["_slug"]
        lines.append(
            f'resource "kubernetes_deployment" "{slug}" {{\n'
            f'  metadata {{\n    name      = "{slug}"\n    namespace = var.k8s_namespace\n  }}\n'
            f'  spec {{\n    replicas = 1\n'
            f'    selector {{ match_labels = {{ app = "{slug}" }} }}\n'
            f'    template {{\n      metadata {{ labels = {{ app = "{slug}" }} }}\n'
            f'      spec {{\n        container {{\n          name  = "{slug}"\n'
            f'          image = "${{var.container_registry}}/{slug}:${{var.image_tag}}"\n'
            f'        }}\n      }}\n    }}\n  }}\n}}\n\n'
        )
    for node in tools:
        slug = node["_slug"]
        lines.append(
            f'resource "kubernetes_service" "{slug}" {{\n'
            f'  metadata {{\n    name      = "{slug}"\n    namespace = var.k8s_namespace\n  }}\n'
            f'  spec {{\n    selector = {{ app = "{slug}" }}\n'
            f'    port {{\n      port        = 8080\n      target_port = 8080\n    }}\n  }}\n}}\n\n'
        )
    return "".join(lines)


def _tf_outputs(agents, tools) -> str:
    lines = ["# Deployment outputs\n\n"]
    for node in agents:
        slug = node["_slug"]
        lines.append(
            f'output "{slug}_deployment" {{\n'
            f'  value = kubernetes_deployment.{slug}.metadata[0].name\n}}\n\n'
        )
    return "".join(lines)


# ---------------------------------------------------------------------------
# Helm emitters
# ---------------------------------------------------------------------------

def _helm_chart_yaml(chart_name: str, version: str, display_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""apiVersion: v2
name: {chart_name}
description: "{display_name} — generated by ICDEV™ AADC IaC Generator"
type: application
version: {version}
appVersion: "1.0.0"
annotations:
  classification: "CUI // SP-CTI"
  generated-at: "{ts}"
"""


def _helm_values(namespace, registry, agents, models, tools, memory) -> str:
    lines = [
        f"namespace: {namespace}\n",
        f"imageRegistry: {registry}\n",
        "imageTag: latest\n\nagents:\n",
    ]
    for n in agents:
        lines.append(f"  {n['_slug']}:\n    replicas: 1\n    resources:\n      requests:\n        memory: \"256Mi\"\n        cpu: \"100m\"\n")
    lines.append("models:\n")
    for n in models:
        props = n.get("properties") or {}
        model_id = props.get("model", n.get("type", "unknown"))
        lines.append(f"  {n['_slug']}:\n    model: {model_id}\n")
    return "".join(lines)


def _helm_deployment(node: dict, namespace: str, registry: str) -> str:
    slug = node["_slug"]
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {slug}
  namespace: {namespace}
  labels:
    app: {slug}
    icdev.io/canvas: aadc
spec:
  replicas: {{ {{ .Values.agents.{slug}.replicas | default 1 }} }}
  selector:
    matchLabels:
      app: {slug}
  template:
    metadata:
      labels:
        app: {slug}
    spec:
      containers:
        - name: {slug}
          image: "{{{{ .Values.imageRegistry }}}}/{slug}:{{{{ .Values.imageTag }}}}"
          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "500m"
          env:
            - name: AGENT_ID
              value: "{node.get('id', slug)}"
"""


def _helm_service(node: dict, namespace: str) -> str:
    slug = node["_slug"]
    return f"""apiVersion: v1
kind: Service
metadata:
  name: {slug}
  namespace: {namespace}
  labels:
    app: {slug}
spec:
  selector:
    app: {slug}
  ports:
    - name: http
      port: 8080
      targetPort: 8080
  type: ClusterIP
"""


def _helm_configmap_model(node: dict, namespace: str) -> str:
    slug = node["_slug"]
    props = node.get("properties") or {}
    model_id = props.get("model", node.get("type", "unknown"))
    return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {slug}-config
  namespace: {namespace}
data:
  model_id: "{model_id}"
  provider: "{node.get('type', 'unknown')}"
  node_id: "{node.get('id', slug)}"
"""


def _helm_ollama(node: dict, namespace: str) -> str:
    slug = node["_slug"]
    props = node.get("properties") or {}
    model_id = props.get("model", "qwen3")
    return f"""# Ollama Helm release for local model node: {slug}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {slug}-ollama-config
  namespace: {namespace}
data:
  model: "{model_id}"
  pull_on_start: "true"
  host: "0.0.0.0"
  port: "11434"
"""


def _helm_vectordb(node: dict, namespace: str) -> str:
    slug = node["_slug"]
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {slug}
  namespace: {namespace}
  labels:
    app: {slug}
    icdev.io/role: vector-db
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {slug}
  template:
    metadata:
      labels:
        app: {slug}
    spec:
      containers:
        - name: {slug}
          image: chromadb/chroma:latest
          ports:
            - containerPort: 8000
          volumeMounts:
            - name: data
              mountPath: /chroma/chroma
      volumes:
        - name: data
          emptyDir: {{}}
---
apiVersion: v1
kind: Service
metadata:
  name: {slug}
  namespace: {namespace}
spec:
  selector:
    app: {slug}
  ports:
    - port: 8000
      targetPort: 8000
"""


def _helm_configmap_generic(node: dict, namespace: str) -> str:
    slug = node["_slug"]
    return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {slug}-config
  namespace: {namespace}
data:
  node_type: "{node.get('type', 'unknown')}"
  node_id: "{node.get('id', slug)}"
  label: "{node.get('label', slug)}"
"""


def _readme(name, chart_name, namespace, agents, models, tools, memory) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# {name} — IaC Bundle

> Auto-generated by ICDEV™ AADC IaC Generator on {ts}
> Classification: CUI // SP-CTI

## Contents

| Directory | Description |
|-----------|-------------|
| `terraform/` | Terraform HCL — Kubernetes namespace + agent deployments + tool services |
| `helm/` | Helm chart — values, per-agent deployments, model configmaps, vector-db manifests |

## Design Summary

| Category | Count | Names |
|----------|-------|-------|
| Agents | {len(agents)} | {', '.join(n.get('label', n['_slug']) for n in agents) or '—'} |
| Models | {len(models)} | {', '.join(n.get('label', n['_slug']) for n in models) or '—'} |
| Tools/MCP | {len(tools)} | {', '.join(n.get('label', n['_slug']) for n in tools) or '—'} |
| Memory | {len(memory)} | {', '.join(n.get('label', n['_slug']) for n in memory) or '—'} |

## Deploy

```bash
# 1. Apply Terraform
cd terraform/
terraform init
terraform apply -var="k8s_namespace={namespace}"

# 2. Install Helm chart
cd ../helm/
helm install {chart_name} . --namespace {namespace} --create-namespace
```

## Notes

- Review all generated files before applying to production.
- Container images reference `ghcr.io/icdev/<slug>:latest` — replace with your registry.
- Sensitive values (API keys, model credentials) must be injected via Kubernetes Secrets.
"""
