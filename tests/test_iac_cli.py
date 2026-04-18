# CUI // SP-CTI
"""Tests for python -m tools.infra_canvas.emit (dt-idc-iac-07)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.infra_canvas import emit


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(args: list[str], capsys) -> tuple[int, str]:
    """Invoke emit.main() with mocked argv. Returns (exit_code, stdout)."""
    sys.argv = ["emit"] + args
    try:
        emit.main()
        code = 0
    except SystemExit as exc:
        code = int(exc.code) if exc.code is not None else 0
    out = capsys.readouterr().out
    return code, out


def _graph_file(tmp_path: Path, graph: dict) -> Path:
    gf = tmp_path / "graph.json"
    gf.write_text(json.dumps(graph), encoding="utf-8")
    return gf


# ── Sample graphs ─────────────────────────────────────────────────────────────

_TF_GRAPH = {
    "nodes": [
        {"id": "vpc1", "type": "aws-vpc", "label": "my-vpc",
         "metadata": {"cidr_block": "10.0.0.0/16"}}
    ],
    "edges": [],
}

_ANSIBLE_GRAPH = {
    "nodes": [
        {"id": "pkg1", "type": "package", "label": "Install nginx",
         "metadata": {"name": "nginx", "state": "present"}}
    ],
    "edges": [],
}

_PULUMI_GRAPH = {
    "nodes": [
        {"id": "vnet1", "type": "az-vnet", "label": "my-vnet",
         "metadata": {"resource_group_name": "rg-test"}}
    ],
    "edges": [],
}

_HELM_GRAPH = {
    "nodes": [
        {"id": "dep1", "type": "k8s-deployment", "label": "api",
         "metadata": {"image": "nginx:latest", "replicas": 2}}
    ],
    "edges": [],
}


# ── Test cases ─────────────────────────────────────────────────────────────────

def test_terraform_emits_main_tf(tmp_path, capsys):
    gf = _graph_file(tmp_path, _TF_GRAPH)
    out = tmp_path / "out-tf"
    code, _ = _run(
        ["--target", "terraform", "--csp", "aws", "--project", str(gf), "--out", str(out)],
        capsys,
    )
    assert code == 0
    assert (out / "main.tf").exists()
    assert "aws_vpc" in (out / "main.tf").read_text(encoding="utf-8")


def test_ansible_emits_playbook(tmp_path, capsys):
    gf = _graph_file(tmp_path, _ANSIBLE_GRAPH)
    out = tmp_path / "out-ans"
    code, _ = _run(
        ["--target", "ansible", "--csp", "aws", "--project", str(gf), "--out", str(out)],
        capsys,
    )
    assert code == 0
    assert (out / "playbook.yaml").exists()
    assert "nginx" in (out / "playbook.yaml").read_text(encoding="utf-8")


def test_pulumi_azure_emits_index_ts(tmp_path, capsys):
    gf = _graph_file(tmp_path, _PULUMI_GRAPH)
    out = tmp_path / "out-pulumi"
    code, _ = _run(
        ["--target", "pulumi", "--csp", "azure", "--project", str(gf), "--out", str(out)],
        capsys,
    )
    assert code == 0
    assert (out / "index.ts").exists()
    assert "VirtualNetwork" in (out / "index.ts").read_text(encoding="utf-8")


def test_helm_emits_chart_yaml(tmp_path, capsys):
    gf = _graph_file(tmp_path, _HELM_GRAPH)
    out = tmp_path / "out-helm"
    code, _ = _run(
        ["--target", "helm", "--csp", "aws", "--project", str(gf), "--out", str(out)],
        capsys,
    )
    assert code == 0
    assert (out / "Chart.yaml").exists()
    assert "ICDEV" in (out / "Chart.yaml").read_text(encoding="utf-8")


def test_missing_target_exits_2(tmp_path, capsys):
    gf = _graph_file(tmp_path, _TF_GRAPH)
    out = tmp_path / "out-err"
    code, _ = _run(
        ["--csp", "aws", "--project", str(gf), "--out", str(out)],
        capsys,
    )
    assert code == 2


def test_json_flag_prints_summary(tmp_path, capsys):
    gf = _graph_file(tmp_path, _TF_GRAPH)
    out = tmp_path / "out-json"
    code, stdout = _run(
        ["--target", "terraform", "--csp", "aws",
         "--project", str(gf), "--out", str(out), "--json"],
        capsys,
    )
    assert code == 0
    summary = json.loads(stdout.strip())
    assert summary["target"] == "terraform"
    assert summary["csp"] == "aws"
    assert "main.tf" in summary["files"]
    assert summary["emitted_count"] >= 1
