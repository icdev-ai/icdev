from __future__ import annotations
# CUI // SP-CTI
"""Ansible playbook emitter for IDC graph nodes (config-mgmt scope).

emit_task(node)           -> dict  — single Ansible task dict
emit_playbook(nodes, ...) -> str   — complete playbook YAML string

Supported node types (OS-level config, post-provision Linux):
  user       ansible.builtin.user
  package    ansible.builtin.package
  service    ansible.builtin.service
  file       ansible.builtin.file
  lineinfile ansible.builtin.lineinfile
"""

from typing import Any

import yaml

# ── Type alias ────────────────────────────────────────────────────────────────
Node = dict[str, Any]

# ── Constants ─────────────────────────────────────────────────────────────────
_MANAGED_BY = "icdev-ansible-emitter"
_SUPPORTED_TYPES = frozenset({"user", "package", "service", "file", "lineinfile"})
_CUI_VALUES = {"CUI", "CUI//SP-CTI", "SECRET", "CUI//SP-CTI/IL4", "CUI//SP-CTI/IL5"}


class UnsupportedResourceError(ValueError):
    """Raised when a node type has no Ansible task emitter."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta(node: Node) -> dict[str, Any]:
    return node.get("metadata") or {}


# ── Per-type task builders ────────────────────────────────────────────────────

def _build_user_task(node: Node) -> dict:
    meta = _meta(node)
    args: dict[str, Any] = {
        "name": meta.get("name", "appuser"),
        "state": meta.get("state", "present"),
    }
    if "shell" in meta:
        args["shell"] = meta["shell"]
    if "groups" in meta:
        args["groups"] = meta["groups"]
        args["append"] = meta.get("append", True)
    if "password" in meta:
        args["password"] = meta["password"]
    if "home" in meta:
        args["home"] = meta["home"]
    return {"name": node.get("label", "Manage user"), "ansible.builtin.user": args}


def _build_package_task(node: Node) -> dict:
    meta = _meta(node)
    args: dict[str, Any] = {
        "name": meta.get("name", ""),
        "state": meta.get("state", "present"),
    }
    return {"name": node.get("label", "Manage package"), "ansible.builtin.package": args}


def _build_service_task(node: Node) -> dict:
    meta = _meta(node)
    args: dict[str, Any] = {
        "name": meta.get("name", ""),
        "state": meta.get("state", "started"),
        "enabled": meta.get("enabled", True),
    }
    return {"name": node.get("label", "Manage service"), "ansible.builtin.service": args}


def _build_file_task(node: Node) -> dict:
    meta = _meta(node)
    args: dict[str, Any] = {
        "path": meta.get("path", "/etc/icdev/placeholder"),
        "state": meta.get("state", "touch"),
    }
    if "owner" in meta:
        args["owner"] = meta["owner"]
    if "group" in meta:
        args["group"] = meta["group"]
    if "mode" in meta:
        args["mode"] = str(meta["mode"])  # always string — Ansible requires quoted octal
    return {"name": node.get("label", "Manage file"), "ansible.builtin.file": args}


def _build_lineinfile_task(node: Node) -> dict:
    meta = _meta(node)
    args: dict[str, Any] = {
        "path": meta.get("path", "/etc/placeholder"),
        "line": meta.get("line", ""),
        "state": meta.get("state", "present"),
    }
    if "regexp" in meta:
        args["regexp"] = meta["regexp"]
    if "backup" in meta:
        args["backup"] = meta["backup"]
    if "insertafter" in meta:
        args["insertafter"] = meta["insertafter"]
    if "insertbefore" in meta:
        args["insertbefore"] = meta["insertbefore"]
    return {"name": node.get("label", "Manage line"), "ansible.builtin.lineinfile": args}


# ── Dispatch ──────────────────────────────────────────────────────────────────

_TASK_BUILDERS: dict[str, Any] = {
    "user": _build_user_task,
    "package": _build_package_task,
    "service": _build_service_task,
    "file": _build_file_task,
    "lineinfile": _build_lineinfile_task,
}


# ── Public API ────────────────────────────────────────────────────────────────

def emit_task(node: Node) -> dict:
    """Return an Ansible task dict for a single IDC graph node.

    Args:
        node: IDC graph node with keys ``id``, ``type``, ``label``, ``metadata``.

    Returns:
        Dict suitable for serialization as one Ansible task entry.

    Raises:
        UnsupportedResourceError: Node type is not one of the 5 supported types.
    """
    node_type = node.get("type", "")
    builder = _TASK_BUILDERS.get(node_type)
    if builder is None:
        raise UnsupportedResourceError(
            f"Node type {node_type!r} not supported. "
            f"Supported: {sorted(_SUPPORTED_TYPES)}"
        )
    return builder(node)


def emit_playbook(
    nodes: list[Node],
    hosts: str = "all",
    become: bool = True,
    classification: str | None = None,
) -> str:
    """Emit a complete Ansible playbook YAML string for a list of IDC nodes.

    Args:
        nodes: List of IDC graph node dicts.
        hosts: Target hosts pattern (default ``"all"``).
        become: Whether to use privilege escalation (default ``True``).
        classification: CUI/classification marking injected into ``vars``.
                        Auto-detected from node metadata when ``None``.

    Returns:
        Complete playbook YAML string prefixed with ``---``.

    Raises:
        UnsupportedResourceError: Any node has an unsupported type.
    """
    tasks = [emit_task(n) for n in nodes]

    if classification is None:
        for n in nodes:
            cls = str(_meta(n).get("classification", "")).strip()
            if cls in _CUI_VALUES:
                classification = cls
                break

    vars_block: dict[str, Any] = {"icdev_managed_by": _MANAGED_BY}
    if classification:
        vars_block["icdev_classification"] = classification
        vars_block["icdev_data_handling"] = "CUI//SP-CTI"

    play: dict[str, Any] = {
        "name": "ICDEV config-mgmt playbook",
        "hosts": hosts,
        "become": become,
        "vars": vars_block,
        "tasks": tasks,
    }

    header = "# CUI // SP-CTI\n" if classification else ""
    body = yaml.dump(
        [play],
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        indent=2,
    )
    return f"---\n{header}{body}"
