# CUI // SP-CTI
"""Every TOOL_REGISTRY entry must resolve to a real, importable handler (oss-fix-01).

Background — the failure mode this test exists to prevent:

``UnifiedMCPServer._resolve_handler()`` catches ImportError/AttributeError and
substitutes a ``_stub`` closure returning
``{"error": "Module not available", "status": "pending"}``.  That is correct
behaviour for an optional dependency, but it also means a registry entry naming
a handler that was never written is indistinguishable, from the outside, from a
working tool: the tool is advertised in ``tools/list``, callers see a plausible
JSON envelope, and nothing fails loudly.

``sandbox_execute`` sat in that state — registered, listed in the ``security``
bundle of ``args/agent_toolsets.yaml``, and silently non-functional, alongside
56 other entries.  These tests make the stub path impossible to reach by
accident: a registry entry whose handler does not exist now fails CI.
"""
from __future__ import annotations

import importlib

import pytest

from tools.mcp.tool_registry import TOOL_REGISTRY


def _resolve(entry: dict):
    """Import an entry's handler, raising the underlying error on failure."""
    module = importlib.import_module(entry["module"])
    return getattr(module, entry["handler"])


def test_registry_is_non_trivial():
    """Guard against the sweep below silently passing on an empty registry."""
    assert len(TOOL_REGISTRY) > 400, f"expected the full tool surface, got {len(TOOL_REGISTRY)}"


def test_every_entry_declares_module_and_handler():
    missing = [
        name
        for name, entry in TOOL_REGISTRY.items()
        if not entry.get("module") or not entry.get("handler")
    ]
    assert not missing, f"registry entries missing module/handler: {missing}"


def test_every_registry_entry_resolves_to_a_real_handler():
    """No entry may name a handler that does not exist.

    This is the regression guard for oss-fix-01. A failure here means the tool
    would be advertised over MCP but answer every call with the "Module not
    available" stub.
    """
    broken: dict[str, str] = {}
    for name, entry in TOOL_REGISTRY.items():
        try:
            handler = _resolve(entry)
        except (ImportError, AttributeError, ModuleNotFoundError) as exc:
            broken[name] = f"{entry['module']}.{entry['handler']} — {type(exc).__name__}: {exc}"
            continue
        if not callable(handler):
            broken[name] = f"{entry['module']}.{entry['handler']} is not callable"

    assert not broken, (
        f"{len(broken)} registry entries do not resolve to a real handler; each would "
        "silently serve the 'Module not available' stub:\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in sorted(broken.items()))
    )


def test_no_tool_resolves_to_the_server_stub():
    """Exercise the real server path, not just importlib.

    ``_resolve_handler`` is what actually runs in production; assert directly
    that it never falls back to its ``_stub`` closure for any registered tool.
    """
    from tools.mcp.unified_server import UnifiedMCPServer

    server = UnifiedMCPServer()
    stubbed = [
        name
        for name, entry in TOOL_REGISTRY.items()
        if getattr(server._resolve_handler(name, entry), "__name__", "") == "_stub"
    ]
    assert not stubbed, f"tools falling back to the import stub: {stubbed}"


def test_handlers_accept_a_single_args_dict():
    """Every handler follows the ``handler(args: dict) -> dict`` contract."""
    import inspect

    bad: dict[str, str] = {}
    for name, entry in TOOL_REGISTRY.items():
        handler = _resolve(entry)
        try:
            params = list(inspect.signature(handler).parameters.values())
        except (TypeError, ValueError):  # builtins / C-implemented callables
            continue
        required = [
            p
            for p in params
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if len(required) > 1:
            bad[name] = f"{entry['handler']}{inspect.signature(handler)}"
    assert not bad, f"handlers must take a single args dict: {bad}"


# ---------------------------------------------------------------------------
# sandbox_execute — the specific gap that motivated this test (oss-fix-01)
# ---------------------------------------------------------------------------


def test_sandbox_execute_is_wired_to_the_real_executor():
    entry = TOOL_REGISTRY["sandbox_execute"]
    handler = _resolve(entry)
    assert handler.__name__ == "handle_sandbox_execute"
    # The handler must reach the real executor, not a placeholder.
    assert "sandbox_executor" in (handler.__doc__ or "") or "SandboxExecutor" in _handler_source(handler)


def _handler_source(handler) -> str:
    import inspect

    return inspect.getsource(handler)


@pytest.mark.parametrize(
    "args, missing",
    [({"language": "python"}, "code"), ({"code": "print(1)"}, "language")],
)
def test_sandbox_execute_rejects_incomplete_args_without_executing(args, missing):
    """Argument validation happens before any container is touched."""
    from tools.mcp.gap_handlers import handle_sandbox_execute

    result = handle_sandbox_execute(args)
    assert result["status"] == "failed"
    assert missing in result["error"]


def test_sandbox_execute_is_advertised_only_if_it_works():
    """Bundles must not advertise a tool that resolves to the stub.

    ``sandbox_execute`` is listed in the ``security`` bundle of
    ``args/agent_toolsets.yaml``; the standalone agent runtime loads that bundle
    and hands the tool to a model. Anything advertised there and present in
    TOOL_REGISTRY must have a working handler.
    """
    import yaml

    from tools.agent_runtime.toolsets import _BUNDLE_PATH

    data = yaml.safe_load(_BUNDLE_PATH.read_text(encoding="utf-8")) or {}
    bundles = data.get("bundles") or {}
    assert "sandbox_execute" in (bundles.get("security", {}).get("tools") or [])

    broken: dict[str, list[str]] = {}
    for bundle_name, bundle in bundles.items():
        bad = []
        for tool in bundle.get("tools") or []:
            entry = TOOL_REGISTRY.get(tool)
            if entry is None:
                continue  # built-in or @tool-decorated; resolved by discovery
            try:
                _resolve(entry)
            except (ImportError, AttributeError, ModuleNotFoundError):
                bad.append(tool)
        if bad:
            broken[bundle_name] = bad
    assert not broken, f"agent bundles advertise non-functional tools: {broken}"
