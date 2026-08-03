# CUI // SP-CTI
"""Gap seeder — a failing scorecard rule becomes a kanban task.

This is the half of an internal developer platform that a catalog product
cannot ship. Cortex.io grades a service, shows you the red cell, and stops:
closing the gap is a human's problem, tracked somewhere else. ICDEV already
owns the other end of that loop — ``kanban_tasks`` plus an autonomous build
pipeline — so a red cell can become work that actually gets done.

What it does, precisely: evaluate ``args/scorecards/*.yaml`` (idp-score-02),
take every ``status == "fail"`` outcome, and emit **one task per failing rule
per component**. The rule's ``failureMessage`` becomes the description and the
IQE expression that measured the failure becomes the acceptance criteria, so
the task carries both the reason it exists and the machine-checkable definition
of done — pointed at the same evidence source the grade came from.

    python tools/idp/gap_seeder.py --json              # dry run (the default)
    python tools/idp/gap_seeder.py --seed --json       # actually write
    python tools/idp/gap_seeder.py --scorecard component-readiness

Three things make this safe enough to run on a schedule.

**It is capped, and truncation is loud.** 66 components times 11 rules is ~700
candidate tasks on the first pass. An unbounded seeder in this repo once
produced 353 branches, so the caps are not decoration: ``max_tasks_per_component``
is applied first (one bad component cannot eat the run budget), then
``max_tasks_per_run``. Both truncations are logged at WARNING and reported in
the JSON as ``truncated`` — a cap that silently drops work reads as "nothing
left to do", which is the failure mode that matters here.

**It does not reseed.** The idempotency key is
``idp-gap:<scorecard>:<component>:<rule>`` — stable across runs, so a second
run over an unchanged estate creates nothing. ``task_factory.create_tasks``
dedupes on it, and the seeder also pre-filters on it so already-seeded gaps
never consume the cap. A closed gap that later regresses will *not* reseed
under its old key; that is the deliberate trade for "re-running produces none",
and the score history (idp-score-03) is where a regression shows up.

**Nothing dispatches without confirmation.** Seeded tasks land as
``suggested`` *and* carry ``depends_on_task_id`` pointing at a ``*-gate-00``
sentinel held ``in_progress``. Only the second of those is a real hold —
``_deps_satisfied()`` refuses to promote past an unfinished dependency, whereas
``suggested`` alone can be promoted by the kanban deadlock-breaker. Releasing
the whole batch is one edit: set the gate to ``done``.

Config is ``args/idp_gap_seeder.yaml``; writing is refused until ``enabled``
is flipped there, so the caps get proven by a dry run first.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.idp.scorecard import (  # noqa: E402
    Scorecard,
    ScorecardError,
    evaluate,
    load_scorecards,
)
from tools.kanban.gates import GATE_ID_SUFFIX, GATE_TITLE_MARKER  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

LOG = get_logger("idp_gap_seeder")

DEFAULT_CONFIG_PATH = BASE_DIR / "args" / "idp_gap_seeder.yaml"

#: Prefix for every seeded task id. Deliberately NOT ``idp-``: that is the IDP
#: project card's ``task_prefix``, and remediation tasks are not card tasks —
#: sharing the prefix would fold them into the card's progress percentage.
TASK_ID_PREFIX = "idpgap-"

#: Namespace for the idempotency key. Changing this string reseeds everything.
IDEMPOTENCY_NAMESPACE = "idp-gap"

#: Statuses that mean a gate is no longer holding its dependents back.
_RELEASED_GATE_STATUSES = ("done", "decomposed")

#: Un-levelled rules sort last. Any real ladder rank is far below this.
_UNGATED_RANK = 10_000

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "max_tasks_per_run": 10,
    "max_tasks_per_component": 2,
    "only_gating_rules": False,
    "exclude_rules": [],
    "include_rules": [],
    "task_type": "fix",
    "gate_task_id": "idpgap-gate-00",
    "status": "suggested",
    "priority_by_level": {
        "Bronze": "high",
        "Silver": "medium",
        "Gold": "medium",
        "Platinum": "low",
    },
    "default_priority": "low",
}


class GapSeederError(RuntimeError):
    """Raised when the seeder refuses to write."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load ``args/idp_gap_seeder.yaml``, falling back to safe defaults.

    A missing or unreadable config is not an error — it yields the defaults,
    which have ``enabled: False``. Failing closed here matters: the packaged
    ``icdev/`` copy of this module has no ``args/`` tree beside it.
    """
    config = dict(DEFAULT_CONFIG)
    config["priority_by_level"] = dict(DEFAULT_CONFIG["priority_by_level"])

    target = Path(path or DEFAULT_CONFIG_PATH)
    if not target.is_file():
        LOG.debug("gap_seeder: no config at %s, using defaults", target)
        return config

    try:
        import yaml  # noqa: PLC0415

        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        LOG.warning("gap_seeder: cannot read %s (%s) — using defaults", target, exc)
        return config

    if not isinstance(raw, dict):
        LOG.warning("gap_seeder: %s is not a mapping — using defaults", target)
        return config

    for key, value in raw.items():
        if key in config and value is not None:
            config[key] = value
    return config


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Gap:
    """One component failing one rule of one scorecard."""

    scorecard_key: str
    scorecard_name: str
    collection: str
    component: str
    rule_id: str
    rule_title: str
    expression: str
    failure_message: str
    weight: int
    level: str | None
    level_rank: int
    component_level: str | None
    component_score: float
    source_path: str = ""

    @property
    def idempotency_key(self) -> str:
        """Stable across runs — this is what makes a re-run a no-op."""
        return f"{IDEMPOTENCY_NAMESPACE}:{self.scorecard_key}:{self.component}:{self.rule_id}"

    @property
    def task_id(self) -> str:
        """Deterministic id derived from the idempotency key.

        Hashed rather than concatenated because ``<component>-<rule>`` is
        ambiguous (component ``a-b`` + rule ``c`` collides with component ``a``
        + rule ``b-c``), and a colliding id is skipped silently by the task
        factory — a seeded task that quietly never exists.
        """
        digest = hashlib.sha256(self.idempotency_key.encode("utf-8")).hexdigest()
        return f"{TASK_ID_PREFIX}{digest[:10]}"

    @property
    def gates_ladder(self) -> bool:
        return self.level is not None

    def to_dict(self) -> dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["idempotency_key"] = self.idempotency_key
        payload["task_id"] = self.task_id
        return payload


def gaps_from_report(scorecard: Scorecard, report: dict[str, Any]) -> list[Gap]:
    """Turn one :func:`tools.idp.scorecard.evaluate` report into gaps.

    Only ``fail`` outcomes become gaps. ``exempt`` is a decision someone has
    already made, and ``not_applicable`` was never in scope — seeding either
    would be manufacturing work out of a rule that was answered.
    """
    rules = {rule.identifier: rule for rule in scorecard.rules}
    ranks = {level.name: level.rank for level in scorecard.levels}

    gaps: list[Gap] = []
    for result in report.get("results") or []:
        component = str(result.get("entity") or "")
        for outcome in result.get("rules") or []:
            if outcome.get("status") != "fail":
                continue
            rule = rules.get(outcome.get("identifier"))
            if rule is None:  # report and scorecard disagree — skip rather than guess
                LOG.warning(
                    "gap_seeder: %s reports rule %r that the scorecard does not define",
                    scorecard.key,
                    outcome.get("identifier"),
                )
                continue
            gaps.append(
                Gap(
                    scorecard_key=scorecard.key,
                    scorecard_name=scorecard.name,
                    collection=scorecard.collection,
                    component=component,
                    rule_id=rule.identifier,
                    rule_title=rule.title or rule.identifier,
                    expression=rule.expression,
                    failure_message=(rule.failure_message or "").strip(),
                    weight=rule.weight,
                    level=rule.level,
                    level_rank=ranks.get(rule.level or "", _UNGATED_RANK),
                    component_level=result.get("level"),
                    component_score=float(result.get("score") or 0.0),
                    source_path=scorecard.source_path,
                )
            )
    return gaps


def collect_gaps(
    conn: Any = None,
    *,
    directory: Path | str | None = None,
    scorecard_key: str | None = None,
) -> tuple[list[Gap], list[str]]:
    """Evaluate every scorecard and return ``(gaps, scorecard_keys)``.

    A scorecard that fails to evaluate does not take the others down with it —
    the estate is graded by several cards and one broken adapter should not
    stop the rest from producing work.
    """
    cards = load_scorecards(directory)
    if scorecard_key:
        cards = [c for c in cards if c.key == scorecard_key]
        if not cards:
            raise GapSeederError(f"no scorecard with key {scorecard_key!r}")

    gaps: list[Gap] = []
    evaluated: list[str] = []
    for card in cards:
        try:
            report = evaluate(card, conn=conn)
        except ScorecardError as exc:
            LOG.warning("gap_seeder: skipping scorecard %s — %s", card.key, exc)
            continue
        evaluated.append(card.key)
        gaps.extend(gaps_from_report(card, report))
    return gaps, evaluated


def filter_gaps(gaps: list[Gap], config: dict[str, Any]) -> list[Gap]:
    """Apply the rule-level selection knobs from config."""
    include = {str(r) for r in (config.get("include_rules") or [])}
    exclude = {str(r) for r in (config.get("exclude_rules") or [])}
    only_gating = bool(config.get("only_gating_rules"))

    kept = []
    for gap in gaps:
        if only_gating and not gap.gates_ladder:
            continue
        if include and gap.rule_id not in include:
            continue
        if gap.rule_id in exclude:
            continue
        kept.append(gap)
    return kept


def prioritize(gaps: list[Gap]) -> list[Gap]:
    """Most-urgent first, deterministically.

    Ladder rank ascending, because a Bronze failure is what keeps a component
    off the bottom rung entirely, while a Platinum failure only stops it
    reaching the top. Then weight descending, then keys — so two runs over the
    same estate select the same tasks and the caps are reproducible.
    """
    return sorted(
        gaps,
        key=lambda g: (g.level_rank, -g.weight, g.component, g.rule_id, g.scorecard_key),
    )


def apply_caps(
    gaps: list[Gap], max_per_component: int, max_per_run: int
) -> tuple[list[Gap], dict[str, Any]]:
    """Cap per component first, then per run. Returns ``(kept, truncation)``.

    Per component first is the load-bearing order: capping only the run would
    let the worst component's eight failures consume a budget of ten and starve
    every other component in the estate.
    """
    per_component: dict[str, int] = {}
    after_component: list[Gap] = []
    dropped_by_component = 0
    capped_components: set[str] = set()

    for gap in gaps:
        count = per_component.get(gap.component, 0)
        if max_per_component > 0 and count >= max_per_component:
            dropped_by_component += 1
            capped_components.add(gap.component)
            continue
        per_component[gap.component] = count + 1
        after_component.append(gap)

    if max_per_run > 0 and len(after_component) > max_per_run:
        kept = after_component[:max_per_run]
        dropped_by_run = len(after_component) - max_per_run
    else:
        kept = after_component
        dropped_by_run = 0

    truncation = {
        "by_component_cap": dropped_by_component,
        "by_run_cap": dropped_by_run,
        "components_capped": sorted(capped_components),
        "truncated": bool(dropped_by_component or dropped_by_run),
    }

    if dropped_by_component:
        LOG.warning(
            "gap_seeder: per-component cap (%d) dropped %d candidate task(s) across %d "
            "component(s): %s",
            max_per_component,
            dropped_by_component,
            len(capped_components),
            ", ".join(sorted(capped_components)),
        )
    if dropped_by_run:
        LOG.warning(
            "gap_seeder: per-run cap (%d) truncated %d candidate task(s) — they remain "
            "unseeded and will be offered on the next run",
            max_per_run,
            dropped_by_run,
        )
    return kept, truncation


# ---------------------------------------------------------------------------
# Task specs
# ---------------------------------------------------------------------------


def _priority_for(gap: Gap, config: dict[str, Any]) -> str:
    """Map the gated ladder level to a kanban priority.

    ``critical`` is never returned. A critical card is auto-promoted out of
    ``suggested`` by the kanban deadlock-breaker, which would dispatch the very
    work this seeder is trying to hold for confirmation.
    """
    mapping = config.get("priority_by_level") or {}
    default = str(config.get("default_priority") or "low")
    priority = str(mapping.get(gap.level, default) if gap.level else default)
    if priority == "critical":
        LOG.warning(
            "gap_seeder: priority 'critical' for level %s clamped to 'high' — a critical "
            "card is auto-promoted out of 'suggested'",
            gap.level,
        )
        return "high"
    return priority


def _description(gap: Gap, gate_task_id: str) -> str:
    gate_line = (
        f"Seeded automatically by tools/idp/gap_seeder.py. It is held behind "
        f"`{gate_task_id}` and will not dispatch until a human releases that gate."
    )
    return "\n".join(
        [
            f"Scorecard `{gap.scorecard_name}` ({gap.scorecard_key}) rule "
            f"`{gap.rule_id}` FAILS for component `{gap.component}`.",
            "",
            gap.failure_message or f"{gap.rule_title} — no failureMessage on the rule.",
            "",
            f"- Rule: {gap.rule_title}",
            f"- Gates ladder level: {gap.level or 'none — scores but does not gate'}",
            f"- Weight: {gap.weight}",
            f"- Component stands at: {gap.component_level or 'unranked'}, "
            f"{gap.component_score:.0f}%",
            f"- Scorecard source: {gap.source_path or 'args/scorecards/'}",
            "",
            "Evidence source (the query that measured this):",
            f"    {gap.expression}",
            "",
            "Re-check after the fix:",
            f"    python tools/idp/scorecard.py --scorecard {gap.scorecard_key} "
            f"--component {gap.component}",
            "",
            gate_line,
        ]
    )


def _acceptance_criteria(gap: Gap) -> str:
    return "\n".join(
        [
            f"`python tools/idp/scorecard.py --scorecard {gap.scorecard_key} "
            f"--component {gap.component}` reports rule `{gap.rule_id}` as passing "
            f"(it currently fails).",
            "",
            f"Evidence source: IQE query over `{gap.collection}` —",
            f"    {gap.expression}",
            "",
            "Satisfy it by making the fact true, not by editing the rule, widening the "
            "rule's filter, or adding an exemption. Changing the measurement instead of "
            "the component does not close the gap.",
        ]
    )


def build_task_spec(gap: Gap, config: dict[str, Any]) -> dict[str, Any]:
    """Build the ``task_factory.create_tasks`` spec for one gap."""
    gate_task_id = str(config.get("gate_task_id") or DEFAULT_CONFIG["gate_task_id"])
    return {
        "id": gap.task_id,
        "title": f"{gap.component}: {gap.rule_title}"[:255],
        "description": _description(gap, gate_task_id),
        "acceptance_criteria": _acceptance_criteria(gap),
        "task_type": str(config.get("task_type") or "fix"),
        "priority": _priority_for(gap, config),
        "status": str(config.get("status") or "suggested"),
        "depends_on_task_id": gate_task_id,
        "idempotency_key": gap.idempotency_key,
        "dispatch_source": "idp_gap_seeder",
    }


def gate_spec(gate_task_id: str) -> dict[str, Any]:
    """The sentinel every seeded task waits behind.

    Held ``in_progress`` forever by design. The id must end in ``-gate-00`` so
    ``tools/kanban/gates.py::is_manual_gate`` recognises it and no sweep
    promotes, dispatches, reaps or auto-completes it.
    """
    return {
        "id": gate_task_id,
        "title": f"{GATE_TITLE_MARKER} — IDP scorecard gap remediation",
        "description": (
            "Sentinel, not work. Every task seeded by tools/idp/gap_seeder.py carries "
            "depends_on_task_id pointing here, so none of them can be promoted while "
            "this sits at in_progress.\n\n"
            "Release the whole batch by setting this task to done. Do that only after "
            "reviewing the seeded cards — they were generated from failing scorecard "
            "rules without a human in the loop."
        ),
        "task_type": "chore",
        "priority": "low",
        "status": "in_progress",
        "idempotency_key": f"{IDEMPOTENCY_NAMESPACE}:gate:{gate_task_id}",
    }


# ---------------------------------------------------------------------------
# Board reads/writes
# ---------------------------------------------------------------------------


def _open_connection() -> Any:
    from tools.db.storage import get_connection  # noqa: PLC0415

    return get_connection()


def existing_idempotency_keys(conn: Any, keys: list[str]) -> set[str]:
    """Which of *keys* already have a task, in any status.

    Pre-filtering here rather than leaning on ``create_tasks``' own dedupe is
    what keeps already-seeded gaps from consuming the run cap — otherwise the
    first ten gaps would be re-offered forever and nothing new would ever land.
    """
    if not keys:
        return set()
    found: set[str] = set()
    chunk = 200
    for start in range(0, len(keys), chunk):
        window = keys[start : start + chunk]
        placeholders = ",".join(["%s"] * len(window))
        try:
            rows = conn.execute(
                f"SELECT idempotency_key FROM kanban_tasks WHERE idempotency_key IN ({placeholders})",
                tuple(window),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("gap_seeder: cannot read existing idempotency keys: %s", exc)
            return found
        for row in rows:
            value = dict(row).get("idempotency_key")
            if value:
                found.add(str(value))
    return found


def gate_state(conn: Any, gate_task_id: str) -> dict[str, Any]:
    """Report whether the gate exists and is still holding."""
    if not gate_task_id.endswith(GATE_ID_SUFFIX):
        raise GapSeederError(
            f"gate_task_id {gate_task_id!r} must end in {GATE_ID_SUFFIX!r} or the kanban "
            "sweeps will treat it as ordinary work and complete it"
        )
    try:
        row = conn.execute(
            "SELECT id, status FROM kanban_tasks WHERE id = %s", (gate_task_id,)
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("gap_seeder: cannot read gate %s: %s", gate_task_id, exc)
        return {"id": gate_task_id, "exists": False, "status": None, "holding": False}

    if not row:
        return {"id": gate_task_id, "exists": False, "status": None, "holding": False}
    status = str(dict(row).get("status") or "")
    return {
        "id": gate_task_id,
        "exists": True,
        "status": status,
        "holding": status not in _RELEASED_GATE_STATUSES,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def seed(
    conn: Any = None,
    *,
    directory: Path | str | None = None,
    scorecard_key: str | None = None,
    dry_run: bool = True,
    force: bool = False,
    config: dict[str, Any] | None = None,
    config_path: Path | str | None = None,
    max_per_run: int | None = None,
    max_per_component: int | None = None,
) -> dict[str, Any]:
    """Evaluate, select under the caps, and (unless *dry_run*) seed the board.

    The dry run is the whole point of the shape: it does every read, applies
    every cap, and reports exactly which tasks it would create — so the caps
    are provable before anything is written.
    """
    config = dict(config or load_config(config_path))
    if max_per_run is not None:
        config["max_tasks_per_run"] = max_per_run
    if max_per_component is not None:
        config["max_tasks_per_component"] = max_per_component

    cap_run = int(config.get("max_tasks_per_run") or 0)
    cap_component = int(config.get("max_tasks_per_component") or 0)
    gate_task_id = str(config.get("gate_task_id") or DEFAULT_CONFIG["gate_task_id"])

    owns_connection = conn is None
    if owns_connection:
        conn = _open_connection()

    try:
        gaps, evaluated = collect_gaps(
            conn, directory=directory, scorecard_key=scorecard_key
        )
        selectable = prioritize(filter_gaps(gaps, config))

        seen = existing_idempotency_keys(conn, [g.idempotency_key for g in selectable])
        fresh = [g for g in selectable if g.idempotency_key not in seen]

        chosen, truncation = apply_caps(fresh, cap_component, cap_run)
        specs = [build_task_spec(g, config) for g in chosen]
        gate = gate_state(conn, gate_task_id)

        report: dict[str, Any] = {
            "dry_run": bool(dry_run),
            "enabled": bool(config.get("enabled")),
            "forced": bool(force),
            "scorecards": evaluated,
            "caps": {
                "max_tasks_per_run": cap_run,
                "max_tasks_per_component": cap_component,
            },
            "gaps_found": len(gaps),
            "eligible_after_filters": len(selectable),
            "already_seeded": len(selectable) - len(fresh),
            "candidates": len(fresh),
            "selected": len(chosen),
            "truncated": truncation,
            "gate": gate,
            "tasks": [
                {
                    "id": spec["id"],
                    "title": spec["title"],
                    "priority": spec["priority"],
                    "status": spec["status"],
                    "component": gap.component,
                    "rule": gap.rule_id,
                    "scorecard": gap.scorecard_key,
                    "level": gap.level,
                    "idempotency_key": spec["idempotency_key"],
                    "depends_on_task_id": spec["depends_on_task_id"],
                }
                for gap, spec in zip(chosen, specs)
            ],
            "created": [],
            "gate_created": False,
            "refused": None,
        }

        if dry_run:
            LOG.info(
                "gap_seeder: DRY RUN — %d gap(s), %d candidate(s), would create %d task(s)",
                len(gaps),
                len(fresh),
                len(chosen),
            )
            return report

        if not config.get("enabled") and not force:
            report["refused"] = (
                "seeding is disabled — set `enabled: true` in args/idp_gap_seeder.yaml "
                "or pass --force once the dry run's caps look right"
            )
            LOG.warning("gap_seeder: refusing to write — %s", report["refused"])
            return report

        if gate["exists"] and not gate["holding"]:
            report["refused"] = (
                f"gate {gate_task_id} is {gate['status']!r}, so nothing would hold the "
                "seeded tasks back; re-hold it at in_progress before seeding"
            )
            LOG.warning("gap_seeder: refusing to write — %s", report["refused"])
            return report

        if not specs:
            LOG.info("gap_seeder: nothing to seed — no unseeded gaps under the caps")
            return report

        from tools.kanban.task_factory import create_tasks  # noqa: PLC0415

        if not gate["exists"]:
            created_gate = create_tasks([gate_spec(gate_task_id)])
            report["gate_created"] = bool(created_gate)
            if created_gate:
                LOG.info("gap_seeder: created manual gate %s (in_progress)", gate_task_id)

        report["created"] = create_tasks(specs)
        LOG.info(
            "gap_seeder: seeded %d task(s) behind %s",
            len(report["created"]),
            gate_task_id,
        )
        return report
    finally:
        if owns_connection and conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(report: dict[str, Any]) -> str:
    mode = "DRY RUN" if report["dry_run"] else "SEED"
    truncation = report["truncated"]
    lines = [
        f"[{mode}] scorecards: {', '.join(report['scorecards']) or '(none)'}",
        f"  gaps found            {report['gaps_found']}",
        f"  eligible after filter {report['eligible_after_filters']}",
        f"  already seeded        {report['already_seeded']}",
        f"  candidates            {report['candidates']}",
        f"  selected              {report['selected']}"
        f"   (cap {report['caps']['max_tasks_per_run']}/run, "
        f"{report['caps']['max_tasks_per_component']}/component)",
    ]
    if truncation["truncated"]:
        lines.append(
            f"  TRUNCATED             {truncation['by_component_cap']} by component cap, "
            f"{truncation['by_run_cap']} by run cap"
        )
        if truncation["components_capped"]:
            lines.append(
                f"    components capped:  {', '.join(truncation['components_capped'])}"
            )
    gate = report["gate"]
    lines.append(
        f"  gate                  {gate['id']} "
        f"({gate['status'] or 'not created yet'}, "
        f"{'holding' if gate['holding'] or not gate['exists'] else 'RELEASED'})"
    )
    if report.get("refused"):
        lines.append(f"  REFUSED: {report['refused']}")
    if report["tasks"]:
        lines.append("")
        verb = "would create" if report["dry_run"] else "created"
        lines.append(f"{verb}:")
        lines.append(f"  {'id':<20} {'pri':<8} {'component':<24} rule")
        lines.append("  " + "-" * 74)
        for task in report["tasks"]:
            lines.append(
                f"  {task['id']:<20} {task['priority']:<8} {task['component']:<24} "
                f"{task['rule']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/idp/gap_seeder.py",
        description="Turn failing scorecard rules into gated kanban tasks.",
    )
    ap.add_argument(
        "--seed",
        action="store_true",
        help="Actually write tasks (default is a dry run that writes nothing)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Explicitly request a dry run")
    ap.add_argument("--scorecard", metavar="KEY", help="Limit to one scorecard")
    ap.add_argument("--dir", metavar="PATH", help="Scorecard directory")
    ap.add_argument("--config", metavar="PATH", help="Seeder config (args/idp_gap_seeder.yaml)")
    ap.add_argument("--max-per-run", type=int, metavar="N", help="Override the per-run cap")
    ap.add_argument(
        "--max-per-component", type=int, metavar="N", help="Override the per-component cap"
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Seed even though the config has enabled: false",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    if args.seed and args.dry_run:
        ap.error("--seed and --dry-run are mutually exclusive")

    try:
        report = seed(
            directory=args.dir,
            scorecard_key=args.scorecard,
            dry_run=not args.seed,
            force=args.force,
            config_path=args.config,
            max_per_run=args.max_per_run,
            max_per_component=args.max_per_component,
        )
    except (GapSeederError, ScorecardError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # The NDJSON logger writes to file only, so a cap that truncated has to be
    # said out loud here too — a silent truncation reads as "nothing left".
    truncation = report["truncated"]
    if truncation["truncated"]:
        print(
            f"warning: caps truncated {truncation['by_component_cap']} candidate(s) by the "
            f"per-component cap and {truncation['by_run_cap']} by the per-run cap; they "
            "remain unseeded and will be offered on the next run",
            file=sys.stderr,
        )

    print(json.dumps(report, indent=2, default=str) if args.json else _render(report))
    return 1 if report.get("refused") else 0


if __name__ == "__main__":
    sys.exit(main())
