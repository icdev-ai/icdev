# CUI // SP-CTI
"""Every skill-tree node lands on at least one mission; no pattern links a dead end.

skill_tree.html links each node to /academy/missions?topic=<node slug>, and the
browser filtered on fa_missions.topic — a different vocabulary ('llm', 'ace',
'compliance'). Only six node slugs happened to equal a topic, so 27 of 33 nodes,
the first one (llm-basics) included, opened "0 missions available".

The route test runs the real view over a seeded catalogue, as a learner whose role
matches none of the role-targeted nodes, because a node the learner deliberately
clicked must not be emptied by the browse grid's role narrowing.
"""
from __future__ import annotations

import pytest
from flask import Flask, g

from apps.forge_academy.constants import SKILL_NODE_MISSIONS, SKILL_NODES
from apps.forge_academy.content_loader import BUILTIN_STEPS, all_missions, discover_steps
from apps.forge_academy.patterns import INJECTION_PATTERNS

NODE_SLUGS = [n["slug"] for n in SKILL_NODES]


def _authored_slugs() -> set[str]:
    discovered = discover_steps()
    return {
        m["slug"] for m in all_missions(discovered)
        if BUILTIN_STEPS.get(m["slug"]) or discovered.get(m["slug"])
    }


def test_every_skill_node_is_mapped():
    assert set(NODE_SLUGS) == set(SKILL_NODE_MISSIONS), (
        "SKILL_NODES and SKILL_NODE_MISSIONS disagree — a node without a mapping "
        "opens an empty mission list"
    )


def test_every_mapped_mission_is_catalogued_and_authored():
    authored = _authored_slugs()
    bad = sorted(
        f"{node} -> {slug}"
        for node, slugs in SKILL_NODE_MISSIONS.items() for slug in slugs
        if slug not in authored
    )
    assert not bad, f"skill nodes point at missions with no authored steps: {bad}"


def test_every_pattern_mission_is_catalogued_and_authored():
    """/academy/patterns/conversational-wrapper linked a 'Coming soon' mission."""
    authored = _authored_slugs()
    bad = sorted(
        f"{p['id']} -> {slug}" for p in INJECTION_PATTERNS
        for slug in p.get("missions", []) if slug not in authored
    )
    assert not bad, f"patterns link missions with no authored steps: {bad}"


@pytest.fixture(scope="module")
def _seeded_academy():
    from apps.forge_academy import content_loader, db

    db.migrate()
    content_loader.seed_mission_catalog()


@pytest.fixture()
def browse(monkeypatch, _seeded_academy):
    """GET the missions browser and return the context it rendered with."""
    import apps.forge_academy.blueprint as bp_mod

    captured: dict = {}

    def _render(name, **ctx):
        captured.clear()
        captured.update(ctx)
        return f"<rendered:{name}>"

    monkeypatch.setattr(bp_mod, "render_template", _render)
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _set_user():
        # 'pm' matches none of the role-targeted nodes' missions on its own.
        g.current_user = {"id": "skilltree", "role": "pm", "email": "skilltree@test.local"}

    app.register_blueprint(bp_mod.bp)
    client = app.test_client()

    def _get(topic: str) -> dict:
        r = client.get(f"/academy/missions?topic={topic}")
        assert r.status_code == 200, r.status_code
        return dict(captured)

    return _get


@pytest.mark.parametrize("node", NODE_SLUGS)
def test_each_skill_node_link_yields_a_mission(browse, node):
    ctx = browse(node)
    slugs = [m["slug"] for m in ctx["missions"]]
    assert slugs, f"/academy/missions?topic={node} shows 0 missions"
    assert all(m.get("is_available", True) for m in ctx["missions"]), (
        f"{node} lists a mission with no steps"
    )


def test_a_plain_topic_still_filters_by_topic(browse):
    ctx = browse("ace")
    assert ctx["missions"], "a mission topic that is not a node slug must keep working"
    assert all(m.get("topic") == "ace" for m in ctx["missions"])
