# CUI // SP-CTI
"""DDC → LocalStack Provisioner.

Reads a Data Design Canvas graph and provisions matching AWS resources in the
local LocalStack emulation environment:

  ent-collection / ent-table  →  DynamoDB table  (key: label, hash-only)
  ent-queue                   →  SQS queue       (FIFO if label ends ".fifo")
  ent-datalake / ent-file     →  S3 bucket       (name: label slug)

Feature flag: LOCALSTACK_ENABLED (default false — air-gap safe).
When disabled returns a typed "disabled" response; no network calls are made.

Usage::

    from tools.data_canvas.localstack_provisioner import provision_design
    result = provision_design(design_id="abc123", graph={"nodes": [...], "edges": [...]})
    # result: {status, provisioned: [{type, name, action, detail}], errors: [...]}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from tools.databridge.feature_flags import IntegrationFeatureFlags

# ── helpers ───────────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9\-]")

def _slug(label: str, max_len: int = 63) -> str:
    """Convert a human label to a DNS-safe resource name."""
    s = label.lower().strip().replace(" ", "-").replace("_", "-")
    s = _SLUG_RE.sub("", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len] or "unnamed"

# Node types that map to each LocalStack service
_DYNAMODB_TYPES = {"ent-collection", "ent-table"}
_SQS_TYPES      = {"ent-queue"}
_S3_TYPES       = {"ent-datalake", "ent-file"}

# ── DDC graph parser ──────────────────────────────────────────────────────────

def _parse_nodes(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return list of DDC nodes, normalising both graph_json and data_nodes formats."""
    nodes = []
    for n in graph.get("nodes", []):
        node_type = n.get("type") or n.get("node_type") or ""
        label     = n.get("label") or n.get("name") or ""
        cfg       = n.get("config") or n.get("properties") or {}
        nodes.append({
            "id":    n.get("id", ""),
            "type":  node_type,
            "label": label,
            "config": cfg,
        })
    return nodes

# ── DynamoDB provisioning ─────────────────────────────────────────────────────

def _provision_dynamodb(adapter, node: Dict[str, Any]) -> Dict[str, Any]:
    label      = node["label"] or "unnamed-table"
    table_name = _slug(label, 63)
    cfg        = node["config"]
    pk         = cfg.get("partition_key") or cfg.get("pk") or "id"
    sk         = cfg.get("sort_key") or cfg.get("sk") or None
    pk_type    = cfg.get("partition_key_type", "S")
    sk_type    = cfg.get("sort_key_type", "S")

    existing = adapter.list_tables()
    if table_name in existing:
        return {"type": "dynamodb", "name": table_name, "action": "exists", "detail": "Table already present in LocalStack"}

    result = adapter.create_table(
        name=table_name,
        partition_key=pk,
        partition_key_type=pk_type,
        sort_key=sk,
        sort_key_type=sk_type,
    )
    if result.get("status") == "error":
        return {"type": "dynamodb", "name": table_name, "action": "error", "detail": result.get("error", "unknown")}
    return {"type": "dynamodb", "name": table_name, "action": "created",
            "detail": f"pk={pk}({pk_type})" + (f", sk={sk}({sk_type})" if sk else "")}

# ── SQS provisioning ──────────────────────────────────────────────────────────

def _provision_sqs(adapter, node: Dict[str, Any]) -> Dict[str, Any]:
    label      = node["label"] or "unnamed-queue"
    fifo       = label.lower().endswith(".fifo") or node["config"].get("fifo", False)
    queue_name = _slug(label, 80)
    if fifo and not queue_name.endswith(".fifo"):
        queue_name += ".fifo"

    existing = adapter.list_queues()
    for url in existing:
        if url.rstrip("/").endswith("/" + queue_name):
            return {"type": "sqs", "name": queue_name, "action": "exists", "detail": "Queue already present in LocalStack"}

    result = adapter.create_queue(name=queue_name, fifo=fifo)
    if result.get("status") == "error":
        return {"type": "sqs", "name": queue_name, "action": "error", "detail": result.get("error", "unknown")}
    return {"type": "sqs", "name": queue_name, "action": "created",
            "detail": "FIFO queue" if fifo else "Standard queue"}

# ── S3 provisioning ───────────────────────────────────────────────────────────

def _provision_s3(adapter, node: Dict[str, Any]) -> Dict[str, Any]:
    label       = node["label"] or "unnamed-bucket"
    bucket_name = _slug(label, 63)

    existing = [b.get("Name", "") for b in adapter.list_buckets()]
    if bucket_name in existing:
        return {"type": "s3", "name": bucket_name, "action": "exists", "detail": "Bucket already present in LocalStack"}

    result = adapter.create_bucket(bucket_name)
    if result.get("status") == "error":
        return {"type": "s3", "name": bucket_name, "action": "error", "detail": result.get("error", "unknown")}
    return {"type": "s3", "name": bucket_name, "action": "created", "detail": "Bucket created"}

# ── Public API ────────────────────────────────────────────────────────────────

def provision_design(design_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
    """Provision LocalStack resources matching the DDC graph.

    Returns::

        {
            "status":      "ok" | "disabled" | "error",
            "design_id":   str,
            "provisioned": [{"type", "name", "action", "detail"}, ...],
            "errors":      [str, ...],
            "summary":     {"dynamodb": int, "sqs": int, "s3": int, "skipped": int},
        }
    """
    flag = IntegrationFeatureFlags.localstack()
    if not flag.enabled:
        return {"status": "disabled", "reason": flag.reason, "design_id": design_id,
                "provisioned": [], "errors": [], "summary": {}}

    from tools.infra_canvas.adapters.localstack_adapter import LocalStackAdapter  # noqa: PLC0415
    adapter = LocalStackAdapter.from_env()

    # Quick health check — surface connection errors early
    health = adapter.health()
    if health.get("status") == "error":
        return {"status": "error", "design_id": design_id,
                "error": "LocalStack unreachable: " + health.get("error", "unknown"),
                "provisioned": [], "errors": [], "summary": {}}

    nodes    = _parse_nodes(graph)
    results:  List[Dict[str, Any]] = []
    errors:   List[str]            = []
    counts    = {"dynamodb": 0, "sqs": 0, "s3": 0, "skipped": 0}

    for node in nodes:
        ntype = node["type"]
        try:
            if ntype in _DYNAMODB_TYPES:
                r = _provision_dynamodb(adapter, node)
                results.append(r)
                if r["action"] in ("created", "exists"):
                    counts["dynamodb"] += 1
                else:
                    errors.append(f"DynamoDB {node['label']}: {r['detail']}")
            elif ntype in _SQS_TYPES:
                r = _provision_sqs(adapter, node)
                results.append(r)
                if r["action"] in ("created", "exists"):
                    counts["sqs"] += 1
                else:
                    errors.append(f"SQS {node['label']}: {r['detail']}")
            elif ntype in _S3_TYPES:
                r = _provision_s3(adapter, node)
                results.append(r)
                if r["action"] in ("created", "exists"):
                    counts["s3"] += 1
                else:
                    errors.append(f"S3 {node['label']}: {r['detail']}")
            else:
                counts["skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ntype}/{node['label']}: {exc}")

    return {
        "status":      "ok" if not errors else "partial",
        "design_id":   design_id,
        "provisioned": results,
        "errors":      errors,
        "summary":     counts,
    }


def validate_design(design_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
    """Check which DDC-designed resources already exist in LocalStack (read-only).

    Same return shape as provision_design but action is always "exists" | "missing".
    No resources are created.
    """
    flag = IntegrationFeatureFlags.localstack()
    if not flag.enabled:
        return {"status": "disabled", "reason": flag.reason, "design_id": design_id,
                "provisioned": [], "errors": [], "summary": {}}

    from tools.infra_canvas.adapters.localstack_adapter import LocalStackAdapter  # noqa: PLC0415
    adapter = LocalStackAdapter.from_env()

    nodes   = _parse_nodes(graph)
    results: List[Dict[str, Any]] = []
    counts  = {"exists": 0, "missing": 0, "skipped": 0}

    existing_tables  = adapter.list_tables()  if adapter.is_enabled() else []
    existing_queues  = adapter.list_queues()  if adapter.is_enabled() else []
    existing_buckets = [b.get("Name", "") for b in adapter.list_buckets()] if adapter.is_enabled() else []

    for node in nodes:
        ntype = node["type"]
        label = node["label"] or "unnamed"

        if ntype in _DYNAMODB_TYPES:
            name   = _slug(label, 63)
            action = "exists" if name in existing_tables else "missing"
            counts["exists" if action == "exists" else "missing"] += 1
            results.append({"type": "dynamodb", "name": name, "action": action, "detail": ""})
        elif ntype in _SQS_TYPES:
            fifo   = label.lower().endswith(".fifo") or node["config"].get("fifo", False)
            name   = _slug(label, 80) + (".fifo" if fifo and not _slug(label).endswith(".fifo") else "")
            exists = any(u.rstrip("/").endswith("/" + name) for u in existing_queues)
            action = "exists" if exists else "missing"
            counts["exists" if exists else "missing"] += 1
            results.append({"type": "sqs", "name": name, "action": action, "detail": ""})
        elif ntype in _S3_TYPES:
            name   = _slug(label, 63)
            action = "exists" if name in existing_buckets else "missing"
            counts["exists" if action == "exists" else "missing"] += 1
            results.append({"type": "s3", "name": name, "action": action, "detail": ""})
        else:
            counts["skipped"] += 1

    return {
        "status":      "ok",
        "design_id":   design_id,
        "provisioned": results,
        "errors":      [],
        "summary":     counts,
    }
