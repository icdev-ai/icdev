# CUI // SP-CTI
"""Constants for the Internal Developer Portal surface (idp-ui-02).

Point 5 of the CLAUDE.md 8-point dashboard-page gate.

Deliberately thin. Everything that is *policy* — the ladder, its level names,
their ranks and colours, the rules and their weights — lives in
``args/scorecards/*.yaml`` and is read at request time. Copying any of it here
would create a second source of truth that drifts the moment someone edits the
YAML, which is precisely what scorecard-as-code exists to prevent.

What belongs here is what the *page* needs and the scorecard cannot know: which
scorecard the portal renders by default, the IQE wiring for the query widget,
and the presentation-only vocabulary for the catalog filters.
"""
from __future__ import annotations

#: Scorecard rendered by /idp and /idp/scorecards. Must match a ``key`` in
#: args/scorecards/. Overridable per request via ``?scorecard=<key>``.
DEFAULT_SCORECARD_KEY = "component-readiness"

#: IQE collection the portal's facts come from. The scorecard declares the same
#: collection; a mismatch is rejected by the evaluator rather than silently
#: scoring the wrong thing.
IQE_COLLECTION = "idp.components"

#: Namespace + endpoint for includes/iqe_query_widget.html (gate point 8).
IQE_CANVAS = "idp"
IQE_API_ROUTE = "/idp/api/iqe-query"

#: Quick-pick chips for the query widget. Plain English — the widget translates
#: to IQE server-side via tools/iqe/nl_to_iqe.py.
IQE_EXAMPLES = (
    {"label": "Unowned components", "query": "show components with no owner"},
    {"label": "Failing the 8-point gate", "query": "which canvases fail the completeness gate"},
    {"label": "No E2E spec", "query": "list components without an e2e spec"},
    {"label": "Everything", "query": "show all components with their kind and route"},
)

#: Human labels for ``kind`` — the registry's own closed vocabulary. Used for
#: the catalog's group headings only; an unknown kind falls back to its raw
#: value rather than being dropped, so a new registry kind still renders.
KIND_LABELS = {
    "canvas": "Canvases",
    "child_app": "Child Apps",
    "feature": "Features",
    "core_extension": "Core Extensions",
}

#: Catalog column set, in render order: (fact key, header, is_boolean).
#: Booleans render as ✅/❌; everything else renders as text.
CATALOG_COLUMNS = (
    ("kind", "Kind", False),
    ("route", "Route", False),
    ("owner", "Owner", False),
    ("has_blueprint", "Blueprint", True),
    ("has_iqe_adapter", "IQE", True),
    ("has_e2e_spec", "E2E", True),
    ("rls_clean", "RLS", True),
)

#: Shown when a component has never been graded on a rule because it is out of
#: that rule's ``filter``. Kept distinct from "failed" on purpose: a component
#: that was never measured must not read as healthy.
NOT_APPLICABLE_LABEL = "n/a"

#: Shown wherever a component or a dimension has no score at all — no rule
#: applied, so nothing measured it. The word matters: "Not assessed" is an
#: admission, "0%" and "F" are findings, and the page must never print the
#: second when it means the first.
UNASSESSED_LABEL = "Not assessed"

#: Letter grade → bootstrap badge class. Keys are the letters
#: ``developer_scorecards.letter_grade`` is declared to hold; the bands
#: themselves live in ``args/scorecards/*.yaml`` under ``grading.bands``, so a
#: scorecard that rebands its grades needs no change here.
GRADE_BADGE = {
    "A": "bg-success",
    "B": "bg-success",
    "C": "bg-warning text-dark",
    "D": "bg-warning text-dark",
    "F": "bg-danger",
}

#: Badge class for an unassessed score. Deliberately NOT bg-danger: an
#: unassessed component is not a failing one, and colouring it red would make
#: the page assert something it has no evidence for.
UNASSESSED_BADGE = "bg-secondary"

#: Rule status → bootstrap badge class. Mirrors tools/idp/scorecard.py's
#: RuleOutcome.status vocabulary (pass | fail | exempt | not_applicable).
STATUS_BADGE = {
    "pass": "bg-success",
    "fail": "bg-danger",
    "exempt": "bg-info text-dark",
    "not_applicable": "bg-secondary",
}

#: Fallback ladder colour when a level declares none in its YAML.
DEFAULT_LEVEL_COLOR = "#6c757d"
