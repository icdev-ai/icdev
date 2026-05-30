# CUI // SP-CTI
"""GNS3 DoD Lab Backup — export topology + ZTP scripts for lab reproduction.

Saves a dated ZIP archive containing:
  - topology.json   : full node/link manifest (positions, console ports, images)
  - ztp/            : all .rsc and .txt ZTP scripts for the project
  - README.md       : step-by-step reproduction guide

Usage:
  python tools/ndc/gns3_backup.py [--project dod-bcap-lab] [--json]
  python tools/ndc/gns3_backup.py --out data/backups/gns3 --json
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT    = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_SERVER  = "http://localhost:3080"
_PROJECT = "dod-bcap-lab"
_ZTP_DIR = _ROOT / "data" / "studio_artifacts" / "ndc" / "dod_lab" / "ztp"
_OUT_DIR = _ROOT / "data" / "backups" / "gns3"


def backup(
    server: str = _SERVER,
    project_name: str = _PROJECT,
    ztp_dir: "Path | str" = _ZTP_DIR,
    out_dir: "Path | str" = _OUT_DIR,
) -> dict:
    """Export topology + ZTP scripts to a dated ZIP archive."""
    from tools.network.adapters.gns3_adapter import GNS3Adapter

    g = GNS3Adapter(server)

    projects = g.list_projects()
    proj = next((p for p in projects if p.get("name") == project_name), None)
    if not proj:
        return {"status": "error", "error": f"Project {project_name!r} not found at {server}"}

    pid = proj["project_id"]
    nodes = g._get_list(f"/v2/projects/{pid}/nodes")
    links = g._get_list(f"/v2/projects/{pid}/links")

    ts       = datetime.now(timezone.utc)
    ts_str   = ts.strftime("%Y%m%d_%H%M%S")
    stamp    = ts.isoformat()

    manifest = {
        "project_name": project_name,
        "project_id":   pid,
        "server":       server,
        "backed_up_at": stamp,
        "node_count":   len(nodes),
        "link_count":   len(links),
        "nodes": [
            {
                "name":         n["name"],
                "node_id":      n["node_id"],
                "node_type":    n.get("node_type"),
                "console_type": n.get("console_type"),
                "console":      n.get("console"),
                "status":       n.get("status"),
                "x":            n.get("x", 0),
                "y":            n.get("y", 0),
                "properties": {
                    k: v for k, v in n.get("properties", {}).items()
                    if k in ("ram", "cpus", "hda_disk_image", "platform", "image",
                             "adapters", "qemu_path", "options")
                },
            }
            for n in nodes
        ],
        "links": [
            {
                "link_id": lk["link_id"],
                "nodes": [
                    {
                        "node_id":        ep["node_id"],
                        "adapter_number": ep.get("adapter_number", 0),
                        "port_number":    ep.get("port_number", 0),
                    }
                    for ep in lk.get("nodes", [])
                ],
            }
            for lk in links
        ],
    }

    ztp_dir = Path(ztp_dir)
    ztp_files = sorted(list(ztp_dir.glob("*.rsc")) + list(ztp_dir.glob("*.txt")))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    archive_path = out_dir / f"{project_name}_{ts_str}.zip"

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("topology.json", json.dumps(manifest, indent=2))
        for f in ztp_files:
            zf.write(f, f"ztp/{f.name}")
        zf.writestr("README.md", _make_readme(manifest, server, ztp_files))

    return {
        "status":       "success",
        "archive":      str(archive_path),
        "project":      project_name,
        "nodes":        len(nodes),
        "links":        len(links),
        "ztp_scripts":  len(ztp_files),
        "backed_up_at": stamp,
    }


def _make_readme(manifest: dict, server: str, ztp_files: list[Path]) -> str:
    nodes     = manifest["nodes"]
    chr_nodes = [n for n in nodes if n.get("node_type") == "qemu"]
    vpcs_nodes = [n for n in nodes if n.get("node_type") == "vpcs"]
    project   = manifest["project_name"]

    lines = [
        f"# GNS3 Lab Backup — {project}",
        "",
        f"Backed up: {manifest['backed_up_at']}  |  Server: {server}",
        f"Nodes: {manifest['node_count']}  |  Links: {manifest['link_count']}",
        "",
        "## Reproduction Steps",
        "",
        "1. Import or rebuild topology in GNS3:",
        "   ```",
        "   python tools/ndc/gns3_dod_topology_builder.py --build",
        "   ```",
        "2. Start all nodes (QEMU CHR routers take ~60s to boot).",
        "3. Push ZTP configs (handles EULA + blank-password first-boot automatically):",
        "   ```",
        "   python tools/ndc/gns3_traffic_engine.py --ztp",
        "   ```",
        "4. Wait 45s for iBGP/eBGP convergence, then run traffic matrix:",
        "   ```",
        "   python tools/ndc/gns3_traffic_engine.py --traffic",
        "   ```",
        "",
        "## Credentials",
        "",
        "Router password is set from `GNS3_ROUTER_PASSWORD` in `.env`.",
        "On fresh CHR disks (blank password), ZTP sets the password automatically.",
        "",
        f"## Nodes ({manifest['node_count']} total)",
        "",
        f"### MikroTik CHR Routers ({len(chr_nodes)})",
        "| Name | Console | Image |",
        "|------|---------|-------|",
    ]

    for n in chr_nodes:
        props = n.get("properties", {})
        img = props.get("hda_disk_image") or props.get("image") or "—"
        lines.append(f"| {n['name']} | :{n.get('console', '?')} | {img} |")

    lines += [
        "",
        f"### VPCS Hosts ({len(vpcs_nodes)})",
        "| Name | Console |",
        "|------|---------|",
    ]
    for n in vpcs_nodes:
        lines.append(f"| {n['name']} | :{n.get('console', '?')} |")

    lines += [
        "",
        f"## ZTP Scripts ({len(ztp_files)} files)",
        "",
        "All scripts are in the `ztp/` directory of this archive.",
        "",
        "| File | Type |",
        "|------|------|",
    ]
    for f in ztp_files:
        kind = "MikroTik RouterOS" if f.suffix == ".rsc" else "VPCS ip-config"
        lines.append(f"| {f.name} | {kind} |")

    lines += [
        "",
        "## BGP Design",
        "",
        "- **AS 65001** — NIPR backbone (iBGP hub-spoke)",
        "- **NIPR-CORE-RTR** — iBGP hub, connects both edge routers",
        "- **NIPR-EDGE-1** — dual-homed eBGP (ATT AS 65101, Lumen AS 65102) + iBGP to core",
        "- **NIPR-EDGE-2** — single-homed eBGP (Verizon AS 65103) + iBGP to core",
        "- **BCAP/JWICS stacks** — static routes only (no routing protocol participation)",
        "",
        "## Canvas-Agnostic Reuse",
        "",
        "To use the traffic engine with a different canvas or lab:",
        "",
        "```python",
        "from tools.ndc.gns3_traffic_engine import GNS3TrafficEngine",
        "",
        "engine = GNS3TrafficEngine(",
        '    server="http://localhost:3080",',
        '    project="sdc-lab",',
        '    ztp_dir="data/studio_artifacts/sdc/lab/ztp",',
        '    traffic_matrix=[',
        '        ("SDC-NODE-A", "10.1.1.1", "Core ping", "s1"),',
        '    ],',
        '    trace_targets=[],',
        '    db_table="sdc_live_flows",',
        ")",
        "result = engine.run(do_ztp=True, do_traffic=True)",
        "```",
    ]

    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="GNS3 DoD Lab Backup")
    p.add_argument("--server",  default=_SERVER,         help="GNS3 server URL")
    p.add_argument("--project", default=_PROJECT,        help="GNS3 project name")
    p.add_argument("--ztp-dir", default=str(_ZTP_DIR),   help="ZTP scripts directory")
    p.add_argument("--out",     default=str(_OUT_DIR),   help="Archive output directory")
    p.add_argument("--json",    action="store_true")
    args = p.parse_args()

    result = backup(
        server=args.server,
        project_name=args.project,
        ztp_dir=Path(args.ztp_dir),
        out_dir=Path(args.out),
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["status"] == "success":
            print(f"[backup] {result['archive']}")
            print(f"  nodes={result['nodes']}  links={result['links']}  "
                  f"ztp_scripts={result['ztp_scripts']}")
            print(f"  backed_up_at={result['backed_up_at']}")
        else:
            print(f"ERROR: {result.get('error')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
