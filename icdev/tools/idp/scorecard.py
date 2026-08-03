# CUI // SP-CTI
"""Scorecard-as-code — ladder + IQE rule expressions.

A scorecard is a YAML file under ``args/scorecards/``. It declares a **ladder**
of ranked levels and a list of **rules**; every rule is an ordinary IQE query
over a registered collection. ICDEV already has a query language, so this
module deliberately does not invent a DSL — the rule text you write in YAML is
the same text you can paste into ``python -m tools.iqe.run`` or the dashboard
IQE widget.

    rules:
      - identifier: has-owner
        weight: 20
        level: Bronze
        expression: foreach c in idp.components where c.has_owner == true select c.key

The set of entities a rule's query returns is the set that **passes** it.

Scoring vs. the ladder — the distinction that makes a ladder usable:
  * Every applicable rule contributes its ``weight`` to the entity's score.
  * Only rules that declare a ``level`` gate ladder progression. An entity
    attains a level when it passes every applicable leveled rule at that rank
    *and* every rank below it. Un-levelled rules still score, but can never
    hold an entity back — which is what lets a scorecard carry aspirational
    checks without turning into a single pass/fail.

Rules may carry a ``filter`` (also an IQE query) naming the entities the rule
applies to at all; entities outside the filter are "not applicable" and are
excluded from both the score denominator and the ladder.

Exemptions (idp-score-04)
-------------------------
An exemption waives ONE rule for ONE entity. The entity is then neither
passing nor failing that rule — it is excluded from it, exactly like a rule
whose ``filter`` does not select it — so the rule leaves the score's
denominator instead of paying out its weight. Crediting an exemption as a pass
would make waiving a rule the cheapest way to raise a score, which is the
incentive a scorecard exists to remove. An exempt rule does not hold the ladder
either: a rung whose only unmet rule is exempted here is met, and progression
continues.

Every exemption must name **who approved it** and **why**; one that does not is
reported as *inert* and waives nothing. Grants come from two places, merged:
the scorecard's own ``exemptions.grants`` block, and the append-only approval
log in ``tools/idp/exemptions.py``. Config carries ``enabled``, ``autoApprove``
(default off) and ``userSpecificNotifications``.

Dimensions and the letter grade (idp-ui-01)
-------------------------------------------
A rule may declare a ``dimension``, which buckets it with the other rules that
measure the same thing. Each dimension is scored exactly the way the overall
score is — earned weight over applicable weight — so the portal can show *why*
a component scores what it does rather than one opaque number. The default
dimension set is the five columns ``developer_scorecards`` already has
(code_quality, security, compliance, test_coverage, velocity); a scorecard may
declare its own. A rule that names no dimension is reported under a synthetic
``unassigned`` bucket rather than being dropped, so drift is visible.

The overall score also carries a letter grade, banded by ``grading.bands``.

Unassessed is not a zero
------------------------
An entity with no applicable counted rules — or a dimension none of whose rules
apply to that entity — has ``score = None``, ``letter_grade = None`` and
``assessed = False``. It is *not* scored 0. A zero and an unknown are different
claims, and the portal renders them differently: 0% is a measured failure, and
"not assessed" is an admission that nothing measured it.

Evidence
--------
Every :class:`RuleOutcome` carries an ``evidence`` block naming what produced
it: the IQE expression, the collection and adapter module it read, the fact
fields the predicate touches, and this entity's observed value for each of
them. A score with no traceable source is an assertion, so nothing in the
report asks to be taken on faith.

Adding a rule, a level, a dimension or a whole new scorecard is a YAML edit. No
Python change is required — that is the point of this module.
"""
from __future__ import annotations

import argparse
import dataclasses
import glob
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.idp.exemptions import (  # noqa: E402
    ACTIVE_STATUS,
    ExemptionPolicy,
    attribution_defect,
)
from tools.iqe import IQESyntaxError, execute_query, parse  # noqa: E402
from tools.iqe.ast_nodes import AttrRef, BinOp, CollectionCall, SelectNode  # noqa: E402

DEFAULT_SCORECARD_DIR = BASE_DIR / "args" / "scorecards"

# Default entity-identifier field and evaluation window, overridable per file.
DEFAULT_ENTITY_KEY = "key"
DEFAULT_WINDOW = "24h"

#: Bucket for rules that declare no ``dimension``. Synthetic on purpose: a rule
#: nobody has classified still has to appear somewhere, and a visible
#: "unassigned" column is how that omission gets noticed and fixed.
UNASSIGNED_DIMENSION = "unassigned"

#: Default dimensions — the five score columns ``developer_scorecards``
#: already carries. ``column`` names the column a persisted row would use
#: (idp-score-03 writes the history); an empty ``column`` means the dimension
#: is report-only and has nowhere to persist.
DEFAULT_DIMENSIONS: tuple[dict[str, str], ...] = (
    {"key": "code_quality", "label": "Code Quality", "column": "code_quality_score"},
    {"key": "security", "label": "Security", "column": "security_score"},
    {"key": "compliance", "label": "Compliance", "column": "compliance_score"},
    {"key": "test_coverage", "label": "Test Coverage", "column": "test_coverage_score"},
    {"key": "velocity", "label": "Velocity", "column": "velocity_score"},
)

#: Default A–F bands, highest first. ``developer_scorecards.letter_grade`` is
#: declared A-F, so these are the letters that table can hold.
DEFAULT_GRADE_BANDS: tuple[tuple[str, float], ...] = (
    ("A", 90.0),
    ("B", 80.0),
    ("C", 70.0),
    ("D", 60.0),
    ("F", 0.0),
)


class ScorecardError(ValueError):
    """Raised when a scorecard file is malformed or a rule cannot be parsed."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Level:
    """One rung of the ladder. Higher ``rank`` is better."""

    name: str
    rank: int
    description: str = ""
    color: str = ""


@dataclasses.dataclass(frozen=True)
class Dimension:
    """One scoring dimension — a named bucket of rules.

    ``column`` is the ``developer_scorecards`` column a persisted score would
    land in, or "" for a dimension with nowhere to persist. This module never
    writes that table; it only reports which column a writer would use, so the
    portal can say whether a dimension is persistable or report-only.
    """

    key: str
    label: str
    description: str = ""
    column: str = ""


@dataclasses.dataclass(frozen=True)
class Rule:
    """One scorecard rule — an IQE query plus its scoring metadata."""

    identifier: str
    expression: str
    weight: int = 1
    level: str | None = None
    title: str = ""
    failure_message: str = ""
    filter_expression: str | None = None
    dimension: str = UNASSIGNED_DIMENSION
    #: Where the fact this rule reads comes from, in prose — shown as evidence
    #: next to the machine-derived field list. Optional.
    evidence_note: str = ""

    @property
    def gates_ladder(self) -> bool:
        """True when this rule blocks ladder progression (it declares a level)."""
        return self.level is not None


@dataclasses.dataclass(frozen=True)
class Exemption:
    """Waives one rule for one entity — if it is attributed and approved.

    ``approved_by`` and ``reason`` are what make a waiver reviewable six months
    later, so an exemption missing either one is *inert*: it is loaded, it is
    reported, and it waives nothing. That is deliberately louder than dropping
    it, because a waiver that quietly failed to apply is as confusing as one
    that quietly applied.

    ``source`` distinguishes a grant declared in the scorecard YAML (attributed
    by the ``approvedBy:`` field and by git history) from one granted through
    the append-only approval log (``tools/idp/exemptions.py``).
    """

    identifier: str
    entity: str
    reason: str = ""
    expires: str = ""
    approved_by: str = ""
    approved_at: str = ""
    status: str = ACTIVE_STATUS
    source: str = "scorecard"
    decision_reason: str = ""
    auto_approved: bool = False

    def defect(self, today: str, policy: "ExemptionPolicy | None" = None) -> str:
        """Why this exemption does not apply, or ``""`` when it does."""
        return attribution_defect(
            status=self.status,
            approved_by=self.approved_by,
            reason=self.reason,
            expires=self.expires,
            today=today,
            policy=policy,
        )

    def is_active(self, today: str, policy: "ExemptionPolicy | None" = None) -> bool:
        """True when this exemption actually waives its rule."""
        return not self.defect(today, policy)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Scorecard:
    """A parsed scorecard definition."""

    key: str
    name: str
    collection: str
    levels: tuple[Level, ...]
    rules: tuple[Rule, ...]
    exemptions: tuple[Exemption, ...] = ()
    description: str = ""
    entity_key: str = DEFAULT_ENTITY_KEY
    window: str = DEFAULT_WINDOW
    adapter_module: str = ""
    source_path: str = ""
    dimensions: tuple[Dimension, ...] = ()
    grade_bands: tuple[tuple[str, float], ...] = DEFAULT_GRADE_BANDS
    #: ``enabled`` / ``autoApprove`` / ``userSpecificNotifications``.
    #: ``field(default_factory=...)`` rather than a shared default instance:
    #: the policy is mutable, and one shared object would let a test (or a
    #: second scorecard) flip auto-approve on for every card in the process.
    exemption_policy: ExemptionPolicy = dataclasses.field(default_factory=ExemptionPolicy)

    def ladder(self) -> list[Level]:
        """Levels ordered worst-to-best."""
        return sorted(self.levels, key=lambda lv: lv.rank)

    def resolved_adapter_module(self) -> str:
        """Module that registers this scorecard's IQE collection."""
        return self.adapter_module or f"tools.iqe.adapters.{self.collection.split('.')[0]}"

    def dimension_order(self) -> list[Dimension]:
        """Declared dimensions, plus ``unassigned`` when some rule needs it.

        The synthetic bucket is appended only when a rule actually lands in it.
        A fully classified scorecard therefore shows exactly its own
        dimensions, and an unclassified rule cannot hide.
        """
        declared = list(self.dimensions)
        known = {d.key for d in declared}
        if any(r.dimension not in known for r in self.rules):
            declared.append(
                Dimension(
                    key=UNASSIGNED_DIMENSION,
                    label="Unassigned",
                    description="Rules that declare no dimension.",
                )
            )
        return declared

    def letter_grade(self, score: float | None) -> str | None:
        """Band *score* to a letter, or ``None`` when there is no score.

        ``None`` in, ``None`` out — an unassessed component has no grade, and
        substituting "F" would turn "nobody measured this" into "this failed".
        """
        if score is None:
            return None
        for letter, minimum in sorted(self.grade_bands, key=lambda b: -b[1]):
            if score >= minimum:
                return letter
        return None


@dataclasses.dataclass
class Evidence:
    """Where one rule outcome came from.

    Everything here is derivable and checkable by hand: paste ``expression``
    into the IQE widget and you get the passing set back. ``observed`` is this
    entity's value for each fact field the predicate reads, so a reader can see
    the input, not just the verdict.
    """

    collection: str
    adapter_module: str
    expression: str
    filter_expression: str = ""
    fields: list[str] = dataclasses.field(default_factory=list)
    observed: dict[str, Any] = dataclasses.field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class RuleOutcome:
    """How one rule landed for one entity."""

    identifier: str
    status: str  # pass | fail | exempt | not_applicable
    weight: int
    level: str | None
    message: str = ""
    dimension: str = UNASSIGNED_DIMENSION
    evidence: Evidence | None = None

    #: Set on an ``exempt`` outcome: who approved the waiver, why, when it
    #: lapses, and which store it came from. An exemption with no attribution
    #: never reaches this point — it is reported as inert instead.
    exemption: dict[str, Any] | None = None

    @property
    def counted(self) -> bool:
        """Does this outcome participate in the score?

        ``exempt`` does not. An exemption removes the entity from the rule; it
        does not hand it the rule's weight. Paying out a waived rule would make
        an exemption a score increase, so the cheapest way to fix a red
        scorecard would be to stop measuring — which is the failure mode the
        approval step exists to prevent.
        """
        return self.status in ("pass", "fail")

    @property
    def credited(self) -> bool:
        """Does this outcome earn its weight?"""
        return self.status == "pass"

    @property
    def gating(self) -> bool:
        """Does this outcome constrain ladder progression?

        Only a leveled rule that actually applies. ``exempt`` sits out
        alongside ``not_applicable``: an exempt rule must not block the rung it
        was waived on, nor the rungs above it.
        """
        return bool(self.level) and self.status not in ("not_applicable", "exempt")

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "status": self.status,
            "weight": self.weight,
            "level": self.level,
            "message": self.message,
            "dimension": self.dimension,
            "counted": self.counted,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "exemption": self.exemption,
        }


def _score_of(earned: int, total: int) -> float | None:
    """Percentage, or ``None`` when nothing applicable was measured.

    The ``None`` is the whole point. ``earned / total`` with ``total == 0``
    used to fall back to 0.0, which is indistinguishable on a page from a
    component that was measured and failed everything.
    """
    return round(earned / total * 100.0, 1) if total else None


@dataclasses.dataclass
class DimensionResult:
    """One entity's standing on one dimension."""

    key: str
    label: str
    earned_weight: int
    total_weight: int
    score: float | None
    letter_grade: str | None
    column: str = ""
    outcomes: list[RuleOutcome] = dataclasses.field(default_factory=list)

    @property
    def assessed(self) -> bool:
        """False when no rule in this dimension applied to this entity."""
        return self.score is not None

    def failures(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes if o.status == "fail"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "column": self.column,
            "score": self.score,
            "letter_grade": self.letter_grade,
            "assessed": self.assessed,
            "earned_weight": self.earned_weight,
            "total_weight": self.total_weight,
            "rule_count": len(self.outcomes),
            "failure_count": len(self.failures()),
            # Every dimension carries the outcomes that produced it, each with
            # its own evidence — this is the "link to the evidence" half of the
            # contract, served as data rather than as a rendered string.
            "rules": [o.to_dict() for o in self.outcomes],
        }


@dataclasses.dataclass
class EntityResult:
    """One entity's standing against a scorecard."""

    entity: str
    level: str | None
    level_rank: int
    score: float | None
    earned_weight: int
    total_weight: int
    letter_grade: str | None = None
    outcomes: list[RuleOutcome] = dataclasses.field(default_factory=list)
    dimensions: list[DimensionResult] = dataclasses.field(default_factory=list)

    @property
    def assessed(self) -> bool:
        """False when no rule applied to this entity at all."""
        return self.score is not None

    def failures(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes if o.status == "fail"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "level": self.level,
            "level_rank": self.level_rank,
            "score": self.score,
            "letter_grade": self.letter_grade,
            "assessed": self.assessed,
            "earned_weight": self.earned_weight,
            "total_weight": self.total_weight,
            "rules": [o.to_dict() for o in self.outcomes],
            "dimensions": [d.to_dict() for d in self.dimensions],
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _as_int(value: Any, field: str, default: int | None = None) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise ScorecardError(f"{field} must be an integer, got {value!r}") from None


def parse_scorecard(data: dict[str, Any], source_path: str = "") -> Scorecard:
    """Build a :class:`Scorecard` from a loaded YAML mapping."""
    if not isinstance(data, dict):
        raise ScorecardError(f"{source_path or 'scorecard'}: top level must be a mapping")

    key = str(data.get("key") or "").strip()
    if not key:
        raise ScorecardError(f"{source_path or 'scorecard'}: missing required 'key'")

    collection = str(data.get("collection") or "").strip()
    if not collection:
        raise ScorecardError(f"{key}: missing required 'collection'")

    ladder_block = data.get("ladder") or {}
    raw_levels = ladder_block.get("levels") if isinstance(ladder_block, dict) else ladder_block
    levels: list[Level] = []
    for raw in raw_levels or []:
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ScorecardError(f"{key}: ladder level missing 'name'")
        levels.append(
            Level(
                name=name,
                rank=_as_int(raw.get("rank"), f"{key}.ladder.{name}.rank"),
                description=str(raw.get("description") or ""),
                color=str(raw.get("color") or ""),
            )
        )
    ranks = [lv.rank for lv in levels]
    if len(set(ranks)) != len(ranks):
        raise ScorecardError(f"{key}: ladder levels must have distinct ranks")

    raw_dimensions = data.get("dimensions")
    if raw_dimensions is None:
        raw_dimensions = DEFAULT_DIMENSIONS
    dimensions: list[Dimension] = []
    for raw in raw_dimensions or []:
        dim_key = str(raw.get("key") or "").strip()
        if not dim_key:
            raise ScorecardError(f"{key}: dimension missing 'key'")
        if dim_key == UNASSIGNED_DIMENSION:
            raise ScorecardError(
                f"{key}: {UNASSIGNED_DIMENSION!r} is reserved for rules that declare "
                f"no dimension and cannot be declared explicitly"
            )
        dimensions.append(
            Dimension(
                key=dim_key,
                label=str(raw.get("label") or dim_key.replace("_", " ").title()),
                description=str(raw.get("description") or ""),
                column=str(raw.get("column") or ""),
            )
        )
    dim_keys = [d.key for d in dimensions]
    if len(set(dim_keys)) != len(dim_keys):
        raise ScorecardError(f"{key}: duplicate dimension keys")

    grading = data.get("grading") or {}
    raw_bands = grading.get("bands") if isinstance(grading, dict) else None
    if raw_bands:
        bands: list[tuple[str, float]] = []
        for raw in raw_bands:
            letter = str(raw.get("letter") or "").strip()
            if not letter:
                raise ScorecardError(f"{key}: grading band missing 'letter'")
            try:
                minimum = float(raw.get("min", 0))
            except (TypeError, ValueError):
                raise ScorecardError(
                    f"{key}: grading band {letter!r} has a non-numeric 'min'"
                ) from None
            bands.append((letter, minimum))
        grade_bands = tuple(bands)
    else:
        grade_bands = DEFAULT_GRADE_BANDS

    level_names = {lv.name for lv in levels}
    rules: list[Rule] = []
    seen: set[str] = set()
    for raw in data.get("rules") or []:
        identifier = str(raw.get("identifier") or "").strip()
        if not identifier:
            raise ScorecardError(f"{key}: rule missing 'identifier'")
        if identifier in seen:
            raise ScorecardError(f"{key}: duplicate rule identifier {identifier!r}")
        seen.add(identifier)
        expression = str(raw.get("expression") or "").strip()
        if not expression:
            raise ScorecardError(f"{key}.{identifier}: rule missing 'expression'")
        level = raw.get("level")
        level = str(level).strip() if level else None
        if level and level not in level_names:
            raise ScorecardError(
                f"{key}.{identifier}: level {level!r} is not on the ladder "
                f"({', '.join(sorted(level_names)) or 'no levels defined'})"
            )
        dimension = str(raw.get("dimension") or "").strip() or UNASSIGNED_DIMENSION
        if dimension != UNASSIGNED_DIMENSION and dimension not in set(dim_keys):
            raise ScorecardError(
                f"{key}.{identifier}: dimension {dimension!r} is not declared "
                f"({', '.join(dim_keys) or 'no dimensions defined'})"
            )
        rules.append(
            Rule(
                identifier=identifier,
                expression=expression,
                weight=_as_int(raw.get("weight", 1), f"{key}.{identifier}.weight", 1),
                level=level,
                title=str(raw.get("title") or ""),
                failure_message=str(raw.get("failureMessage") or raw.get("failure_message") or ""),
                filter_expression=(str(raw["filter"]).strip() if raw.get("filter") else None),
                dimension=dimension,
                evidence_note=str(raw.get("evidence") or raw.get("evidence_note") or ""),
            )
        )
    if not rules:
        raise ScorecardError(f"{key}: scorecard defines no rules")

    exemption_block = data.get("exemptions")
    exemption_policy = ExemptionPolicy.from_config(exemption_block)
    # ``exemptions:`` may be either a bare list of grants (the shape shipped
    # before idp-score-04) or a mapping carrying the policy plus ``grants:``.
    # Both are accepted so an existing scorecard keeps working unchanged.
    if isinstance(exemption_block, dict):
        raw_grants = exemption_block.get("grants") or []
    else:
        raw_grants = exemption_block or []
    exemptions = [
        Exemption(
            identifier=str(raw.get("identifier") or "").strip(),
            entity=str(raw.get("entity") or "").strip(),
            reason=str(raw.get("reason") or ""),
            expires=str(raw.get("expires") or ""),
            approved_by=str(raw.get("approvedBy") or raw.get("approved_by") or ""),
            approved_at=str(raw.get("approvedAt") or raw.get("approved_at") or ""),
            # A grant written into the scorecard file is approved by whoever
            # the file says approved it; there is no pending state in YAML,
            # because merging the edit *is* the approval step for config.
            status=ACTIVE_STATUS,
            source="scorecard",
            decision_reason=str(raw.get("decisionReason") or raw.get("decision_reason") or ""),
        )
        for raw in raw_grants
    ]

    evaluation = data.get("evaluation") or {}
    return Scorecard(
        key=key,
        name=str(data.get("name") or key),
        description=str(data.get("description") or ""),
        collection=collection,
        entity_key=str(data.get("entity_key") or DEFAULT_ENTITY_KEY),
        levels=tuple(levels),
        rules=tuple(rules),
        exemptions=tuple(exemptions),
        window=str(evaluation.get("window") or DEFAULT_WINDOW),
        adapter_module=str(data.get("adapter_module") or ""),
        source_path=source_path,
        dimensions=tuple(dimensions),
        grade_bands=grade_bands,
        exemption_policy=exemption_policy,
    )


def load_scorecards(directory: Path | str | None = None) -> list[Scorecard]:
    """Load every ``*.yaml`` scorecard in *directory* (default args/scorecards)."""
    import yaml  # noqa: PLC0415

    root = Path(directory or DEFAULT_SCORECARD_DIR)
    if not root.is_dir():
        return []
    cards: list[Scorecard] = []
    for path in sorted(glob.glob(str(root / "*.yaml")) + glob.glob(str(root / "*.yml"))):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        cards.append(parse_scorecard(raw, source_path=str(path)))
    return cards


def load_scorecard(key: str, directory: Path | str | None = None) -> Scorecard:
    """Load one scorecard by key."""
    for card in load_scorecards(directory):
        if card.key == key:
            return card
    raise ScorecardError(f"No scorecard with key {key!r} in {directory or DEFAULT_SCORECARD_DIR}")


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------


def _ensure_adapter(scorecard: Scorecard) -> None:
    """Import the module that registers the scorecard's collection.

    Derived from the collection prefix (``idp.components`` ->
    ``tools.iqe.adapters.idp``) unless the YAML names ``adapter_module``
    explicitly. Config, not code: pointing a scorecard at a different
    collection needs no change here.
    """
    module = scorecard.resolved_adapter_module()
    try:
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001
        raise ScorecardError(
            f"{scorecard.key}: cannot import adapter module {module!r} for "
            f"collection {scorecard.collection!r}: {exc}"
        ) from exc


def _collection_name(ast: Any) -> str:
    coll = ast.collection
    return str(coll.name) if isinstance(coll, CollectionCall) else str(coll)


def _run_query(
    expression: str,
    scorecard: Scorecard,
    conn: Any,
    where: str,
) -> list[str]:
    """Execute an IQE *expression* and return the entity keys it matched.

    The rule's own SELECT is replaced with the scorecard's ``entity_key`` so a
    rule author can project whatever is useful for reading the query by hand
    without changing what the evaluator scores.
    """
    try:
        ast = parse(expression)
    except IQESyntaxError as exc:
        raise ScorecardError(f"{scorecard.key}.{where}: IQE parse error: {exc}") from exc

    found = _collection_name(ast)
    if found != scorecard.collection:
        raise ScorecardError(
            f"{scorecard.key}.{where}: query reads collection {found!r} but the "
            f"scorecard declares {scorecard.collection!r}"
        )

    ast.select = SelectNode(
        fields=[AttrRef(parts=[ast.var, scorecard.entity_key])], wildcard=False
    )
    try:
        rows = execute_query(ast, conn)
    except Exception as exc:  # noqa: BLE001
        raise ScorecardError(f"{scorecard.key}.{where}: IQE execution failed: {exc}") from exc
    return [str(r.get(scorecard.entity_key)) for r in rows if r.get(scorecard.entity_key) is not None]


def _universe(scorecard: Scorecard, conn: Any) -> list[str]:
    """Every entity the scorecard covers, in collection order."""
    return _run_query(
        f"foreach e in {scorecard.collection} select e.{scorecard.entity_key}",
        scorecard,
        conn,
        where="universe",
    )


def _fact_rows(scorecard: Scorecard, conn: Any) -> dict[str, dict[str, Any]]:
    """Every entity's full fact row, keyed by entity id.

    This is the evidence source: the rule expressions read these fields, so
    showing the reader the same row is what makes a verdict checkable rather
    than merely asserted. One wildcard query, not one per entity.

    Degrades to ``{}`` rather than raising — evidence is an enrichment, and a
    collection that will not project its rows should cost the report its
    observed values, not its scores.
    """
    try:
        ast = parse(f"foreach e in {scorecard.collection} select e.{scorecard.entity_key}")
        ast.select = SelectNode(fields=[], wildcard=True)
        rows = execute_query(ast, conn)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        entity = row.get(scorecard.entity_key)
        if entity is not None:
            out[str(entity)] = row
    return out


def _predicate_fields(expression: str, entity_key: str) -> list[str]:
    """Fact fields an IQE expression's WHERE clauses read, in first-seen order.

    Used only to decide which observed values to show as evidence, so a parse
    failure costs the evidence its field list, not the evaluation — the
    expression itself is already re-parsed (and its errors raised) by
    :func:`_run_query`.
    """
    try:
        ast = parse(expression)
    except Exception:  # noqa: BLE001
        return []

    fields: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, AttrRef):
            # ``c.has_owner`` -> "has_owner"; a bare ``c`` contributes nothing.
            if len(node.parts) > 1:
                name = node.parts[-1]
                if name not in fields:
                    fields.append(name)
        elif isinstance(node, BinOp):
            walk(node.left)
            walk(node.right)

    for clause in getattr(ast, "where_clauses", None) or []:
        walk(getattr(clause, "predicate", None))

    # The entity key is how the row is addressed, not something the rule
    # measures — showing it as evidence is noise on every single rule.
    return [f for f in fields if f != entity_key]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _today() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).date().isoformat()


def _assign_level(
    outcomes: Iterable[RuleOutcome], ladder: list[Level]
) -> tuple[str | None, int]:
    """Walk the ladder bottom-up, stopping at the first rung not fully met.

    Only leveled rules gate. A rule that is not applicable to this entity
    cannot hold it back. Returns ``(level_name, rank)``; ``(None, 0)`` when the
    entity does not clear the lowest rung *or* when nothing measured it.

    A rung whose rules are all not-applicable is met vacuously, which is what
    lets a non-canvas reach Gold on ``e2e-spec`` alone while the canvas-only
    ``completeness-gate`` sits out. But when *every* leveled rule is
    not-applicable, that same vacuous-truth walk runs clean to the TOP of the
    ladder and hands the highest rung to an entity nothing ever looked at —
    ``all([])`` is ``True`` at every rung. Measured 2026-08-02 against a
    canvas-filtered scorecard: 30 of 67 components were awarded the top rung
    with ``assessed=False``. An empty ``by_level`` is therefore the "no
    evidence at all" case and returns unranked, so the ladder cannot be
    climbed by never being looked at.

    An ``exempt`` outcome sits out the same way a not-applicable one does (see
    :attr:`RuleOutcome.gating`), so an approved waiver lets progression
    continue past the rung it was granted on rather than freezing the entity
    below it — which is the whole reason to grant one.
    """
    by_level: dict[str, list[RuleOutcome]] = {}
    for outcome in outcomes:
        if outcome.gating:
            by_level.setdefault(outcome.level or "", []).append(outcome)

    if not by_level:
        return (None, 0)

    attained: Level | None = None
    for level in ladder:
        if not all(o.credited for o in by_level.get(level.name, [])):
            break
        attained = level
    return (attained.name, attained.rank) if attained else (None, 0)


def _store_exemptions(scorecard: Scorecard, conn: Any) -> list[Exemption]:
    """Approved waivers from the append-only log, as :class:`Exemption` values.

    Only attempted when a connection was handed in. Opening the ambient
    database from inside a pure evaluation would make the result depend on
    whichever database the process happens to be pointed at — the "ambient host
    state" trap — and would break every caller that evaluates a synthetic
    collection with ``conn=None``. A missing table costs the report its stored
    waivers, not its scores.
    """
    if conn is None:
        return []
    try:
        from tools.idp.exemptions import current_states  # noqa: PLC0415

        states = current_states(scorecard.key, conn=conn)
    except Exception:  # noqa: BLE001
        return []
    return [
        Exemption(
            identifier=str(state.get("rule_identifier") or ""),
            entity=str(state.get("component_key") or ""),
            reason=str(state.get("reason") or ""),
            expires=str(state.get("expires_on") or ""),
            approved_by=str(state.get("decided_by") or ""),
            approved_at=str(state.get("recorded_at") or ""),
            status=str(state.get("status") or ""),
            source="approval-log",
            decision_reason=str(state.get("decision_reason") or ""),
            auto_approved=bool(state.get("auto_approved")),
        )
        for state in states
    ]


def _resolve_exemptions(
    scorecard: Scorecard,
    conn: Any,
    today: str,
) -> tuple[dict[tuple[str, str], Exemption], list[dict[str, Any]]]:
    """Split every known grant into the ones that apply and the ones that do not.

    The approval log wins over a YAML grant for the same (rule, entity): a
    revocation recorded in the log must be able to switch off a waiver that is
    still written in the file, otherwise revoking would require a code change
    and would not take effect until it merged.

    Returns ``(active_by_target, inert)``. The inert list is reported rather
    than discarded — an exemption that silently failed to apply is as confusing
    as one that silently applied.
    """
    policy = scorecard.exemption_policy
    active: dict[tuple[str, str], Exemption] = {}
    inert: list[dict[str, Any]] = []
    for grant in list(scorecard.exemptions) + _store_exemptions(scorecard, conn):
        target = (grant.identifier, grant.entity)
        defect = grant.defect(today, policy)
        if defect:
            inert.append({**grant.to_dict(), "defect": defect})
            # A revoked/denied log entry also clears any YAML grant it shadows.
            active.pop(target, None)
        else:
            active[target] = grant
    return active, inert


def evaluate(
    scorecard: Scorecard,
    conn: Any = None,
    today: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate *scorecard* over every entity in its collection.

    Returns a JSON-safe report: the ladder, per-rule pass counts, and one
    :class:`EntityResult` per entity.

    Tenant scoping (idp-mt-01)
    --------------------------
    ``tenant_id`` binds the scope for the whole evaluation — the universe, each
    rule's query, each filter and the fact rows behind the evidence all read
    the same reduced collection, so a rule can never pass an entity the
    universe excluded. The reduction itself happens in the collection adapter
    (see ``tools/idp/tenancy.py``); this function only pins which tenant is in
    scope, and reports it back as ``tenant_id`` so a caller persisting the
    result knows whose row it is writing.

    The binding is explicit rather than ambient because the same process serves
    both readings: a scheduled reflex recording the platform's own series must
    keep the platform view even when it runs inside a tenant's request context,
    and ``evaluate(card, tenant_id=None)`` from that reflex says so.
    """
    with _tenant_scope(tenant_id):
        return _evaluate_scoped(scorecard, conn, today, tenant_id)


def _tenant_scope(tenant_id: str | None) -> Any:
    """Bind the IDP tenant scope, or a no-op when the tenancy layer is absent."""
    try:
        from tools.idp.tenancy import tenant_scope  # noqa: PLC0415

        return tenant_scope(tenant_id)
    except Exception:  # noqa: BLE001
        from contextlib import nullcontext  # noqa: PLC0415

        return nullcontext()


def _evaluate_scoped(
    scorecard: Scorecard,
    conn: Any,
    today: str | None,
    tenant_id: str | None,
) -> dict[str, Any]:
    """Body of :func:`evaluate`, run with the tenant scope already bound."""
    _ensure_adapter(scorecard)
    today = today or _today()

    entities = _universe(scorecard, conn)
    ladder = scorecard.ladder()
    dimensions = scorecard.dimension_order()
    facts = _fact_rows(scorecard, conn)
    adapter_module = scorecard.resolved_adapter_module()

    active_exemptions, inert_exemptions = _resolve_exemptions(scorecard, conn, today)

    # Evidence field lists are per rule, not per entity — parse once.
    rule_fields = {
        rule.identifier: _predicate_fields(rule.expression, scorecard.entity_key)
        for rule in scorecard.rules
    }

    # One IQE query per rule (plus one per filter) — not one per entity.
    passing: dict[str, set[str]] = {}
    applicable: dict[str, set[str]] = {}
    for rule in scorecard.rules:
        passing[rule.identifier] = set(
            _run_query(rule.expression, scorecard, conn, where=rule.identifier)
        )
        if rule.filter_expression:
            applicable[rule.identifier] = set(
                _run_query(
                    rule.filter_expression, scorecard, conn, where=f"{rule.identifier}.filter"
                )
            )
        else:
            applicable[rule.identifier] = set(entities)

    results: list[EntityResult] = []
    for entity in entities:
        outcomes: list[RuleOutcome] = []
        for rule in scorecard.rules:
            waiver: Exemption | None = active_exemptions.get((rule.identifier, entity))
            if entity not in applicable[rule.identifier]:
                status = "not_applicable"
                message = ""
                waiver = None
            elif entity in passing[rule.identifier]:
                status = "pass"
                message = ""
                # A passing rule needs no waiver; reporting one here would
                # suggest the pass was bought rather than earned.
                waiver = None
            elif waiver is not None:
                status = "exempt"
                message = waiver.reason
            else:
                status = "fail"
                message = rule.failure_message
            fact_row = facts.get(entity) or {}
            fields = rule_fields.get(rule.identifier) or []
            outcomes.append(
                RuleOutcome(
                    identifier=rule.identifier,
                    status=status,
                    weight=rule.weight,
                    level=rule.level,
                    message=message,
                    dimension=rule.dimension,
                    evidence=Evidence(
                        collection=scorecard.collection,
                        adapter_module=adapter_module,
                        expression=rule.expression,
                        filter_expression=rule.filter_expression or "",
                        fields=list(fields),
                        # Only fields the fact row actually carries. A field the
                        # collection does not expose is absent from the evidence
                        # rather than rendered as null, which would read as "the
                        # value is empty" instead of "this was never read".
                        observed={f: fact_row[f] for f in fields if f in fact_row},
                        note=rule.evidence_note,
                    ),
                    exemption=waiver.to_dict() if waiver is not None else None,
                )
            )

        total = sum(o.weight for o in outcomes if o.counted)
        earned = sum(o.weight for o in outcomes if o.credited)
        score = _score_of(earned, total)
        level_name, level_rank = _assign_level(outcomes, ladder)
        results.append(
            EntityResult(
                entity=entity,
                level=level_name,
                level_rank=level_rank,
                score=score,
                letter_grade=scorecard.letter_grade(score),
                earned_weight=earned,
                total_weight=total,
                outcomes=outcomes,
                dimensions=_dimension_results(scorecard, dimensions, outcomes),
            )
        )

    return {
        "scorecard": scorecard.key,
        "name": scorecard.name,
        "collection": scorecard.collection,
        "window": scorecard.window,
        "evaluated_on": today,
        # Whose estate this report describes. None is the platform's own view.
        # Carried through to the persisted history row so a stored score is
        # never ambiguous about which tenant it graded.
        "tenant_id": tenant_id,
        "entity_count": len(results),
        "ladder": [dataclasses.asdict(lv) for lv in ladder],
        "level_distribution": _level_distribution(results, ladder),
        "dimensions": [dataclasses.asdict(d) for d in dimensions],
        "grade_bands": [{"letter": lt, "min": mn} for lt, mn in scorecard.grade_bands],
        "grade_distribution": _grade_distribution(results, scorecard),
        "assessed_count": sum(1 for r in results if r.assessed),
        "adapter_module": adapter_module,
        # Exemptions are reported, never implicit: the policy in force, every
        # waiver that applied (with its approver), and every one that did not
        # (with the reason it did not).
        "exemptions": {
            "policy": scorecard.exemption_policy.to_dict(),
            "active_count": len(active_exemptions),
            "inert_count": len(inert_exemptions),
            "active": [ex.to_dict() for ex in active_exemptions.values()],
            "inert": inert_exemptions,
        },
        # Repo-relative: the absolute path is a property of whichever worktree
        # happened to render the page, and printing it in the UI leaks a local
        # filesystem layout while telling the reader nothing they can act on.
        "source_path": _relative_source(scorecard.source_path),
        "rules": [
            {
                "identifier": r.identifier,
                "title": r.title,
                "weight": r.weight,
                "level": r.level,
                "dimension": r.dimension,
                "gates_ladder": r.gates_ladder,
                "expression": r.expression,
                "filter_expression": r.filter_expression or "",
                "evidence_fields": rule_fields.get(r.identifier) or [],
                "evidence_note": r.evidence_note,
                "applicable": len(applicable[r.identifier]),
                "passing": len(applicable[r.identifier] & passing[r.identifier]),
                # Entities the rule covers but was waived for. Kept as its own
                # count rather than folded into either side: an exempt entity
                # is neither a pass to celebrate nor a failure to chase.
                "exempt": len(
                    {
                        entity
                        for (identifier, entity) in active_exemptions
                        if identifier == r.identifier
                        and entity in applicable[r.identifier]
                        and entity not in passing[r.identifier]
                    }
                ),
            }
            for r in scorecard.rules
        ],
        "results": [r.to_dict() for r in results],
    }


def _relative_source(path: str) -> str:
    """``source_path`` relative to the repo root, or unchanged when outside it."""
    if not path:
        return ""
    try:
        return Path(path).resolve().relative_to(BASE_DIR).as_posix()
    except (ValueError, OSError):
        return path


def _dimension_results(
    scorecard: Scorecard,
    dimensions: list[Dimension],
    outcomes: list[RuleOutcome],
) -> list[DimensionResult]:
    """Score one entity's outcomes per dimension, in declared order.

    A dimension whose rules all came back ``not_applicable`` for this entity
    scores ``None``, not 0 — see :func:`_score_of`.
    """
    by_dimension: dict[str, list[RuleOutcome]] = {}
    for outcome in outcomes:
        by_dimension.setdefault(outcome.dimension, []).append(outcome)

    out: list[DimensionResult] = []
    for dim in dimensions:
        members = by_dimension.get(dim.key, [])
        total = sum(o.weight for o in members if o.counted)
        earned = sum(o.weight for o in members if o.credited)
        score = _score_of(earned, total)
        out.append(
            DimensionResult(
                key=dim.key,
                label=dim.label,
                column=dim.column,
                earned_weight=earned,
                total_weight=total,
                score=score,
                letter_grade=scorecard.letter_grade(score),
                outcomes=members,
            )
        )
    return out


def _level_distribution(results: list[EntityResult], ladder: list[Level]) -> dict[str, int]:
    """Ladder histogram. ``unassessed`` is its own bucket, never folded into
    ``unranked`` — "measured and did not clear Bronze" and "nothing measured
    it" are different facts, and the ladder chart is the one place a reader
    counts components per rung.
    """
    dist = {"unranked": 0, "unassessed": 0}
    for level in ladder:
        dist[level.name] = 0
    for result in results:
        if not result.assessed:
            dist["unassessed"] += 1
        else:
            dist[result.level or "unranked"] += 1
    return dist


def _grade_distribution(results: list[EntityResult], scorecard: Scorecard) -> dict[str, int]:
    """Letter-grade histogram, with ``unassessed`` kept as its own bucket."""
    dist = {letter: 0 for letter, _ in scorecard.grade_bands}
    dist["unassessed"] = 0
    for result in results:
        dist[result.letter_grade or "unassessed"] += 1
    return dist


def evaluate_all(
    directory: Path | str | None = None,
    conn: Any = None,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every scorecard on disk, all against the same tenant scope."""
    return [
        evaluate(card, conn=conn, tenant_id=tenant_id)
        for card in load_scorecards(directory)
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _pct(score: float | None) -> str:
    """Percentage for the CLI table — "n/a", never "0%", when unassessed.

    The CLI must not blur unassessed into zero any more than the page does.
    """
    return f"{score:.0f}%" if score is not None else "n/a"


def _render(report: dict[str, Any], component: str | None) -> str:
    lines = [
        f"{report['name']} ({report['scorecard']}) — {report['entity_count']} entities "
        f"over {report['collection']}",
        # Printed only when scoped: an unqualified entity count read as the
        # whole estate when it was one tenant's slice would be the wrong
        # number in the most convincing possible way.
        *(
            [f"Tenant: {report['tenant_id']} (scoped to this tenant's enabled components)"]
            if report.get("tenant_id")
            else []
        ),
        "",
    ]
    dist = report["level_distribution"]
    lines.append("Ladder: " + ", ".join(f"{k}={v}" for k, v in dist.items()))
    lines.append("Grades: " + ", ".join(f"{k}={v}" for k, v in report["grade_distribution"].items()))
    lines.append("")
    lines.append("Rules:")
    for rule in report["rules"]:
        gate = f"gates {rule['level']}" if rule["gates_ladder"] else "scores only"
        waived = f" +{rule['exempt']} exempt" if rule.get("exempt") else ""
        lines.append(
            f"  {rule['identifier']:<28} {rule['passing']:>3}/{rule['applicable']:<3} "
            f"w={rule['weight']:<3} [{rule['dimension']}] ({gate}){waived}"
        )
    exemptions = report.get("exemptions") or {}
    if exemptions.get("active") or exemptions.get("inert"):
        lines.append("")
        lines.append("Exemptions:")
        for grant in exemptions.get("active") or []:
            lines.append(
                f"  {grant['identifier']}:{grant['entity']} — approved by "
                f"{grant['approved_by']} ({grant['source']})"
                + (f", expires {grant['expires']}" if grant.get("expires") else "")
                + f" — {grant.get('reason') or ''}"
            )
        for grant in exemptions.get("inert") or []:
            # An exemption that does NOT apply is printed too. Dropping it
            # would leave an operator staring at a failing rule they believe
            # they waived, with nothing on screen explaining why it did not
            # take.
            lines.append(
                f"  {grant['identifier']}:{grant['entity']} — INERT: {grant['defect']}"
            )
    lines.append("")
    rows = report["results"]
    if component:
        rows = [r for r in rows if r["entity"] == component]
        if not rows:
            lines.append(f"(no entity {component!r} in this scorecard)")
    dim_keys = [d["key"] for d in report.get("dimensions") or []]
    header = "".join(f"{k[:9]:>10}" for k in dim_keys)
    lines.append(f"{'entity':<24} {'level':<10} {'grade':<6}{'score':>6} {header}  failures")
    lines.append("-" * (78 + 10 * len(dim_keys)))
    for row in sorted(
        rows, key=lambda r: (-r["level_rank"], -(r["score"] or -1), r["entity"])
    ):
        failures = [o["identifier"] for o in row["rules"] if o["status"] == "fail"]
        by_dim = {d["key"]: d for d in row.get("dimensions") or []}
        dims = "".join(f"{_pct(by_dim.get(k, {}).get('score')):>10}" for k in dim_keys)
        score = _pct(row["score"])
        lines.append(
            f"{row['entity']:<24} {row['level'] or '-':<10} "
            f"{row['letter_grade'] or '-':<6}{score:>6} {dims}  "
            + (", ".join(failures) if failures else "-")
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/idp/scorecard.py",
        description="Evaluate scorecard-as-code definitions (args/scorecards/*.yaml).",
    )
    ap.add_argument("--list", action="store_true", help="List available scorecards and exit")
    ap.add_argument("--scorecard", metavar="KEY", help="Evaluate only this scorecard")
    ap.add_argument("--component", metavar="KEY", help="Show only this entity's row")
    ap.add_argument("--dir", metavar="PATH", help="Scorecard directory (default args/scorecards)")
    ap.add_argument(
        "--tenant",
        metavar="ID",
        help="Score only the components this tenant has enabled "
        "(default: the platform's own full estate)",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    try:
        cards = load_scorecards(args.dir)
    except ScorecardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.scorecard:
        cards = [c for c in cards if c.key == args.scorecard]
        if not cards:
            print(f"error: no scorecard {args.scorecard!r}", file=sys.stderr)
            return 2

    if args.list:
        payload = [
            {
                "key": c.key,
                "name": c.name,
                "collection": c.collection,
                "levels": [lv.name for lv in c.ladder()],
                "rules": len(c.rules),
                "gating_rules": sum(1 for r in c.rules if r.gates_ladder),
                "source": c.source_path,
            }
            for c in cards
        ]
        print(json.dumps(payload, indent=2) if args.json else "\n".join(
            f"{p['key']:<24} {p['rules']:>2} rules ({p['gating_rules']} gating)  "
            f"ladder: {' < '.join(p['levels'])}"
            for p in payload
        ))
        return 0

    if not cards:
        print("error: no scorecards found", file=sys.stderr)
        return 2

    conn = None
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()
    except Exception:
        conn = None

    try:
        reports = []
        for card in cards:
            reports.append(evaluate(card, conn=conn, tenant_id=args.tenant))
    except ScorecardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass

    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2, default=str))
    else:
        print("\n\n".join(_render(r, args.component) for r in reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
