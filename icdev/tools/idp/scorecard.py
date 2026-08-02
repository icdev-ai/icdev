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

Adding a rule, a level, or a whole new scorecard is a YAML edit. No Python
change is required — that is the point of this module.
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

from tools.iqe import IQESyntaxError, execute_query, parse  # noqa: E402
from tools.iqe.ast_nodes import AttrRef, CollectionCall, SelectNode  # noqa: E402

DEFAULT_SCORECARD_DIR = BASE_DIR / "args" / "scorecards"

# Default entity-identifier field and evaluation window, overridable per file.
DEFAULT_ENTITY_KEY = "key"
DEFAULT_WINDOW = "24h"


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
class Rule:
    """One scorecard rule — an IQE query plus its scoring metadata."""

    identifier: str
    expression: str
    weight: int = 1
    level: str | None = None
    title: str = ""
    failure_message: str = ""
    filter_expression: str | None = None

    @property
    def gates_ladder(self) -> bool:
        """True when this rule blocks ladder progression (it declares a level)."""
        return self.level is not None


@dataclasses.dataclass(frozen=True)
class Exemption:
    """Waives one rule for one entity. Approval/audit lands in idp-score-04."""

    identifier: str
    entity: str
    reason: str = ""
    expires: str = ""

    def is_active(self, today: str) -> bool:
        """Active unless it carries an ``expires`` date that has passed."""
        if not self.expires:
            return True
        return str(self.expires) >= today


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

    def ladder(self) -> list[Level]:
        """Levels ordered worst-to-best."""
        return sorted(self.levels, key=lambda lv: lv.rank)


@dataclasses.dataclass
class RuleOutcome:
    """How one rule landed for one entity."""

    identifier: str
    status: str  # pass | fail | exempt | not_applicable
    weight: int
    level: str | None
    message: str = ""

    @property
    def counted(self) -> bool:
        """Does this outcome participate in the score?"""
        return self.status in ("pass", "fail", "exempt")

    @property
    def credited(self) -> bool:
        """Does this outcome earn its weight? Exemptions credit, by design."""
        return self.status in ("pass", "exempt")


@dataclasses.dataclass
class EntityResult:
    """One entity's standing against a scorecard."""

    entity: str
    level: str | None
    level_rank: int
    score: float
    earned_weight: int
    total_weight: int
    outcomes: list[RuleOutcome] = dataclasses.field(default_factory=list)

    def failures(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes if o.status == "fail"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "level": self.level,
            "level_rank": self.level_rank,
            "score": round(self.score, 1),
            "earned_weight": self.earned_weight,
            "total_weight": self.total_weight,
            "rules": [dataclasses.asdict(o) for o in self.outcomes],
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
        rules.append(
            Rule(
                identifier=identifier,
                expression=expression,
                weight=_as_int(raw.get("weight", 1), f"{key}.{identifier}.weight", 1),
                level=level,
                title=str(raw.get("title") or ""),
                failure_message=str(raw.get("failureMessage") or raw.get("failure_message") or ""),
                filter_expression=(str(raw["filter"]).strip() if raw.get("filter") else None),
            )
        )
    if not rules:
        raise ScorecardError(f"{key}: scorecard defines no rules")

    exemptions = [
        Exemption(
            identifier=str(raw.get("identifier") or "").strip(),
            entity=str(raw.get("entity") or "").strip(),
            reason=str(raw.get("reason") or ""),
            expires=str(raw.get("expires") or ""),
        )
        for raw in data.get("exemptions") or []
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
    module = scorecard.adapter_module or (
        f"tools.iqe.adapters.{scorecard.collection.split('.')[0]}"
    )
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
    entity does not clear the lowest rung.
    """
    by_level: dict[str, list[RuleOutcome]] = {}
    for outcome in outcomes:
        if outcome.level and outcome.status != "not_applicable":
            by_level.setdefault(outcome.level, []).append(outcome)

    attained: Level | None = None
    for level in ladder:
        if not all(o.credited for o in by_level.get(level.name, [])):
            break
        attained = level
    return (attained.name, attained.rank) if attained else (None, 0)


def evaluate(
    scorecard: Scorecard,
    conn: Any = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Evaluate *scorecard* over every entity in its collection.

    Returns a JSON-safe report: the ladder, per-rule pass counts, and one
    :class:`EntityResult` per entity.
    """
    _ensure_adapter(scorecard)
    today = today or _today()

    entities = _universe(scorecard, conn)
    ladder = scorecard.ladder()

    active_exemptions = {
        (ex.identifier, ex.entity) for ex in scorecard.exemptions if ex.is_active(today)
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
            if entity not in applicable[rule.identifier]:
                status = "not_applicable"
                message = ""
            elif entity in passing[rule.identifier]:
                status = "pass"
                message = ""
            elif (rule.identifier, entity) in active_exemptions:
                status = "exempt"
                message = next(
                    (
                        ex.reason
                        for ex in scorecard.exemptions
                        if ex.identifier == rule.identifier and ex.entity == entity
                    ),
                    "",
                )
            else:
                status = "fail"
                message = rule.failure_message
            outcomes.append(
                RuleOutcome(
                    identifier=rule.identifier,
                    status=status,
                    weight=rule.weight,
                    level=rule.level,
                    message=message,
                )
            )

        total = sum(o.weight for o in outcomes if o.counted)
        earned = sum(o.weight for o in outcomes if o.credited)
        level_name, level_rank = _assign_level(outcomes, ladder)
        results.append(
            EntityResult(
                entity=entity,
                level=level_name,
                level_rank=level_rank,
                score=(earned / total * 100.0) if total else 0.0,
                earned_weight=earned,
                total_weight=total,
                outcomes=outcomes,
            )
        )

    return {
        "scorecard": scorecard.key,
        "name": scorecard.name,
        "collection": scorecard.collection,
        "window": scorecard.window,
        "evaluated_on": today,
        "entity_count": len(results),
        "ladder": [dataclasses.asdict(lv) for lv in ladder],
        "level_distribution": _level_distribution(results, ladder),
        "rules": [
            {
                "identifier": r.identifier,
                "title": r.title,
                "weight": r.weight,
                "level": r.level,
                "gates_ladder": r.gates_ladder,
                "applicable": len(applicable[r.identifier]),
                "passing": len(applicable[r.identifier] & passing[r.identifier]),
            }
            for r in scorecard.rules
        ],
        "results": [r.to_dict() for r in results],
    }


def _level_distribution(results: list[EntityResult], ladder: list[Level]) -> dict[str, int]:
    dist = {"unranked": 0}
    for level in ladder:
        dist[level.name] = 0
    for result in results:
        dist[result.level or "unranked"] += 1
    return dist


def evaluate_all(
    directory: Path | str | None = None, conn: Any = None
) -> list[dict[str, Any]]:
    """Evaluate every scorecard on disk."""
    return [evaluate(card, conn=conn) for card in load_scorecards(directory)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(report: dict[str, Any], component: str | None) -> str:
    lines = [
        f"{report['name']} ({report['scorecard']}) — {report['entity_count']} entities "
        f"over {report['collection']}",
        "",
    ]
    dist = report["level_distribution"]
    lines.append("Ladder: " + ", ".join(f"{k}={v}" for k, v in dist.items()))
    lines.append("")
    lines.append("Rules:")
    for rule in report["rules"]:
        gate = f"gates {rule['level']}" if rule["gates_ladder"] else "scores only"
        lines.append(
            f"  {rule['identifier']:<28} {rule['passing']:>3}/{rule['applicable']:<3} "
            f"w={rule['weight']:<3} ({gate})"
        )
    lines.append("")
    rows = report["results"]
    if component:
        rows = [r for r in rows if r["entity"] == component]
        if not rows:
            lines.append(f"(no entity {component!r} in this scorecard)")
    lines.append(f"{'entity':<24} {'level':<12} {'score':>6}  failures")
    lines.append("-" * 78)
    for row in sorted(rows, key=lambda r: (-r["level_rank"], -r["score"], r["entity"])):
        failures = [o["identifier"] for o in row["rules"] if o["status"] == "fail"]
        lines.append(
            f"{row['entity']:<24} {row['level'] or '-':<12} {row['score']:>5.0f}%  "
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
            reports.append(evaluate(card, conn=conn))
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
