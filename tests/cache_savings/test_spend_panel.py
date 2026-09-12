# CUI // SP-CTI
"""The Spend panel on /cache-savings (xrv-cost-04).

WHAT THESE PIN, and why each one could go wrong QUIETLY:

  1. AN EMPTY BOARD IS UNMEASURABLE, IN WORDS. Every figure is ``None`` and the
     panel says "no attributed dispatches in the window". A cost surface that
     renders ``$0.00`` over a board nothing attributed is claiming the work was
     free, which is the ``args/perfect_score_gate.yaml`` defect wearing a dollar
     sign -- and unlike a missing number, a zero closes the question.

  2. ``unpriced`` IS NOT ``$0`` AND IS NOT ``unmeasurable``. ``record_task_cost``
     writes the envelope's ``total_cost_usd`` and falls back to 0.0 when the
     envelope carries no cost field, so an ABSENT price reaches the ledger as a
     zero. Summed naively that understates the bill in the direction that makes
     work look cheap. A dispatch with no price still SHIPPED or was still
     ABANDONED, so it is counted apart from the verdict rather than inside it.

  3. THE ROUTE IS GET ONLY. No POST sibling anywhere in the module, asserted the
     way ``tests/test_merge_readiness_surface.py`` asserts it, and nothing in the
     handler's EXECUTABLE code mutates anything.

  4. THE PANEL COMPUTES NO COST OF ITS OWN. ``spend.py`` reaches the ledger only
     through ``task_attribution``; a private cursor here is how a panel comes to
     disagree with the CLI about a number neither of them changed.

The page render goes through a real Flask ``test_client`` and never
``test_request_context``: the latter runs no ``before_request``, so the security
context never attaches and an RLS defect reproduces as a PASSING test.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from flask import Flask

from tools.cache_savings import spend as sp
from tools.cost import task_attribution as ta

REPO = Path(__file__).resolve().parents[2]
SPEND_PY = REPO / "tools" / "cache_savings" / "spend.py"
BLUEPRINT_PY = REPO / "tools" / "cache_savings" / "blueprint.py"
TEMPLATE = REPO / "tools" / "dashboard" / "templates" / "cache_savings" / "page.html"
MIRROR = REPO / "icdev" / "tools" / "dashboard" / "templates" / "cache_savings" / "page.html"


@pytest.fixture(autouse=True)
def _no_cache_between_tests():
    """The 120s cache is process-global; a test that inherited another's
    payload would pass for the wrong reason."""
    sp.reset_cache()
    yield
    sp.reset_cache()


def _survey(**over):
    """A ``survey_by_verdict`` report, measured, with one shipped card."""
    base = {
        "state": "measured",
        "window_days": 7.0,
        "forge_consulted": False,
        "generated_at": "2026-09-12T00:00:00+00:00",
        "tasks": 1,
        "total_cost_usd": 12.5,
        "measured_cost_usd": 12.5,
        "unmeasurable_cost_usd": None,
        "shipped_cost_share_pct": 100.0,
        "unpriced_dispatches": 0,
        "unpriced_tasks": 0,
        "by_verdict": {
            v: {"tasks": 0, "dispatches": 0, "cost_usd": None, "task_ids": [],
                "unpriced_dispatches": 0} for v in ta.VERDICTS
        },
        "tasks_detail": [{
            "task_id": "xrv-cost-04", "verdict": "shipped", "reasons": ["landed:merge_ref"],
            "status": "done", "cost_usd": 12.5, "cost_basis": "priced",
            "dispatches": 1, "unpriced_dispatches": 0,
            "models": ["claude-opus-5"], "last_at": "2026-09-12T00:00:00",
        }],
    }
    base["by_verdict"]["shipped"] = {"tasks": 1, "dispatches": 1, "cost_usd": 12.5,
                                     "task_ids": ["xrv-cost-04"], "unpriced_dispatches": 0}
    base.update(over)
    return base


# -- 1. the empty board ------------------------------------------------------

def test_an_unattributed_window_is_unmeasurable_and_never_zero_dollars():
    """The wording is load-bearing: a reader must be able to tell "nobody
    recorded what this cost" from "this cost nothing"."""
    payload = sp.shape({"state": "unmeasurable", "reason": "no_attributed_rows",
                        "tasks": 0}, window_days=7.0)

    assert payload["state"] == "unmeasurable"
    assert payload["headline"] == sp.EMPTY_HEADLINE == \
        "no attributed dispatches in the window"
    for field in ("total_cost_usd", "measured_cost_usd", "shipped_cost_share_pct",
                  "unpriced_dispatches", "unmeasurable_tasks"):
        assert payload[field] is None, field
    # `tasks_shown` IS legitimately 0 -- zero rows were rendered, which is a
    # measured fact about the table and not a claim about money. The rule is
    # about the figures a reader would quote, so it is asserted on those.
    assert payload["tasks_shown"] == 0


def test_every_verdict_row_is_present_even_when_nothing_was_measured():
    """A verdict MISSING from the table is indistinguishable from one that
    measured zero, so all five always render -- with None, not 0."""
    payload = sp.shape({"state": "unmeasurable", "reason": "no_attributed_rows"},
                       window_days=7.0)
    assert [r["verdict"] for r in payload["by_verdict"]] == list(sp.VERDICT_ORDER)
    assert set(sp.VERDICT_ORDER) == set(ta.VERDICTS), "the closed set must not drift"
    for row in payload["by_verdict"]:
        assert row["tasks"] is None and row["cost_usd"] is None
        assert row["note"], "a bare label invites the reader to invent the meaning"


def test_an_unreadable_ledger_and_an_empty_one_do_not_read_the_same():
    unreadable = sp.shape({"state": "unmeasurable", "reason": "ledger_unreadable"},
                          window_days=7.0)
    empty = sp.shape({"state": "unmeasurable", "reason": "no_attributed_rows"},
                     window_days=7.0)
    assert unreadable["reason"] != empty["reason"]
    assert unreadable["headline"] != empty["headline"]


# -- 2. unpriced is its own thing --------------------------------------------

def test_a_dispatch_with_tokens_and_no_dollars_is_unpriced_not_free():
    assert ta.row_is_unpriced({"cost_estimate_usd": None, "input_tokens": 10})
    assert ta.row_is_unpriced({"cost_estimate_usd": 0.0, "input_tokens": 28_000_000,
                               "output_tokens": 100})
    assert not ta.row_is_unpriced({"cost_estimate_usd": 12.5, "input_tokens": 10})
    # A row with NO tokens is never written at all (`envelope_has_usage`), so a
    # zero-cost zero-token row is not the unpriced class.
    assert not ta.row_is_unpriced({"cost_estimate_usd": 0.0, "input_tokens": 0,
                                   "output_tokens": 0})


def test_a_wholly_unpriced_card_reports_no_cost_rather_than_zero():
    survey = _survey(
        total_cost_usd=None, measured_cost_usd=0.0, shipped_cost_share_pct=None,
        unpriced_dispatches=2, unpriced_tasks=1,
        tasks_detail=[{
            "task_id": "xrv-cost-04", "verdict": "shipped", "reasons": [],
            "status": "done", "cost_usd": None, "cost_basis": "unpriced",
            "dispatches": 2, "unpriced_dispatches": 2, "models": [], "last_at": "",
        }],
    )
    payload = sp.shape(survey, window_days=7.0)

    assert payload["state"] == "measured", "the OUTCOME was measured; the price was not"
    assert payload["total_cost_usd"] is None
    assert payload["by_task"][0]["cost_usd"] is None
    assert payload["by_task"][0]["cost_basis"] == "unpriced"
    assert payload["unpriced_dispatches"] == 2
    # It is NOT folded into `unmeasurable`: the card shipped.
    assert payload["unmeasurable_tasks"] == 0
    assert "no priced spend" in payload["headline"]


def test_an_unpriced_card_sorts_last_not_as_the_cheapest():
    """None is not zero, so it must not win a "costliest first" sort by being
    treated as the smallest number."""
    survey = _survey(tasks=3, tasks_detail=[
        {"task_id": "a", "verdict": "shipped", "cost_usd": None, "cost_basis": "unpriced",
         "dispatches": 1, "unpriced_dispatches": 1, "models": [], "last_at": ""},
        {"task_id": "b", "verdict": "shipped", "cost_usd": 1.0, "cost_basis": "priced",
         "dispatches": 1, "unpriced_dispatches": 0, "models": [], "last_at": ""},
        {"task_id": "c", "verdict": "shipped", "cost_usd": 9.0, "cost_basis": "priced",
         "dispatches": 1, "unpriced_dispatches": 0, "models": [], "last_at": ""},
    ])
    assert [r["task_id"] for r in sp.shape(survey, window_days=7.0)["by_task"]] \
        == ["c", "b", "a"]


def test_the_unmeasurable_bucket_is_outside_the_share_denominator():
    """Sharing against a total that includes spend nobody could judge is a
    share of nothing knowable -- the same rule `shipped_cost_share_pct` uses."""
    survey = _survey(tasks=2, total_cost_usd=20.0, measured_cost_usd=12.5)
    survey["by_verdict"]["unmeasurable"] = {"tasks": 1, "dispatches": 1, "cost_usd": 7.5,
                                            "task_ids": ["x"], "unpriced_dispatches": 0}
    rows = {r["verdict"]: r for r in sp.shape(survey, window_days=7.0)["by_verdict"]}
    assert rows["shipped"]["cost_share_pct"] == 100.0     # 12.5 of 12.5 measured
    assert rows["unmeasurable"]["cost_share_pct"] is None
    assert rows["unmeasurable"]["cost_usd"] == 7.5        # the dollars are still shown


def test_a_share_is_none_over_an_empty_denominator():
    assert sp._share(None, 10.0) is None
    assert sp._share(5.0, 0) is None
    assert sp._share(5.0, 10.0) == 50.0


# -- 3. the route ------------------------------------------------------------

def test_the_api_returns_the_shape_with_none_not_zero_over_an_empty_board(monkeypatch):
    monkeypatch.setattr(ta, "survey_by_verdict",
                        lambda **kw: {"state": "unmeasurable",
                                      "reason": "no_attributed_rows", "tasks": 0,
                                      "total_cost_usd": None, "by_verdict": None,
                                      "shipped_cost_share_pct": None})
    app = Flask(__name__)
    from tools.cache_savings.blueprint import bp
    app.register_blueprint(bp)
    with app.test_client() as c:
        payload = c.get("/api/cache-savings/spend").get_json()

    assert payload["state"] == "unmeasurable"
    assert payload["headline"] == sp.EMPTY_HEADLINE
    assert payload["total_cost_usd"] is None
    assert payload["ok"] is True and payload["error"] is None
    assert payload["cache_ttl_seconds"] == 120


def test_the_api_carries_a_measured_window_and_its_cache_age(monkeypatch):
    monkeypatch.setattr(ta, "survey_by_verdict", lambda **kw: _survey())
    app = Flask(__name__)
    from tools.cache_savings.blueprint import bp
    app.register_blueprint(bp)
    with app.test_client() as c:
        payload = c.get("/api/cache-savings/spend?window_days=7").get_json()

    assert payload["state"] == "measured"
    assert payload["total_cost_usd"] == 12.5
    assert payload["by_task"][0]["task_id"] == "xrv-cost-04"
    assert payload["cache_age_seconds"] >= 0
    assert payload["window_days"] == 7.0


def test_a_malformed_window_is_a_400_and_not_a_silent_default():
    app = Flask(__name__)
    from tools.cache_savings.blueprint import bp
    app.register_blueprint(bp)
    with app.test_client() as c:
        assert c.get("/api/cache-savings/spend?window_days=abc").status_code == 400
        assert c.get("/api/cache-savings/spend?window_days=0").status_code == 400
        assert c.get("/api/cache-savings/spend?window_days=-3").status_code == 400


def test_the_spend_route_is_get_only_and_mutates_nothing():
    """No POST sibling on the path anywhere in the module, and nothing in the
    handler's EXECUTABLE code writes. Comments are stripped before scanning, so
    a comment EXPLAINING that nothing here writes cannot fail its own test."""
    src = BLUEPRINT_PY.read_text(encoding="utf-8")
    assert '@bp.route("/api/cache-savings/spend")' in src
    assert not re.search(
        r'@bp\.route\(\s*"/api/cache-savings/spend[^"]*"\s*,\s*methods', src)

    tree = ast.parse(src)
    body = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "api_cache_savings_spend":
            stmts = list(node.body)
            if (stmts and isinstance(stmts[0], ast.Expr)
                    and isinstance(stmts[0].value, ast.Constant)):
                stmts = stmts[1:]
            body = "\n".join(ast.unparse(s) for s in stmts)
    assert body, "the route is not defined"
    for forbidden in ("INSERT", "UPDATE", "DELETE", "commit", "record_task_cost",
                      "log_usage", "move_task", "set_status"):
        assert forbidden not in body, forbidden


def test_a_failed_fetch_is_reported_and_never_rendered_as_an_empty_board():
    """A panel must not 500 -- and must not present a failure as a clean board."""
    def boom(**kw):
        raise RuntimeError("git unreachable")

    payload = sp.spend_panel(7.0, survey_fn=boom)
    assert payload["ok"] is False
    assert "git unreachable" in payload["error"]
    assert payload["state"] == "unmeasurable"
    assert payload["total_cost_usd"] is None
    assert payload["headline"] != sp.EMPTY_HEADLINE, (
        "'could not measure' must not read as 'nothing was dispatched'")


def test_the_cache_serves_one_fetch_per_ttl_and_ages_it():
    calls = {"n": 0}

    def counting(**kw):
        calls["n"] += 1
        return _survey()

    first = sp.spend_panel(7.0, survey_fn=counting, ttl=120.0, now=1000.0)
    again = sp.spend_panel(7.0, survey_fn=counting, ttl=120.0, now=1060.0)
    assert calls["n"] == 1, "the page render and the API poll share one sweep"
    assert first["cache_age_seconds"] == 0 and again["cache_age_seconds"] == 60

    sp.spend_panel(7.0, survey_fn=counting, ttl=120.0, now=1121.0)
    assert calls["n"] == 2, "the TTL must actually expire"


def test_a_stale_payload_is_served_with_its_error_beside_it():
    """A stale answer presented as CURRENT is the defect this surface refuses."""
    state = {"fail": False}

    def flaky(**kw):
        if state["fail"]:
            raise RuntimeError("board down")
        return _survey()

    sp.spend_panel(7.0, survey_fn=flaky, ttl=10.0, now=1000.0)
    state["fail"] = True
    stale = sp.spend_panel(7.0, survey_fn=flaky, ttl=10.0, now=1020.0)

    assert stale["total_cost_usd"] == 12.5, "the last good report is kept"
    assert stale["ok"] is False and "board down" in stale["error"]
    assert stale["cache_age_seconds"] == 0, "the failed fetch re-stamps the clock"


# -- 4. no second reader of the ledger ---------------------------------------

def test_the_panel_module_never_reaches_the_database_itself():
    """Every figure comes through `task_attribution`. A private cursor here is
    how a panel and a CLI come to disagree about a number neither changed.

    Read from the AST rather than from behaviour: today's code path is clean,
    and the failure mode is a future edit adding a convenience SELECT that a
    behavioural test over the current callers would still pass.

    Scanned over the EXECUTABLE code, never the raw source. The module docstring
    NAMES `agent_token_usage` in order to say that it does not read it, and a
    naive substring scan would flag the module's own explanation of itself --
    which teaches the next reader to delete the explanation (dwr-ev-01)."""
    tree = ast.parse(SPEND_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            module = getattr(node, "module", "") or ""
            assert "storage" not in module, module
            assert "get_connection" not in names, names
        if isinstance(node, ast.Attribute) and node.attr in ("execute", "cursor"):
            raise AssertionError("spend.py must not run SQL of its own")

    # Docstrings stripped, then unparsed: what is scanned is what RUNS.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:]
    code = ast.unparse(tree)
    for forbidden in ("SELECT ", "INSERT ", "UPDATE ", "agent_token_usage"):
        assert forbidden not in code, forbidden


def test_the_panel_asks_the_survey_without_consulting_the_forge():
    """A PR lookup per task would put a network round-trip on a page render."""
    seen = {}

    def capture(**kw):
        seen.update(kw)
        return _survey()

    sp.collect(7.0, survey_fn=capture)
    assert seen["consult_forge"] is False
    assert seen["window_days"] == 7.0


# -- 5. the page ------------------------------------------------------------

def test_the_template_renders_the_section_and_its_mirror_is_in_parity():
    page = TEMPLATE.read_text(encoding="utf-8")
    assert "Spend by Card" in page
    assert "/api/cache-savings/spend" in page
    assert "cache.spend" in page, "the two IQE seed queries"
    # The empty state is in WORDS on the page, not only in the payload.
    assert "not an absence of spending" in page
    assert MIRROR.read_text(encoding="utf-8") == page, (
        "run: python tools/dx/mirror_parity.py --files "
        "tools/dashboard/templates/cache_savings/page.html --fix")


def _page_app(monkeypatch, survey):
    """A Flask app that serves the REAL page template over a stub ``base.html``.

    The page is reached through a real ``test_client`` request and never through
    ``test_request_context``: the latter runs no ``before_request``, so the
    security context never attaches and an RLS defect reproduces as a PASSING
    test. ``base.html`` itself is stubbed because it needs the dashboard's own
    context processors -- the chrome is the Playwright smoke's job; this asserts
    the SECTION.
    """
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    monkeypatch.setattr(ta, "survey_by_verdict", lambda **kw: survey)
    import tools.cache_savings.savings as sv
    monkeypatch.setattr(sv, "get_savings_stats", lambda *a, **k: {
        "enabled": True, "backend": "sqlite", "state": "cold", "state_detail": "",
        "stored_entries": 0,
        "summary": {"hit_rate_pct": None, "total_entries": 0, "total_hits": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "total_usd_saved": 0.0, "resp_cache_usd_saved": 0.0,
                    "context_cache_usd_saved": 0.0},
        "by_function": [],
    })

    from tools.cache_savings.blueprint import bp
    app = Flask(__name__)
    app.jinja_loader = ChoiceLoader([
        DictLoader({
            "base.html": "{% block title %}{% endblock %}{% block content %}{% endblock %}",
            "includes/iqe_query_widget.html": "<!-- iqe -->",
        }),
        FileSystemLoader(str(REPO / "tools" / "dashboard" / "templates")),
    ])
    app.register_blueprint(bp)
    return app


def test_the_page_renders_measured_spend_through_a_real_request(monkeypatch):
    app = _page_app(monkeypatch, _survey())
    with app.test_client() as c:
        response = c.get("/cache-savings")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Spend by Card" in html
    assert "xrv-cost-04" in html and "$12.5000" in html
    assert "Attributed Spend" in html and "Spend That Shipped" in html
    assert "Showing 1 of 1 attributed card" in html


def _visible(html: str) -> str:
    """Rendered TEXT, tags dropped. A `$`/`%` assertion over raw markup reads
    `width:100%` out of a style attribute and fails for the one reason the
    assertion is not about."""
    return re.sub(r"<[^>]+>", " ", html)


def _unmeasured_section(monkeypatch, reason="no_attributed_rows"):
    app = _page_app(monkeypatch, {"state": "unmeasurable", "reason": reason,
                                  "tasks": 0, "total_cost_usd": None,
                                  "by_verdict": None,
                                  "shipped_cost_share_pct": None})
    with app.test_client() as c:
        html = c.get("/cache-savings").get_data(as_text=True)
    return html.split("Spend by Card", 1)[1].split("<!-- iqe -->", 1)[0]


def test_the_page_says_unmeasurable_in_words_over_an_empty_board(monkeypatch):
    """The panel must never render `$0.00` for a board nothing attributed."""
    section = _unmeasured_section(monkeypatch)
    assert "Unmeasurable" in section
    assert sp.EMPTY_HEADLINE in section
    assert "not an absence of spending" in section
    assert "$0.00" not in section and "0.0000" not in section


def test_the_closed_set_renders_on_the_PAGE_and_not_only_in_the_API(monkeypatch):
    """``shape`` already hands the template all five rows with every figure
    ``None`` (``_empty_verdicts``) and the API serves them -- and until
    xrv-cost-06 the template threw them away behind an ``{% else %}``, so the
    JSON said five outcomes and the page said none. One surface, two answers.

    A verdict absent from the table is indistinguishable from one that measured
    zero, and that argument does not stop applying on the board where NOTHING
    was measured -- it is the board where it applies most.
    """
    section = _unmeasured_section(monkeypatch)
    for label in sp.VERDICT_LABELS.values():
        assert label in section, "outcome missing from the unmeasured table: %s" % label
    for note in sp.VERDICT_NOTES.values():
        assert note.split(" - ")[0][:40] in section, (
            "a bare label invites the reader to invent the meaning: %s" % note)
    for kpi in ("Attributed Spend", "Spend That Shipped", "Unpriced Dispatches",
                "Unmeasurable Cards"):
        assert kpi in section, "KPI missing from the unmeasured panel: %s" % kpi
    # ...and the whole point of rendering them: not one carries a figure. Read
    # the rendered TEXT -- `width:100%` in a style attribute is not a figure.
    text = _visible(section)
    assert "$" not in text, "an unmeasured panel drew a dollar figure"
    assert not re.search(r"\d\s*%", text), "an unmeasured panel drew a percentage"


def test_a_kpi_caption_does_not_claim_dispatches_ran_over_an_unattributed_board(
        monkeypatch):
    """`total_cost_usd is none` means two DIFFERENT things and they send a
    reader to different fixes: in the MEASURED state dispatches ran and every
    one was unpriced (the `unpriced_dispatches` finding), in the UNMEASURABLE
    state nothing was attributed at all. The em-dash is right either way; the
    caption under it is not."""
    section = _unmeasured_section(monkeypatch)
    assert "no dispatch reported a price" not in section, (
        "the unpriced caption claims dispatches ran and were unpriced")
    assert "ran, but reported no dollars" not in section
    assert sp.UNATTRIBUTED_CAPTION in section


def test_the_row_count_footnote_is_not_asserted_over_an_unreadable_ledger(
        monkeypatch):
    """"Showing 0 of 0 attributed card(s)" is TRUE when the window held no
    attributed rows and FALSE when the ledger could not be read -- there, how
    many cards were attributed is exactly what is unknown."""
    unreadable = _unmeasured_section(monkeypatch, reason="ledger_unreadable")
    assert "Showing 0 of 0" not in unreadable
    assert "costliest first" not in unreadable


def test_the_page_route_passes_the_panel_into_the_template():
    """The section can be perfect and render nothing if the route never hands
    it over -- the gap no unit test of either half alone can see."""
    tree = ast.parse(BLUEPRINT_PY.read_text(encoding="utf-8"))
    rendered = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "render_template"
                and any(kw.arg == "spend" for kw in node.keywords)):
            rendered = True
    assert rendered, "cache_savings_page must pass spend=... to the template"
