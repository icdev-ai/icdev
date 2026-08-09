# CUI // SP-CTI
"""``read_only`` is declared, not guessed from the tool name (hgx-guard-03).

The agent loop partitions a turn's tool calls on the read-only flag and runs the
read-only half CONCURRENTLY. That partition is chosen before the safety layer
runs, so a mutating tool that lands in it has already been handed to a worker
thread by the time any gate could object. These tests pin the three properties
that make the flag trustworthy:

1. every registered tool carries an explicit boolean declaration;
2. the name heuristic is reached only for undeclared tools, and logs when it is;
3. no declared-mutating tool lands in the agent loop's parallel partition.
"""
from __future__ import annotations

import logging

import pytest

from tools.agent_runtime import discovery
from tools.llm.agent_loop import _build_read_only_set
from tools.mcp import tool_registry as reg


def _registered_tools() -> dict[str, dict]:
    """Every registered TOOL — both registries, resources excluded.

    ``RESOURCE_REGISTRY`` holds genuine MCP resources (``projects://list``)
    *and* 51 late-added tools; an ``input_schema`` is what distinguishes them.
    """
    tools: dict[str, dict] = {}
    for registry in (reg.TOOL_REGISTRY, reg.RESOURCE_REGISTRY):
        for name, entry in registry.items():
            if isinstance(entry, dict) and "input_schema" in entry:
                tools[name] = entry
    return tools


#: Tools that unambiguously mutate state and must never be dispatched in
#: parallel. Deliberately hand-listed rather than derived from the name — a
#: name-derived list would be the very heuristic this task removed. Covers the
#: deploy / apply / delete / heal families the card calls out, plus the
#: read-only-*sounding* writers that motivated the change.
KNOWN_MUTATING = [
    # deploy / apply
    "terraform_apply", "k8s_deploy", "ansible_run", "pipeline_generate", "rollback",
    "generate_platform_artifacts", "install_modules",
    # delete / revoke / uninstall
    "kanban_delete_task", "rag_delete_source", "revoke_binding", "uninstall_asset",
    "canvas_unlink_design",
    # heal / remediate
    "self_heal", "remediate", "production_remediate",
    # execute / launch
    "sandbox_execute", "runbook_execute", "run_e2e_tests", "run_tests",
    "ace_launch", "ace_abort", "cortex_agent_launch", "send_command",
    # write / create / update
    "project_create", "kanban_create_task", "kanban_update_task", "kanban_move_task",
    "incident_create", "incident_update", "slo_define", "slo_measure",
    "add_pattern", "add_vendor", "kg_merge_entities", "kg_add_alias",
    "proxy_key_issue", "credential_broker_request", "record_canvas_decision",
    # ingest / sync / import
    "rag_ingest", "rag_reindex", "dic_ingest", "news_ingest_once", "sync_jira",
    "sync_gitlab", "sync_servicenow", "emass_sync", "xacta_sync", "import_xmi",
    "import_reqif", "mc_net_ingest_csv", "mc_net_ingest_netbox", "conflict_mesh_etl",
    # network / security actuation
    "dsoc_rtbh_trigger", "dsoc_flowspec_activate", "pvm_map_attack_surface",
    "pvm_create_patch_plan", "ohc_promote_model", "browser_navigate", "browser_click",
    "browser_type",
    # read-only-SOUNDING writers — the actual bug this task fixes
    "scan_web", "scan_dependencies", "check_vulnerabilities", "detect_trends",
    "detect_behavioral_drift", "scan_code_patterns", "csp_monitor_scan",
    "detect_gaps", "detect_drift", "asset_scan",
]


def test_every_registered_tool_has_a_read_only_declaration():
    """No tool may be left to the name heuristic."""
    tools = _registered_tools()
    assert tools, "tool registry failed to load"
    undeclared = sorted(n for n in tools if n not in reg.READ_ONLY_DECLARATIONS)
    assert undeclared == [], (
        f"{len(undeclared)} tool(s) have no read_only declaration in "
        f"READ_ONLY_DECLARATIONS: {undeclared}"
    )
    assert reg.undeclared_tools() == []


def test_declarations_are_booleans_and_immutable():
    for name in _registered_tools():
        declared = reg.READ_ONLY_DECLARATIONS[name]
        assert isinstance(declared, bool), f"{name}: declaration is not a bool"
        assert reg.is_read_only(name) is declared

    # A mutating tool must not be flippable into the parallel partition at
    # runtime by anything that can reach the module.
    with pytest.raises(TypeError):
        reg.READ_ONLY_DECLARATIONS["terraform_apply"] = True


def test_no_stray_declarations():
    """A declaration for a tool that no longer exists is dead weight."""
    tools = _registered_tools()
    stray = sorted(n for n in reg.READ_ONLY_DECLARATIONS if n not in tools)
    assert stray == [], f"declarations for unregistered tools: {stray}"


def test_known_mutating_tools_are_declared_mutating():
    wrong = [n for n in KNOWN_MUTATING if reg.READ_ONLY_DECLARATIONS.get(n) is not False]
    assert wrong == [], f"these mutate state but are not declared read_only=False: {wrong}"


def test_guess_is_not_reached_for_declared_tools(monkeypatch):
    """``_guess_read_only`` must not run at all while every tool is declared."""
    def _boom(name: str) -> bool:  # pragma: no cover - only runs on regression
        raise AssertionError(f"_guess_read_only was reached for declared tool {name!r}")

    monkeypatch.setattr(discovery, "_guess_read_only", _boom)
    specs = discovery.discover_mcp_tools()
    assert len(specs) > 400, "MCP registry did not load"


class _Capture(logging.Handler):
    """Collect records straight off ``discovery.logger``.

    ``icdev_logger.get_logger`` sets ``propagate = False``, so ``caplog`` — which
    hangs off the root logger — never sees these records.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def discovery_logs():
    handler = _Capture()
    discovery.logger.addHandler(handler)
    try:
        yield handler.messages
    finally:
        discovery.logger.removeHandler(handler)


def test_undeclared_tool_falls_back_to_the_guess_and_logs(monkeypatch, discovery_logs):
    """The fallback still works — and is measurable."""
    monkeypatch.setattr(discovery, "_UNDECLARED_READ_ONLY", set())
    entry = {"description": "synthetic", "input_schema": {"type": "object"}}

    schema = discovery.schema_from_mcp_entry("get_synthetic_thing", entry)

    assert schema["function"]["is_read_only"] is True  # the heuristic's answer
    assert any("no read_only declaration" in m for m in discovery_logs)
    assert any("get_synthetic_thing" in m for m in discovery_logs)
    assert discovery.undeclared_read_only_tools() == ["get_synthetic_thing"]

    # Second resolution of the same tool must not re-log (once per process).
    discovery_logs.clear()
    discovery.schema_from_mcp_entry("get_synthetic_thing", entry)
    assert discovery_logs == []


def test_declaration_beats_the_name_heuristic():
    """A declared flag wins even when the name says the opposite."""
    # Mutating tool with a read-only-looking name.
    assert discovery._guess_read_only("scan_web") is True
    assert discovery._resolve_read_only("scan_web", reg.TOOL_REGISTRY["scan_web"]) is False
    # Read-only tool with a mutating-looking name.
    assert discovery._guess_read_only("dsoc_threat_ingest") is False
    assert discovery._resolve_read_only(
        "dsoc_threat_ingest", reg.RESOURCE_REGISTRY["dsoc_threat_ingest"]
    ) is True


def test_entry_level_flag_overrides_the_declaration():
    """A caller synthesising an entry can classify it inline."""
    entry = dict(reg.TOOL_REGISTRY["rag_search"], read_only=False)
    assert reg.READ_ONLY_DECLARATIONS["rag_search"] is True
    assert discovery._resolve_read_only("rag_search", entry) is False


def test_no_declared_mutating_tool_lands_in_the_parallel_partition():
    """The load-bearing assertion: mutators stay out of the concurrent half.

    ``_build_read_only_set`` is the exact function ``run_agent_loop`` uses to
    decide which of a turn's tool calls are submitted to the thread pool, so
    running it over the real discovered schemas proves the partition itself.
    """
    specs = discovery.discover_mcp_tools()
    tools = [s.schema for s in specs]
    parallel = _build_read_only_set(tools)

    declared_mutating = {n for n, ro in reg.READ_ONLY_DECLARATIONS.items() if ro is False}
    leaked = sorted(parallel & declared_mutating)
    assert leaked == [], f"declared-mutating tools in the parallel partition: {leaked}"

    # And reproduce the loop's own partition expression over a turn that mixes
    # both kinds, to assert on the indices the loop would actually compute.
    tool_calls = [
        {"name": "scan_web"},        # mutating, read-only-looking name
        {"name": "rag_search"},      # genuinely read-only
        {"name": "terraform_apply"}, # mutating
        {"name": "kanban_get_task"}, # genuinely read-only
    ]
    ro_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name", "") in parallel]
    assert ro_indices == [1, 3]


def test_the_heuristic_alone_would_have_leaked_mutators():
    """Guard the guard: prove the old behaviour was actually broken.

    If this ever finds nothing, the heuristic and the declarations agree and
    this whole mechanism has stopped earning its keep — which is worth knowing.
    """
    leaked_by_guess = sorted(
        name
        for name, declared in reg.READ_ONLY_DECLARATIONS.items()
        if declared is False and discovery._guess_read_only(name)
    )
    assert len(leaked_by_guess) >= 10, (
        "expected the name heuristic to misclassify many mutating tools; "
        f"found {leaked_by_guess}"
    )
    for name in ("scan_web", "check_vulnerabilities", "detect_trends"):
        assert name in leaked_by_guess


@pytest.mark.parametrize("name", ["rag_search", "kanban_get_task", "nist_lookup"])
def test_genuine_readers_stay_parallel(name):
    """The fix must not collapse every tool into the sequential half."""
    assert reg.READ_ONLY_DECLARATIONS[name] is True
    assert discovery._resolve_read_only(name, reg.TOOL_REGISTRY[name]) is True
