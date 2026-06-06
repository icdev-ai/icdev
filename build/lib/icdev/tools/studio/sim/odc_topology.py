# CUI // SP-CTI
"""ODC topology builder — observability stack simulation (collectors, agents, SIEM).

Artifact parsing order:
  1. prometheus.yml / prometheus.yaml  — extract scrape job targets
  2. otel-config.yaml / otel-collector.yaml  — detect receivers/exporters
  3. datasources.yaml (Grafana)  — detect configured data sources
  4. Default — hardcoded LGTM stack (Loki/Grafana/Tempo/Mimir)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from tools.studio.sim.base_topology import (
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec, TopologySpec,
)


def _parse_prometheus_config(path: Path) -> Optional[dict]:
    """Extract scrape job names from prometheus.yml."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        jobs = [sc.get("job_name", "") for sc in data.get("scrape_configs", [])]
        jobs = [j for j in jobs if j]
        return {"jobs": jobs} if jobs else None
    except Exception:
        return None


def _parse_otel_config(path: Path) -> Optional[dict]:
    """Extract receiver and exporter names from otel-config.yaml."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        receivers = list((data.get("receivers") or {}).keys())
        exporters = list((data.get("exporters") or {}).keys())
        return {"receivers": receivers, "exporters": exporters}
    except Exception:
        return None


def _parse_grafana_datasources(path: Path) -> Optional[list[str]]:
    """Extract datasource type names from Grafana datasources.yaml."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        ds = data.get("datasources") or []
        return [d.get("type", "") for d in ds if isinstance(d, dict) and d.get("type")]
    except Exception:
        return None


class ODCTopologyBuilder(BaseTopologyBuilder):
    canvas = "odc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        # Attempt to read observability stack config from artifacts
        prom_data: Optional[dict] = None
        otel_data: Optional[dict] = None
        grafana_sources: Optional[list] = None

        for fname in ("prometheus.yml", "prometheus.yaml"):
            p = artifacts_dir / fname
            if p.exists():
                prom_data = _parse_prometheus_config(p)
                break

        for fname in ("otel-config.yaml", "otel-collector.yaml", "otelcol.yaml"):
            p = artifacts_dir / fname
            if p.exists():
                otel_data = _parse_otel_config(p)
                break

        ds_path = artifacts_dir / "datasources.yaml"
        if ds_path.exists():
            grafana_sources = _parse_grafana_datasources(ds_path)

        # Build nodes — always include the core LGTM stack; extend from artifacts
        nodes = [
            NodeSpec(name="app-nodes",      node_type="ethernet_switch",
                     meta={"role": "instrumented-apps",
                           "scrape_jobs": (prom_data or {}).get("jobs", [])}),
            NodeSpec(name="otel-collector", node_type="vpcs",
                     meta={"role": "collector", "tool": "opentelemetry",
                           "receivers": (otel_data or {}).get("receivers", []),
                           "exporters": (otel_data or {}).get("exporters", [])}),
            NodeSpec(name="prometheus",     node_type="vpcs",
                     meta={"role": "metrics", "tool": "prometheus"}),
            NodeSpec(name="loki",           node_type="vpcs",
                     meta={"role": "logs", "tool": "loki"}),
            NodeSpec(name="tempo",          node_type="vpcs",
                     meta={"role": "traces", "tool": "tempo"}),
            NodeSpec(name="grafana",        node_type="vpcs",
                     meta={"role": "dashboards", "tool": "grafana",
                           "datasources": grafana_sources or ["prometheus", "loki", "tempo"]}),
            NodeSpec(name="alertmanager",   node_type="vpcs",
                     meta={"role": "alerting", "tool": "alertmanager"}),
            NodeSpec(name="obs-fabric",     node_type="ethernet_switch",
                     meta={"role": "observability-fabric"}),
        ]

        links = [
            LinkSpec("app-nodes",      "otel-collector"),
            LinkSpec("otel-collector", "obs-fabric"),
            LinkSpec("prometheus",     "obs-fabric"),
            LinkSpec("loki",           "obs-fabric"),
            LinkSpec("tempo",          "obs-fabric"),
            LinkSpec("grafana",        "obs-fabric"),
            LinkSpec("alertmanager",   "prometheus"),
        ]

        source = "artifact" if (prom_data or otel_data or grafana_sources) else "default"

        return TopologySpec(
            project_name="icdev-odc-sim",
            canvas="odc",
            nodes=nodes,
            links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count", type="link_count", expected_value=len(links)),
                ProbeSpec(name="grafana_up", type="http_get",
                          target_node="grafana", port=3000, path="/api/health"),
                ProbeSpec(name="prometheus_up", type="http_get",
                          target_node="prometheus", port=9090, path="/-/healthy"),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-prometheus-odc",
                    image="prom/prometheus:latest",
                    ports={9090: 9090},
                    healthcheck_url="http://localhost:9090/-/healthy",
                ),
                DockerServiceSpec(
                    name="icdev-grafana-odc",
                    image="grafana/grafana:latest",
                    ports={3000: 3000},
                    env={"GF_AUTH_ANONYMOUS_ENABLED": "true",
                         "GF_AUTH_ANONYMOUS_ORG_ROLE": "Admin"},
                    healthcheck_url="http://localhost:3000/api/health",
                ),
                DockerServiceSpec(
                    name="icdev-loki-odc",
                    image="grafana/loki:latest",
                    ports={3100: 3100},
                    healthcheck_url="http://localhost:3100/ready",
                ),
            ],
            metadata={
                "signals": ["metrics", "logs", "traces"],
                "source": source,
                "prom_jobs": (prom_data or {}).get("jobs", []),
                "otel_receivers": (otel_data or {}).get("receivers", []),
                "grafana_datasources": grafana_sources or [],
            },
        )
