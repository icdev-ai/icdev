# CUI // SP-CTI
"""aca-trn-03-d3/d4 — the objective is actually visible on both learner surfaces.

The column (migration 20260803005919), the extractor
(``content_loader.extract_learning_objective``) and the JSON projection
(``_LEARNER_MISSION_FIELDS``) each have tests. Nothing asserted the two places a
learner would ever read the objective: the mission card in the browser (d3) and
the top of the runner (d4). Both were written as ``{% if m.learning_objective %}``
blocks, which is precisely the shape that degrades to *silence* — Jinja's default
``Undefined`` is falsey, so a route that stops passing the field, a query that
stops selecting it, or a rename removes the objective from the page with no error
anywhere. A grep for the string in the template file cannot see any of that.

So these render the **shipped** templates through a real Jinja environment with
``StrictUndefined``, and assert on the rendered HTML rather than on the source.
Both template roots are exercised — ``tools/`` and the ``icdev/`` package mirror —
because the mirror is what a pip-installed ICDEV serves, and a feature that exists
only in one of them is a feature half the installs do not have.

The negative cases carry the weight. This card exists because the Academy told a
learner things it had not verified, so an *invented* objective is worse than an
absent one: where the content states none the column is NULL and both surfaces
must render nothing at all — no empty label, no bare rule, no placeholder.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[1]

# Both roots the dashboard can be served from. Parametrising the environment is
# what makes "mirrored" an assertion rather than an assumption.
TEMPLATE_ROOTS = {
    "tools": REPO_ROOT / "tools" / "dashboard" / "templates",
    "icdev": REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates",
}

# Enough of base.html to render a child that extends it. base.html belongs to the
# dashboard shell and carries 30+ injected canvas flags; loading it here would test
# the harness instead of these two views.
_STUB_BASE = (
    "<!doctype html><html><head><title>{% block title %}{% endblock %}</title>"
    "{% block extra_css %}{% endblock %}</head><body>"
    "{% block content %}{% endblock %}"
    "{% block extra_js %}{% endblock %}</body></html>"
)

OBJECTIVE = "Design a 3-stage RAG pipeline and defend the chunking choice."
TAGLINE = "The difference between a chatbot and a weapon is the prompt."


@pytest.fixture(params=sorted(TEMPLATE_ROOTS), ids=sorted(TEMPLATE_ROOTS))
def env(request):
    """Jinja env over a shipped template dir, with undefined names fatal.

    ``StrictUndefined`` is the point: the default renders a missing variable as an
    empty string, which is exactly how ``{% if m.learning_objective %}`` around a
    field the route no longer passes reaches production looking fine.
    """
    root = TEMPLATE_ROOTS[request.param]
    if not root.is_dir():
        pytest.skip(f"template root not present: {root}")
    environment = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": _STUB_BASE}),
            FileSystemLoader(str(root)),
        ]),
        undefined=StrictUndefined,
        autoescape=True,
    )
    # mission.html links the CodeMirror assets through url_for (aca-ux-01). The
    # asset resolution is tests/test_aca_static_assets_resolve.py's job; here it
    # only has to not raise.
    environment.globals["url_for"] = (
        lambda endpoint, **kw: "/static/" + str(kw.get("filename", ""))
    )
    return environment


# ---------------------------------------------------------------------------
# Context builders — the shape each route actually passes
# ---------------------------------------------------------------------------

def _mission(**overrides) -> dict:
    mission = {
        "id": 7,
        "slug": "m-t2-04-rag",
        "title": "Retrieval Grounding",
        "tagline": TAGLINE,
        "tier": 2,
        "topic": "rag",
        "mission_type": "guided",
        "xp_reward": 50,
        "difficulty": "intermediate",
        "estimated_minutes": 30,
        "is_available": True,
        "is_locked": False,
        "learning_objective": OBJECTIVE,
        "steps": [],
    }
    mission.update(overrides)
    return mission


def _body(rendered: str) -> str:
    """Just the content block.

    Both templates define their objective styling in ``{% block extra_css %}``, so
    the class name is in the page whether or not any card uses it. Asserting the
    *absence* of the block therefore has to look at markup, not at the stylesheet —
    otherwise "no objective was rendered" is untestable.
    """
    return rendered.split("<body>", 1)[1]


def _card_html(env, **overrides) -> str:
    """Render the mission browser (d3) around a single mission."""
    mission = _mission(**overrides)
    return _body(env.get_template("forge_academy/missions.html").render(
        fa_user={"id": 1, "role": "swe"},
        missions=[mission],
        progress_map={},
        prereq_state={},
        tier_info={2: {"unlocked": True}},
        level_ctx={"earned_xp": 0, "level": {"label": "Recruit"}, "pct": 0},
        roles=["swe"],
        active_tier=None,
        active_topic="",
        active_type="",
        error=None,
    ))


def _runner_html(env, *, tier_locked: bool = False, steps: list | None = None,
                 **overrides) -> str:
    """Render the mission runner (d4).

    Defaults to one watch step so "above the first step" is a claim with something
    below it. The step partials are exercised by their own step-type tests; this
    file only needs one pane to exist so ordering can be asserted.
    """
    if steps is None:
        steps = [{
            "id": 101,
            "step_num": 1,
            "title": "Read the retrieval trace",
            "step_type": "watch",
            "content_md": "<p>Trace walkthrough.</p>",
            # The keys _step_watch.html reads. Spelled out because the env is
            # strict: an absent key is an error here where production Jinja would
            # render it away, and this file is not the place that decides how a
            # watch step behaves without a demo (fga-fix-02 owns that).
            "config_schema": {"demo_output": None, "demo_url": None},
        }]
    mission = _mission(steps=steps, **overrides)
    return _body(env.get_template("forge_academy/mission.html").render(
        fa_user={"id": 1, "role": "swe"},
        mission=mission,
        tier_locked=tier_locked,
        tier_state={"unlocked": not tier_locked, "gating_tier": 1, "required_pct": 70},
        gating_state={"pct": 40, "completed": 4, "completable": 10, "total": 10},
        steps_client=[{"id": s["id"], "step_type": s["step_type"]} for s in steps],
        assessed_step_ids=set(),
        step_states={},
        level_ctx={"earned_xp": 0, "level": {"label": "Recruit"}, "pct": 0},
    ))


# ---------------------------------------------------------------------------
# d3 — the mission card
# ---------------------------------------------------------------------------

def test_the_card_states_what_the_mission_teaches(env):
    """The card advertised three costs and never the outcome."""
    html = _card_html(env)
    assert OBJECTIVE in html
    assert "Objective" in html


def test_the_card_marks_the_objective_as_its_own_thing(env):
    """Not a second tagline — an auditor has to be able to find it."""
    html = _card_html(env)
    assert "fa-card-objective" in html


def test_the_objective_is_not_the_tagline(env):
    """Both render, and the objective is the one that is not marketing copy."""
    html = _card_html(env)
    assert TAGLINE in html
    assert html.index(TAGLINE) < html.index(OBJECTIVE), (
        "the objective belongs above the cost badges and below the tagline"
    )


def test_a_mission_with_no_authored_objective_shows_no_objective_line(env):
    """NULL is the honest state for 68 of 116 missions; it must render as nothing.

    Not an empty label, not a bare rule. An absent objective is a visible content
    gap someone can go fix; a decorative empty one hides it.
    """
    html = _card_html(env, learning_objective=None)
    assert "fa-card-objective" not in html
    assert TAGLINE in html, "the rest of the card still renders"


def test_an_empty_objective_is_treated_as_no_objective(env):
    """A backfill that writes '' rather than NULL must not print an empty label."""
    html = _card_html(env, learning_objective="")
    assert "fa-card-objective" not in html


def test_authored_markup_in_an_objective_is_escaped_not_executed(env):
    """The objective is extracted from authored markdown, so it is untrusted text."""
    html = _card_html(env, learning_objective="Explain <script>alert(1)</script> injection")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_card_route_missing_the_field_entirely_is_caught_not_silent(env):
    """The regression this file exists for.

    Under Jinja's default Undefined a route that stops passing the objective
    renders a card that simply has no objective on it — indistinguishable from a
    mission that states none. StrictUndefined turns that into the error it is.
    """
    mission = _mission()
    del mission["learning_objective"]
    with pytest.raises(Exception) as exc:
        env.get_template("forge_academy/missions.html").render(
            fa_user={"id": 1, "role": "swe"},
            missions=[mission],
            progress_map={},
            prereq_state={},
            tier_info={2: {"unlocked": True}},
            level_ctx={"earned_xp": 0, "level": {"label": "Recruit"}, "pct": 0},
            roles=["swe"],
            active_tier=None,
            active_topic="",
            active_type="",
            error=None,
        )
    assert "learning_objective" in str(exc.value)


# ---------------------------------------------------------------------------
# d4 — the mission runner
# ---------------------------------------------------------------------------

def test_the_runner_states_the_objective_before_the_first_step(env):
    """A learner started step 1 without ever being told what the mission is for."""
    html = _runner_html(env)
    assert OBJECTIVE in html
    assert html.index(OBJECTIVE) < html.index('id="step-pane-0"'), (
        "the objective must precede the first step pane, not follow the last one"
    )


def test_the_runner_labels_the_objective(env):
    html = _runner_html(env)
    assert "Learning objective" in html
    assert "fa-mission-objective" in html


def test_the_runner_omits_the_objective_when_the_content_states_none(env):
    html = _runner_html(env, learning_objective=None)
    assert "fa-mission-objective" not in html
    assert 'id="step-pane-0"' in html, "the rest of the runner still renders"


def test_a_tier_locked_mission_still_states_its_objective_first(env):
    """The stated reason the panel sits above the locked notice (aca-ux-04).

    "Readable but earns no XP" is only a decision a learner can make if they know
    what the mission teaches before they read the refusal.
    """
    html = _runner_html(env, tier_locked=True)
    assert OBJECTIVE in html
    assert "is not unlocked yet" in html
    assert html.index(OBJECTIVE) < html.index("is not unlocked yet")


def test_the_runner_escapes_authored_markup_too(env):
    html = _runner_html(env, learning_objective="Explain <img src=x onerror=1> handling")
    assert "<img src=x onerror=1>" not in html
    assert "&lt;img" in html


def test_a_runner_route_missing_the_field_entirely_is_caught_not_silent(env):
    mission = _mission(steps=[])
    del mission["learning_objective"]
    with pytest.raises(Exception) as exc:
        env.get_template("forge_academy/mission.html").render(
            fa_user={"id": 1, "role": "swe"},
            mission=mission,
            tier_locked=False,
            tier_state={"unlocked": True, "gating_tier": 1, "required_pct": 70},
            gating_state={},
            steps_client=[],
            assessed_step_ids=set(),
            step_states={},
            level_ctx={"earned_xp": 0, "level": {"label": "Recruit"}, "pct": 0},
        )
    assert "learning_objective" in str(exc.value)


# ---------------------------------------------------------------------------
# The route has to supply what the template reads
# ---------------------------------------------------------------------------

def test_the_browser_route_hands_the_template_whole_mission_rows():
    """``list_missions`` projects nothing away, so the column reaches the card.

    The JSON path is allowlisted (``_LEARNER_MISSION_FIELDS``) and tested
    separately; the Jinja path reads the row directly, and that is only true while
    the query stays unprojected.
    """
    import inspect

    from apps.forge_academy import db as fadb

    src = inspect.getsource(fadb.list_missions)
    assert "SELECT * FROM fa_missions" in src, (
        "a column list here would drop learning_objective from the card silently"
    )


def test_the_runner_route_hands_the_template_the_whole_mission_row():
    import inspect

    from apps.forge_academy import db as fadb

    assert "SELECT * FROM fa_missions" in inspect.getsource(fadb.get_mission)
