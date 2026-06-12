# CUI // SP-CTI
"""QDC topology builder — test environment simulation (runners, coverage, DORA metrics).

Artifact parsing order:
  1. pytest.ini / setup.cfg / pyproject.toml  — test markers, coverage thresholds
  2. sonar-project.properties                  — project key, coverage gate
  3. .coveragerc / coverage.ini                — coverage include/exclude paths
  4. Default — canonical 7-node test topology
"""
from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Optional

from tools.studio.sim.base_topology import (
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec, TopologySpec,
)


def _parse_pytest_config(artifacts_dir: Path) -> Optional[dict]:
    """Extract test configuration from pytest.ini, setup.cfg, or pyproject.toml."""
    # pytest.ini
    ini = artifacts_dir / "pytest.ini"
    if ini.exists():
        cfg = configparser.ConfigParser()
        try:
            cfg.read_string(ini.read_text(encoding="utf-8"))
            markers = cfg.get("pytest", "markers", fallback="").strip().splitlines()
            return {"markers": [m.split(":")[0].strip() for m in markers if m.strip()]}
        except Exception:
            pass

    # setup.cfg
    setup = artifacts_dir / "setup.cfg"
    if setup.exists():
        cfg = configparser.ConfigParser()
        try:
            cfg.read_string(setup.read_text(encoding="utf-8"))
            if cfg.has_section("tool:pytest") or cfg.has_section("pytest"):
                sec = "tool:pytest" if cfg.has_section("tool:pytest") else "pytest"
                markers = cfg.get(sec, "markers", fallback="").strip().splitlines()
                return {"markers": [m.split(":")[0].strip() for m in markers if m.strip()]}
        except Exception:
            pass

    # pyproject.toml (simple regex, avoids tomllib dependency)
    ppt = artifacts_dir / "pyproject.toml"
    if ppt.exists():
        try:
            text = ppt.read_text(encoding="utf-8")
            markers = re.findall(r'"([a-z_]+)\s*:', text)
            if markers:
                return {"markers": markers[:10]}
        except Exception:
            pass

    return None


def _parse_sonar_props(path: Path) -> Optional[dict]:
    """Parse sonar-project.properties for project key and coverage gate."""
    try:
        props: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()
        result: dict = {}
        if "sonar.projectKey" in props:
            result["project_key"] = props["sonar.projectKey"]
        if "sonar.coverage.exclusions" in props:
            result["coverage_exclusions"] = props["sonar.coverage.exclusions"]
        coverage_pct = props.get("sonar.coverage.minimum", "80")
        result["min_coverage"] = coverage_pct
        return result if result else None
    except Exception:
        return None


class QDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "qdc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        pytest_cfg = _parse_pytest_config(artifacts_dir)
        sonar_cfg: Optional[dict] = None
        sonar_path = artifacts_dir / "sonar-project.properties"
        if sonar_path.exists():
            sonar_cfg = _parse_sonar_props(sonar_path)

        # Derive runner names from markers if present
        markers = (pytest_cfg or {}).get("markers", [])
        runner_nodes: list[NodeSpec] = []
        if markers:
            for m in markers[:3]:          # cap at 3 extra runners
                runner_nodes.append(NodeSpec(
                    name=f"runner-{m}",
                    node_type="vpcs",
                    meta={"role": "test-runner", "marker": m},
                ))
        if not runner_nodes:
            runner_nodes = [
                NodeSpec(name="test-runner-1", node_type="vpcs", meta={"role": "unit-test"}),
                NodeSpec(name="test-runner-2", node_type="vpcs", meta={"role": "integration-test"}),
            ]

        sonar_meta = sonar_cfg or {"project_key": "icdev", "min_coverage": "80"}
        nodes = runner_nodes + [
            NodeSpec(name="e2e-runner",   node_type="vpcs",            meta={"role": "e2e-test", "tool": "selenium"}),
            NodeSpec(name="coverage-srv", node_type="vpcs",            meta={"role": "coverage", "tool": "pytest-cov"}),
            NodeSpec(name="quality-gate", node_type="vpcs",            meta={"role": "gate", "tool": "sonarqube", **sonar_meta}),
            NodeSpec(name="dora-metrics", node_type="vpcs",            meta={"role": "dora"}),
            NodeSpec(name="test-fabric",  node_type="ethernet_switch", meta={"role": "test-fabric"}),
        ]

        links = [n for n in [
            LinkSpec(n.name, "test-fabric")
            for n in nodes if n.name != "test-fabric"
        ]]

        source = "artifact" if (pytest_cfg or sonar_cfg) else "default"

        return TopologySpec(
            project_name="icdev-qdc-sim",
            canvas="qdc",
            nodes=nodes,
            links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count",   type="link_count", expected_value=len(links)),
                ProbeSpec(name="sonar_up",     type="http_get",
                          target_node="quality-gate", port=9001, path="/api/system/status"),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-sonarqube-qdc",
                    image="sonarqube:community",
                    ports={9001: 9000},
                    healthcheck_url="http://localhost:9001/api/system/status",
                ),
            ],
            metadata={
                "test_types": ["unit", "integration", "e2e", "coverage"],
                "source": source,
                "markers": markers,
                "sonar": sonar_meta,
            },
        )
