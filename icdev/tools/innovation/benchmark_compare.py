#!/usr/bin/env python3
# CUI // SP-CTI
"""Benchmark comparator — join a tracked external project to ICDEV's own inventory (xbm-cmp-01).

The scout reflex answers "what is this external project doing". That is a fact
about someone else and it generates no action on its own. This module answers
the second half: *which ICDEV subsystem does that project benchmark, and what is
measurably true about that subsystem here.*

It automates by measurement what two hand-written documents did by hand —
``docs/research/external-benchmark-map.md`` (ten subsystems, a verdict each) and
``docs/research/canvas-engine-sweep.md`` (per-component liveness). Those were a
point-in-time reading; this re-derives the local half on every run, so a
finding that has since been fixed stops being reported as a finding.

Verdicts in both directions
---------------------------
``ahead`` is a first-class outcome and is deliberately reachable. Three of the
ten subsystems the map benchmarked came out ahead of the commercial field —
compliance/ATO, the closed delivery loop, and OTel-native LLM telemetry — and a
comparator that can only emit deficits would misrepresent the platform and be
ignored once anyone noticed. ``ahead — no adaptation needed`` is rendered as
plainly as a gap.

How a verdict is reached
------------------------
Two independent measurements, kept separate on purpose because they mean
different things:

``code_state``
    ``absent`` / ``thin`` / ``built`` — non-``__init__`` module count across the
    declared directories, against the subsystem's ``min_modules`` floor.
``data_state``
    ``unfed`` / ``populated`` / ``not_expected`` / ``not_assessed`` — summed row
    counts across the declared tables, against ``data_floor``.

Then, in order:

1. ``owned: false``           → ``out_of_scope``  (no module tree here owns it)
2. ``code_state == absent``   → ``gap`` (not built)
3. ``code_state == thin``     → ``gap`` (thin against the floor)
4. ``data_state == unfed``    → ``gap`` (built but unfed)
5. any outstanding adaptation → ``parity``
6. otherwise                  → ``ahead`` — no adaptation needed

An adaptation is outstanding when its ``status`` is ``open`` or ``deferred``.
Deferred still blocks ``ahead``: the concept is unadopted either way, and the
only difference is whether anyone scheduled it.

Emptiness is not always a finding
---------------------------------
``data_expected: false`` marks a subsystem whose tables fill per engagement
rather than by the platform running — an empty ``ssp_documents`` on a
development instance says nothing about compliance tooling. Those subsystems
skip rule 4. Where emptiness *is* the finding, the catalogue says so and the
floor is set accordingly: ``data_lineage`` carries ``data_floor: 50`` because
the map reached "machinery with nothing running through it" while looking at
four rows and eight rows, and a floor of zero would score that as fed.

Never raises, never fakes
-------------------------
A missing directory, an absent table or an unreachable database costs a
measurement, not the run. An unavailable database yields ``not_assessed`` — not
``unfed`` — so a comparator that cannot see the data half can never manufacture
a "built but unfed" gap out of its own blindness. Each table probe rolls the
connection back on failure, because on PostgreSQL one error aborts the
transaction and every subsequent probe would otherwise report a table that
exists as missing.

A tracked project whose ``subsystem`` tag matches no catalogue entry is
reported as ``unmapped`` with its tag, never dropped. An unroutable finding is
a finding about ``args/benchmark_subsystems.yaml``.

CLI
---
::

    python tools/innovation/benchmark_compare.py --json
    python tools/innovation/benchmark_compare.py --markdown
    python tools/innovation/benchmark_compare.py --subsystem observability --json
    python tools/innovation/benchmark_compare.py --no-db --markdown   # code half only
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DEFAULT_CATALOG = BASE_DIR / "args" / "benchmark_subsystems.yaml"
DEFAULT_WATCHLIST = BASE_DIR / "context" / "genesis" / "competitors.yaml"

# ── Verdicts ─────────────────────────────────────────────────────────
VERDICT_AHEAD = "ahead"
VERDICT_PARITY = "parity"
VERDICT_GAP = "gap"
VERDICT_UNMAPPED = "unmapped"
VERDICT_OUT_OF_SCOPE = "out_of_scope"

#: The headline string for each verdict. ``ahead`` spells out that no
#: adaptation is needed because that is the outcome most easily lost.
VERDICT_LABELS: Dict[str, str] = {
    VERDICT_AHEAD: "ahead — no adaptation needed",
    VERDICT_PARITY: "parity — named adaptations outstanding",
    VERDICT_GAP: "gap",
    VERDICT_UNMAPPED: "unmapped — no catalogue entry for this subsystem tag",
    VERDICT_OUT_OF_SCOPE: "out of scope — no module tree in this repository owns it",
}

# ── Measured states ──────────────────────────────────────────────────
CODE_ABSENT = "absent"
CODE_THIN = "thin"
CODE_BUILT = "built"

DATA_UNFED = "unfed"
DATA_POPULATED = "populated"
DATA_NOT_EXPECTED = "not_expected"

#: The database could not be read. Distinct from ``unfed`` on purpose: an
#: unreachable database is ignorance, not evidence of emptiness.
DATA_NOT_ASSESSED = "not_assessed"

#: Adaptation statuses that keep a subsystem off ``ahead``.
OUTSTANDING_STATUSES = frozenset({"open", "deferred"})

#: How a project's subsystem was resolved.
ROUTE_TAG = "tag"
ROUTE_CATEGORY_FALLBACK = "category_fallback"
ROUTE_NONE = "unresolved"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────
def _load_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML mapping. Returns ``{}`` rather than raising on any failure."""
    try:
        import yaml
    except ImportError:
        return {}
    try:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ``args/benchmark_subsystems.yaml``."""
    return _load_yaml(Path(path) if path else DEFAULT_CATALOG)


def load_watchlist(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load the scout watchlist targets from ``context/genesis/competitors.yaml``."""
    data = _load_yaml(Path(path) if path else DEFAULT_WATCHLIST)
    targets = data.get("targets", [])
    return [t for t in targets if isinstance(t, dict)]


def project_name(target: Dict[str, Any]) -> str:
    """Display name for a watchlist entry: the repo slug, or the manual-review name."""
    return str(target.get("repo") or target.get("name") or "unknown")


def resolve_subsystem(
    target: Dict[str, Any],
    catalog: Dict[str, Any],
) -> Tuple[Optional[str], str, Optional[str]]:
    """Resolve one watchlist entry to a catalogue subsystem key.

    Returns ``(key, route, declared)`` where ``route`` is :data:`ROUTE_TAG`,
    :data:`ROUTE_CATEGORY_FALLBACK` or :data:`ROUTE_NONE`, and ``declared`` is
    whatever the entry actually said — kept so an unmapped project can name the
    tag that failed to route instead of vanishing.
    """
    subsystems = catalog.get("subsystems", {}) or {}
    fallback = catalog.get("category_fallback", {}) or {}

    tag = target.get("subsystem")
    if tag:
        tag = str(tag)
        if tag in subsystems:
            return tag, ROUTE_TAG, tag
        return None, ROUTE_NONE, tag

    # Watchlists predating the subsystem tag carry only a category.
    category = target.get("category")
    if category:
        category = str(category)
        mapped = fallback.get(category)
        if mapped and mapped in subsystems:
            return str(mapped), ROUTE_CATEGORY_FALLBACK, category
        return None, ROUTE_NONE, category

    return None, ROUTE_NONE, None


# ─────────────────────────────────────────────────────────────────────
# Local measurement — the ICDEV half
# ─────────────────────────────────────────────────────────────────────
def count_modules(paths: Sequence[str], base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Count non-``__init__`` ``.py`` files in each declared directory.

    Directories that do not exist are reported in ``missing`` rather than
    counted as zero and forgotten — the two are different findings.
    """
    root = Path(base_dir) if base_dir else BASE_DIR
    per_path: Dict[str, int] = {}
    missing: List[str] = []
    total = 0
    for rel in paths or []:
        directory = root / rel
        if not directory.is_dir():
            missing.append(rel)
            continue
        try:
            n = sum(1 for p in directory.glob("*.py") if p.name != "__init__.py")
        except OSError:
            missing.append(rel)
            continue
        per_path[rel] = n
        total += n
    return {"total": total, "per_path": per_path, "missing": missing}


def _count_rows(conn: Any, table: str) -> Optional[int]:
    """Row count for one table, or ``None`` if it cannot be read.

    Rolls back on failure. On PostgreSQL a failed statement aborts the
    transaction, and every later probe on the same connection would then fail
    too — which reads as "all these tables are missing" when only the first one
    was.
    """
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608 -- table names come from the committed catalogue, never from input
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def measure_tables(
    tables: Sequence[str],
    conn: Any = None,
    row_counts: Optional[Dict[str, Optional[int]]] = None,
) -> Dict[str, Any]:
    """Measure row counts for the declared tables.

    ``row_counts`` injects the measurement directly, which is how the tests
    exercise every state without a database. When neither ``conn`` nor
    ``row_counts`` is given the ambient connection is opened once; if that
    fails, every table reports ``None`` and the caller renders
    :data:`DATA_NOT_ASSESSED`.
    """
    tables = list(tables or [])
    if not tables:
        return {"rows": {}, "total": 0, "readable": 0, "database": "not_needed"}

    if row_counts is not None:
        rows = {t: row_counts.get(t) for t in tables}
        database = "injected"
    else:
        owned_conn = None
        if conn is None:
            try:
                from tools.db.storage import get_connection

                conn = owned_conn = get_connection()
            except Exception:
                return {
                    "rows": {t: None for t in tables},
                    "total": 0,
                    "readable": 0,
                    "database": "unreachable",
                }
        rows = {t: _count_rows(conn, t) for t in tables}
        database = "read"
        if owned_conn is not None:
            try:
                owned_conn.close()
            except Exception:
                pass

    readable = sum(1 for v in rows.values() if v is not None)
    total = sum(v for v in rows.values() if v is not None)
    return {"rows": rows, "total": total, "readable": readable, "database": database}


def _code_state(module_count: int, min_modules: int) -> str:
    if module_count <= 0:
        return CODE_ABSENT
    if module_count < max(int(min_modules or 0), 0):
        return CODE_THIN
    return CODE_BUILT


def _data_state(measurement: Dict[str, Any], data_expected: bool, data_floor: int) -> str:
    if not measurement.get("rows"):
        # Nothing declared to measure. Silence is not evidence of emptiness.
        return DATA_NOT_EXPECTED if not data_expected else DATA_NOT_ASSESSED
    if measurement.get("readable", 0) == 0:
        return DATA_NOT_ASSESSED
    if not data_expected:
        return DATA_NOT_EXPECTED
    return DATA_UNFED if measurement.get("total", 0) <= int(data_floor or 0) else DATA_POPULATED


def outstanding_adaptations(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Adaptations an external project has that ICDEV has not adopted."""
    out = []
    for item in spec.get("adaptations", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "open")).lower() in OUTSTANDING_STATUSES:
            out.append(item)
    return out


def assess_subsystem(
    key: str,
    spec: Dict[str, Any],
    base_dir: Optional[Path] = None,
    conn: Any = None,
    row_counts: Optional[Dict[str, Optional[int]]] = None,
    modules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Measure one subsystem and compute its verdict.

    ``modules`` and ``row_counts`` inject the two measurements so a caller can
    assess without touching disk or database.
    """
    spec = spec or {}
    owned = bool(spec.get("owned", True))
    min_modules = int(spec.get("min_modules", 0) or 0)
    data_expected = bool(spec.get("data_expected", False))
    data_floor = int(spec.get("data_floor", 0) or 0)

    module_measure = modules if modules is not None else count_modules(spec.get("modules", []), base_dir)
    table_measure = measure_tables(spec.get("tables", []), conn=conn, row_counts=row_counts)

    code_state = _code_state(int(module_measure.get("total", 0)), min_modules)
    data_state = _data_state(table_measure, data_expected, data_floor)
    pending = outstanding_adaptations(spec)

    limits: List[str] = []
    if data_state == DATA_NOT_ASSESSED and spec.get("tables"):
        limits.append(
            "database unreachable or tables unreadable — data liveness not assessed, "
            "so this run cannot report a built-but-unfed gap for this subsystem"
        )
    if module_measure.get("missing"):
        limits.append(
            "declared module paths not found on disk: " + ", ".join(module_measure["missing"])
        )

    reasons: List[str] = []
    if not owned:
        verdict = VERDICT_OUT_OF_SCOPE
        reasons.append("no module tree in this repository owns this subsystem")
    elif code_state == CODE_ABSENT:
        verdict = VERDICT_GAP
        reasons.append("not built — no modules found in the declared paths")
    elif code_state == CODE_THIN:
        verdict = VERDICT_GAP
        reasons.append(
            f"thin — {module_measure['total']} modules against a floor of {min_modules}"
        )
    elif data_state == DATA_UNFED:
        verdict = VERDICT_GAP
        reasons.append(
            f"built but unfed — {module_measure['total']} modules, "
            f"{table_measure['total']} rows across {len(table_measure['rows'])} tables "
            f"(floor {data_floor})"
        )
    elif pending:
        verdict = VERDICT_PARITY
        reasons.append(
            f"built and running, {len(pending)} adaptation(s) outstanding: "
            + "; ".join(str(a.get("name", "unnamed")) for a in pending)
        )
    else:
        verdict = VERDICT_AHEAD
        reasons.append(
            f"built and running ({module_measure['total']} modules), and no watched project "
            "has a concept ICDEV has not adopted"
        )

    if spec.get("map_section") is None and owned:
        # Never benchmarked. The local half is measured either way, but the
        # external half is unexamined — so an empty adaptation list here is
        # silence, not a clean bill of health, and an ahead verdict rests on
        # nobody having looked. Say so on every verdict, not only on ahead.
        limits.append(
            "no benchmark pass has been run against this subsystem — its adaptation list "
            "reflects an absent review, not a review that found nothing"
        )

    return {
        "subsystem": key,
        "title": spec.get("title", key),
        "map_section": spec.get("map_section"),
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS[verdict],
        "adaptation_needed": bool(pending),
        "local_state": {
            "code_state": code_state,
            "modules": module_measure.get("total", 0),
            "modules_by_path": module_measure.get("per_path", {}),
            "modules_missing": module_measure.get("missing", []),
            "min_modules": min_modules,
            "data_state": data_state,
            "data_expected": data_expected,
            "data_floor": data_floor,
            "table_rows": table_measure.get("rows", {}),
            "table_rows_total": table_measure.get("total", 0),
            "database": table_measure.get("database"),
        },
        "outstanding_adaptations": [
            {
                "name": a.get("name"),
                "source": a.get("source"),
                "status": a.get("status", "open"),
                "tracked": a.get("tracked"),
            }
            for a in pending
        ],
        "adopted_adaptations": [
            {"name": a.get("name"), "source": a.get("source"), "tracked": a.get("tracked")}
            for a in (spec.get("adaptations") or [])
            if isinstance(a, dict) and str(a.get("status", "")).lower() == "done"
        ],
        "caveats": list(spec.get("caveats") or []),
        "measurement_limits": limits,
        "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────
# Comparison
# ─────────────────────────────────────────────────────────────────────
def compare(
    catalog: Optional[Dict[str, Any]] = None,
    targets: Optional[Iterable[Dict[str, Any]]] = None,
    base_dir: Optional[Path] = None,
    conn: Any = None,
    row_counts: Optional[Dict[str, Optional[int]]] = None,
    use_db: bool = True,
    only_subsystem: Optional[str] = None,
) -> Dict[str, Any]:
    """Join every tracked project to its ICDEV subsystem and its measured state.

    Every tracked project yields exactly one row. A project that cannot be
    routed yields an ``unmapped`` row naming the tag that failed, so the count
    of rows always equals the count of tracked projects.
    """
    catalog = catalog if catalog is not None else load_catalog()
    subsystems: Dict[str, Any] = catalog.get("subsystems", {}) or {}
    targets = list(targets) if targets is not None else load_watchlist()

    db_mode = "read"
    if not use_db and row_counts is None:
        # Measure the code half only, and say so, rather than opening a
        # connection the caller asked us not to open. Every table then reads
        # as not_assessed, which is what a skipped measurement means.
        row_counts = {}
        db_mode = "skipped"
    elif row_counts is not None:
        db_mode = "injected"

    assessed: Dict[str, Dict[str, Any]] = {}
    shared_conn = conn
    owned_conn = None
    if shared_conn is None and row_counts is None:
        # One connection for the whole run rather than one per subsystem.
        try:
            from tools.db.storage import get_connection

            shared_conn = owned_conn = get_connection()
        except Exception:
            # Fail once, not once per subsystem.
            row_counts = {}
            db_mode = "unreachable"

    def _assess(key: str) -> Dict[str, Any]:
        if key not in assessed:
            assessed[key] = assess_subsystem(
                key,
                subsystems.get(key, {}),
                base_dir=base_dir,
                conn=shared_conn,
                row_counts=row_counts,
            )
        return assessed[key]

    projects: List[Dict[str, Any]] = []
    for target in targets:
        key, route, declared = resolve_subsystem(target, catalog)
        name = project_name(target)
        if only_subsystem and key != only_subsystem:
            continue
        row: Dict[str, Any] = {
            "project": name,
            "repo": target.get("repo"),
            "url": target.get("url"),
            "trackable": bool(target.get("trackable", bool(target.get("repo")))),
            "declared": declared,
            "route": route,
        }
        if key is None:
            row.update(
                {
                    "subsystem": None,
                    "title": None,
                    "verdict": VERDICT_UNMAPPED,
                    "verdict_label": VERDICT_LABELS[VERDICT_UNMAPPED],
                    "local_state": None,
                    "reasons": [
                        f"watchlist entry declares {declared!r}, which has no entry in the "
                        "subsystem catalogue — route it or add it"
                        if declared
                        else "watchlist entry declares neither a subsystem nor a category"
                    ],
                }
            )
        else:
            assessment = _assess(key)
            row.update(
                {
                    "subsystem": key,
                    "title": assessment["title"],
                    "verdict": assessment["verdict"],
                    "verdict_label": assessment["verdict_label"],
                    "adaptation_needed": assessment["adaptation_needed"],
                    "local_state": assessment["local_state"],
                    "outstanding_adaptations": assessment["outstanding_adaptations"],
                    "measurement_limits": assessment["measurement_limits"],
                    "reasons": assessment["reasons"],
                }
            )
        projects.append(row)

    # Subsystems with no tracked project are still assessed and reported: a
    # subsystem nobody watches is a hole in the watchlist, not an absence.
    for key in subsystems:
        if only_subsystem and key != only_subsystem:
            continue
        _assess(key)

    if owned_conn is not None:
        try:
            owned_conn.close()
        except Exception:
            pass

    watched = {p["subsystem"] for p in projects if p.get("subsystem")}
    unwatched = sorted(k for k in assessed if k not in watched)

    counts: Dict[str, int] = {}
    for a in assessed.values():
        counts[a["verdict"]] = counts.get(a["verdict"], 0) + 1
    project_counts: Dict[str, int] = {}
    for p in projects:
        project_counts[p["verdict"]] = project_counts.get(p["verdict"], 0) + 1

    return {
        "generated_at": _utcnow_iso(),
        "classification": "CUI // SP-CTI",
        "projects": projects,
        "subsystems": [assessed[k] for k in sorted(assessed)],
        "summary": {
            "projects_tracked": len(projects),
            "projects_by_verdict": project_counts,
            "subsystems_assessed": len(assessed),
            "subsystems_by_verdict": counts,
            "subsystems_ahead": sorted(
                k for k, a in assessed.items() if a["verdict"] == VERDICT_AHEAD
            ),
            "subsystems_unwatched": unwatched,
            "unmapped_projects": [p["project"] for p in projects if p["verdict"] == VERDICT_UNMAPPED],
            "database": db_mode,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────
_VERDICT_ORDER = {
    VERDICT_AHEAD: 0,
    VERDICT_PARITY: 1,
    VERDICT_GAP: 2,
    VERDICT_OUT_OF_SCOPE: 3,
    VERDICT_UNMAPPED: 4,
}


def _local_state_phrase(state: Optional[Dict[str, Any]]) -> str:
    if not state:
        return "not measured"
    parts = [f"{state['modules']} modules ({state['code_state']})"]
    data_state = state.get("data_state")
    if data_state == DATA_NOT_ASSESSED:
        parts.append("rows not assessed")
    elif data_state == DATA_NOT_EXPECTED:
        parts.append(f"{state['table_rows_total']} rows (emptiness not a finding here)")
    else:
        parts.append(f"{state['table_rows_total']} rows ({data_state})")
    return ", ".join(parts)


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the comparison as the benchmark map's table, recomputed."""
    summary = report["summary"]
    by_verdict = summary["subsystems_by_verdict"]
    lines = [
        "# Benchmark Comparison — external findings against ICDEV's own inventory",
        "",
        f"CUI // SP-CTI · generated {report['generated_at']} · "
        "`tools/innovation/benchmark_compare.py`",
        "",
        f"**{summary['projects_tracked']} tracked projects** routed to "
        f"**{summary['subsystems_assessed']} ICDEV subsystems**. "
        f"Ahead: {by_verdict.get(VERDICT_AHEAD, 0)} · "
        f"Parity: {by_verdict.get(VERDICT_PARITY, 0)} · "
        f"Gap: {by_verdict.get(VERDICT_GAP, 0)}.",
        "",
    ]
    if summary["subsystems_ahead"]:
        lines += [
            "Ahead — no adaptation needed: "
            + ", ".join(f"`{k}`" for k in summary["subsystems_ahead"])
            + ". Nothing on the watchlist has a concept these subsystems have not adopted.",
            "",
        ]
    if summary["database"] in ("skipped", "unreachable"):
        why = (
            "Run with `--no-db`"
            if summary["database"] == "skipped"
            else "The database could not be reached"
        )
        lines += [
            f"> {why}: the code half is measured, the data half is not. "
            "No built-but-unfed verdict can be reached from this run.",
            "",
        ]

    lines += [
        "## Subsystems",
        "",
        "| Subsystem | Verdict | Measured local state | Outstanding |",
        "|---|---|---|---|",
    ]
    for a in sorted(
        report["subsystems"], key=lambda x: (_VERDICT_ORDER.get(x["verdict"], 9), x["subsystem"])
    ):
        pending = a.get("outstanding_adaptations") or []
        outstanding = (
            "; ".join(f"{p['name']} ({p['source']})" for p in pending) if pending else "—"
        )
        lines.append(
            f"| `{a['subsystem']}` — {a['title']} | **{a['verdict_label']}** | "
            f"{_local_state_phrase(a['local_state'])} | {outstanding} |"
        )

    lines += ["", "## Tracked projects", "", "| Project | ICDEV subsystem | Verdict | Local state |", "|---|---|---|---|"]
    for p in sorted(
        report["projects"], key=lambda x: (_VERDICT_ORDER.get(x["verdict"], 9), x["project"])
    ):
        subsystem = f"`{p['subsystem']}`" if p["subsystem"] else f"_unmapped: {p['declared']}_"
        route = "" if p["route"] == ROUTE_TAG else f" ({p['route']})"
        lines.append(
            f"| {p['project']} | {subsystem}{route} | {p['verdict_label']} | "
            f"{_local_state_phrase(p.get('local_state'))} |"
        )

    limits = [
        (a["subsystem"], limit)
        for a in report["subsystems"]
        for limit in a.get("measurement_limits", [])
    ]
    if limits:
        lines += ["", "## Measurement limits", ""]
        lines += [f"- `{key}` — {limit}" for key, limit in limits]

    if summary["unmapped_projects"]:
        lines += [
            "",
            "## Unmapped projects",
            "",
            "These are tracked but route nowhere. Each is a missing entry in "
            "`args/benchmark_subsystems.yaml` or a mistyped tag in the watchlist.",
            "",
        ]
        lines += [f"- {name}" for name in summary["unmapped_projects"]]

    if summary["subsystems_unwatched"]:
        lines += [
            "",
            "## Subsystems nobody watches",
            "",
            "Assessed, but no tracked project benchmarks them — a hole in the watchlist "
            "rather than in the subsystem.",
            "",
        ]
        lines += [f"- `{key}`" for key in summary["subsystems_unwatched"]]

    lines += ["", "---", "", "*Generated by benchmark_compare (xbm-cmp-01). Nothing was written to the database.*"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare tracked external projects against ICDEV's own measured inventory."
    )
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--markdown", action="store_true", help="emit a markdown report")
    parser.add_argument("--subsystem", help="restrict to one subsystem key")
    parser.add_argument("--catalog", help="path to benchmark_subsystems.yaml")
    parser.add_argument("--watchlist", help="path to competitors.yaml")
    parser.add_argument("--no-db", action="store_true", help="skip database measurement")
    parser.add_argument("--out", help="write the rendered output to this path as well")
    args = parser.parse_args(argv)

    catalog = load_catalog(Path(args.catalog) if args.catalog else None)
    if not catalog.get("subsystems"):
        print(
            f"ERROR: no subsystems in {args.catalog or DEFAULT_CATALOG}",
            file=sys.stderr,
        )
        return 1
    targets = load_watchlist(Path(args.watchlist) if args.watchlist else None)

    report = compare(
        catalog=catalog,
        targets=targets,
        use_db=not args.no_db,
        only_subsystem=args.subsystem,
    )

    if args.json:
        rendered = json.dumps(report, indent=2, default=str)
    else:
        rendered = render_markdown(report)
    print(rendered)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
