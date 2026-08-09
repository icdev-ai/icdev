# CUI // SP-CTI
"""CLI entry-point for the IDC IaC emitter.

Usage:
    python -m tools.infra_canvas.emit \\
      --target {terraform,pulumi,ansible,helm} \\
      --project <design-id|graph.json> \\
      --csp     {aws,azure,gcp,oci,on-prem} \\
      --out     <directory> \\
      [--json]

Exit codes:
  0 — success
  2 — validation failure (bad args, unsupported CSP, no emittable nodes)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

Node = dict[str, Any]

_TARGETS = ("terraform", "pulumi", "ansible", "helm")
_CSPS = ("aws", "azure", "gcp", "oci", "on-prem")

# CSPs each target supports
_SUPPORTED_CSPS: dict[str, frozenset[str]] = {
    "terraform": frozenset({"aws", "gcp", "oci"}),
    "pulumi":    frozenset({"aws", "azure"}),
    "ansible":   frozenset(_CSPS),
    "helm":      frozenset(_CSPS),
}

# CLI --csp value → emitter-internal CSP key
_CSP_KEY: dict[str, dict[str, str]] = {
    "terraform": {"aws": "aws-govcloud", "gcp": "gcp", "oci": "oci"},
    "pulumi":    {"aws": "aws-govcloud", "azure": "azure-government"},
}


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m tools.infra_canvas.emit",
        description="Emit IaC files from an IDC infrastructure design.",
    )
    p.add_argument("--target",  required=True, choices=_TARGETS,
                   help="IaC target")
    p.add_argument("--project-id", dest="project_id",
                   metavar="<id|graph.json>",
                   help="Design ID (DB lookup) or path to a graph JSON file")
    p.add_argument("--project", dest="project_id", help=argparse.SUPPRESS)  # backward compat — use --project-id
    p.add_argument("--csp",     required=True, choices=_CSPS,
                   help="Target cloud provider")
    p.add_argument("--out",     required=True, metavar="<dir>",
                   help="Output directory (created if absent)")
    p.add_argument("--json",    action="store_true",
                   help="Print JSON summary to stdout on success")
    return p.parse_args(argv)


def _load_graph(project: str) -> dict[str, Any]:
    """Load graph JSON from a .json file path or the infra_canvas DB."""
    fp = Path(project)
    if fp.suffix.lower() == ".json" or fp.is_file():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:
            _fail(f"Cannot read graph file {project!r}: {exc}")
    # DB lookup
    try:
        from tools.infra_canvas.db.init_db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT graph_json FROM infra_designs WHERE id = %s", (project,))
        except Exception:
            cur.execute("SELECT graph_json FROM infra_designs WHERE id = %s", (project,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            _fail(f"Project {project!r} not found in database.")
        return json.loads(row[0])
    except SystemExit:
        raise
    except Exception as exc:
        _fail(f"Database error loading project {project!r}: {exc}")


# ── Per-target emitters ────────────────────────────────────────────────────────

def _emit_terraform(nodes: list[Node], csp: str, out_dir: Path) -> tuple[list[str], int]:
    from tools.infra_canvas.emitters.terraform import emit_resource, UnsupportedResourceError
    csp_key = _CSP_KEY["terraform"][csp]
    blocks: list[str] = []
    skipped = 0
    for node in nodes:
        try:
            blocks.append(emit_resource(node, csp_key))
        except UnsupportedResourceError:
            skipped += 1
    if not blocks:
        _fail(f"No terraform-emittable nodes found for csp={csp!r}.")
    (out_dir / "main.tf").write_text("\n\n".join(blocks), encoding="utf-8", newline="")
    return ["main.tf"], skipped


def _emit_ansible(nodes: list[Node], csp: str, out_dir: Path) -> tuple[list[str], int]:
    from tools.infra_canvas.emitters.ansible import emit_playbook, UnsupportedResourceError
    ok_types = {"user", "package", "service", "file", "lineinfile"}
    good = [n for n in nodes if n.get("type") in ok_types]
    skipped = len(nodes) - len(good)
    if not good:
        _fail("No Ansible-compatible nodes found (expected: user/package/service/file/lineinfile).")
    try:
        content = emit_playbook(good)
    except UnsupportedResourceError as exc:
        _fail(str(exc))
    (out_dir / "playbook.yaml").write_text(content, encoding="utf-8", newline="")
    return ["playbook.yaml"], skipped


def _emit_pulumi(nodes: list[Node], csp: str, out_dir: Path) -> tuple[list[str], int]:
    from tools.infra_canvas.emitters.pulumi import emit_resource, UnsupportedResourceError
    csp_key = _CSP_KEY["pulumi"][csp]
    blocks: list[str] = []
    skipped = 0
    for node in nodes:
        try:
            blocks.append(emit_resource(node, csp_key))
        except UnsupportedResourceError:
            skipped += 1
    if not blocks:
        _fail(f"No pulumi-emittable nodes found for csp={csp!r}.")
    aws_import = 'import * as aws from "@pulumi/aws";' if csp == "aws" else ""
    az_import = 'import * as azure_native from "@pulumi/azure-native";' if csp == "azure" else ""
    header = "\n".join(line for line in [aws_import, az_import] if line)
    body = "\n\n".join(blocks)
    content = f"{header}\n\n{body}" if header else body
    (out_dir / "index.ts").write_text(content, encoding="utf-8", newline="")
    return ["index.ts"], skipped


def _emit_helm(
    nodes: list[Node], csp: str, out_dir: Path, project: str
) -> tuple[list[str], int]:
    from tools.infra_canvas.emitters.helm import emit_chart, UnsupportedResourceError
    ok_types = {"k8s-deployment", "k8s-service", "k8s-configmap", "k8s-secret", "k8s-hpa"}
    good = [n for n in nodes if n.get("type") in ok_types]
    skipped = len(nodes) - len(good)
    if not good:
        _fail("No Helm-compatible nodes found (expected: k8s-*).")
    fp = Path(project)
    chart_name = fp.stem if fp.suffix else project
    try:
        files_map = emit_chart(good, chart_name=chart_name)
    except UnsupportedResourceError as exc:
        _fail(str(exc))
    written: list[str] = []
    for filename, content in files_map.items():
        path = out_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        written.append(filename)
    return written, skipped


# ── Entry point ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if not args.project_id:
        _fail("--project-id is required")

    if args.csp not in _SUPPORTED_CSPS[args.target]:
        _fail(
            f"CSP {args.csp!r} is not supported for --target {args.target!r}. "
            f"Supported: {sorted(_SUPPORTED_CSPS[args.target])}"
        )

    graph = _load_graph(args.project_id)
    nodes: list[Node] = graph.get("nodes", [])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.target == "terraform":
        written, skipped = _emit_terraform(nodes, args.csp, out_dir)
    elif args.target == "ansible":
        written, skipped = _emit_ansible(nodes, args.csp, out_dir)
    elif args.target == "pulumi":
        written, skipped = _emit_pulumi(nodes, args.csp, out_dir)
    else:  # helm
        written, skipped = _emit_helm(nodes, args.csp, out_dir, args.project_id)

    if args.json:
        summary = {
            "target": args.target,
            "csp": args.csp,
            "project_id": args.project_id,
            "node_count": len(nodes),
            "emitted_count": len(nodes) - skipped,
            "skipped_count": skipped,
            "files": written,
            "out": str(out_dir.resolve()),
        }
        print(json.dumps(summary))

    sys.exit(0)


if __name__ == "__main__":
    main()
