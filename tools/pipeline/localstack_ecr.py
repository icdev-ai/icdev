# CUI // SP-CTI
"""PDC → LocalStack ECR Provisioner.

Reads a Pipeline Design Canvas graph and provisions ECR repositories in
LocalStack for every container build / registry node found:

  build-docker                →  ECR repo  (name: pipeline-slug/service-label)
  registry-*  (not IronBank)  →  ECR repo  (name: pipeline-slug/registry-label)

Also emits a Dockerfile-per-service map when IDC dockerfile_generator is
available, giving the pipeline a complete local build + push workflow.

Feature flag: LOCALSTACK_ENABLED (default false — air-gap safe).
Returns {"status": "disabled"} when flag is off; no network calls are made.

Usage::

    from tools.pipeline.localstack_ecr import provision_pipeline_ecr
    result = provision_pipeline_ecr(pipe_id="abc", pipe_name="my-pipeline", graph={...})
    # result: {status, repositories, errors, push_commands, summary}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from tools.databridge.feature_flags import IntegrationFeatureFlags

# ── constants ─────────────────────────────────────────────────────────────────

# Node types that require a container image registry
_DOCKER_BUILD_TYPES  = {"build-docker"}
_REGISTRY_TYPES      = {
    "registry-generic", "registry-harbor", "registry-nexus",
    "registry-jfrog", "registry-zot",
}
# IronBank is a pull-only trusted registry — no provisioning needed
_SKIP_TYPES = {"registry-ironbank"}

_SLUG_RE = re.compile(r"[^a-z0-9\-]")

def _slug(s: str, max_len: int = 100) -> str:
    out = s.lower().strip().replace(" ", "-").replace("_", "-")
    out = _SLUG_RE.sub("", out)
    out = re.sub(r"-{2,}", "-", out).strip("-")
    return out[:max_len] or "unnamed"

# ── graph helpers ─────────────────────────────────────────────────────────────

def _parse_nodes(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = []
    for n in graph.get("nodes", []):
        node_type = n.get("type") or n.get("node_type") or ""
        label     = n.get("label") or n.get("name") or ""
        nodes.append({"id": n.get("id", ""), "type": node_type, "label": label,
                       "config": n.get("config") or n.get("properties") or {}})
    return nodes

# ── push command generator ────────────────────────────────────────────────────

def _push_commands(endpoint: str, repo_name: str) -> List[str]:
    """Return shell commands to build and push an image to LocalStack ECR."""
    registry = endpoint.lstrip("http://").lstrip("https://")
    return [
        f"# Build and push {repo_name} to LocalStack ECR",
        f"docker build -t {repo_name}:latest .",
        f"docker tag {repo_name}:latest {registry}/{repo_name}:latest",
        f"docker push {registry}/{repo_name}:latest",
    ]

# ── public API ────────────────────────────────────────────────────────────────

def provision_pipeline_ecr(
    pipe_id: str,
    pipe_name: str,
    graph: Dict[str, Any],
) -> Dict[str, Any]:
    """Provision LocalStack ECR repositories for all container build/registry nodes.

    Returns::

        {
            "status":        "ok" | "partial" | "disabled" | "error",
            "pipe_id":       str,
            "repositories":  [{"name", "action", "detail"}, ...],
            "push_commands": {repo_name: [shell lines]},
            "errors":        [str],
            "summary":       {"created": int, "exists": int, "skipped": int},
        }
    """
    flag = IntegrationFeatureFlags.localstack()
    if not flag.enabled:
        return {"status": "disabled", "reason": flag.reason, "pipe_id": pipe_id,
                "repositories": [], "push_commands": {}, "errors": [], "summary": {}}

    from tools.infra_canvas.adapters.localstack_adapter import LocalStackAdapter  # noqa: PLC0415
    adapter = LocalStackAdapter.from_env()

    health = adapter.health()
    if health.get("status") == "error":
        return {"status": "error", "pipe_id": pipe_id,
                "error": "LocalStack unreachable: " + health.get("error", "unknown"),
                "repositories": [], "push_commands": {}, "errors": [], "summary": {}}

    pipe_slug    = _slug(pipe_name, 50)
    nodes        = _parse_nodes(graph)
    repos:       List[Dict[str, Any]] = []
    push_cmds:   Dict[str, List[str]] = {}
    errors:      List[str]            = []
    counts       = {"created": 0, "exists": 0, "skipped": 0}

    existing_repos = {r.get("repositoryName", "") for r in adapter.list_repositories()}
    endpoint       = adapter.get_endpoint()

    for node in nodes:
        ntype = node["type"]
        label = node["label"] or node["id"] or "unnamed"

        if ntype in _SKIP_TYPES:
            counts["skipped"] += 1
            continue

        if ntype not in (_DOCKER_BUILD_TYPES | _REGISTRY_TYPES):
            continue

        repo_name = f"{pipe_slug}/{_slug(label, 50)}"

        if repo_name in existing_repos:
            repos.append({"name": repo_name, "action": "exists",
                          "detail": "Repository already present in LocalStack ECR"})
            counts["exists"] += 1
        else:
            result = adapter.create_repository(repo_name)
            if result.get("status") == "error":
                err = result.get("error", "unknown")
                repos.append({"name": repo_name, "action": "error", "detail": err})
                errors.append(f"ECR {repo_name}: {err}")
            else:
                repos.append({"name": repo_name, "action": "created",
                              "detail": f"ECR repo created at {endpoint}"})
                counts["created"] += 1
                existing_repos.add(repo_name)

        # Always emit push commands for build-docker nodes
        if ntype in _DOCKER_BUILD_TYPES:
            push_cmds[repo_name] = _push_commands(endpoint, repo_name)

    return {
        "status":        "ok" if not errors else "partial",
        "pipe_id":       pipe_id,
        "repositories":  repos,
        "push_commands": push_cmds,
        "errors":        errors,
        "summary":       counts,
        "endpoint":      endpoint,
    }


def validate_pipeline_ecr(
    pipe_id: str,
    pipe_name: str,
    graph: Dict[str, Any],
) -> Dict[str, Any]:
    """Read-only check: which ECR repos exist vs. are missing in LocalStack."""
    flag = IntegrationFeatureFlags.localstack()
    if not flag.enabled:
        return {"status": "disabled", "reason": flag.reason, "pipe_id": pipe_id,
                "repositories": [], "push_commands": {}, "errors": [], "summary": {}}

    from tools.infra_canvas.adapters.localstack_adapter import LocalStackAdapter  # noqa: PLC0415
    adapter  = LocalStackAdapter.from_env()
    existing = {r.get("repositoryName", "") for r in adapter.list_repositories()}

    pipe_slug = _slug(pipe_name, 50)
    nodes     = _parse_nodes(graph)
    repos: List[Dict[str, Any]] = []
    counts    = {"exists": 0, "missing": 0, "skipped": 0}

    for node in nodes:
        ntype = node["type"]
        label = node["label"] or node["id"] or "unnamed"

        if ntype in _SKIP_TYPES:
            counts["skipped"] += 1
            continue
        if ntype not in (_DOCKER_BUILD_TYPES | _REGISTRY_TYPES):
            continue

        repo_name = f"{pipe_slug}/{_slug(label, 50)}"
        action    = "exists" if repo_name in existing else "missing"
        counts["exists" if action == "exists" else "missing"] += 1
        repos.append({"name": repo_name, "action": action, "detail": ""})

    return {
        "status":       "ok",
        "pipe_id":      pipe_id,
        "repositories": repos,
        "push_commands": {},
        "errors":       [],
        "summary":      counts,
        "endpoint":     adapter.get_endpoint(),
    }
