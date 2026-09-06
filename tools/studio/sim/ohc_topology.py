# CUI // SP-CTI
"""OHC topology builder — ops runbook automation and incident response nodes.

Artifact parsing order:
  1. runbooks.yaml / runbooks/*.yaml  — enumerate runbook names and trigger types
  2. slo.yaml / slo.json              — SLO targets and error budget windows
  3. oncall.yaml                      — on-call rotation names
  4. Default — 7-node incident response topology with LocalStack
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)


def _parse_runbooks(artifacts_dir: Path) -> Optional[list[dict]]:
    """Enumerate runbook definitions from runbooks.yaml or runbooks/*.yaml."""
    runbooks: list[dict] = []

    # Single file
    for fname in ("runbooks.yaml", "runbooks.yml"):
        p = artifacts_dir / fname
        if p.exists():
            try:
                import yaml
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    runbooks = data[:10]
                elif isinstance(data, dict):
                    runbooks = data.get("runbooks") or []
                if runbooks:
                    return runbooks[:10]
            except Exception:
                pass

    # Directory of runbooks
    rb_dir = artifacts_dir / "runbooks"
    if rb_dir.is_dir():
        for p in list(rb_dir.glob("*.yaml"))[:10]:
            try:
                import yaml
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    runbooks.append({
                        "name": data.get("name", p.stem),
                        "trigger": data.get("trigger", "incident"),
                    })
            except Exception:
                pass
        if runbooks:
            return runbooks

    return None


def _parse_slo(path: Path) -> Optional[dict]:
    """Parse slo.yaml or slo.json for SLO targets."""
    try:
        if path.suffix in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        slos = data.get("slos") or data.get("targets") or []
        return {"slos": slos[:5], "window": data.get("window", "30d")}
    except Exception:
        return None


def _parse_oncall(path: Path) -> Optional[list[str]]:
    """Parse oncall.yaml for rotation names."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            rotations = data.get("rotations") or data.get("schedules") or []
            return [r.get("name", r) if isinstance(r, dict) else str(r) for r in rotations[:4]]
    except Exception:
        pass
    return None


class OHCTopologyBuilder(BaseTopologyBuilder):
    canvas = "ohc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        runbooks = _parse_runbooks(artifacts_dir)
        slo_data: Optional[dict] = None
        oncall_rotations: Optional[list] = None

        for fname in ("slo.yaml", "slo.yml", "slo.json"):
            p = artifacts_dir / fname
            if p.exists():
                slo_data = _parse_slo(p)
                if slo_data:
                    break

        oncall_path = artifacts_dir / "oncall.yaml"
        if oncall_path.exists():
            oncall_rotations = _parse_oncall(oncall_path)

        runbook_names = [r.get("name", r) if isinstance(r, dict) else str(r)
                         for r in (runbooks or [])]
        slo_targets = (slo_data or {}).get("slos", [])
        rotations = oncall_rotations or ["primary", "secondary"]

        nodes = [
            NodeSpec(name="incident-mgr",  node_type="vpcs",
                     meta={"role": "incident-management", "tool": "pagerduty/opsgenie"}),
            NodeSpec(name="runbook-exec",  node_type="vpcs",
                     meta={"role": "runbook-executor",    "tool": "ansible",
                           "runbooks": runbook_names[:5]}),
            NodeSpec(name="cmdb",          node_type="vpcs",
                     meta={"role": "cmdb",                "tool": "servicenow"}),
            NodeSpec(name="sla-monitor",   node_type="vpcs",
                     meta={"role": "sla-monitor",         "slos": slo_targets}),
            NodeSpec(name="on-call-node",  node_type="vpcs",
                     meta={"role": "on-call-notification", "rotations": rotations}),
            NodeSpec(name="postmortem-db", node_type="vpcs",
                     meta={"role": "postmortem-storage"}),
            NodeSpec(name="ops-fabric",    node_type="ethernet_switch",
                     meta={"role": "ops-fabric"}),
        ]

        links = [
            LinkSpec("incident-mgr",  "ops-fabric"),
            LinkSpec("runbook-exec",  "ops-fabric"),
            LinkSpec("cmdb",          "ops-fabric"),
            LinkSpec("sla-monitor",   "ops-fabric"),
            LinkSpec("on-call-node",  "ops-fabric"),
            LinkSpec("postmortem-db", "ops-fabric"),
            # Incident mgr drives runbook execution
            LinkSpec("incident-mgr",  "runbook-exec", 1, 1),
        ]

        source = "artifact" if (runbooks or slo_data or oncall_rotations) else "default"

        return TopologySpec(
            project_name="icdev-ohc-sim",
            canvas="ohc",
            nodes=nodes,
            links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count",        type="link_count", expected_value=len(links)),
                ProbeSpec(name="emulator_apply",  type="emulator_apply"),
                ProbeSpec(name="ops_fabric_ready",  type="tcp_connect",
                          target_node="ops-fabric",
                          port=CANVAS_EMULATOR_HOST_PORTS["ohc"]),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-floci-ohc",
                    image=EMULATOR_IMAGE,
                    ports={CANVAS_EMULATOR_HOST_PORTS["ohc"]: EMULATOR_CONTAINER_PORT},
                    env={"DEFAULT_REGION": "us-east-1"},
                    healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["ohc"]),
                ),
            ],
            metadata={
                "runbook_types": ["incident", "escalation", "postmortem", "scheduled"],
                "runbook_count": len(runbook_names),
                "slo_count": len(slo_targets),
                "oncall_rotations": rotations,
                "source": source,
            },
        )
