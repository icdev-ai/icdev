# CUI // SP-CTI
"""Scorecard-as-code — YAML ladder + IQE rule expressions (idp-score-02).

A scorecard lives entirely in ``args/scorecards/<key>.yaml``.  Adding, removing
or re-weighting a rule is a YAML edit; no Python changes and no redeploy.  The
precedent is ``args/mirror_parity.yaml`` — adding a mirrored root is config, not
code — and this module is the same idea applied to component grading.

Why there is no scorecard DSL here
----------------------------------
cortex.io's scorecard schema pairs a ``ladder`` of ranked levels with ``rules``
written in a bespoke expression language.  ICDEV already has that language:
IQE.  So a rule's ``expression`` is a plain IQE query that returns the entities
which PASS it:

    expression: foreach c in idp.components where c.owned == true select c.key

The engine runs the query, reads the ``entity_key`` field out of each returned
row, and everything in that set passed.  Everything in scope but not in the set
failed.  That is the entire evaluation model — the rule language is already
implemented, already sandboxed, and already has a query surface in the UI.

Scope and applicability
-----------------------
An optional per-rule ``filter`` is a second IQE query returning the entities the
rule APPLIES to.  A component outside the filter is *not applicable*: it neither
passes nor fails, and the rule's weight is excluded from its score.  This is how
the 8-point completeness gate — which is defined for dashboard canvases — can
sit in the same scorecard as rules that apply to all 66 components without
failing 30 features for a gate that was never written for them.

The ladder
----------
A rule WITH a ``level`` gates ladder progression.  A component attains the
highest level *L* such that every applicable, non-exempt rule at every rank up
to and including *L* passes.  Ranks with no applicable rules are vacuously
satisfied — a component is never blocked by a level it cannot be measured on.

A rule WITHOUT a ``level`` still scores but does not gate.  Preserving that
distinction is what makes a ladder usable rather than a single pass/fail: a
weak signal ("does the owner have a contact handle?") can move the score
without holding a component off Bronze.

The evaluation window
---------------------
``evaluation.window`` bounds the time-series evidence a scorecard grades on —
health probes older than the window are not counted.  It is applied once, when
the catalog snapshot is fetched, as an IQE parameterised collection call
(``idp.components(90)``).  That is why no rule has to mention the window: every
rule is graded against the same windowed snapshot.

Exemptions
----------
``exemptions`` remove a (rule, component) pair from both the pass count and the
weight, and drop it from ladder gating.  An exemption with an ``expires`` date
in the past is ignored.  Approval workflow and audit logging for exemptions are
idp-score-04; this module implements the schema and the arithmetic only.

CLI
---
    python -m tools.idp.scorecard --list
    python -m tools.idp.scorecard --scorecard component-readiness --json
    python -m tools.idp.scorecard --scorecard component-readiness --failures
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent.parent

#: Where scorecard definitions live. One YAML file per scorecard.
SCORECARD_DIR = BASE_DIR / "args" / "scorecards"

#: Rank reported for a component that has not cleared the lowest ladder level.
UNRATED_RANK = 0
UNRATED_NAME = "Unrated"


class ScorecardError(ValueError):
    """Raised when a scorecard YAML is malformed or a rule cannot be evaluated."""


# ---------------------------------------------------------------------------
# Spec model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Level:
    """One rung of the ladder. Higher *rank* is better."""

    name: str
    rank: int
    description: str = ""
    color: str = ""


@dataclasses.dataclass(frozen=True)
class Rule:
    """One scorecard rule.

    Attributes:
        identifier: Stable id, unique within the scorecard.
        title: Human-readable statement of what passing means.
        expression: IQE query returning the entities that PASS.
        weight: Contribution to the numeric score.
        level: Ladder level this rule gates, or None for score-only rules.
        failure_message: What to do about it when the rule fails.
        filter: Optional IQE query returning the entities the rule applies to.
    """

    identifier: str
    title: str
    expression: str
    weight: float = 1.0
    level: str | None = None
    failure_message: str = ""
    filter: str | None = None


@dataclasses.dataclass(frozen=True)
class Exemption:
    """A (rule, component) pair excluded from scoring and ladder gating."""

    rule: str
    component: str
    reason: str = ""
    approved_by: str = ""
    expires: str | None = None

    def is_active(self, today: date | None = None) -> bool:
        """True unless ``expires`` is a date that has already passed."""
        if not self.expires:
            return True
        try:
            expiry = date.fromisoformat(str(self.expires)[:10])
        except ValueError:
            return True  # an unparseable date is not a silent expiry
        return expiry >= (today or datetime.now(timezone.utc).date())


@dataclasses.dataclass(frozen=True)
class Scorecard:
    """A parsed scorecard definition."""

    key: str
    name: str
    description: str
    collection: str
    entity_key: str
    window: Any
    levels: tuple[Level, ...]
    rules: tuple[Rule, ...]
    exemptions: tuple[Exemption, ...]
    source_path: Path | None = None

    def level_by_name(self, name: str) -> Level | None:
        for lv in self.levels:
            if lv.name == name:
                return lv
        return None

    @property
    def ladder_ranks(self) -> tuple[int, ...]:
        """Ladder ranks in ascending order."""
        return tuple(sorted(lv.rank for lv in self.levels))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _require(mapping: dict, key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ScorecardError(f"{where}: missing required field '{key}'")
    return mapping[key]


def parse_scorecard(data: dict, source_path: Path | None = None) -> Scorecard:
    """Build a :class:`Scorecard` from an already-loaded YAML mapping."""
    where = str(source_path or "<scorecard>")
    if not isinstance(data, dict):
        raise ScorecardError(f"{where}: scorecard must be a mapping")

    key = str(_require(data, "key", where))
    collection = str(data.get("collection") or "idp.components")
    entity_key = str(data.get("entity_key") or "key")

    ladder = data.get("ladder") or {}
    raw_levels = ladder.get("levels") or []
    if not raw_levels:
        raise ScorecardError(f"{where}: ladder.levels must declare at least one level")

    levels: list[Level] = []
    seen_names: set[str] = set()
    seen_ranks: set[int] = set()
    for raw in raw_levels:
        name = str(_require(raw, "name", f"{where}: ladder level"))
        rank = int(_require(raw, "rank", f"{where}: ladder level '{name}'"))
        if name in seen_names:
            raise ScorecardError(f"{where}: duplicate ladder level name '{name}'")
        if rank in seen_ranks:
            raise ScorecardError(f"{where}: duplicate ladder rank {rank}")
        if rank <= UNRATED_RANK:
            raise ScorecardError(
                f"{where}: ladder level '{name}' has rank {rank}; ranks must be >= 1 "
                f"({UNRATED_RANK} is reserved for '{UNRATED_NAME}')"
            )
        seen_names.add(name)
        seen_ranks.add(rank)
        levels.append(
            Level(
                name=name,
                rank=rank,
                description=str(raw.get("description") or ""),
                color=str(raw.get("color") or ""),
            )
        )
    levels.sort(key=lambda lv: lv.rank)

    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for raw in data.get("rules") or []:
        ident = str(_require(raw, "identifier", f"{where}: rule"))
        if ident in seen_ids:
            raise ScorecardError(f"{where}: duplicate rule identifier '{ident}'")
        seen_ids.add(ident)
        level_name = raw.get("level")
        if level_name is not None and not any(lv.name == level_name for lv in levels):
            raise ScorecardError(
                f"{where}: rule '{ident}' references unknown ladder level '{level_name}'"
            )
        weight = float(raw.get("weight", 1.0))
        if weight < 0:
            raise ScorecardError(f"{where}: rule '{ident}' has negative weight {weight}")
        rules.append(
            Rule(
                identifier=ident,
                title=str(raw.get("title") or ident),
                expression=str(_require(raw, "expression", f"{where}: rule '{ident}'")),
                weight=weight,
                level=str(level_name) if level_name is not None else None,
                failure_message=str(raw.get("failure_message") or ""),
                filter=str(raw["filter"]) if raw.get("filter") else None,
            )
        )
    if not rules:
        raise ScorecardError(f"{where}: scorecard declares no rules")

    exemptions = [
        Exemption(
            rule=str(_require(raw, "rule", f"{where}: exemption")),
            component=str(_require(raw, "component", f"{where}: exemption")),
            reason=str(raw.get("reason") or ""),
            approved_by=str(raw.get("approved_by") or ""),
            expires=str(raw["expires"]) if raw.get("expires") else None,
        )
        for raw in data.get("exemptions") or []
    ]
    for ex in exemptions:
        if ex.rule not in seen_ids:
            raise ScorecardError(f"{where}: exemption references unknown rule '{ex.rule}'")

    return Scorecard(
        key=key,
        name=str(data.get("name") or key),
        description=str(data.get("description") or ""),
        collection=collection,
        entity_key=entity_key,
        window=(data.get("evaluation") or {}).get("window"),
        levels=tuple(levels),
        rules=tuple(rules),
        exemptions=tuple(exemptions),
        source_path=source_path,
    )


def load_scorecard(path: Path | str) -> Scorecard:
    """Load and validate one scorecard YAML file."""
    import yaml

    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    except OSError as exc:
        raise ScorecardError(f"{p}: cannot read scorecard: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ScorecardError(f"{p}: invalid YAML: {exc}") from exc
    return parse_scorecard(data, source_path=p)


def list_scorecards(directory: Path | str | None = None) -> list[Path]:
    """Return every scorecard YAML path under *directory*, sorted."""
    d = Path(directory or SCORECARD_DIR)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file())


def load_all_scorecards(directory: Path | str | None = None) -> list[Scorecard]:
    """Load every scorecard in *directory*."""
    return [load_scorecard(p) for p in list_scorecards(directory)]


def find_scorecard(key: str, directory: Path | str | None = None) -> Scorecard:
    """Load the scorecard whose ``key`` (or filename stem) matches *key*."""
    for sc in load_all_scorecards(directory):
        if sc.key == key or (sc.source_path and sc.source_path.stem == key):
            return sc
    raise ScorecardError(f"no scorecard named {key!r} in {Path(directory or SCORECARD_DIR)}")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _run_iqe(query: str, entity_key: str, executor: Any, conn: Any, where: str) -> set[str]:
    """Execute one IQE query on *executor* and return the entity keys it returned."""
    from tools.iqe import IQESyntaxError, parse

    try:
        ast = parse(query)
    except IQESyntaxError as exc:
        raise ScorecardError(f"{where}: IQE parse error: {exc}") from exc

    try:
        rows = executor.run(ast, conn)
    except Exception as exc:  # noqa: BLE001 — surface as a scorecard error, with context
        raise ScorecardError(f"{where}: IQE execution failed: {exc}") from exc

    keys: set[str] = set()
    for row in rows:
        if entity_key not in row:
            raise ScorecardError(
                f"{where}: query must project the entity key '{entity_key}' "
                f"(got fields {sorted(row)}); use 'select <var>.{entity_key}'"
            )
        keys.add(str(row[entity_key]))
    return keys


def fetch_catalog(scorecard: Scorecard, conn: Any = None) -> list[dict]:
    """Fetch the scorecard's collection ONCE, honouring ``evaluation.window``.

    Every rule is graded against this one snapshot rather than re-querying the
    adapter per rule: the adapter behind ``idp.components`` runs a coherence
    check and a probe query, so eight rules would otherwise mean eight full
    rebuilds — and eight chances for two rules to disagree because the catalog
    moved underneath them.

    The window is applied here, as an IQE parameterised collection call
    (``idp.components(90)``), which is why ``evaluation.window`` shapes every
    rule without any rule having to mention it.  Adapters that take no window
    argument are re-fetched without one.
    """
    from tools.idp.component_facts import parse_window
    from tools.iqe import execute_query, parse

    # Collections register as an import side effect; without the import the
    # executor would fall through to its raw-SQL path and look for a table.
    _ensure_collection_registered(scorecard.collection)

    days = parse_window(scorecard.window)
    try:
        return list(execute_query(parse(f"foreach _e in {scorecard.collection}({days}) select *"), conn))
    except TypeError:
        # Adapter does not accept a window argument — it has no time-series facts.
        return list(execute_query(parse(f"foreach _e in {scorecard.collection} select *"), conn))
    except Exception as exc:  # noqa: BLE001
        raise ScorecardError(
            f"scorecard '{scorecard.key}': cannot fetch collection "
            f"'{scorecard.collection}': {exc}"
        ) from exc


def _ensure_collection_registered(collection: str) -> None:
    """Import the adapter module that owns *collection* if it is not registered."""
    from tools.iqe.executor import list_collections

    if collection in list_collections():
        return
    module_name = collection.split(".")[0]
    try:
        __import__(f"tools.iqe.adapters.{module_name}")
    except ImportError:
        pass  # unregistered collection falls through to the executor's own handling


def evaluate_scorecard(
    scorecard: Scorecard,
    conn: Any = None,
    today: date | None = None,
    catalog: list[dict] | None = None,
) -> dict[str, Any]:
    """Evaluate *scorecard* over its collection and grade every entity.

    Args:
        scorecard: A loaded scorecard definition.
        conn: Optional DB connection handed to the IQE collection adapters.
        today: Optional date used to expire exemptions (for deterministic tests).
        catalog: Optional pre-fetched collection rows. Defaults to
            :func:`fetch_catalog`. Supplying rows directly makes the evaluator
            testable without a registry or a database.

    Returns:
        A dict with ``scorecard`` metadata, per-entity ``results``, and a
        ``summary`` giving the level distribution.

    Raises:
        ScorecardError: A rule expression fails to parse, fails to execute, or
            does not project the entity key.
    """
    from tools.iqe.executor import Executor

    rows = fetch_catalog(scorecard, conn) if catalog is None else list(catalog)

    # A private executor bound to the snapshot. Rules therefore cannot mutate or
    # race the global collection registry, and every rule sees the same catalog.
    executor = Executor()
    executor.register_collection(scorecard.collection, lambda _conn=None, *_a: rows)

    entities = sorted(
        {str(r[scorecard.entity_key]) for r in rows if r.get(scorecard.entity_key)}
    )

    active_exemptions = {
        (ex.rule, ex.component) for ex in scorecard.exemptions if ex.is_active(today)
    }

    passing: dict[str, set[str]] = {}
    scoped: dict[str, set[str]] = {}
    for rule in scorecard.rules:
        where = f"scorecard '{scorecard.key}': rule '{rule.identifier}'"
        passing[rule.identifier] = _run_iqe(
            rule.expression, scorecard.entity_key, executor, conn, where
        )
        scoped[rule.identifier] = (
            _run_iqe(rule.filter, scorecard.entity_key, executor, conn, f"{where} filter")
            if rule.filter
            else set(entities)
        )

    results = [
        _grade_entity(scorecard, entity, passing, scoped, active_exemptions)
        for entity in entities
    ]

    distribution: dict[str, int] = {UNRATED_NAME: 0}
    for lv in scorecard.levels:
        distribution[lv.name] = 0
    for r in results:
        distribution[r["level"]] = distribution.get(r["level"], 0) + 1

    return {
        "scorecard": {
            "key": scorecard.key,
            "name": scorecard.name,
            "description": scorecard.description,
            "collection": scorecard.collection,
            "window": scorecard.window,
            "source": str(scorecard.source_path) if scorecard.source_path else None,
            "levels": [dataclasses.asdict(lv) for lv in scorecard.levels],
            "rule_count": len(scorecard.rules),
            "gating_rule_count": sum(1 for r in scorecard.rules if r.level),
        },
        "evaluated_at": (datetime.now(timezone.utc)).isoformat(),
        "entity_count": len(entities),
        "results": results,
        "summary": {
            "level_distribution": distribution,
            "average_score": (
                round(sum(r["score"] for r in results) / len(results), 1) if results else 0.0
            ),
        },
    }


def _grade_entity(
    scorecard: Scorecard,
    entity: str,
    passing: dict[str, set[str]],
    scoped: dict[str, set[str]],
    active_exemptions: set[tuple[str, str]],
) -> dict[str, Any]:
    """Grade one entity: per-rule outcomes, weighted score, ladder level."""
    rule_results: list[dict[str, Any]] = []
    earned = possible = 0.0
    # rank -> did every applicable gating rule at that rank pass?
    rank_ok: dict[int, bool] = {lv.rank: True for lv in scorecard.levels}

    for rule in scorecard.rules:
        exempt = (rule.identifier, entity) in active_exemptions
        applicable = entity in scoped[rule.identifier] and not exempt
        passed = entity in passing[rule.identifier]

        if exempt:
            status = "exempt"
        elif not applicable:
            status = "not_applicable"
        else:
            status = "pass" if passed else "fail"
            possible += rule.weight
            if passed:
                earned += rule.weight

        if rule.level and status == "fail":
            level = scorecard.level_by_name(rule.level)
            if level is not None:
                rank_ok[level.rank] = False

        rule_results.append(
            {
                "identifier": rule.identifier,
                "title": rule.title,
                "level": rule.level,
                "weight": rule.weight,
                "status": status,
                "gating": bool(rule.level),
                "failure_message": rule.failure_message if status == "fail" else "",
            }
        )

    attained_rank = UNRATED_RANK
    attained_name = UNRATED_NAME
    for lv in scorecard.levels:  # ascending rank
        if not rank_ok.get(lv.rank, True):
            break
        attained_rank, attained_name = lv.rank, lv.name

    return {
        "entity": entity,
        "level": attained_name,
        "rank": attained_rank,
        "score": round(100.0 * earned / possible, 1) if possible else 0.0,
        "weight_earned": round(earned, 2),
        "weight_possible": round(possible, 2),
        "rules": rule_results,
        "failures": [r["identifier"] for r in rule_results if r["status"] == "fail"],
    }


def evaluate_named(
    scorecard: str = "component-readiness",
    failures_only: bool = False,
    directory: Path | str | None = None,
) -> dict[str, Any]:
    """Load a scorecard by key and evaluate it. Programmatic / MCP entrypoint.

    Args:
        scorecard: Scorecard key or filename stem.
        failures_only: Drop entities with no failing rule from ``results``.
            ``summary`` and ``entity_count`` still describe the whole catalog.
        directory: Optional scorecard directory override.
    """
    report = evaluate_scorecard(find_scorecard(scorecard, directory))
    if failures_only:
        report["results"] = [r for r in report["results"] if r["failures"]]
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_human(report: dict[str, Any], failures_only: bool) -> str:
    sc = report["scorecard"]
    lines = [
        f"{sc['name']} ({sc['key']}) — {report['entity_count']} entities, "
        f"{sc['rule_count']} rules ({sc['gating_rule_count']} gating)",
        "",
        "Level distribution:",
    ]
    lines += [
        f"  {name:<12} {count}"
        for name, count in report["summary"]["level_distribution"].items()
    ]
    lines += ["", f"Average score: {report['summary']['average_score']}", ""]

    for r in report["results"]:
        if failures_only and not r["failures"]:
            continue
        lines.append(f"  {r['entity']:<24} {r['level']:<10} {r['score']:>5}")
        for rule in r["rules"]:
            if rule["status"] == "fail":
                lines.append(f"      FAIL {rule['identifier']}: {rule['failure_message']}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entrypoint. Returns 0 on success, 2 on a scorecard error."""
    ap = argparse.ArgumentParser(
        prog="python -m tools.idp.scorecard",
        description="Evaluate a YAML scorecard over the ICDEV component catalog.",
    )
    ap.add_argument("--scorecard", help="Scorecard key or filename stem")
    ap.add_argument("--dir", dest="directory", help="Scorecard directory override")
    ap.add_argument("--list", action="store_true", help="List available scorecards and exit")
    ap.add_argument("--failures", action="store_true", help="Show only entities with failures")
    ap.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = ap.parse_args(list(argv) if argv is not None else None)

    try:
        if args.list:
            cards = load_all_scorecards(args.directory)
            if args.json:
                print(json.dumps(
                    [{"key": c.key, "name": c.name, "rules": len(c.rules),
                      "source": str(c.source_path)} for c in cards],
                    indent=2,
                ))
            else:
                for c in cards:
                    print(f"{c.key:<24} {c.name} ({len(c.rules)} rules)")
            return 0

        if not args.scorecard:
            cards = load_all_scorecards(args.directory)
            if len(cards) != 1:
                ap.error("--scorecard is required when more than one scorecard exists")
            scorecard = cards[0]
        else:
            scorecard = find_scorecard(args.scorecard, args.directory)

        report = evaluate_scorecard(scorecard)
    except ScorecardError as exc:
        print(f"scorecard error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, default=str) if args.json
          else _render_human(report, args.failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
