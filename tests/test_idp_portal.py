# CUI // SP-CTI
"""Tests for the Internal Developer Portal surface (idp-ui-02).

Two things are being pinned here.

**The gate.** The portal is a new dashboard page, so CLAUDE.md's 8-point
completeness gate applies in full. Asserting ``validate_canvas_completeness``
alone would be circular — that function reads the very registry block the page
declares — so each point is also asserted against the filesystem and against
the derived wiring (IQE dispatch map, PATH_CANVAS regex, nav tree, CLI toggle),
which is what would actually break if the registry entry regressed.

**The dogfood.** The portal must appear in its own catalog and pass its own
scorecard's completeness rule. A component catalog that cannot find itself is
not a catalog, and a scorecard whose own surface is exempt has never been run
against anything that matters.

No DB fixture: every fact here derives from the registry, the repo tree and
``args/scorecards/*.yaml``. The optional backing tables are asserted to degrade
gracefully when absent, which is the state these tests run in.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.config.component_registry import get_registry, validate_canvas_completeness
from tools.idp import portal
from tools.idp.constants import DEFAULT_SCORECARD_KEY, IQE_API_ROUTE, IQE_COLLECTION

REPO_ROOT = Path(__file__).resolve().parent.parent
KEY = portal.SELF_KEY  # "idp"


@pytest.fixture(scope="module")
def registry():
    return get_registry()


@pytest.fixture(scope="module")
def facts():
    """Fact rows for every component, computed once per module.

    Collecting them walks the tree and AST-parses every canvas blueprint, so
    the adapter memoizes. Reset first: another test module may have populated
    the cache from a different tree state.
    """
    from tools.iqe.adapters.idp import reset_cache

    reset_cache()
    rows = portal.component_facts()
    reset_cache()
    return rows


@pytest.fixture(scope="module")
def report():
    return portal.scorecard_report(DEFAULT_SCORECARD_KEY)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_portal_is_registered_as_a_canvas(registry):
    """Only ``kind: canvas`` is in scope for the 8-point gate."""
    comp = registry.get(KEY)
    assert comp is not None, "idp must be declared in args/component_registry.yaml"
    assert comp.kind == "canvas"
    assert comp.url_prefix == "/idp"
    assert comp.module == "tools.idp.blueprint"


def test_registration_is_derived_not_hardcoded(registry):
    """Blueprint, nav, toggle, IQE dispatch and PATH_CANVAS all come from YAML.

    The CLAUDE.md registry rule forbids adding Python lists to app.py,
    cli/enable.py or base.html for a new component. If any of these assertions
    fails, something was hardcoded instead.
    """
    assert registry.get_iqe_mapping().get(KEY) == (
        "tools.iqe.adapters.idp",
        [IQE_COLLECTION],
    )
    assert (f"^/{KEY}", KEY) in registry.get_iqe_path_canvas()
    assert registry.get_cli_toggles().get(KEY) == ["ICDEV_IDP_ENABLED"]
    labels = [
        g["label"] for g in registry.get_nav_context()["sections"]["Canvases"]["groups"]
    ]
    assert "Developer Portal" in labels


# ---------------------------------------------------------------------------
# The 8 points
# ---------------------------------------------------------------------------


def test_all_eight_completeness_points_are_present():
    """Every point present — not merely "passing because it was optional"."""
    rep = validate_canvas_completeness(KEY)
    assert rep.passed, [i.message for i in rep.items if i.required and not i.present]
    assert len(rep.items) == 8
    missing = [i.point for i in rep.items if not i.present]
    assert not missing, f"points declared but not present: {missing}"


@pytest.mark.parametrize(
    "relpath",
    [
        "tools/dashboard/templates/idp/page.html",          # 1 template
        "icdev/tools/dashboard/templates/idp/page.html",    # 2 icdev mirror
        "tools/idp/blueprint.py",                           # 3 route
        "tools/idp/portal.py",                              # 4 backing module
        "tools/idp/constants.py",                           # 5 constants
        "tools/idp/db/init_db.py",                          # 6 DB dependency
        "tools/iqe/adapters/idp.py",                        # 8 IQE adapter
        "icdev/tools/iqe/adapters/idp.py",                  # 8 mirror-parity root
    ],
)
def test_gate_file_exists_on_disk(relpath):
    """Filesystem check, independent of the validator's own fallback logic."""
    assert (REPO_ROOT / relpath).is_file(), f"missing {relpath}"


def test_template_and_icdev_mirror_are_identical():
    """A mirror that drifts is worse than no mirror — it hides the drift."""
    a = (REPO_ROOT / "tools/dashboard/templates/idp/page.html").read_text(encoding="utf-8")
    b = (REPO_ROOT / "icdev/tools/dashboard/templates/idp/page.html").read_text(encoding="utf-8")
    assert a == b


def test_blueprint_declares_the_iqe_query_route():
    src = (REPO_ROOT / "tools/idp/blueprint.py").read_text(encoding="utf-8")
    assert '@bp.route("/api/iqe-query", methods=["POST"])' in src
    assert '@bp.route("/")' in src


def test_page_includes_the_iqe_query_widget():
    """Gate point 8 asks for the widget include, not just the adapter."""
    html = (REPO_ROOT / "tools/dashboard/templates/idp/page.html").read_text(encoding="utf-8")
    assert '{% include "includes/iqe_query_widget.html" %}' in html
    assert IQE_API_ROUTE == "/idp/api/iqe-query"


def test_at_least_three_seed_queries():
    seeds = sorted((REPO_ROOT / "context/iqe/queries/idp").glob("*.yaml"))
    assert len(seeds) >= 3, f"gate asks for >=3 seed queries, found {len(seeds)}"
    import yaml

    for path in seeds:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert data.get("collection") == IQE_COLLECTION, path.name
        assert data.get("query"), path.name


def test_e2e_spec_exists():
    """The Gold ``e2e-spec`` rule globs ``tests/e2e/idp*.spec.ts``."""
    assert sorted((REPO_ROOT / "tests/e2e").glob("idp*.spec.ts"))


# ---------------------------------------------------------------------------
# Dogfood
# ---------------------------------------------------------------------------


def test_portal_appears_in_its_own_catalog(facts, report):
    rows = portal.build_catalog(facts, report)
    keys = [r["key"] for r in rows]
    assert KEY in keys, "the portal is missing from the catalog it renders"
    assert len(rows) == len(facts)


def test_portal_passes_its_own_completeness_rule(report):
    """The scorecard rule this task exists to satisfy, evaluated for real."""
    assert not report.get("error"), report.get("error")
    result = next(r for r in report["results"] if r["entity"] == KEY)
    outcome = next(o for o in result["rules"] if o["identifier"] == "completeness-gate")
    assert outcome["status"] == "pass", outcome


def test_self_check_agrees_with_the_validator(facts, report):
    rows = portal.build_catalog(facts, report)
    check = portal.self_check(rows=rows, report=report)
    assert check["in_catalog"] is True
    assert check["completeness_passed"] is True
    assert check["completeness"]["declared"] is True
    assert len(check["completeness"]["items"]) == 8


def test_portal_reports_its_own_ownership_gap(facts):
    """The portal must not exempt itself from the rule it grades others on.

    ICDEV declares no owner for any component (no CODEOWNERS, no roster), and
    naming a placeholder here would make the one surface whose job is to report
    that gap the one surface that lies about it. So this asserts the honest
    state, and it is the assertion that should be *changed* — not deleted —
    when a real owner is assigned.
    """
    row = next(f for f in facts if f["key"] == KEY)
    assert row["has_owner"] is False
    assert row["owner"] == ""


# ---------------------------------------------------------------------------
# View-model behaviour
# ---------------------------------------------------------------------------


def test_ungraded_component_keeps_a_null_score_not_a_zero():
    """A zero is indistinguishable from a real failing grade; None is not."""
    facts = [{"key": "ghost", "display_name": "Ghost", "kind": "feature"}]
    rows = portal.build_catalog(facts, {"results": [], "ladder": []})
    assert rows[0]["score"] is None
    assert rows[0]["level"] is None
    assert rows[0]["graded"] is False


def test_group_by_kind_renders_an_unknown_kind():
    """A kind added to the registry must appear the day it is added."""
    rows = portal.build_catalog(
        [
            {"key": "a", "kind": "canvas"},
            {"key": "b", "kind": "brand_new_kind"},
        ],
        {"results": [], "ladder": []},
    )
    groups = {g["kind"]: g for g in portal.group_by_kind(rows)}
    assert "brand_new_kind" in groups
    assert groups["brand_new_kind"]["label"] == "brand_new_kind"
    assert groups["canvas"]["label"] == "Canvases"


def test_scorecard_failure_degrades_instead_of_raising():
    """A page that 500s tells on-call less than one that names what is dark."""
    result = portal.scorecard_report("no-such-scorecard-anywhere")
    assert result["error"]
    assert result["results"] == []


def test_schema_status_reports_absent_tables_without_raising():
    """Point 6: absent backing tables render as "not measured", never as a pass."""
    rows = portal.schema_status()
    assert {r["table"] for r in rows} >= {"developer_scorecards", "awareness_component_health"}
    for row in rows:
        assert isinstance(row["present"], bool)
        assert row["purpose"]


def test_component_detail_reports_a_missing_component():
    detail = portal.component_detail("definitely-not-a-component")
    assert detail["found"] is False


def test_completeness_points_are_out_of_scope_for_non_canvases(registry):
    """The 8-point gate is a dashboard-page gate.

    Grading a headless feature against it would manufacture failures nobody
    can fix, so ``declared`` must be False rather than ``passed`` False.
    """
    non_canvas = next(c for c in registry.list_all() if c.kind != "canvas")
    result = portal.completeness_points(non_canvas.key)
    assert result["declared"] is False
    assert result["items"] == []
