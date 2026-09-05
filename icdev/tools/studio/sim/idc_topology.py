# CUI // SP-CTI
"""IDC topology builder — cloud infrastructure nodes (multi-CSP aware).

CSP detection order:
  1. Terraform provider blocks in artifact .tf files
  2. Env var ICDEV_TARGET_CSP  (aws | azure | gcp | ibm | oci)
  3. Default: aws

GCP emulator: LocalGCP (ghcr.io/slokam-ai/localgcp) — 14 services in one container
  Cloud Storage :4443  Pub/Sub :8085  Firestore :8088  Cloud Tasks :8089
  Vertex AI     :8090  KMS     :8091  Logging   :8092  Cloud Run   :8093
  BigQuery      :9060  Secrets :8086  Spanner   :9010  Bigtable    :9094
  Cloud SQL     :5432  Memorystore :6379
  Env vars auto-set via standard GCP emulator host variables.
  Fallback (Storage-only): fsouza/fake-gcs-server :4443
"""
from __future__ import annotations

import os
from pathlib import Path

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)

# LocalGCP — comprehensive GCP emulator (14 services, MIT license, no telemetry)
# https://github.com/slokam-ai/localgcp
_LOCALGCP_SERVICE = DockerServiceSpec(
    name="icdev-localgcp-idc",
    image="ghcr.io/slokam-ai/localgcp:latest",
    ports={
        4443: 4443,   # Cloud Storage (GCS)
        8085: 8085,   # Pub/Sub
        8086: 8086,   # Secret Manager
        8088: 8088,   # Firestore
        8089: 8089,   # Cloud Tasks
        8090: 8090,   # Vertex AI
        8091: 8091,   # Cloud KMS
        8092: 8092,   # Cloud Logging
        8093: 8093,   # Cloud Run
        9010: 9010,   # Spanner (gRPC)
        9060: 9060,   # BigQuery
        9094: 9094,   # Bigtable
    },
    env={
        "STORAGE_EMULATOR_HOST":    "localhost:4443",
        "PUBSUB_EMULATOR_HOST":     "localhost:8085",
        "FIRESTORE_EMULATOR_HOST":  "localhost:8088",
        "BIGTABLE_EMULATOR_HOST":   "localhost:9094",
        "SPANNER_EMULATOR_HOST":    "localhost:9010",
        "BIGQUERY_EMULATOR_HOST":   "localhost:9060",
    },
    # Pub/Sub emulator exposes a simple HTTP endpoint for health
    healthcheck_url="http://localhost:8085/",
)

_CSP_DOCKER_SERVICES: dict[str, list[DockerServiceSpec]] = {
    "aws": [DockerServiceSpec(
        name="icdev-floci-idc",
        image=EMULATOR_IMAGE,
        ports={CANVAS_EMULATOR_HOST_PORTS["idc-aws"]: EMULATOR_CONTAINER_PORT},
        env={"DEFAULT_REGION": "us-east-1"},
        healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["idc-aws"]),
    )],
    "azure": [DockerServiceSpec(
        name="icdev-azurite-idc",
        image="mcr.microsoft.com/azure-storage/azurite",
        ports={10000: 10000, 10001: 10001, 10002: 10002},
        env={},
        # Azurite Blob endpoint
        healthcheck_url="http://localhost:10000/",
    )],
    "gcp": [
        _LOCALGCP_SERVICE,
        # Lightweight fallback: GCS-only via fake-gcs-server (no auth required)
        DockerServiceSpec(
            name="icdev-fake-gcs-idc",
            image="fsouza/fake-gcs-server:latest",
            ports={4444: 4443},
            env={"STORAGE_EMULATOR_HOST": "localhost:4444"},
            healthcheck_url="http://localhost:4444/storage/v1/b",
        ),
    ],
    "ibm": [DockerServiceSpec(
        name="icdev-minio-idc",
        image="minio/minio:latest",
        ports={9000: 9000, 9001: 9001},
        env={"MINIO_ROOT_USER": "minioadmin", "MINIO_ROOT_PASSWORD": "minioadmin"},
        healthcheck_url="http://localhost:9000/minio/health/live",
    )],
    "oci": [DockerServiceSpec(
        # The AWS-shaped emulator standing in for OCI's S3-compatible surface,
        # which is what this container has always been. `floci-oci` proper
        # (port 4599) is flx-oci-01's, and is deliberately NOT claimed here.
        name="icdev-floci-idc-oci",
        image=EMULATOR_IMAGE,
        ports={CANVAS_EMULATOR_HOST_PORTS["idc-oci"]: EMULATOR_CONTAINER_PORT},
        env={"DEFAULT_REGION": "us-ashburn-1"},
        healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["idc-oci"]),
    )],
}


def _detect_csp(artifacts_dir: Path) -> str:
    env_csp = os.environ.get("ICDEV_TARGET_CSP", "").lower()
    if env_csp in _CSP_DOCKER_SERVICES:
        return env_csp
    for tf in (artifacts_dir / "terraform").glob("*.tf") if (artifacts_dir / "terraform").exists() else []:
        text = tf.read_text(encoding="utf-8", errors="ignore")
        if 'provider "azurerm"' in text:
            return "azure"
        if 'provider "google"' in text:
            return "gcp"
        if 'provider "ibm"' in text:
            return "ibm"
    return "aws"


class IDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "idc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        csp = _detect_csp(artifacts_dir)
        nodes = [
            NodeSpec(name="vpc-fabric",    node_type="ethernet_switch", meta={"role": "vpc", "csp": csp}),
            NodeSpec(name="public-subnet", node_type="ethernet_switch", meta={"role": "subnet", "tier": "public"}),
            NodeSpec(name="private-subnet",node_type="ethernet_switch", meta={"role": "subnet", "tier": "private"}),
            NodeSpec(name="igw",           node_type="nat",             meta={"role": "gateway", "csp": csp}),
            NodeSpec(name="compute-node",  node_type="vpcs",            meta={"role": "compute", "type": "ec2/vm"}),
            NodeSpec(name="lb-node",       node_type="vpcs",            meta={"role": "load-balancer"}),
            NodeSpec(name="cdn-edge",      node_type="cloud",           meta={"role": "cdn/edge"}),
            NodeSpec(name="storage-node",  node_type="cloud",           meta={"role": "object-storage", "csp": csp}),
        ]
        links = [
            LinkSpec("cdn-edge",      "igw"),
            LinkSpec("igw",           "vpc-fabric"),
            LinkSpec("vpc-fabric",    "public-subnet"),
            LinkSpec("vpc-fabric",    "private-subnet"),
            LinkSpec("public-subnet", "lb-node"),
            LinkSpec("lb-node",       "compute-node"),
            LinkSpec("private-subnet","compute-node"),
            LinkSpec("private-subnet","storage-node"),
        ]
        docker_services = _CSP_DOCKER_SERVICES.get(csp, _CSP_DOCKER_SERVICES["aws"])
        return TopologySpec(
            project_name="icdev-idc-sim",
            canvas="idc",
            nodes=nodes, links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count", type="link_count", expected_value=len(links)),
                ProbeSpec(name="emulator_apply", type="emulator_apply"),
            ],
            docker_services=docker_services,
            metadata={"csp": csp},
        )
