"""Add HPC section to AI/ML Network Fabric template."""

import json
import uuid
from tools.network.db.init_db import get_connection


def eid():
    return str(uuid.uuid4())[:8]


def main():
    conn = get_connection()

    gj = json.loads(conn.execute("SELECT graph_json FROM nc_templates WHERE id=%s", ("tpl-aiml-fabric",)).fetchone()[0])
    nodes = gj["nodes"]
    edges = gj["edges"]

    # Remove old HPC nodes if re-running
    nodes = [n for n in nodes if not n["id"].startswith("hpc-")]
    edges = [e for e in edges if not e["source"].startswith("hpc-") and not e["target"].startswith("hpc-")]
    # Also remove old zone nodes that reference hpc
    nodes = [n for n in nodes if "hpc" not in n["id"]]

    # HPC devices
    hpc_nodes = [
        {"id": "hpc-sp1", "label": "HPC Spine 1", "type": "switch-l3", "x": 300, "y": 620, "config": {}},
        {"id": "hpc-sp2", "label": "HPC Spine 2", "type": "switch-l3", "x": 600, "y": 620, "config": {}},
        {"id": "hpc-lf1", "label": "HPC Leaf 1", "type": "switch-l3", "x": 100, "y": 760, "config": {}},
        {"id": "hpc-lf2", "label": "HPC Leaf 2", "type": "switch-l3", "x": 350, "y": 760, "config": {}},
        {"id": "hpc-lf3", "label": "HPC Leaf 3", "type": "switch-l3", "x": 600, "y": 760, "config": {}},
        {"id": "hpc-cn1", "label": "Compute Node A (128c)", "type": "server", "x": 60, "y": 910, "config": {}},
        {"id": "hpc-cn2", "label": "Compute Node B (128c)", "type": "server", "x": 230, "y": 910, "config": {}},
        {"id": "hpc-cn3", "label": "Compute Node C (128c)", "type": "server", "x": 400, "y": 910, "config": {}},
        {"id": "hpc-cn4", "label": "Compute Node D (128c)", "type": "server", "x": 570, "y": 910, "config": {}},
        {"id": "hpc-pfs", "label": "Lustre / GPFS PFS", "type": "server", "x": 800, "y": 760, "config": {}},
        {"id": "hpc-sched", "label": "Slurm Scheduler", "type": "server", "x": 850, "y": 620, "config": {}},
        {"id": "hpc-login", "label": "Login / Head Node", "type": "server", "x": 100, "y": 620, "config": {}},
        {"id": "hpc-mgmt", "label": "IPMI / BMC Mgmt", "type": "switch-l2", "x": 750, "y": 910, "config": {}},
    ]

    hpc_edges = [
        # HPC spines to main spines (shared super-spine)
        {"id": eid(), "source": "sp400_1", "target": "hpc-sp1", "label": "400G", "protocol": "eBGP"},
        {"id": eid(), "source": "sp400_2", "target": "hpc-sp2", "label": "400G", "protocol": "eBGP"},
        {"id": eid(), "source": "sp400_1", "target": "hpc-sp2", "label": "400G ECMP", "protocol": "eBGP"},
        {"id": eid(), "source": "sp400_2", "target": "hpc-sp1", "label": "400G ECMP", "protocol": "eBGP"},
        # HPC spine to leaf (full mesh ECMP)
        {"id": eid(), "source": "hpc-sp1", "target": "hpc-lf1", "label": "100G", "protocol": "RoCEv2"},
        {"id": eid(), "source": "hpc-sp1", "target": "hpc-lf2", "label": "100G", "protocol": "RoCEv2"},
        {"id": eid(), "source": "hpc-sp2", "target": "hpc-lf2", "label": "100G", "protocol": "RoCEv2"},
        {"id": eid(), "source": "hpc-sp2", "target": "hpc-lf3", "label": "100G", "protocol": "RoCEv2"},
        {"id": eid(), "source": "hpc-sp1", "target": "hpc-lf3", "label": "100G ECMP", "protocol": "RoCEv2"},
        {"id": eid(), "source": "hpc-sp2", "target": "hpc-lf1", "label": "100G ECMP", "protocol": "RoCEv2"},
        # Leaf to compute (RDMA)
        {"id": eid(), "source": "hpc-lf1", "target": "hpc-cn1", "label": "", "protocol": "100G RDMA"},
        {"id": eid(), "source": "hpc-lf1", "target": "hpc-cn2", "label": "", "protocol": "100G RDMA"},
        {"id": eid(), "source": "hpc-lf2", "target": "hpc-cn3", "label": "", "protocol": "100G RDMA"},
        {"id": eid(), "source": "hpc-lf3", "target": "hpc-cn4", "label": "", "protocol": "100G RDMA"},
        # Parallel filesystem
        {"id": eid(), "source": "hpc-lf3", "target": "hpc-pfs", "label": "100G NVMe-oF", "protocol": "RoCEv2"},
        {"id": eid(), "source": "hpc-lf2", "target": "hpc-pfs", "label": "100G NVMe-oF", "protocol": "RoCEv2"},
        # Scheduler + Login
        {"id": eid(), "source": "hpc-sched", "target": "hpc-sp2", "label": "10GbE", "protocol": ""},
        {"id": eid(), "source": "hpc-login", "target": "hpc-sp1", "label": "10GbE", "protocol": ""},
        # Shared storage bridge (AI ↔ HPC)
        {"id": eid(), "source": "stor", "target": "hpc-pfs", "label": "Shared Storage", "protocol": "NVMe/TCP"},
        # IPMI management
        {"id": eid(), "source": "hpc-mgmt", "target": "hpc-cn1", "label": "IPMI", "protocol": ""},
        {"id": eid(), "source": "hpc-mgmt", "target": "hpc-cn2", "label": "IPMI", "protocol": ""},
        {"id": eid(), "source": "hpc-mgmt", "target": "hpc-cn3", "label": "IPMI", "protocol": ""},
        {"id": eid(), "source": "hpc-mgmt", "target": "hpc-cn4", "label": "IPMI", "protocol": ""},
    ]

    nodes.extend(hpc_nodes)
    edges.extend(hpc_edges)
    gj["nodes"] = nodes
    gj["edges"] = edges

    conn.execute(
        "UPDATE nc_templates SET graph_json=%s, name=%s, description=%s, tags=%s WHERE id=%s",
        (
            json.dumps(gj),
            "AI/ML + HPC Network Fabric (RDMA/RoCE)",
            "High-performance compute cluster with AI/ML GPU fabric and HPC partition: "
            "RDMA over Converged Ethernet (RoCE v2), 400G spine, Lustre/GPFS parallel "
            "filesystem, Slurm scheduler, and IPMI management.",
            json.dumps(["ai-ml", "hpc", "rdma", "roce", "infiniband", "gpu", "slurm", "lustre", "gpfs"]),
            "tpl-aiml-fabric",
        ),
    )
    conn.commit()
    conn.close()
    print(f"Done — {len(hpc_nodes)} HPC nodes, {len(hpc_edges)} HPC edges added")
    print(f"Total: {len(nodes)} nodes, {len(edges)} edges")


if __name__ == "__main__":
    main()
