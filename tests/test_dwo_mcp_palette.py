"""dwo-mcp-03-d3 — the MCP section of the workflow-editor palette.

The palette is what makes a `node_type: mcp` step reachable from the editor.
It draws from tools/mcp/tool_registry.py, which exposes 500+ tools, so these
tests pin the filter: only what `mcp_workflow_tools.allowed` names may appear,
and each entry must carry the registry's own description plus a params form
generated from that tool's input_schema.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

we = importlib.import_module("tools.studio.workflow_editor")

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "args" / "security_gates.yaml"


@pytest.fixture(autouse=True)
def _clear_section_cache():
    """The built section is process-global; no test may inherit another's."""
    we._MCP_SECTION_CACHE = None
    yield
    we._MCP_SECTION_CACHE = None


@pytest.fixture(scope="module")
def policy() -> dict:
    return yaml.safe_load(GATES.read_text(encoding="utf-8"))["mcp_workflow_tools"]


@pytest.fixture
def section() -> dict:
    return we.build_mcp_palette_section(refresh=True)


# ── Allowlist loading ──────────────────────────────────────


def test_allowlist_matches_the_gate_policy(policy):
    assert we.load_mcp_allowlist() == [str(t) for t in policy["allowed"]]


def test_allowlist_excludes_tools_that_need_a_human_gate(policy):
    """`requires_approval` tools are refused at dispatch — never offer them."""
    allowed = set(we.load_mcp_allowlist())
    assert allowed.isdisjoint({str(t) for t in policy["requires_approval"]})


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("mcp_workflow_tools:\n  default: allow\n  allowed: [health_check]\n", "not default-deny"),
        ("other_key: 1\n", "no policy section"),
        ("mcp_workflow_tools:\n  default: deny\n  allowed:\n", "null allowed list"),
        ("{{ not yaml\n", "unparseable"),
    ],
)
def test_unusable_policy_yields_no_tools(tmp_path, body, reason):
    """The palette shows nothing rather than guessing — same default-deny outcome."""
    gates = tmp_path / "security_gates.yaml"
    gates.write_text(body, encoding="utf-8")
    assert we.load_mcp_allowlist(gates) == [], reason


def test_missing_gate_file_yields_no_tools(tmp_path):
    assert we.load_mcp_allowlist(tmp_path / "absent.yaml") == []


def test_allowlist_preserves_policy_order_and_dedupes(tmp_path):
    gates = tmp_path / "security_gates.yaml"
    gates.write_text(
        'mcp_workflow_tools:\n  default: deny\n  allowed: [b, a, b, ""]\n', encoding="utf-8"
    )
    assert we.load_mcp_allowlist(gates) == ["b", "a"]


# ── Section contents ───────────────────────────────────────


def test_section_lists_only_allowlisted_tools(section, policy):
    from tools.mcp.tool_registry import list_tools

    registered = set(list_tools())
    allowed = [t for t in policy["allowed"] if t in registered]
    assert [t["mcp_tool"] for t in section["tools"]] == allowed
    # The filter is doing real work: the registry is far larger than the palette.
    assert len(registered) > len(allowed)


def test_section_omits_the_rest_of_the_registry(section):
    from tools.mcp.tool_registry import list_tools

    shown = {t["mcp_tool"] for t in section["tools"]}
    assert shown.isdisjoint(set(list_tools()) - shown - {""})
    assert "terraform_apply" not in shown
    assert "ssp_generate" not in shown


def test_entries_carry_the_mcp_step_fields(section):
    for tool in section["tools"]:
        assert tool["node_type"] == "mcp"
        assert tool["tool"] == we.MCP_EXECUTOR
        assert tool["id"] == f"mcp_{tool['mcp_tool']}"
        # The label is the registry key verbatim: it is what goes in `mcp_tool`.
        assert tool["name"] == tool["mcp_tool"]


def test_descriptions_come_from_the_registry(section):
    from tools.mcp.tool_registry import TOOL_REGISTRY

    for tool in section["tools"]:
        entry = TOOL_REGISTRY.get(tool["mcp_tool"])
        if entry:
            assert tool["description"] == entry["description"]
            assert tool["description"], f"{tool['mcp_tool']} has no help text"


def test_allowlisted_name_unknown_to_the_registry_is_dropped(monkeypatch):
    """MCP-WF-001 `mcp_tool_unknown_to_registry` — a typo must not become a step."""
    monkeypatch.setattr(we, "load_mcp_allowlist", lambda *a, **k: ["health_check", "no_such_tool"])
    section = we.build_mcp_palette_section(refresh=True)
    assert [t["mcp_tool"] for t in section["tools"]] == ["health_check"]


def test_section_is_omitted_when_nothing_is_allowlisted(monkeypatch):
    monkeypatch.setattr(we, "load_mcp_allowlist", lambda *a, **k: [])
    assert we.build_mcp_palette_section(refresh=True) == {}
    assert "mcp" not in we.get_tool_catalog(refresh=True)


# ── Params forms ───────────────────────────────────────────


def test_form_fields_match_the_input_schema(section):
    from tools.mcp.tool_registry import TOOL_REGISTRY

    for tool in section["tools"]:
        schema = (TOOL_REGISTRY.get(tool["mcp_tool"]) or {}).get("input_schema") or {}
        properties = schema.get("properties") or {}
        assert {f["name"] for f in tool["params_form"]} == set(properties)


def test_required_fields_come_first():
    schema = {
        "type": "object",
        "properties": {"opt": {"type": "string"}, "req": {"type": "string"}},
        "required": ["req"],
    }
    form = we.build_params_form(schema)
    assert [f["name"] for f in form] == ["req", "opt"]
    assert [f["required"] for f in form] == [True, False]


def test_widgets_follow_the_schema_type():
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "count": {"type": "integer"},
            "ratio": {"type": "number"},
            "flag": {"type": "boolean"},
            "tags": {"type": "array"},
            "cfg": {"type": "object"},
        },
    }
    widgets = {f["name"]: f["widget"] for f in we.build_params_form(schema)}
    assert widgets == {
        "text": "text",
        "count": "number",
        "ratio": "number",
        "flag": "checkbox",
        "tags": "textarea",
        "cfg": "textarea",
    }


def test_composite_fields_are_marked_json():
    """The editor must parse these before they reach mcp_params."""
    schema = {"type": "object", "properties": {"tags": {"type": "array"}, "s": {"type": "string"}}}
    fields = {f["name"]: f for f in we.build_params_form(schema)}
    assert fields["tags"]["json"] is True
    assert "json" not in fields["s"]


def test_enum_becomes_a_select_with_options():
    schema = {"type": "object", "properties": {"level": {"type": "string", "enum": ["IL4", "IL5"]}}}
    field = we.build_params_form(schema)[0]
    assert field["widget"] == "select"
    assert field["options"] == ["IL4", "IL5"]


def test_defaults_and_help_text_are_carried_through():
    schema = {
        "type": "object",
        "properties": {"top_k": {"type": "integer", "default": 5, "description": "How many"}},
    }
    field = we.build_params_form(schema)[0]
    assert field["default"] == 5
    assert field["description"] == "How many"
    assert field["label"] == "Top K"


@pytest.mark.parametrize("schema", [None, {}, {"type": "object"}, {"properties": []}, "nope"])
def test_schema_without_properties_yields_an_empty_form(schema):
    assert we.build_params_form(schema) == []


# ── Catalog integration ────────────────────────────────────


def test_catalog_exposes_the_section_without_mutating_the_static_categories():
    before = dict(we.TOOL_CATEGORIES)
    catalog = we.get_tool_catalog(refresh=True)
    assert catalog["mcp"]["tools"], "MCP section is empty"
    assert catalog["mcp"]["label"] == "MCP Tools"
    # The palette renders `cat.color` into a CSS class; an unknown one is unstyled.
    assert catalog["mcp"]["color"] in {c["color"] for c in we.TOOL_CATEGORIES.values()}
    assert we.TOOL_CATEGORIES == before
    assert "mcp" not in we.TOOL_CATEGORIES


def test_catalog_keeps_every_static_category():
    catalog = we.get_tool_catalog(refresh=True)
    assert set(we.TOOL_CATEGORIES) <= set(catalog)
