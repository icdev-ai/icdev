# CUI // SP-CTI
"""DDC topology builder — translates data infrastructure Terraform artifacts to GNS3 topology.

Parsing strategy:
  1. Scan terraform/*.tf files for resource blocks
  2. Map AWS resource types → GNS3 node types:
       aws_rds_*/aws_neptune_* → vpcs  (DB server)
       aws_s3_bucket           → cloud (object storage)
       aws_elasticache_*       → vpcs  (cache layer)
       aws_kinesis_*/aws_glue_*/aws_dms_* → ethernet_switch (ETL fabric)
       aws_vpc/aws_subnet      → ethernet_switch (network fabric)
  3. All data nodes link to a central "data-fabric" switch
  4. Docker services: LocalStack for AWS API simulation
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)

# resource_type → (gns3_node_type, role_label)
_TF_NODE_MAP: dict[str, tuple[str, str]] = {
    "aws_rds_cluster":                    ("vpcs", "rds-cluster"),
    "aws_rds_cluster_instance":           ("vpcs", "rds-instance"),
    "aws_db_instance":                    ("vpcs", "rds"),
    "aws_neptune_cluster":                ("vpcs", "neptune"),
    "aws_neptune_cluster_instance":       ("vpcs", "neptune-instance"),
    "aws_s3_bucket":                      ("cloud", "s3"),
    "aws_elasticache_cluster":            ("vpcs", "elasticache"),
    "aws_elasticache_replication_group":  ("vpcs", "elasticache"),
    "aws_kinesis_stream":                 ("ethernet_switch", "kinesis"),
    "aws_kinesis_firehose_delivery_stream": ("ethernet_switch", "firehose"),
    "aws_glue_job":                       ("ethernet_switch", "glue"),
    "aws_glue_crawler":                   ("ethernet_switch", "glue-crawler"),
    "aws_emr_cluster":                    ("ethernet_switch", "emr"),
    "aws_dms_replication_instance":       ("ethernet_switch", "dms"),
    "aws_dms_endpoint":                   ("vpcs", "dms-endpoint"),
    "aws_redshift_cluster":               ("vpcs", "redshift"),
    "aws_dynamodb_table":                 ("vpcs", "dynamodb"),
    "aws_sqs_queue":                      ("ethernet_switch", "sqs"),
    "aws_sns_topic":                      ("ethernet_switch", "sns"),
    "aws_ssm_parameter":                  ("vpcs", "ssm"),
    "aws_secretsmanager_secret":          ("vpcs", "secrets"),
    "aws_vpc":                            ("ethernet_switch", "vpc"),
    "aws_subnet":                         ("ethernet_switch", "subnet"),
}

_RESOURCE_RE = re.compile(r'^resource\s+"([^"]+)"\s+"([^"]+)"', re.MULTILINE)


def _parse_tf_resources(tf_paths: list[Path]) -> list[tuple[str, str]]:
    """Return list of (resource_type, logical_name) from all tf files."""
    resources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in tf_paths:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _RESOURCE_RE.finditer(text):
            key = f"{m.group(1)}.{m.group(2)}"
            if key not in seen:
                seen.add(key)
                resources.append((m.group(1), m.group(2)))
    return resources


class DDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "ddc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        tf_dir = artifacts_dir / "terraform"
        tf_paths = sorted(tf_dir.glob("main_*.tf")) if tf_dir.exists() else []
        resources = _parse_tf_resources(tf_paths)

        # Pre-declare 32 ports so GNS3 doesn't reject links after port 8
        _fabric_ports = [{"port_number": i, "name": f"e{i}", "type": "access", "vlan": 1}
                         for i in range(32)]
        nodes: list[NodeSpec] = [
            NodeSpec(name="data-fabric", node_type="ethernet_switch",
                     properties={"ports_mapping": _fabric_ports},
                     meta={"role": "fabric", "description": "Central data fabric switch"}),
            NodeSpec(name="mgmt", node_type="vpcs",
                     meta={"role": "management", "description": "Management / ops node"}),
        ]
        links: list[LinkSpec] = [
            LinkSpec(a_node="mgmt", b_node="data-fabric"),
        ]

        name_counts: dict[str, int] = {}
        data_nodes: list[str] = []

        for res_type, res_name in resources:
            if res_type not in _TF_NODE_MAP:
                continue
            gns3_type, role = _TF_NODE_MAP[res_type]
            safe = re.sub(r"[^a-z0-9-]", "-", res_name.lower())[:20]
            count = name_counts.get(safe, 0)
            name_counts[safe] = count + 1
            node_name = f"{safe}-{count}" if count else safe
            nodes.append(NodeSpec(
                name=node_name,
                node_type=gns3_type,
                meta={"role": role, "tf_resource": res_type, "tf_name": res_name},
            ))
            data_nodes.append(node_name)
            # port 0 on fabric is reserved for mgmt; data nodes start at port 1
            links.append(LinkSpec(a_node=node_name, b_node="data-fabric",
                                  a_port=0, b_port=len(data_nodes)))

        # Fallback: at least a db and storage node if nothing parsed
        if not data_nodes:
            nodes += [
                NodeSpec(name="db-primary", node_type="vpcs", meta={"role": "rds"}),
                NodeSpec(name="db-replica", node_type="vpcs", meta={"role": "rds-replica"}),
                NodeSpec(name="s3-store", node_type="cloud", meta={"role": "s3"}),
                NodeSpec(name="etl-engine", node_type="ethernet_switch", meta={"role": "glue"}),
            ]
            data_nodes = ["db-primary", "db-replica", "s3-store", "etl-engine"]
            for i, n in enumerate(data_nodes):
                links.append(LinkSpec(a_node=n, b_node="data-fabric",
                                      a_port=0, b_port=i))

        probes = [
            ProbeSpec(name="topology_deployed", type="topology_deployed"),
            ProbeSpec(name="link_count", type="link_count",
                      expected_value=len(links)),
            ProbeSpec(name="emulator_apply", type="emulator_apply"),
        ]

        docker_services = [
            DockerServiceSpec(
                name="icdev-floci-ddc",
                image=EMULATOR_IMAGE,
                ports={CANVAS_EMULATOR_HOST_PORTS["ddc"]: EMULATOR_CONTAINER_PORT},
                env={"DEFAULT_REGION": "us-east-1"},
                healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["ddc"]),
            ),
        ]

        return TopologySpec(
            project_name="icdev-ddc-sim",
            canvas="ddc",
            nodes=nodes,
            links=links,
            probes=probes,
            docker_services=docker_services,
            metadata={
                "tf_files": len(tf_paths),
                "resources_parsed": len(resources),
                "data_nodes": len(data_nodes),
            },
        )
