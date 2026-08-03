# CUI // SP-CTI
"""Component scorer — dimension extractors over ICDEV's own subsystems (idp-score-01).

Where ``tools/idp/scorecard.py`` grades components against *declared rules*
(scorecard-as-code: YAML ladders evaluated as IQE queries), this module reads
the platform's already-existing measurement subsystems directly and turns each
into one **dimension score**. The two are complementary: a scorecard rule
answers "does this component satisfy the policy we wrote down", an extractor
here answers "what do the probes / posture engine / coherence checker actually
say about it".

Four sources, one extractor each:

===================  =====================================================
dimension            source
===================  =====================================================
``health``           ``awareness_component_health`` HTTP probe rows, with
                     ``tools/canvas_health/health_data.py`` green/amber/red
                     as the fallback when nothing probed the component
``compliance``       ``tools/canvas_compliance/posture.py``
                     ``compute_canvas_posture``
``coherence``        ``tools/workflow/coherence_checker.py`` report
``completeness``     ``component_registry.validate_canvas_completeness``
                     (the CLAUDE.md 8-point dashboard-page gate)
===================  =====================================================

Not assessed is not a zero
--------------------------
Every extractor returns a :class:`DimensionScore` carrying ``score``,
``evidence_count`` and a ``status`` that is either :data:`ASSESSED` or
:data:`NOT_ASSESSED`. When the source has no data for a component the score is
``None`` and the status is :data:`NOT_ASSESSED` — never ``0.0``.

This is the whole point of the module, and it is not a hypothetical concern:
both upstream sources hand back something that *looks* like a measured failure
when they mean "nothing measured this".

  * :func:`compute_canvas_posture` builds each canvas score with
    ``float(row[0] or 0)``, so a canvas whose assessment table is empty scores
    a confident ``0.0``. The function itself already declines to fold those
    into its own average (``if score > 0``); this module applies the same
    reading and treats *score 0 with zero findings* as no evidence at all.
  * :func:`validate_canvas_completeness` returns ``passed=False`` with a single
    ``registered`` item both when a key is unknown and when the component is
    not a canvas. Scoring that as 0% would grade every child app and feature as
    a failing canvas. :func:`extract_completeness` detects that sentinel shape
    and reports it as not-assessed instead.

Constructing a measured zero out of nothing is made impossible rather than
merely discouraged: :meth:`DimensionScore.measured` downgrades itself to
:data:`NOT_ASSESSED` whenever ``evidence_count <= 0``, so a caller cannot
publish a 0% that no evidence supports.

Extractors never raise. A missing table, an unimportable module or an
unreachable database costs a dimension its score, not the caller its run — the
result is :data:`NOT_ASSESSED` with a ``reason`` naming what was unavailable.

Injecting source data
---------------------
Every extractor accepts its source data as an optional keyword so a caller (or
a test) can supply it directly instead of paying for the fetch. Passing an
empty source is the "no data" case and is exactly what the unit tests assert.
No extractor opens the ambient database on its own except through the same
guarded path the IQE adapter already uses.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

#: Status for a dimension backed by at least one piece of evidence.
ASSESSED = "assessed"

#: Sentinel status: the source held no data for this component. A dimension in
#: this state has ``score is None``. Renderers must show it as "not assessed"
#: and never coerce it to 0 — a zero is a measured failure and this is not one.
NOT_ASSESSED = "not_assessed"

#: Dimension keys this module produces.
DIMENSION_HEALTH = "health"
DIMENSION_COMPLIANCE = "compliance"
DIMENSION_COHERENCE = "coherence"
DIMENSION_COMPLETENESS = "completeness"

DIMENSIONS: tuple[str, ...] = (
    DIMENSION_HEALTH,
    DIMENSION_COMPLIANCE,
    DIMENSION_COHERENCE,
    DIMENSION_COMPLETENESS,
)

#: Credit given to each probe status when averaging a component's route probes.
#: ``warn`` is deliberately half rather than a pass: the prober escalates a
#: first failure to ``warn`` and only a second consecutive one to ``fail``, so
#: treating ``warn`` as healthy would hide the first half of every regression.
_PROBE_CREDIT: dict[str, float] = {
    "pass": 1.0,
    "ok": 1.0,
    "success": 1.0,
    "warn": 0.5,
    "degraded": 0.5,
    "fail": 0.0,
    "error": 0.0,
}

#: Canvas health rollup (green/amber/red) expressed as a score, used only when
#: no probe row exists for the component. Amber is "shipped but incomplete",
#: red is "missing something required" — see tools/canvas_health/constants.py.
_CANVAS_HEALTH_CREDIT: dict[str, float] = {
    "green": 100.0,
    "amber": 60.0,
    "red": 0.0,
}

#: Coherence check statuses and the credit each earns.
_COHERENCE_CREDIT: dict[str, float] = {"pass": 1.0, "warn": 0.5, "fail": 0.0}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DimensionScore:
    """One component's standing on one dimension.

    ``score`` is a 0–100 percentage, or ``None`` when ``status`` is
    :data:`NOT_ASSESSED`. The two always agree: there is no such thing here as
    an assessed dimension with no score, or an unassessed one with a score.
    """

    dimension: str
    score: float | None
    evidence_count: int
    status: str
    source: str = ""
    #: Why a dimension is unassessed, in plain language. Empty when assessed.
    reason: str = ""
    #: Source-specific breakdown (probe tallies, matched posture row, …).
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def assessed(self) -> bool:
        """True when real evidence produced this score."""
        return self.status == ASSESSED

    @classmethod
    def unassessed(
        cls,
        dimension: str,
        source: str = "",
        reason: str = "",
        detail: dict[str, Any] | None = None,
    ) -> "DimensionScore":
        """A dimension nothing measured."""
        return cls(
            dimension=dimension,
            score=None,
            evidence_count=0,
            status=NOT_ASSESSED,
            source=source,
            reason=reason,
            detail=detail or {},
        )

    @classmethod
    def measured(
        cls,
        dimension: str,
        score: float,
        evidence_count: int,
        source: str = "",
        detail: dict[str, Any] | None = None,
    ) -> "DimensionScore":
        """A dimension backed by ``evidence_count`` observations.

        Downgrades itself to :data:`NOT_ASSESSED` when the evidence count is
        zero or negative. That guard is what makes "unassessed is not a zero"
        an invariant of the type rather than a rule each extractor has to
        remember: no caller can hand out a 0% that nothing supports.
        """
        if evidence_count <= 0:
            return cls.unassessed(
                dimension,
                source=source,
                reason="no evidence rows",
                detail=detail or {},
            )
        return cls(
            dimension=dimension,
            score=round(max(0.0, min(100.0, float(score))), 1),
            evidence_count=int(evidence_count),
            status=ASSESSED,
            source=source,
            detail=detail or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "score": self.score,
            "evidence_count": self.evidence_count,
            "status": self.status,
            "assessed": self.assessed,
            "source": self.source,
            "reason": self.reason,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Component identity
# ---------------------------------------------------------------------------


def _as_component(component: Any) -> dict[str, Any]:
    """Normalize a key, a registry component or a fact dict to a plain dict.

    Accepts whatever the caller already has so the extractors do not force a
    registry lookup on a caller that has the component in hand.
    """
    if isinstance(component, str):
        return {"key": component}
    if isinstance(component, dict):
        out = dict(component)
        out.setdefault("key", out.get("name") or "")
        return out
    return {
        "key": getattr(component, "key", "") or "",
        "kind": getattr(component, "kind", "") or "",
        "display_name": getattr(component, "display_name", "") or "",
        "url_prefix": getattr(component, "url_prefix", "") or "",
        "module": getattr(component, "module", "") or "",
    }


def resolve_component(key: str, registry: Any = None) -> dict[str, Any]:
    """Look a component up in the registry, degrading to ``{"key": key}``.

    A registry that cannot be loaded costs the caller the component's route and
    kind, not the call — the extractors all treat a missing field as missing
    source data rather than as an error.
    """
    try:
        if registry is None:
            from tools.config.component_registry import get_registry  # noqa: PLC0415

            registry = get_registry()
        comp = registry.get(key)
    except Exception:  # noqa: BLE001
        return {"key": key}
    if comp is None:
        return {"key": key}
    return _as_component(comp)


def _route_of(component: dict[str, Any]) -> str:
    """The component's URL prefix, however the caller spelled the field."""
    for field in ("url_prefix", "route", "prefix"):
        value = component.get(field)
        if value:
            return str(value)
    return ""


#: Trailing words that name the *kind* of thing rather than the thing, dropped
#: when folding a display name. "Network Design Canvas" and the posture
#: engine's "Network" are the same canvas.
_NAME_NOISE = ("canvas", "design")


def _normalize_name(value: str) -> str:
    """Fold a key or display name to a comparable token.

    ``security_canvas`` -> ``security``; ``Network Design Canvas`` ->
    ``network``; ``AI/ML`` -> ``ai ml``. Used to match a component against the
    posture engine's display-name-keyed rows when the module join below cannot.
    """
    text = str(value or "").strip().lower()
    for char in ("_", "-", "/", "."):
        text = text.replace(char, " ")
    tokens = text.split()
    while tokens and tokens[-1] in _NAME_NOISE:
        tokens.pop()
    return " ".join(tokens)


def _module_package(module: str) -> str:
    """``tools.network.blueprint`` -> ``network``; ``""`` when unparseable."""
    parts = [p for p in str(module or "").replace("/", ".").split(".") if p]
    return parts[1] if len(parts) >= 2 else ""


def _posture_name_by_package() -> dict[str, str]:
    """Posture display name keyed by the canvas package it reads.

    ``compute_canvas_posture`` labels its rows with dashboard display names
    ("Network", "AI/ML") while the registry keys components as ``ndc`` /
    ``aimc``. Neither string resembles the other, so name matching alone finds
    nothing for almost every canvas. The two *do* agree on one thing: the
    package the canvas lives in. ``_CANVAS_MODULES`` already maps posture name
    to ``tools.<package>.db.init_db`` and the registry maps component to
    ``tools.<package>.blueprint``, so the package is the reliable join.

    Read from the posture module rather than copied here — a duplicate of that
    table would drift the moment a canvas is added.
    """
    try:
        from tools.canvas_compliance.posture import _CANVAS_MODULES  # noqa: PLC0415

        return {
            _module_package(module): name
            for name, module in _CANVAS_MODULES.items()
            if _module_package(module)
        }
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Dimension 1 — health (awareness_component_health probes, canvas health)
# ---------------------------------------------------------------------------


def _probe_rows_for(route: str, conn: Any) -> list[dict[str, Any]]:
    """Newest probe row per route under *route*, or ``[]``.

    Delegates to the IDP IQE adapter, which already owns this join: it keeps
    only the newest snapshot per node (so a recovered route stops counting
    against a component) and reports the *probed* set separately from the
    *failing* set, which is what makes never-probed distinguishable from
    healthy. Re-implementing that here would be a second source of truth for
    the same table.
    """
    if not route:
        return []
    try:
        from tools.iqe.adapters.idp import probe_evidence  # noqa: PLC0415

        return list(probe_evidence(route, conn=conn) or [])
    except Exception:  # noqa: BLE001
        return []


def _canvas_health_row(key: str, canvas_health: Sequence[dict[str, Any]] | None) -> dict[str, Any] | None:
    """This component's row from ``get_canvas_health()``, if any."""
    if canvas_health is None:
        try:
            from tools.canvas_health.health_data import get_canvas_health  # noqa: PLC0415

            canvas_health = get_canvas_health() or []
        except Exception:  # noqa: BLE001
            return None
    for row in canvas_health or []:
        if isinstance(row, dict) and str(row.get("key") or "") == key:
            return row
    return None


def extract_health(
    component: Any,
    conn: Any = None,
    evidence: Sequence[dict[str, Any]] | None = None,
    canvas_health: Sequence[dict[str, Any]] | None = None,
) -> DimensionScore:
    """Score a component's runtime health.

    Primary source is ``awareness_component_health``: the newest HTTP probe per
    route under the component's ``url_prefix``, credited by
    :data:`_PROBE_CREDIT`. When no route under the component has ever been
    probed, falls back to the static green/amber/red rollup in
    ``tools/canvas_health/health_data.py``.

    Returns :data:`NOT_ASSESSED` when the component owns no route *and* has no
    canvas-health row — which is the honest answer for a component nothing has
    ever looked at. A component with a route that was never probed is not
    scored 100; an unprobed route is absence of evidence, not evidence of
    health.

    Args:
        component: Registry key, registry component, or fact dict.
        conn: Optional DB connection for the probe table.
        evidence: Pre-fetched probe rows. Pass ``[]`` for "nothing probed".
        canvas_health: Pre-fetched ``get_canvas_health()`` rows.
    """
    comp = _as_component(component)
    key = str(comp.get("key") or "")
    route = _route_of(comp)

    rows = list(evidence) if evidence is not None else _probe_rows_for(route, conn)

    tallies: dict[str, int] = {}
    credited = 0.0
    counted = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        tallies[status] = tallies.get(status, 0) + 1
        credit = _PROBE_CREDIT.get(status)
        if credit is None:
            # An unknown probe status is not silently treated as a pass — it is
            # left out of the average and reported, so a new status value shows
            # up as missing evidence rather than as free credit.
            continue
        credited += credit
        counted += 1

    if counted:
        return DimensionScore.measured(
            DIMENSION_HEALTH,
            credited / counted * 100.0,
            counted,
            source="awareness_component_health",
            detail={
                "route": route,
                "probe_counts": tallies,
                "probes_considered": counted,
                "probes_seen": len(rows),
            },
        )

    row = _canvas_health_row(key, canvas_health)
    if row is not None:
        status = str(row.get("status") or "").strip().lower()
        credit = _CANVAS_HEALTH_CREDIT.get(status)
        if credit is not None:
            return DimensionScore.measured(
                DIMENSION_HEALTH,
                credit,
                1,
                source="canvas_health.health_data",
                detail={
                    "route": route,
                    "canvas_status": status,
                    "issues": list(row.get("issues") or []),
                    "note": "no probe rows; scored from static canvas health rollup",
                },
            )

    return DimensionScore.unassessed(
        DIMENSION_HEALTH,
        source="awareness_component_health",
        reason=(
            "no probe rows for this component's routes and no canvas-health row"
            if route
            else "component declares no url_prefix and has no canvas-health row"
        ),
        detail={"route": route, "probes_seen": len(rows)},
    )


# ---------------------------------------------------------------------------
# Dimension 2 — compliance posture
# ---------------------------------------------------------------------------


def _load_posture(conn: Any) -> list[dict[str, Any]] | None:
    """``compute_canvas_posture(conn)`` rows, or ``None`` when unavailable."""
    if conn is None:
        return None
    try:
        from tools.canvas_compliance.posture import compute_canvas_posture  # noqa: PLC0415

        rows, _overall = compute_canvas_posture(conn)
        return list(rows or [])
    except Exception:  # noqa: BLE001
        return None


def _match_posture_row(
    comp: dict[str, Any], posture: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """Find this component's row among posture rows keyed by display name.

    Two passes, strongest first:

    1. **Package join** — the component's module package against the package
       each posture row reads (see :func:`_posture_name_by_package`). This is
       what actually matches ``ndc`` to ``Network``.
    2. **Normalized name** — for rows outside ``_CANVAS_MODULES`` ("GovLift",
       "Zero Trust", "AI-ify") and for callers passing synthetic rows.
    """
    rows = [row for row in posture if isinstance(row, dict)]

    package = _module_package(str(comp.get("module") or ""))
    if package:
        target = _posture_name_by_package().get(package)
        if target:
            for row in rows:
                if str(row.get("name") or "") == target:
                    return row

    candidates = {
        _normalize_name(str(comp.get("key") or "")),
        _normalize_name(str(comp.get("display_name") or "")),
    }
    candidates.discard("")
    if not candidates:
        return None
    for row in rows:
        if _normalize_name(str(row.get("name") or "")) in candidates:
            return row
    return None


def extract_compliance(
    component: Any,
    conn: Any = None,
    posture: Sequence[dict[str, Any]] | None = None,
) -> DimensionScore:
    """Score a component's compliance posture.

    Reads ``tools/canvas_compliance/posture.py::compute_canvas_posture``, whose
    rows are keyed by dashboard display name ("Security", "AI/ML") rather than
    by registry key, so the component is matched on a normalized name.

    A matched row scoring ``0`` with **no** open and **no** closed findings is
    reported as :data:`NOT_ASSESSED`, not as 0%. ``compute_canvas_posture``
    derives each canvas score with ``float(value or 0)`` over an assessment
    table that is frequently empty, so that particular shape means "this canvas
    has never been assessed" — and the posture engine agrees, excluding those
    rows from its own overall average with ``if score > 0``. A zero *with*
    findings is a genuine measured failure and is scored as one.

    KNOWN UPSTREAM LIMITATION — a vacuous 100
    -----------------------------------------
    The subtraction-style canvases (Migration, Boundary) score
    ``100 - cat1*20 - cat2*10 - cat3*5``, which over an **empty** assessment
    table evaluates to a confident ``100.0`` with zero findings. That is the
    mirror image of the zero trap and it cannot be resolved from the row alone:
    a never-assessed canvas and a genuinely clean one produce byte-identical
    rows. Measured 2026-08-03 on this worktree — ``mc_assessments`` held 0 rows
    while posture reported Migration at 100.0.

    Resolving it belongs in ``compute_canvas_posture`` (which knows whether it
    read any assessment rows); inferring it here would mean duplicating that
    module's per-canvas table knowledge and creating a second source of truth.
    So this extractor does not guess — it reports ``findings_backed`` in
    ``detail``, which is ``False`` exactly when a score rests on no findings at
    all, so a portal can mark such a 100 as provisional rather than banking it.

    Args:
        component: Registry key, registry component, or fact dict.
        conn: Main ICDEV connection. Required unless ``posture`` is supplied.
        posture: Pre-fetched posture rows. Pass ``[]`` for "no posture data".
    """
    comp = _as_component(component)

    rows = list(posture) if posture is not None else _load_posture(conn)
    if rows is None:
        return DimensionScore.unassessed(
            DIMENSION_COMPLIANCE,
            source="canvas_compliance.posture",
            reason=(
                "no database connection supplied for the posture engine"
                if conn is None
                else "posture engine unavailable or raised"
            ),
        )
    if not rows:
        return DimensionScore.unassessed(
            DIMENSION_COMPLIANCE,
            source="canvas_compliance.posture",
            reason="posture engine returned no canvas rows",
        )

    row = _match_posture_row(comp, rows)
    if row is None:
        return DimensionScore.unassessed(
            DIMENSION_COMPLIANCE,
            source="canvas_compliance.posture",
            reason="no posture row matches this component",
            detail={"posture_rows": len(rows)},
        )

    try:
        score = float(row.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    open_f = int(row.get("open_findings") or 0)
    closed_f = int(row.get("closed_findings") or 0)
    findings = open_f + closed_f

    if score <= 0.0 and findings == 0:
        return DimensionScore.unassessed(
            DIMENSION_COMPLIANCE,
            source="canvas_compliance.posture",
            reason=(
                "matched posture row scores 0 with no findings — the canvas "
                "assessment table is empty, not failing"
            ),
            detail={"matched_name": row.get("name"), "score": score},
        )

    return DimensionScore.measured(
        DIMENSION_COMPLIANCE,
        score,
        max(1, findings),
        source="canvas_compliance.posture",
        detail={
            "matched_name": row.get("name"),
            "open_findings": open_f,
            "closed_findings": closed_f,
            # False when no finding backs this score. A subtraction-style
            # canvas with an empty assessment table lands here at 100.0 — see
            # the "vacuous 100" note above. Consumers should treat an
            # unbacked score as provisional.
            "findings_backed": findings > 0,
        },
    )


# ---------------------------------------------------------------------------
# Dimension 3 — coherence
# ---------------------------------------------------------------------------


def _coherence_checks(report: Any) -> list[dict[str, Any]] | None:
    """Normalize a report/dict/list of checks to a list of check dicts."""
    if report is None:
        return None
    checks = report
    if isinstance(report, dict):
        checks = report.get("checks")
    elif hasattr(report, "checks"):
        checks = report.checks
    if checks is None:
        return None
    out: list[dict[str, Any]] = []
    for check in checks:
        if isinstance(check, dict):
            out.append(check)
        elif hasattr(check, "to_dict"):
            try:
                out.append(check.to_dict())
            except Exception:  # noqa: BLE001
                continue
    return out


def _component_tokens(comp: dict[str, Any]) -> set[str]:
    """Path-ish tokens that identify this component inside a check's file list."""
    tokens: set[str] = set()
    key = str(comp.get("key") or "").strip()
    if key:
        tokens.add(key)
    module = str(comp.get("module") or "").strip()
    if module:
        # "tools.security_canvas.blueprint" -> "security_canvas"
        parts = [p for p in module.replace("/", ".").split(".") if p]
        if len(parts) >= 2:
            tokens.add(parts[1])
    tokens.discard("")
    return tokens


def _mentions(entry: Any, tokens: set[str]) -> bool:
    """Does a check entry name this component as a whole path segment?

    Segment-wise rather than substring: a bare ``in`` test would let the key
    ``data`` match ``tools/metadata/...`` and pull unrelated checks into the
    component's score.
    """
    text = str(entry or "").replace("\\", "/")
    if not text:
        return False
    segments = {seg for part in text.split("/") for seg in (part, part.split(".")[0])}
    return bool(segments & tokens)


def extract_coherence(
    component: Any,
    report: Any = None,
) -> DimensionScore:
    """Score the coherence checks that actually name this component.

    A check is *relevant* to a component when the component appears as a path
    segment anywhere in the check's ``expected``, ``actual``, ``missing`` or
    ``extra`` lists. Relevance is computed over all four lists on purpose: a
    passing check has an empty ``missing``, so scoring only on ``missing``
    would make every assessed component score 0.

    Returns :data:`NOT_ASSESSED` when no check names the component. This is the
    important half — the coherence checker is a platform-wide sweep, so
    "no check mentioned it" means nothing examined this component, and crediting
    that as 100% would hand a clean bill of health to whatever the sweep does
    not cover. ``tools/idp/scorecard.py::_assign_level`` documents the same
    vacuous-truth failure on the ladder side, measured at 30 of 67 components.

    ``report`` is not fetched implicitly: a full coherence sweep is a minutes-long
    operation and running one as a side effect of scoring a component would be a
    surprise. Callers pass the report the sweep or reflex already produced.

    Args:
        component: Registry key, registry component, or fact dict.
        report: ``CoherenceReport``, its ``to_dict()``, or a list of checks.
    """
    comp = _as_component(component)
    checks = _coherence_checks(report)

    if checks is None:
        return DimensionScore.unassessed(
            DIMENSION_COHERENCE,
            source="workflow.coherence_checker",
            reason="no coherence report supplied",
        )
    if not checks:
        return DimensionScore.unassessed(
            DIMENSION_COHERENCE,
            source="workflow.coherence_checker",
            reason="coherence report contains no checks",
        )

    tokens = _component_tokens(comp)
    if not tokens:
        return DimensionScore.unassessed(
            DIMENSION_COHERENCE,
            source="workflow.coherence_checker",
            reason="component has no key to match against check findings",
        )

    credited = 0.0
    counted = 0
    relevant_ids: list[str] = []
    failing_ids: list[str] = []
    for check in checks:
        entries: Iterable[Any] = (
            list(check.get("expected") or [])
            + list(check.get("actual") or [])
            + list(check.get("missing") or [])
            + list(check.get("extra") or [])
        )
        if not any(_mentions(entry, tokens) for entry in entries):
            continue
        status = str(check.get("status") or "").strip().lower()
        credit = _COHERENCE_CREDIT.get(status)
        if credit is None:
            continue
        check_id = str(check.get("check_id") or check.get("check") or "")
        relevant_ids.append(check_id)
        if credit < 1.0:
            failing_ids.append(check_id)
        credited += credit
        counted += 1

    if not counted:
        return DimensionScore.unassessed(
            DIMENSION_COHERENCE,
            source="workflow.coherence_checker",
            reason="no coherence check names this component",
            detail={"checks_examined": len(checks)},
        )

    return DimensionScore.measured(
        DIMENSION_COHERENCE,
        credited / counted * 100.0,
        counted,
        source="workflow.coherence_checker",
        detail={
            "checks_examined": len(checks),
            "relevant_checks": relevant_ids,
            "failing_checks": failing_ids,
        },
    )


# ---------------------------------------------------------------------------
# Dimension 4 — 8-point completeness gate
# ---------------------------------------------------------------------------


def _completeness_items(report: Any) -> list[dict[str, Any]] | None:
    """Normalize a completeness report to a list of item dicts."""
    if report is None:
        return None
    items = report.get("items") if isinstance(report, dict) else getattr(report, "items", None)
    if items is None:
        return None
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        elif dataclasses.is_dataclass(item):
            out.append(dataclasses.asdict(item))
    return out


def _is_not_applicable_report(items: Sequence[dict[str, Any]]) -> bool:
    """Is this the "unknown key / not a canvas" sentinel rather than a result?

    ``validate_canvas_completeness`` returns ``passed=False`` with exactly one
    ``registered`` item in both cases. That is absence of applicability, not a
    failed gate, and scoring it as 0% would grade every child app, feature and
    core extension as a broken canvas.
    """
    return (
        len(items) == 1
        and str(items[0].get("point") or "") == "registered"
        and not items[0].get("present")
    )


def extract_completeness(
    component: Any,
    registry: Any = None,
    repo_root: Path | str | None = None,
    report: Any = None,
) -> DimensionScore:
    """Score a canvas against the CLAUDE.md 8-point completeness gate.

    Scores the *required* points only: present required points over all
    required points. Optional points do not dilute the score.

    Returns :data:`NOT_ASSESSED` when the component is not a canvas or is not
    registered — see :func:`_is_not_applicable_report`.

    Args:
        component: Registry key, registry component, or fact dict.
        registry: Optional registry instance.
        repo_root: Optional repo root override.
        report: Pre-computed ``CanvasCompletenessReport``.
    """
    comp = _as_component(component)
    key = str(comp.get("key") or "")

    if report is None:
        if not key:
            return DimensionScore.unassessed(
                DIMENSION_COMPLETENESS,
                source="component_registry.validate_canvas_completeness",
                reason="component has no key",
            )
        try:
            from tools.config.component_registry import (  # noqa: PLC0415
                validate_canvas_completeness,
            )

            report = validate_canvas_completeness(key, registry=registry, repo_root=repo_root)
        except Exception as exc:  # noqa: BLE001
            return DimensionScore.unassessed(
                DIMENSION_COMPLETENESS,
                source="component_registry.validate_canvas_completeness",
                reason=f"completeness validator unavailable: {exc}",
            )

    items = _completeness_items(report)
    if items is None:
        return DimensionScore.unassessed(
            DIMENSION_COMPLETENESS,
            source="component_registry.validate_canvas_completeness",
            reason="completeness report carries no items",
        )
    if not items:
        return DimensionScore.unassessed(
            DIMENSION_COMPLETENESS,
            source="component_registry.validate_canvas_completeness",
            reason="completeness report is empty",
        )
    if _is_not_applicable_report(items):
        return DimensionScore.unassessed(
            DIMENSION_COMPLETENESS,
            source="component_registry.validate_canvas_completeness",
            reason=str(items[0].get("message") or "not a registered canvas"),
        )

    required = [item for item in items if item.get("required")]
    if not required:
        return DimensionScore.unassessed(
            DIMENSION_COMPLETENESS,
            source="component_registry.validate_canvas_completeness",
            reason="completeness report declares no required points",
            detail={"item_count": len(items)},
        )

    present = [item for item in required if item.get("present")]
    missing = [str(item.get("point") or "") for item in required if not item.get("present")]
    return DimensionScore.measured(
        DIMENSION_COMPLETENESS,
        len(present) / len(required) * 100.0,
        len(required),
        source="component_registry.validate_canvas_completeness",
        detail={
            "required_points": len(required),
            "present_points": len(present),
            "missing_points": missing,
            "optional_points": len(items) - len(required),
        },
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

#: Extractor per dimension, in report order.
EXTRACTORS: dict[str, Any] = {
    DIMENSION_HEALTH: extract_health,
    DIMENSION_COMPLIANCE: extract_compliance,
    DIMENSION_COHERENCE: extract_coherence,
    DIMENSION_COMPLETENESS: extract_completeness,
}


def score_component(
    component: Any,
    conn: Any = None,
    registry: Any = None,
    repo_root: Path | str | None = None,
    coherence_report: Any = None,
    posture: Sequence[dict[str, Any]] | None = None,
    canvas_health: Sequence[dict[str, Any]] | None = None,
    evidence: Sequence[dict[str, Any]] | None = None,
    completeness_report: Any = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Run every extractor for one component and aggregate the result.

    Two aggregations are reported, and they answer different questions:

    ``overall``
        Mean of the **assessed** dimensions only, ``None`` when nothing
        assessed the component. A diagnostic — "of what we did measure, how
        does it look".
    ``aggregate``
        The publishable verdict from :func:`aggregate_score`, carrying the
        weighted ``overall_score``, the ``letter_grade`` and the honesty rule:
        any unassessed dimension leaves ``overall_score`` at ``None``. This is
        what :func:`persist_scorecard` writes.

    Unassessed dimensions are neither counted as zeros (which would punish a
    component for gaps in ICDEV's own measurement coverage) nor silently
    dropped from the report — they appear with ``status = "not_assessed"`` and
    the reason they are missing.

    Every dimension's source can be injected — ``evidence``/``canvas_health``,
    ``posture``, ``coherence_report``, ``completeness_report`` — so a caller
    that has already fetched them pays once, and a test can drive all four
    dimensions without touching the database, the registry or the filesystem.
    """
    comp = _as_component(component)
    if len(comp) == 1 and comp.get("key"):
        comp = resolve_component(str(comp["key"]), registry=registry)

    dimensions = {
        DIMENSION_HEALTH: extract_health(
            comp, conn=conn, evidence=evidence, canvas_health=canvas_health
        ),
        DIMENSION_COMPLIANCE: extract_compliance(comp, conn=conn, posture=posture),
        DIMENSION_COHERENCE: extract_coherence(comp, report=coherence_report),
        DIMENSION_COMPLETENESS: extract_completeness(
            comp, registry=registry, repo_root=repo_root, report=completeness_report
        ),
    }

    assessed = [d for d in dimensions.values() if d.assessed]
    overall = (
        round(sum(d.score or 0.0 for d in assessed) / len(assessed), 1) if assessed else None
    )

    return {
        "key": comp.get("key", ""),
        "display_name": comp.get("display_name", ""),
        "kind": comp.get("kind", ""),
        "route": _route_of(comp),
        "overall": overall,
        "assessed": bool(assessed),
        "assessed_dimensions": len(assessed),
        "total_dimensions": len(dimensions),
        "evidence_count": sum(d.evidence_count for d in dimensions.values()),
        "dimensions": {name: d.to_dict() for name, d in dimensions.items()},
        # The publishable verdict. `overall` above is the mean of whatever
        # happened to be assessed and is retained as a diagnostic; `aggregate`
        # applies the honesty rule and is what gets persisted and rendered.
        "aggregate": aggregate_score(dimensions, weights=weights),
    }


# ---------------------------------------------------------------------------
# Aggregation — weighted overall score, letter grade, dimension details
# ---------------------------------------------------------------------------

#: Relative weight of each dimension in the overall score.
#:
#: Deliberately uniform. Ranking these four sources against each other would be
#: an assertion about their relative predictive value, and nothing in the
#: platform has measured that — inventing 0.4/0.3/0.2/0.1 here would dress a
#: guess up as a finding. The parameter exists so a program that *has* done
#: that work can supply its own; the default declines to pretend.
#:
#: Weights are normalized before use, so a caller may pass unnormalized
#: numbers (``{"health": 3, "compliance": 1}``) without doing the arithmetic.
DEFAULT_WEIGHTS: dict[str, float] = {
    DIMENSION_HEALTH: 0.25,
    DIMENSION_COMPLIANCE: 0.25,
    DIMENSION_COHERENCE: 0.25,
    DIMENSION_COMPLETENESS: 0.25,
}

#: Fallback A–F bands, used only when ``tools.idp.scorecard`` cannot be
#: imported. Kept identical to that module's ``DEFAULT_GRADE_BANDS``, which is
#: the source of truth: ``developer_scorecards.letter_grade`` carries a
#: ``CHECK(letter_grade IN ('A','B','C','D','F'))`` and these are the letters
#: that CHECK admits.
_FALLBACK_GRADE_BANDS: tuple[tuple[str, float], ...] = (
    ("A", 90.0),
    ("B", 80.0),
    ("C", 70.0),
    ("D", 60.0),
    ("F", 0.0),
)


def grade_bands() -> tuple[tuple[str, float], ...]:
    """The A–F bands, read from ``tools.idp.scorecard`` when importable."""
    try:
        from tools.idp.scorecard import DEFAULT_GRADE_BANDS  # noqa: PLC0415

        return tuple(DEFAULT_GRADE_BANDS)
    except Exception:  # noqa: BLE001
        return _FALLBACK_GRADE_BANDS


def letter_grade(
    score: float | None,
    bands: Sequence[tuple[str, float]] | None = None,
) -> str | None:
    """Band *score* to a letter, or ``None`` when there is no score.

    ``None`` in, ``None`` out. Substituting ``"F"`` for an absent score would
    turn "nobody measured this" into "this failed", which is the single defect
    this whole module is built to avoid.
    """
    if score is None:
        return None
    for letter, minimum in sorted(bands or grade_bands(), key=lambda b: -b[1]):
        if score >= minimum:
            return letter
    return None


def _as_dimension_dict(value: Any) -> dict[str, Any]:
    """Normalize a :class:`DimensionScore` or its ``to_dict()`` to a dict."""
    if isinstance(value, DimensionScore):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _normalized_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Weights for :data:`DIMENSIONS`, normalized to sum to 1.0.

    Unknown keys are dropped and missing ones default to their
    :data:`DEFAULT_WEIGHTS` share. A weight set that sums to zero (or to a
    negative) is not a weighting — it is a caller error that would divide by
    zero — so it falls back to the uniform default rather than raising.
    """
    source = DEFAULT_WEIGHTS if weights is None else weights
    raw = {}
    for name in DIMENSIONS:
        try:
            value = float(source.get(name, DEFAULT_WEIGHTS[name]))
        except (TypeError, ValueError):
            value = DEFAULT_WEIGHTS[name]
        raw[name] = max(0.0, value)

    total = sum(raw.values())
    if total <= 0.0:
        return dict(DEFAULT_WEIGHTS)
    return {name: value / total for name, value in raw.items()}


def aggregate_score(
    dimensions: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Fold four dimension scores into one overall score and letter grade.

    THE HONESTY RULE
    ----------------
    **Any** unassessed dimension caps ``overall_score`` at ``None``. A
    component is graded only when all four dimensions were actually measured.

    This is stricter than averaging whatever happened to be assessed, and the
    strictness is the point. A weighted average is only meaningful when its
    weights sum to the whole; dropping an unassessed dimension and renormalizing
    the rest silently redefines what the number means, so an ``85`` computed
    from one dimension and an ``85`` computed from four would print identically
    while resting on completely different amounts of evidence. Worse, the
    dimensions most often unassessed are the ones that would have *lowered* the
    score — a component nothing probed, nothing assessed for compliance and no
    coherence check names would score on completeness alone and grade ``A``.

    So an unmeasured dimension makes the score unavailable rather than
    optimistic. ``developer_scorecards.overall_score`` and ``letter_grade`` are
    both nullable (migration ``20260802145147``) precisely so this state is
    representable and does not have to be encoded as a ``0``/``F``.

    A dimension absent from *dimensions* entirely counts as unassessed — a key
    that was never computed is not a measured one.

    Args:
        dimensions: ``{dimension_name: DimensionScore | dict}``, as produced by
            :func:`score_component`'s ``dimensions`` field.
        weights: Optional relative weights; see :data:`DEFAULT_WEIGHTS`.
            Normalized before use.

    Returns:
        A dict carrying ``overall_score``, ``letter_grade``, ``status``,
        ``dimension_details`` and the reason a score is missing when it is.
    """
    effective = _normalized_weights(weights)
    normalized = {name: _as_dimension_dict(dimensions.get(name)) for name in DIMENSIONS}

    details: dict[str, Any] = {}
    unassessed: list[str] = []
    weighted_total = 0.0
    evidence_total = 0

    for name in DIMENSIONS:
        dim = normalized[name]
        # `assessed` is authoritative when present; fall back to the status
        # string for a caller that hand-built the dict. An empty dict — the
        # missing-dimension case — falls through to unassessed.
        is_assessed = bool(dim.get("assessed")) or dim.get("status") == ASSESSED
        score = dim.get("score")
        if score is None:
            is_assessed = False

        count = int(dim.get("evidence_count") or 0)
        evidence_total += count

        details[name] = {
            "score": score,
            "status": ASSESSED if is_assessed else NOT_ASSESSED,
            "assessed": is_assessed,
            "weight": round(effective[name], 4),
            # The acceptance criterion for this dimension: how many
            # observations, and from which subsystem, back the number.
            "evidence_count": count,
            "source": str(dim.get("source") or ""),
            "reason": str(dim.get("reason") or ""),
            "detail": dim.get("detail") or {},
        }

        if is_assessed:
            weighted_total += float(score) * effective[name]
        else:
            unassessed.append(name)

    if unassessed:
        return {
            "overall_score": None,
            "letter_grade": None,
            "status": NOT_ASSESSED,
            "assessed": False,
            "reason": (
                "not scored: "
                + ", ".join(unassessed)
                + (" was" if len(unassessed) == 1 else " were")
                + " not assessed — a weighted score over a subset of "
                "dimensions would overstate the evidence behind it"
            ),
            "unassessed_dimensions": unassessed,
            "assessed_dimensions": len(DIMENSIONS) - len(unassessed),
            "total_dimensions": len(DIMENSIONS),
            "evidence_count": evidence_total,
            "weights": {k: round(v, 4) for k, v in effective.items()},
            "dimension_details": details,
        }

    overall = round(max(0.0, min(100.0, weighted_total)), 1)
    return {
        "overall_score": overall,
        "letter_grade": letter_grade(overall),
        "status": ASSESSED,
        "assessed": True,
        "reason": "",
        "unassessed_dimensions": [],
        "assessed_dimensions": len(DIMENSIONS),
        "total_dimensions": len(DIMENSIONS),
        "evidence_count": evidence_total,
        "weights": {k: round(v, 4) for k, v in effective.items()},
        "dimension_details": details,
    }


# ---------------------------------------------------------------------------
# Persistence — developer_scorecards, one row per component
# ---------------------------------------------------------------------------

#: The table this module owns. Re-keyed from the project grain to the component
#: grain by migration ``20260802145147_scorecard_component_id``.
PERSIST_TABLE = "developer_scorecards"

#: Columns :func:`persist_scorecard` writes. Every one of these must exist in
#: the LIVE schema — see the guard in :func:`_require_component_grain`.
#:
#: The four other dimension columns the table declares (``code_quality_score``,
#: ``security_score``, ``test_coverage_score``, ``velocity_score``) are left
#: NULL on purpose. They were designed for the original per-developer grain and
#: none of this module's four dimensions means the same thing; filling
#: ``test_coverage_score`` with a completeness percentage because both are
#: percentages would put a number under a label that does not describe it.
#: ``compliance_score`` is written because it is the one genuine match — the
#: compliance dimension *is* a compliance posture score. Everything else lives
#: in ``dimension_details``, which has no such naming problem.
INSERT_COLUMNS: tuple[str, ...] = (
    "id",
    "component_id",
    "overall_score",
    "letter_grade",
    "compliance_score",
    "dimension_details",
    "classification",
    "evaluated_at",
)

#: Columns an update refreshes. ``id`` and ``component_id`` identify the row.
_UPDATE_COLUMNS: tuple[str, ...] = (
    "overall_score",
    "letter_grade",
    "compliance_score",
    "dimension_details",
    "classification",
    "evaluated_at",
)

DEFAULT_CLASSIFICATION = "CUI // SP-CTI"


class ScorecardPersistError(RuntimeError):
    """Raised when a scorecard cannot be persisted.

    Deliberately a raise rather than a swallowed warning. The failure mode this
    guards against is the one CLAUDE.md's INSERT/schema-parity rule was written
    for: an INSERT names a column the live table lacks, raises, is caught by a
    surrounding ``except Exception``, and the caller reports success while
    persisting nothing — which is exactly how ``developer_scorecards`` sat at 0
    rows with a fully-designed schema.
    """


def _row_id(component_id: str) -> str:
    """Deterministic primary key for a component's scorecard row.

    Deterministic rather than a UUID so that re-running the scorer is
    idempotent even if the ``component_id`` lookup below is bypassed, and so a
    row is greppable from its component key. Prefixed to keep it out of the
    namespace of the legacy project-grain rows.
    """
    return f"sc-comp-{component_id}"


def _require_component_grain(conn: Any) -> None:
    """Fail loudly unless the component-grain migration has been applied.

    ``component_id`` is the marker: it and ``evaluated_at`` arrive together
    with the three ``DROP NOT NULL`` relaxations in migration
    ``20260802145147``. Without it, an INSERT here fails twice over — on the
    unknown column *and* on ``project_id NOT NULL``, which a component
    scorecard has nothing to put in.
    """
    try:
        from tools.db.storage import column_exists, table_exists  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise ScorecardPersistError(f"tools.db.storage unavailable: {exc}") from exc

    if not table_exists(conn, PERSIST_TABLE):
        raise ScorecardPersistError(
            f"{PERSIST_TABLE} does not exist — run tools/db/init_icdev_db.py"
        )
    for column in ("component_id", "evaluated_at"):
        if not column_exists(conn, PERSIST_TABLE, column):
            raise ScorecardPersistError(
                f"{PERSIST_TABLE}.{column} is missing — apply migration "
                "20260802145147_scorecard_component_id before persisting "
                "component scorecards"
            )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_scorecard(
    result: dict[str, Any],
    conn: Any = None,
    evaluated_at: str | None = None,
    classification: str = DEFAULT_CLASSIFICATION,
) -> dict[str, Any]:
    """Write one component's scorecard to ``developer_scorecards``.

    Upserts on ``component_id``: a component has one current scorecard row,
    updated in place on re-evaluation. (Score *history* is a separate table,
    ``idp_scorecard_history``, owned by ``tools/idp/score_history.py`` — this
    row is the current standing, not the trend.)

    An unassessed component is persisted with ``overall_score`` and
    ``letter_grade`` both NULL. That row is not a gap in the table; it is the
    record that ICDEV looked and could not measure, and ``dimension_details``
    carries which dimension was missing and why. Skipping such components
    instead would make the table silently describe only the well-covered
    corners of the platform.

    Args:
        result: A :func:`score_component` result, or any dict carrying ``key``
            and either ``aggregate`` or ``dimensions``.
        conn: Storage connection. Opened and closed here when omitted.
        evaluated_at: Evaluation timestamp; defaults to now (UTC, ISO-8601).
            Distinct from the row's ``created_at``, which is insert time.
        classification: Marking for the row.

    Returns:
        ``{"component_id", "action": "insert"|"update", "overall_score",
        "letter_grade", "status"}``.

    Raises:
        ScorecardPersistError: The component has no key, or the live schema
            predates the component-grain migration.
    """
    component_id = str(result.get("key") or "").strip()
    if not component_id:
        raise ScorecardPersistError("result has no component key to persist under")

    aggregate = result.get("aggregate")
    if not isinstance(aggregate, dict):
        aggregate = aggregate_score(result.get("dimensions") or {})

    own = conn is None
    if own:
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            raise ScorecardPersistError(f"cannot open a database connection: {exc}") from exc

    try:
        _require_component_grain(conn)

        details = aggregate.get("dimension_details") or {}
        compliance = (details.get(DIMENSION_COMPLIANCE) or {}).get("score")

        payload = {
            "overall_score": aggregate.get("overall_score"),
            "letter_grade": aggregate.get("letter_grade"),
            "compliance_score": compliance,
            "dimension_details": json.dumps(
                {
                    "component": {
                        "key": component_id,
                        "display_name": result.get("display_name") or "",
                        "kind": result.get("kind") or "",
                        "route": result.get("route") or "",
                    },
                    "status": aggregate.get("status"),
                    "reason": aggregate.get("reason") or "",
                    "unassessed_dimensions": aggregate.get("unassessed_dimensions") or [],
                    "evidence_count": aggregate.get("evidence_count", 0),
                    "weights": aggregate.get("weights") or {},
                    "dimensions": details,
                },
                default=str,
                sort_keys=True,
            ),
            "classification": classification,
            "evaluated_at": evaluated_at or _utc_now(),
        }

        cursor = conn.execute(
            f"SELECT id FROM {PERSIST_TABLE} WHERE component_id = %s",  # noqa: S608
            (component_id,),
        )
        existing = cursor.fetchone() if hasattr(cursor, "fetchone") else None
        row_id = None
        if existing is not None:
            row_id = existing[0] if not isinstance(existing, dict) else existing.get("id")

        if row_id:
            assignments = ", ".join(f"{col} = %s" for col in _UPDATE_COLUMNS)
            conn.execute(
                f"UPDATE {PERSIST_TABLE} SET {assignments} WHERE id = %s",  # noqa: S608
                tuple(payload[col] for col in _UPDATE_COLUMNS) + (row_id,),
            )
            action = "update"
        else:
            row_id = _row_id(component_id)
            values = {"id": row_id, "component_id": component_id, **payload}
            placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
            conn.execute(
                f"INSERT INTO {PERSIST_TABLE} ({', '.join(INSERT_COLUMNS)}) "  # noqa: S608
                f"VALUES ({placeholders})",
                tuple(values[col] for col in INSERT_COLUMNS),
            )
            action = "insert"

        conn.commit()
        return {
            "component_id": component_id,
            "id": row_id,
            "action": action,
            "overall_score": aggregate.get("overall_score"),
            "letter_grade": aggregate.get("letter_grade"),
            "status": aggregate.get("status"),
        }
    finally:
        if own and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass


def registry_component_keys(registry: Any = None) -> list[str]:
    """Every component key declared in ``args/component_registry.yaml``."""
    if registry is None:
        from tools.config.component_registry import get_registry  # noqa: PLC0415

        registry = get_registry()
    return [str(c.key) for c in registry.list_all() if getattr(c, "key", "")]


def persist_all(
    conn: Any = None,
    registry: Any = None,
    keys: Sequence[str] | None = None,
    evaluated_at: str | None = None,
    **score_kwargs: Any,
) -> dict[str, Any]:
    """Score and persist every registered component.

    One component's failure does not abort the sweep — it is recorded in
    ``errors`` and the rest still persist. A single unimportable canvas module
    should not cost the platform its whole scorecard refresh.

    Args:
        conn: Storage connection. Opened and closed here when omitted.
        registry: Optional registry instance.
        keys: Restrict to these component keys instead of the whole registry.
        evaluated_at: Shared evaluation timestamp for the sweep, so every row
            written by one pass sorts together on ``idx_sc_component_evaluated``.
        **score_kwargs: Forwarded to :func:`score_component` (``coherence_report``,
            ``posture``, ``canvas_health``, ``weights``, …).

    Returns:
        ``{"persisted": [...], "errors": [...], "assessed", "unassessed"}``.
    """
    own = conn is None
    if own:
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            raise ScorecardPersistError(f"cannot open a database connection: {exc}") from exc

    stamp = evaluated_at or _utc_now()
    persisted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        component_keys = list(keys) if keys is not None else registry_component_keys(registry)
        for key in component_keys:
            try:
                result = score_component(key, conn=conn, registry=registry, **score_kwargs)
                persisted.append(
                    persist_scorecard(result, conn=conn, evaluated_at=stamp)
                )
            except Exception as exc:  # noqa: BLE001
                errors.append({"component_id": key, "error": str(exc)})
    finally:
        if own and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass

    return {
        "evaluated_at": stamp,
        "persisted": persisted,
        "errors": errors,
        "assessed": sum(1 for r in persisted if r.get("overall_score") is not None),
        "unassessed": sum(1 for r in persisted if r.get("overall_score") is None),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _pct(score: float | None) -> str:
    """Percentage for the CLI — "n/a", never "0%", when unassessed."""
    return f"{score:.0f}%" if score is not None else "n/a"


def _render(result: dict[str, Any]) -> str:
    aggregate = result.get("aggregate") or {}
    grade = aggregate.get("letter_grade") or "—"
    lines = [
        f"{result['key']} ({result.get('kind') or 'unknown kind'}) — "
        f"score {_pct(aggregate.get('overall_score'))} grade {grade} "
        f"from {result['assessed_dimensions']}/{result['total_dimensions']} dimensions",
        "",
    ]
    for name, dim in result["dimensions"].items():
        detail = dim["reason"] if not dim["assessed"] else f"{dim['evidence_count']} evidence"
        lines.append(f"  {name:<14} {_pct(dim['score']):>6}  [{dim['source']}] {detail}")
    if aggregate.get("reason"):
        lines += ["", f"  {aggregate['reason']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/quality/component_scorer.py",
        description="Score a registered component across health, compliance, "
        "coherence and completeness dimensions.",
    )
    ap.add_argument("component", nargs="?", help="Component registry key")
    ap.add_argument("--all", action="store_true", help="Score every registered component")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    ap.add_argument(
        "--persist",
        action="store_true",
        help="Write each result to developer_scorecards (upsert on component_id)",
    )
    args = ap.parse_args(argv)

    if not args.component and not args.all:
        ap.error("give a component key or --all")

    conn = None
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()
    except Exception:  # noqa: BLE001
        conn = None

    try:
        keys: list[str]
        if args.all:
            try:
                from tools.config.component_registry import get_registry  # noqa: PLC0415

                keys = [c.key for c in get_registry().list_all()]
            except Exception as exc:  # noqa: BLE001
                print(f"error: cannot load registry: {exc}", file=sys.stderr)
                return 2
        else:
            keys = [args.component]

        results = [score_component(key, conn=conn) for key in keys]

        written: list[dict[str, Any]] = []
        if args.persist:
            if conn is None:
                print("error: --persist needs a database connection", file=sys.stderr)
                return 2
            stamp = _utc_now()
            for result in results:
                written.append(persist_scorecard(result, conn=conn, evaluated_at=stamp))
    finally:
        if conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass

    if args.json:
        payload: Any = results if len(results) > 1 else results[0]
        if args.persist:
            payload = {"results": results, "persisted": written}
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n\n".join(_render(r) for r in results))
        for row in written:
            print(f"  persisted {row['component_id']} ({row['action']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
