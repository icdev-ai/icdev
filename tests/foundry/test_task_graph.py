# CUI // SP-CTI
"""Tests for tools/foundry/task_graph.py — canvas_contract -> canonical epic skeleton.

Covers the task's acceptance criterion (a contract with 2 modules + mcp + reflex yields a
valid chained graph whose every depends_on_task_id resolves) plus the canonical epic shape,
the f'{slug}-{epic}-{n:02d}' id convention, the single-parent linear chain, the
mcp/reflex gating, core-epic module ownership, determinism, and the Guardrails +
integrity_gate marker embedded in every build task.
"""
from __future__ import annotations

from tools.foundry import spec_generator as sg
from tools.foundry import task_graph as tg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _contract(**overrides):
    """A minimal but well-formed contract: 2 core modules + mcp + reflex."""
    base = {
        "slug": "widget-forge",
        "title": "Widget Forge",
        "env_flag": "ICDEV_WIDGET_FORGE_ENABLED",
        "tables": [
            {"name": "wf_items", "append_only": False, "purpose": "Primary records"},
            {"name": "wf_events", "append_only": True, "purpose": "Append-only log"},
        ],
        "modules": [
            {"path": "tools/wf/analyzer.py", "purpose": "Analyze widgets"},
            {"path": "tools/wf/reporter.py", "purpose": "Report results"},
            {"path": "tools/wf/mcp_adapter.py", "purpose": "MCP registration"},
            {"path": "tools/genesis/reflexes/wf_cycle.py", "purpose": "Periodic cycle"},
        ],
        "routes": [
            {"method": "GET", "path": "/widget-forge", "kind": "page", "purpose": "Page"},
            {"method": "POST", "path": "/api/wf/items", "kind": "api", "purpose": "API"},
            {"method": "POST", "path": "/api/iqe-query", "kind": "iqe", "purpose": "IQE"},
        ],
        "iqe_collections": ["wf_items", "wf_events"],
        "needs_mcp": True,
        "needs_reflex": True,
    }
    base.update(overrides)
    return base


def _concept():
    return {"id": 1, "name": "Widget Forge", "slug": "widget-forge"}


# ---------------------------------------------------------------------------
# Acceptance criterion: valid chained graph, every depends_on resolves
# ---------------------------------------------------------------------------
def test_graph_is_a_valid_resolving_linear_chain():
    tasks = tg.build_task_graph(_concept(), _contract())

    assert len(tasks) >= 1
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "task ids must be unique"

    # Root has no parent; every other parent resolves to a prior id.
    assert tasks[0]["depends_on_task_id"] is None
    id_set = set(ids)
    for i, t in enumerate(tasks):
        dep = t["depends_on_task_id"]
        if i == 0:
            assert dep is None
        else:
            assert dep in id_set, f"unresolved parent {dep!r}"
            # Single-parent linear chain: parent is the immediately preceding task.
            assert dep == tasks[i - 1]["id"]

    # Required task shape.
    for t in tasks:
        assert set(t).issuperset(
            {"id", "title", "description", "task_type", "priority", "depends_on_task_id"}
        )


# ---------------------------------------------------------------------------
# ID convention + per-epic numbering
# ---------------------------------------------------------------------------
def test_ids_follow_slug_epic_n_convention():
    tasks = tg.build_task_graph(_concept(), _contract())
    for t in tasks:
        slug, epic, n = t["id"].rsplit("-", 2)
        assert slug == "widget-forge"
        assert epic in tg.EPIC_ORDER
        assert n.isdigit() and len(n) == 2

    # Numbering restarts per epic (db-01 exists, and the first of each epic is 01).
    seen_first = {}
    for t in tasks:
        _, epic, n = t["id"].rsplit("-", 2)
        seen_first.setdefault(epic, n)
    for epic, first_n in seen_first.items():
        assert first_n == "01", f"{epic} should start at 01"


# ---------------------------------------------------------------------------
# Canonical epic skeleton present and ordered
# ---------------------------------------------------------------------------
def test_canonical_epics_present_and_ordered():
    tasks = tg.build_task_graph(_concept(), _contract())
    epics_in_order = []
    for t in tasks:
        _, epic, _ = t["id"].rsplit("-", 2)
        if epic not in epics_in_order:
            epics_in_order.append(epic)

    # With mcp + reflex enabled, all eight canonical epics appear.
    assert epics_in_order == ["db", "core", "engine", "dash", "mcp", "reflex", "doc", "vv"]

    # The 8-component dash gate is 4 tasks; db is 4; engine 2; vv 4.
    counts = {}
    for t in tasks:
        _, epic, _ = t["id"].rsplit("-", 2)
        counts[epic] = counts.get(epic, 0) + 1
    assert counts["db"] == 4
    assert counts["engine"] == 2
    assert counts["dash"] == 4
    assert counts["vv"] == 4
    assert counts["mcp"] == 1
    assert counts["reflex"] == 1


# ---------------------------------------------------------------------------
# Core epic = one task per non-owned domain module
# ---------------------------------------------------------------------------
def test_core_epic_one_task_per_domain_module():
    tasks = tg.build_task_graph(_concept(), _contract())
    core = [t for t in tasks if t["id"].rsplit("-", 2)[1] == "core"]
    # analyzer.py + reporter.py are core; mcp_adapter + reflex are owned elsewhere.
    assert len(core) == 2
    titles = " ".join(t["title"] for t in core)
    assert "analyzer.py" in titles and "reporter.py" in titles


def test_scaffold_only_contract_has_empty_core():
    """A stock spec_generator contract's modules are all epic-owned -> core is empty."""
    contract = sg.build_canvas_contract(
        {"name": "Smart Contract Auditor", "slug": "smart-contract-auditor"}
    )
    tasks = tg.build_task_graph({"name": "Smart Contract Auditor"}, contract)
    core = [t for t in tasks if t["id"].rsplit("-", 2)[1] == "core"]
    assert core == []


# ---------------------------------------------------------------------------
# mcp / reflex gating
# ---------------------------------------------------------------------------
def test_mcp_and_reflex_omitted_when_not_needed():
    tasks = tg.build_task_graph(
        _concept(), _contract(needs_mcp=False, needs_reflex=False)
    )
    epics = {t["id"].rsplit("-", 2)[1] for t in tasks}
    assert "mcp" not in epics
    assert "reflex" not in epics
    # Chain still resolves with the optional epics removed.
    ids = {t["id"] for t in tasks}
    for i, t in enumerate(tasks):
        if i:
            assert t["depends_on_task_id"] in ids


# ---------------------------------------------------------------------------
# Guardrails + integrity_gate marker on build tasks
# ---------------------------------------------------------------------------
def test_build_tasks_embed_guardrails_and_integrity_gate():
    tasks = tg.build_task_graph(_concept(), _contract())
    builds = [t for t in tasks if t["task_type"] == "build"]
    assert builds, "expected build tasks"
    for t in builds:
        assert t.get("integrity_gate") is True
        assert tg.INTEGRITY_GATE_MARKER in t["description"]
        # The four named Guardrails surfaces are present.
        assert "get_connection()" in t["description"]
        assert "constants.py" in t["description"]
        assert "APPEND_ONLY_TABLES" in t["description"]
        assert "8-component" in t["description"]


def test_non_build_tasks_have_no_integrity_gate_flag():
    tasks = tg.build_task_graph(_concept(), _contract())
    for t in tasks:
        if t["task_type"] != "build":
            assert "integrity_gate" not in t


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_graph_is_deterministic():
    a = tg.build_task_graph(_concept(), _contract())
    b = tg.build_task_graph(_concept(), _contract())
    assert a == b


# ---------------------------------------------------------------------------
# End-to-end: spec_generator contract -> graph
# ---------------------------------------------------------------------------
def test_consumes_spec_generator_contract():
    concept = {
        "id": 7,
        "name": "Threat Feed Correlator",
        "slug": "threat-feed-correlator",
        "proposed_capability": "Continuously monitor and correlate external threat feeds via API.",
        "problem_statement": "Analysts manually cross-reference feeds.",
    }
    contract = sg.build_canvas_contract(concept)
    tasks = tg.build_task_graph(concept, contract)

    ids = {t["id"] for t in tasks}
    # Every parent resolves.
    for t in tasks:
        dep = t["depends_on_task_id"]
        assert dep is None or dep in ids
    # Slug propagated into the ids.
    assert all(t["id"].startswith("threat-feed-correlator-") for t in tasks)
