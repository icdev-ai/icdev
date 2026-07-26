# CUI // SP-CTI
"""The browser primitive must be reachable from all four agent-tool seams.

There are four parallel agent-tool registries in this repo, and registering in
the wrong one strands the capability — that is the whole point of oss-browse-03.
These tests pin each seam so a capability cannot silently exist in one and not
the others.

They also pin the property that matters more than reachability: **no seam
re-implements policy.** Scope, budget and audit live in tools/browser/scope.py.
oss-browse-01 shipped a second, fail-open copy of the domain gate and had to
have it removed before merge; a third copy in a seam adapter would be the same
defect wearing a different hat.
"""
from __future__ import annotations

import importlib

import pytest
import yaml

BROWSER_TOOLS = [
    "browser_navigate",
    "browser_read_state",
    "browser_click",
    "browser_type",
    "browser_screenshot",
]


# ── Seam 1: tools/agent_toolkit ───────────────────────────────────────────────


def test_seam1_agent_toolkit_exports_the_browser_surface():
    toolkit = importlib.import_module("tools.agent_toolkit")
    for name in BROWSER_TOOLS + ["browser_session", "browser_select", "browser_press"]:
        assert hasattr(toolkit, name), f"{name} not exported from tools.agent_toolkit"
        assert name in toolkit.__all__, f"{name} missing from __all__"


def test_seam1_denials_are_returned_not_raised():
    """A scope refusal is information for the model, not a crash.

    An agent loop that dies on a denied navigation cannot route around it; one
    that receives ``denied: True`` can.
    """
    from tools.agent_toolkit import browser_navigate

    result = browser_navigate("https://not-on-the-allowlist.example/secret")
    assert result["ok"] is False
    assert result.get("denied") is True
    assert "reason" in result


@pytest.mark.parametrize("scheme_url", ["javascript:alert(1)", "file:///etc/passwd"])
def test_seam1_dangerous_schemes_are_refused(scheme_url):
    from tools.agent_toolkit import browser_navigate

    assert browser_navigate(scheme_url)["ok"] is False


# ── Seam 2: MCP TOOL_REGISTRY + gap_handlers ─────────────────────────────────


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_seam2_registry_entry_resolves_to_a_real_handler(name):
    """oss-fix-01's lesson: a registry entry whose handler does not exist is
    silently replaced by a stub returning 'Module not available', and looks like
    a working tool from the outside."""
    from tools.mcp.tool_registry import TOOL_REGISTRY

    entry = TOOL_REGISTRY[name]
    module = importlib.import_module(entry["module"])
    handler = getattr(module, entry["handler"])
    assert callable(handler)


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_seam2_entry_declares_a_usable_schema(name):
    from tools.mcp.tool_registry import TOOL_REGISTRY

    entry = TOOL_REGISTRY[name]
    assert entry["input_schema"]["type"] == "object"
    assert entry["description"].strip()


def test_seam2_handlers_validate_required_arguments():
    from tools.mcp.gap_handlers import handle_browser_click, handle_browser_navigate

    assert "error" in handle_browser_navigate({})
    assert "error" in handle_browser_click({})


# ── Seam 3: args/agent_toolsets.yaml ─────────────────────────────────────────


def _bundles() -> dict:
    from tools.rag.config_path import BASE_DIR  # repo root resolver

    path = BASE_DIR / "args" / "agent_toolsets.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("bundles", {})


def test_seam3_browser_bundle_lists_the_tools():
    bundle = _bundles().get("browser")
    assert bundle is not None, "no 'browser' bundle in args/agent_toolsets.yaml"
    assert set(bundle["tools"]) == set(BROWSER_TOOLS)


def test_seam3_bundle_is_marked_mutating():
    """Clicking and typing change state on the far side of the network.

    `mutating: true` is what makes default_safety_gate deny these unless
    ICDEV_SAG_ALLOW_MUTATION is set, so the standalone agent cannot reach them
    by default. Flipping this to false silently un-gates the browser.
    """
    assert _bundles()["browser"]["mutating"] is True


def test_seam3_every_bundled_tool_is_in_the_registry():
    from tools.mcp.tool_registry import TOOL_REGISTRY

    missing = [t for t in _bundles()["browser"]["tools"] if t not in TOOL_REGISTRY]
    assert not missing, f"bundle advertises tools absent from TOOL_REGISTRY: {missing}"


# ── Seam 4: tools/ace/agent_tools.py ─────────────────────────────────────────


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_seam4_ace_declares_the_schema(name):
    from tools.ace.agent_tools import _SCHEMAS

    schema = _SCHEMAS.get(name)
    assert schema is not None, f"{name} missing from ACE _SCHEMAS"
    assert schema["function"]["name"] == name


def test_seam4_read_and_write_tools_are_labelled_correctly():
    """is_read_only drives ACE's own gating; mislabelling click as read-only
    would let a read-only role mutate a page."""
    from tools.ace.agent_tools import _SCHEMAS

    assert _SCHEMAS["browser_read_state"]["is_read_only"] is True
    assert _SCHEMAS["browser_screenshot"]["is_read_only"] is True
    for mutating in ("browser_navigate", "browser_click", "browser_type"):
        assert _SCHEMAS[mutating]["is_read_only"] is False, f"{mutating} mislabelled"


def test_seam4_browser_tools_are_not_in_the_default_role_set():
    """A co-worker that can click inside an ATO platform is a deliberate grant."""
    import inspect

    from tools.ace import agent_tools

    src = inspect.getsource(agent_tools)
    default_line = [ln for ln in src.splitlines() if 'names = ["read_file"' in ln]
    assert default_line, "default tool list not found — update this test"
    assert "browser" not in default_line[0]


# ── The property that matters across all four ────────────────────────────────


def test_no_seam_reimplements_scope_policy():
    """Policy lives in tools/browser/scope.py. A seam adapter that grew its own
    allowlist would be oss-browse-01's fail-open duplicate all over again."""
    from tools.rag.config_path import BASE_DIR

    banned = ("allowed_domains", "denied_domains", "allow_non_local")
    for rel in ("tools/agent_toolkit/_browser.py", "tools/mcp/gap_handlers.py"):
        text = (BASE_DIR / rel).read_text(encoding="utf-8")
        # gap_handlers is huge and covers many tools; scope the check to the
        # browser section this change added.
        if "gap_handlers" in rel:
            text = text.split("Browser agent tools (oss-browse-03")[-1]
        for token in banned:
            assert token not in text, (
                f"{rel} names {token!r} — scope policy belongs to "
                "tools/browser/scope.py, not a seam adapter"
            )
