# CUI // SP-CTI
"""Sweep runner — score every registered component and persist the result (idp-score-01-d4).

``tools/quality/component_scorer.py`` knows how to score *a* component;
this module is the operational entry point that runs the sweep across the
whole of ``args/component_registry.yaml`` and writes each verdict to
``developer_scorecards`` via
:func:`~tools.quality.component_scorer.persist_scorecard`.

Two things it adds over calling ``component_scorer.py --all --persist``:

**A dry run that is genuinely dry.**
    ``--dry-run`` scores everything and writes nothing, so the sweep can be
    exercised in CI — where there is no probe history, no compliance
    assessment and no coherence report — without needing a migrated database
    and without leaving rows behind. It is the acceptance path for this task
    and the shape the CI job runs.

**Shared sources fetched once.**
    ``compute_canvas_posture`` and ``get_canvas_health`` return the whole
    platform in one call, but :func:`score_component` re-fetches them per
    component when they are not supplied. Over 68 components that is 68 full
    posture computations. This runner fetches each once and injects it.

Not assessed is the expected outcome here, not a failure
--------------------------------------------------------
A sweep on a checkout with no live signal data reports every component
``NOT_ASSESSED`` — and that is the correct answer, not a broken run. The
coherence dimension in particular is unassessed unless a report is supplied
(``--coherence-report``), because :func:`extract_coherence` deliberately
refuses to trigger a minutes-long sweep as a side effect of scoring. So the
runner exits ``0`` on an all-unassessed sweep and prints a per-dimension
tally of *why* nothing was assessed, which is the actionable part: it names
which of ICDEV's own measurement subsystems has no coverage.

Exit codes are load-bearing:

``0``
    The sweep ran to completion. Components may be unassessed.
``1``
    At least one component raised while being scored or persisted. The rest
    still completed — one unimportable canvas must not cost the platform its
    whole scorecard refresh — but the run is not clean.
``2``
    The sweep could not start: the registry would not load, or persistence
    was asked for without a usable database connection.

An unassessed component is *not* an error and never sets a non-zero exit.
Conflating "we looked and could not measure this" with "the run broke" is the
same category error the scorer itself exists to avoid.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.quality.component_scorer import (  # noqa: E402
    DIMENSIONS,
    NOT_ASSESSED,
    ScorecardPersistError,
    persist_scorecard,
    registry_component_keys,
    score_component,
)

#: Where the component list comes from, for the run report.
REGISTRY_FILE = "args/component_registry.yaml"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Shared source data
# ---------------------------------------------------------------------------


def prefetch_sources(conn: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch the platform-wide sources once for the whole sweep.

    Returns ``(kwargs, report)`` — the first is spread into
    :func:`score_component`, the second describes what was actually available
    so the operator can tell "no rows" from "the source blew up".

    A source that raises is reported and **not** injected: the per-component
    extractor then runs its own fetch, fails the same way, and produces its own
    accurate ``reason`` ("posture engine unavailable or raised") rather than
    the misleading one an injected empty list would produce ("posture engine
    returned no canvas rows"). Paying 68 fast failures to keep the reason
    honest is the right trade — a wrong reason on an unassessed dimension is
    exactly the kind of quiet inaccuracy this subsystem is built to avoid.

    Args:
        conn: Storage connection, or ``None`` when none could be opened.

    Returns:
        ``({"posture": [...], "canvas_health": [...]}, {"posture": {...}, ...})``
    """
    kwargs: dict[str, Any] = {}
    report: dict[str, Any] = {}

    if conn is None:
        report["posture"] = {"available": False, "reason": "no database connection"}
    else:
        try:
            from tools.canvas_compliance.posture import (  # noqa: PLC0415
                compute_canvas_posture,
            )

            rows, _overall = compute_canvas_posture(conn)
            kwargs["posture"] = list(rows or [])
            report["posture"] = {"available": True, "rows": len(kwargs["posture"])}
        except Exception as exc:  # noqa: BLE001
            report["posture"] = {"available": False, "reason": str(exc)}

    try:
        from tools.canvas_health.health_data import get_canvas_health  # noqa: PLC0415

        health = list(get_canvas_health() or [])
        kwargs["canvas_health"] = health
        report["canvas_health"] = {"available": True, "rows": len(health)}
    except Exception as exc:  # noqa: BLE001
        report["canvas_health"] = {"available": False, "reason": str(exc)}

    return kwargs, report


def load_coherence_report(path: Path | str) -> dict[str, Any]:
    """Read a coherence report emitted by ``coherence_checker.py --all --json``.

    Raises rather than degrading to ``None``: a caller who named a report file
    asked for the coherence dimension to be assessed, and silently scoring
    every component as "no coherence report supplied" would look identical to a
    successful run while measuring one dimension less.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "checks" not in payload:
        raise ValueError(
            f"{path} is not a coherence report — expected a JSON object with a "
            "'checks' key, as produced by "
            "`python tools/workflow/coherence_checker.py --all --json`"
        )
    return payload


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def run_scoring(
    conn: Any = None,
    registry: Any = None,
    keys: Sequence[str] | None = None,
    dry_run: bool = False,
    coherence_report: Any = None,
    evaluated_at: str | None = None,
    weights: dict[str, float] | None = None,
    source_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score every registered component, persisting unless *dry_run*.

    Does not open or close connections — ``main`` owns that, so the whole sweep
    runs on one connection instead of 68.

    Args:
        conn: Storage connection. May be ``None`` in a dry run, in which case
            the database-backed dimensions report themselves unassessed.
        registry: Optional registry instance.
        keys: Restrict the sweep to these component keys.
        dry_run: Score and report, write nothing.
        coherence_report: A coherence report (or its ``to_dict()``). Without
            one the coherence dimension is unassessed for every component.
        evaluated_at: Shared timestamp for the pass, so every row it writes
            sorts together on ``idx_sc_component_evaluated``.
        weights: Optional dimension weights, forwarded to the aggregator.
        source_kwargs: Pre-fetched shared sources; :func:`prefetch_sources`
            supplies these when omitted.

    Returns:
        The run report — see :func:`render` for the fields that matter.

    Raises:
        ScorecardPersistError: The registry could not be loaded, so there is
            nothing to sweep. A *component* that raises is recorded in
            ``errors`` and does not abort the sweep.
    """
    stamp = evaluated_at or _utc_now()

    try:
        component_keys = list(keys) if keys is not None else registry_component_keys(registry)
    except Exception as exc:  # noqa: BLE001
        raise ScorecardPersistError(f"cannot load the component registry: {exc}") from exc

    if source_kwargs is None:
        source_kwargs, sources = prefetch_sources(conn)
    else:
        source_kwargs = dict(source_kwargs)
        sources = {"injected": True}
    sources["coherence_report"] = {"available": coherence_report is not None}

    components: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    # How often each dimension went unmeasured. The actionable half of an
    # all-unassessed sweep: it names which measurement subsystem is dark.
    unassessed_by_dimension = {name: 0 for name in DIMENSIONS}

    for key in component_keys:
        try:
            result = score_component(
                key,
                conn=conn,
                registry=registry,
                coherence_report=coherence_report,
                weights=weights,
                **source_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"component_id": key, "phase": "score", "error": str(exc)})
            continue

        aggregate = result.get("aggregate") or {}
        unassessed = list(aggregate.get("unassessed_dimensions") or [])
        for name in unassessed:
            if name in unassessed_by_dimension:
                unassessed_by_dimension[name] += 1

        row: dict[str, Any] = {
            "component_id": result.get("key", key),
            "kind": result.get("kind", ""),
            "route": result.get("route", ""),
            "overall_score": aggregate.get("overall_score"),
            "letter_grade": aggregate.get("letter_grade"),
            "status": aggregate.get("status", NOT_ASSESSED),
            "unassessed_dimensions": unassessed,
            "evidence_count": aggregate.get("evidence_count", 0),
            "action": "dry-run",
        }

        if not dry_run:
            try:
                written = persist_scorecard(result, conn=conn, evaluated_at=stamp)
                row["action"] = written.get("action", "")
                persisted.append(written)
            except Exception as exc:  # noqa: BLE001
                errors.append({"component_id": key, "phase": "persist", "error": str(exc)})
                row["action"] = "failed"

        components.append(row)

    assessed = sum(1 for c in components if c["overall_score"] is not None)
    return {
        "dry_run": dry_run,
        "evaluated_at": stamp,
        "registry_file": REGISTRY_FILE,
        "component_count": len(component_keys),
        "scored": len(components),
        "assessed": assessed,
        "unassessed": len(components) - assessed,
        "unassessed_by_dimension": unassessed_by_dimension,
        "sources": sources,
        "components": components,
        "persisted": persisted,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _source_line(name: str, info: dict[str, Any]) -> str:
    if info.get("available"):
        rows = info.get("rows")
        return f"  {name:<16} available" + (f" ({rows} rows)" if rows is not None else "")
    return f"  {name:<16} UNAVAILABLE — {info.get('reason') or 'not supplied'}"


def render(report: dict[str, Any]) -> str:
    """Human-readable run report.

    Every component gets a line whether or not it was assessed. Printing only
    the graded ones would make a sweep that measured nothing look like a sweep
    with nothing to report.
    """
    mode = "DRY RUN — nothing written" if report["dry_run"] else "persisting to developer_scorecards"
    lines = [
        f"Component scoring sweep — {mode}",
        f"  registry       {report['registry_file']} ({report['component_count']} components)",
        f"  evaluated_at   {report['evaluated_at']}",
        "",
        "Shared sources:",
    ]
    for name, info in report["sources"].items():
        if isinstance(info, dict):
            lines.append(_source_line(name, info))
    lines.append("")

    for row in report["components"]:
        if row["overall_score"] is None:
            missing = ", ".join(row["unassessed_dimensions"]) or "all dimensions"
            lines.append(
                f"  {row['component_id']:<24} {row['kind']:<15} NOT_ASSESSED  "
                f"(no signal for: {missing})"
            )
        else:
            lines.append(
                f"  {row['component_id']:<24} {row['kind']:<15} "
                f"{row['overall_score']:>5.1f}  grade {row['letter_grade']}  "
                f"({row['evidence_count']} evidence)"
            )

    lines += [
        "",
        f"Summary: {report['scored']} scored | {report['assessed']} assessed | "
        f"{report['unassessed']} NOT_ASSESSED | {len(report['errors'])} errors",
    ]
    tally = report["unassessed_by_dimension"]
    if any(tally.values()):
        detail = ", ".join(f"{name} {count}" for name, count in tally.items() if count)
        lines.append(f"Unassessed by dimension: {detail}")
        lines.append(
            "  NOT_ASSESSED is a finding, not a failure — it names a component "
            "ICDEV's own measurement subsystems have no data for."
        )
    for err in report["errors"]:
        lines.append(f"  ERROR {err['component_id']} ({err['phase']}): {err['error']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _open_connection() -> tuple[Any, str]:
    """Best-effort connection. Returns ``(conn, reason_it_is_None)``."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415

        return get_connection(), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.quality.run_component_scoring",
        description=(
            "Score every component in args/component_registry.yaml across the "
            "four measurement dimensions and upsert each verdict into "
            "developer_scorecards."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Score and report without writing anything to the database",
    )
    ap.add_argument("--json", action="store_true", help="Emit the run report as JSON")
    ap.add_argument(
        "--keys",
        default="",
        help="Comma-separated component keys to restrict the sweep to (default: all)",
    )
    ap.add_argument(
        "--coherence-report",
        default="",
        metavar="PATH",
        help=(
            "JSON report from `coherence_checker.py --all --json`. Without it the "
            "coherence dimension is NOT_ASSESSED for every component, which caps "
            "every overall score at NULL."
        ),
    )
    args = ap.parse_args(argv)

    coherence_report = None
    if args.coherence_report:
        try:
            coherence_report = load_coherence_report(args.coherence_report)
        except Exception as exc:  # noqa: BLE001
            print(f"error: cannot read coherence report: {exc}", file=sys.stderr)
            return 2

    conn, conn_error = _open_connection()
    if conn is None and not args.dry_run:
        print(
            f"error: persisting needs a database connection: {conn_error}",
            file=sys.stderr,
        )
        return 2

    keys = [k.strip() for k in args.keys.split(",") if k.strip()] or None

    try:
        try:
            report = run_scoring(
                conn=conn,
                keys=keys,
                dry_run=args.dry_run,
                coherence_report=coherence_report,
            )
        except ScorecardPersistError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    finally:
        if conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass

    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
