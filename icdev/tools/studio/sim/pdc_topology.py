# CUI // SP-CTI
"""PDC topology builder — CI/CD pipeline stages as virtual nodes.

Artifact parsing order:
  1. .gitlab-ci.yml  — extract stages and jobs
  2. Jenkinsfile      — extract stage blocks
  3. pipeline.yaml    — generic pipeline definition
  4. Default          — hardcoded 8-node CI/CD topology
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)

# Gitlab CI stage → GNS3 role
_STAGE_ROLE_MAP = {
    "build":    ("ci-runner",    "vpcs",            "build"),
    "test":     ("test-runner",  "vpcs",            "unit-test"),
    "scan":     ("sast-scanner", "vpcs",            "sast"),
    "lint":     ("lint-node",    "vpcs",            "lint"),
    "package":  ("artifact-reg", "vpcs",            "registry"),
    "deploy":   ("deploy-agent", "vpcs",            "deploy"),
    "release":  ("release-node", "vpcs",            "release"),
    "staging":  ("staging-env",  "ethernet_switch", "staging"),
    "production": ("prod-env",   "ethernet_switch", "production"),
    "validate": ("validate-node","vpcs",            "validate"),
    "security": ("sast-scanner", "vpcs",            "sast"),
    "publish":  ("artifact-reg", "vpcs",            "registry"),
    "review":   ("review-node",  "vpcs",            "review"),
}


def _parse_gitlab_ci(path: Path):
    """Parse .gitlab-ci.yml for stages list."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        stages = data.get("stages", [])
        if not stages:
            # Infer from job-level 'stage:' keys
            stages = list({
                v.get("stage", "") for v in data.values()
                if isinstance(v, dict) and "stage" in v
            })
        return [s.lower() for s in stages if s]
    except Exception:
        return None


def _parse_jenkinsfile(path: Path):
    """Extract stage names from Jenkinsfile (simple regex, no groovy parser)."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        stages = re.findall(r"stage\s*\(\s*['\"]([^'\"]+)['\"]", text)
        return [s.lower() for s in stages] if stages else None
    except Exception:
        return None


def _parse_pipeline_yaml(path: Path):
    """Parse generic pipeline.yaml with 'stages' or 'steps' key."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        stages = data.get("stages") or data.get("steps") or data.get("phases") or []
        if isinstance(stages, list) and stages:
            return [str(s).lower() for s in stages]
    except Exception:
        pass
    return None


def _build_from_stages(stages: list[str]):
    """Convert a stages list into NodeSpec + LinkSpec lists."""
    seen: set[str] = set()
    nodes: list[NodeSpec] = [
        NodeSpec(name="scm",          node_type="vpcs",            meta={"role": "source-control"}),
        NodeSpec(name="pipeline-bus", node_type="ethernet_switch", meta={"role": "pipeline-fabric"}),
    ]
    seen.add("scm")
    seen.add("pipeline-bus")

    for stage in stages:
        name, node_type, role = _STAGE_ROLE_MAP.get(
            stage, (f"stage-{stage}", "vpcs", stage))
        if name not in seen:
            nodes.append(NodeSpec(name=name, node_type=node_type,
                                  meta={"role": role, "stage": stage}))
            seen.add(name)

    links: list[LinkSpec] = []
    bus_ports: dict[str, int] = {}
    port_counter = 0
    for node in nodes:
        if node.name == "pipeline-bus":
            continue
        port_counter += 1
        bus_ports[node.name] = port_counter
        links.append(LinkSpec(node.name, "pipeline-bus", 0, port_counter))

    # Staging→Prod direct link (deploy path)
    stage_names = {n.name for n in nodes}
    if "staging-env" in stage_names and "prod-env" in stage_names and "deploy-agent" in stage_names:
        links.append(LinkSpec("deploy-agent", "staging-env", 1, 0))
        links.append(LinkSpec("deploy-agent", "prod-env", 2, 0))

    return nodes, links


def _try_parse_artifact(artifacts_dir: Path) -> Optional[tuple]:
    for fname, parser in [
        (".gitlab-ci.yml", _parse_gitlab_ci),
        ("Jenkinsfile",    _parse_jenkinsfile),
        ("pipeline.yaml",  _parse_pipeline_yaml),
        ("pipeline.yml",   _parse_pipeline_yaml),
    ]:
        p = artifacts_dir / fname
        if p.exists():
            stages = parser(p)
            if stages:
                nodes, links = _build_from_stages(stages)
                return nodes, links, stages
    return None


class PDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "pdc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        parsed = _try_parse_artifact(artifacts_dir)
        if parsed:
            nodes, links, stages = parsed
            source = "pipeline_artifact"
        else:
            nodes = [
                NodeSpec(name="scm",          node_type="vpcs",            meta={"role": "source-control", "tool": "gitlab"}),
                NodeSpec(name="ci-runner",    node_type="vpcs",            meta={"role": "build", "tool": "gitlab-runner"}),
                NodeSpec(name="sast-scanner", node_type="vpcs",            meta={"role": "sast", "tool": "bandit/semgrep"}),
                NodeSpec(name="artifact-reg", node_type="vpcs",            meta={"role": "registry", "tool": "harbor"}),
                NodeSpec(name="staging-env",  node_type="ethernet_switch", meta={"role": "staging"}),
                NodeSpec(name="prod-env",     node_type="ethernet_switch", meta={"role": "production"}),
                NodeSpec(name="deploy-agent", node_type="vpcs",            meta={"role": "deploy", "tool": "ansible"}),
                NodeSpec(name="pipeline-bus", node_type="ethernet_switch", meta={"role": "pipeline-fabric"}),
            ]
            links = [
                LinkSpec("scm",          "pipeline-bus"),
                LinkSpec("ci-runner",    "pipeline-bus"),
                LinkSpec("sast-scanner", "pipeline-bus"),
                LinkSpec("artifact-reg", "pipeline-bus"),
                LinkSpec("deploy-agent", "pipeline-bus"),
                LinkSpec("deploy-agent", "staging-env", 1, 0),
                LinkSpec("deploy-agent", "prod-env",    2, 0),
            ]
            stages = ["source", "build", "scan", "package", "deploy"]
            source = "default"

        return TopologySpec(
            project_name="icdev-pdc-sim",
            canvas="pdc",
            nodes=nodes,
            links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count", type="link_count", expected_value=len(links)),
                ProbeSpec(name="gitlab_up", type="http_get",
                          target_node="scm", port=8929, path="/-/health"),
                ProbeSpec(name="emulator_apply", type="emulator_apply"),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-gitlab-pdc",
                    image="gitlab/gitlab-ce:latest",
                    ports={8929: 80},
                    healthcheck_url="http://localhost:8929/-/health",
                ),
                DockerServiceSpec(
                    name="icdev-floci-pdc",
                    image=EMULATOR_IMAGE,
                    ports={CANVAS_EMULATOR_HOST_PORTS["pdc"]: EMULATOR_CONTAINER_PORT},
                    env={"DEFAULT_REGION": "us-east-1"},
                    healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["pdc"]),
                ),
            ],
            metadata={"stages": stages, "source": source, "stage_count": len(stages)},
        )
