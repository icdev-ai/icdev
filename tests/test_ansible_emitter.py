# CUI // SP-CTI
"""Tests for tools/infra_canvas/emitters/ansible.py — 6 deterministic cases."""

import sys
import pathlib

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from tools.infra_canvas.emitters.ansible import (
    UnsupportedResourceError,
    emit_playbook,
    emit_task,
)

# ── Node fixtures ─────────────────────────────────────────────────────────────

_USER = {
    "id": "user-admin",
    "type": "user",
    "label": "Ensure admin user",
    "metadata": {"name": "admin", "state": "present", "shell": "/bin/bash", "groups": ["wheel"]},
}

_PACKAGE = {
    "id": "pkg-httpd",
    "type": "package",
    "label": "Install httpd",
    "metadata": {"name": "httpd", "state": "present"},
}

_SERVICE = {
    "id": "svc-httpd",
    "type": "service",
    "label": "Enable httpd",
    "metadata": {"name": "httpd", "state": "started", "enabled": True},
}

_FILE = {
    "id": "file-etc-app",
    "type": "file",
    "label": "Create /etc/app",
    "metadata": {"path": "/etc/app", "state": "directory", "owner": "root", "mode": "0755"},
}

_LINEINFILE = {
    "id": "lif-sshd",
    "type": "lineinfile",
    "label": "Disable root SSH",
    "metadata": {
        "path": "/etc/ssh/sshd_config",
        "line": "PermitRootLogin no",
        "regexp": "^PermitRootLogin",
    },
}

_CUI_PKG = {
    "id": "pkg-aide",
    "type": "package",
    "label": "Install AIDE",
    "metadata": {"name": "aide", "state": "present", "classification": "CUI"},
}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_emit_task_user():
    task = emit_task(_USER)
    assert task["name"] == "Ensure admin user"
    assert "ansible.builtin.user" in task
    args = task["ansible.builtin.user"]
    assert args["name"] == "admin"
    assert args["state"] == "present"
    assert args["shell"] == "/bin/bash"
    assert "wheel" in args["groups"]


def test_emit_task_package():
    task = emit_task(_PACKAGE)
    assert "ansible.builtin.package" in task
    args = task["ansible.builtin.package"]
    assert args["name"] == "httpd"
    assert args["state"] == "present"


def test_emit_task_service():
    task = emit_task(_SERVICE)
    assert "ansible.builtin.service" in task
    args = task["ansible.builtin.service"]
    assert args["name"] == "httpd"
    assert args["state"] == "started"
    assert args["enabled"] is True


def test_emit_task_file():
    task = emit_task(_FILE)
    assert "ansible.builtin.file" in task
    args = task["ansible.builtin.file"]
    assert args["path"] == "/etc/app"
    assert args["state"] == "directory"
    assert args["owner"] == "root"
    assert args["mode"] == "0755"


def test_emit_task_lineinfile():
    task = emit_task(_LINEINFILE)
    assert "ansible.builtin.lineinfile" in task
    args = task["ansible.builtin.lineinfile"]
    assert args["path"] == "/etc/ssh/sshd_config"
    assert args["line"] == "PermitRootLogin no"
    assert args["regexp"] == "^PermitRootLogin"


def test_emit_playbook_cui_vars():
    nodes = [_USER, _CUI_PKG, _SERVICE]
    playbook_str = emit_playbook(nodes, classification="CUI")

    # Must start with YAML document marker
    assert playbook_str.startswith("---")

    # Must round-trip through YAML parser
    plays = yaml.safe_load(playbook_str)
    assert isinstance(plays, list) and len(plays) == 1

    play = plays[0]
    assert play["hosts"] == "all"
    assert play["become"] is True
    assert len(play["tasks"]) == 3

    # CUI classification must appear in vars
    vars_block = play.get("vars", {})
    assert vars_block.get("icdev_classification") == "CUI"
    assert "icdev_managed_by" in vars_block
    assert "icdev_data_handling" in vars_block


def test_unsupported_type_raises():
    node = {"id": "bad", "type": "aws-vpc", "label": "VPC", "metadata": {}}
    with pytest.raises(UnsupportedResourceError, match="aws-vpc"):
        emit_task(node)
