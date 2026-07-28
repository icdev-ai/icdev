"""dwo-mcp-03-d4 — every surface that enumerates Studio node types must know 'mcp'.

`node_type: mcp` shipped in dwo-mcp-03, but several places still enumerated the
old three-value vocabulary.  A surface that omits 'mcp' does not fail loudly —
the workflow-chat prompt simply never generates an MCP step, the builder's
dropdown simply never offers one, and the README simply contradicts the linter.
These tests pin each enumeration so the next node type added has to update them.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The canonical Studio node-type vocabulary, owned by template_linter.py.
EXPECTED_NODE_TYPES = {"tool", "human", "approval", "mcp"}


def test_template_linter_is_the_source_of_truth():
    """VALID_NODE_TYPES is the vocabulary every other surface is checked against."""
    from tools.studio.template_linter import VALID_NODE_TYPES

    assert set(VALID_NODE_TYPES) == EXPECTED_NODE_TYPES


def test_workflow_chat_prompt_offers_mcp():
    """The LLM authoring prompt must enumerate mcp, or chat can never emit one."""
    from tools.studio.workflow_chat import _SYSTEM_PROMPT_TEMPLATE

    node_type_line = next(
        ln
        for ln in _SYSTEM_PROMPT_TEMPLATE.splitlines()
        if ln.startswith("node_type values:")
    )
    listed = {v.strip() for v in node_type_line.split(":", 1)[1].split(",")}
    assert listed == EXPECTED_NODE_TYPES

    # An mcp step is useless without naming the tool to dispatch.
    assert "mcp_tool" in _SYSTEM_PROMPT_TEMPLATE


def test_template_readme_matches_the_linter():
    """The README's 'Allowed node_type values' line must not contradict the linter."""
    readme = (REPO_ROOT / "args" / "workflow_templates" / "README.md").read_text(
        encoding="utf-8"
    )
    allowed_line = next(
        ln for ln in readme.splitlines() if ln.startswith("Allowed `node_type` values:")
    )
    for node_type in EXPECTED_NODE_TYPES:
        assert f"`{node_type}`" in allowed_line, f"README omits {node_type!r}"


def test_builder_js_offers_and_persists_mcp_nodes():
    """The visual builder must be able to author, save and reload an mcp node."""
    js = (
        REPO_ROOT / "tools" / "dashboard" / "static" / "js" / "workflow-studio.js"
    ).read_text(encoding="utf-8")

    # Selectable in the node-config dropdown.
    assert 'value="mcp"' in js
    # The config section toggles with the other three.
    assert "cfg-mcp-section" in js
    # mcp_tool survives a save → YAML → reload round trip.
    assert "mcp_tool:" in js
    assert "mcp_params" in js


def test_icdev_mirror_matches_root_for_touched_files():
    """A stale mirror ships the old vocabulary to pip-installed users."""
    pairs = [
        ("tools/studio/workflow_chat.py", "icdev/tools/studio/workflow_chat.py"),
        (
            "tools/dashboard/static/js/workflow-studio.js",
            "icdev/tools/dashboard/static/js/workflow-studio.js",
        ),
        (
            "args/workflow_templates/README.md",
            "icdev/data/args/workflow_templates/README.md",
        ),
    ]
    for root_rel, mirror_rel in pairs:
        root = (REPO_ROOT / root_rel).read_text(encoding="utf-8")
        mirror = (REPO_ROOT / mirror_rel).read_text(encoding="utf-8")
        assert root == mirror, f"{mirror_rel} is stale relative to {root_rel}"
