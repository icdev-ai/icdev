#!/usr/bin/env python3
# CUI // SP-CTI
"""The benchmark comparison as a document, generated instead of written (xbm-cmp-01-d4).

d1 measured ICDEV's own half, d2 joined each external project to the subsystem it
benchmarks, d3 turned the two into a verdict. All three are libraries and JSON.
This is the fourth step and the one that makes the chain readable: one command,
one markdown file, the same structure a human wrote by hand in
``docs/research/external-benchmark-map.md``.

Usage:
    python tools/innovation/benchmark_report.py --write
    python tools/innovation/benchmark_report.py --check      # CI gate
    python tools/innovation/benchmark_report.py --live       # measured, to stdout
    python tools/innovation/benchmark_report.py --json

**It does not overwrite the hand-written map, and that is deliberate.** The task
that asked for this said "replacing hand-written map", and the honest reading of
that is *replacing the maintenance burden*, not the file. Two reasons, both of
which would be defects if reversed:

1. **Circular sourcing.** ``benchmark_compare`` cites
   ``docs/research/external-benchmark-map.md`` as the *origin* of every declared
   reading — it is the value of ``MAP_DOC``, it is what every ``source`` field
   points at, and ``measurement_limits`` tells a reader to go re-read §4 by hand.
   A generator that writes over its own cited source produces a document whose
   provenance is itself.

2. **The narrative is not in any config.** The map's "External." paragraphs — what
   Backstage actually is, why TheHive's contract is the valuable part, the §7
   correction about reading a lifetime aggregate across a policy change — exist in
   no YAML file anywhere. Regenerating over them would delete them and report
   success. What the configs *do* carry is the per-project ``notes`` on the
   watchlist, which is where this report's external half comes from.

So the generated report lands beside the map at
``docs/research/external-benchmark-map.generated.md``: the map stays the human
reading that supplies the declared half, and this file is that reading re-checked
against the tree on every run.

**Offline by default, because a CI diff check is only worth having if it can
pass.** The checked-in artifact is a pure function of the repo tree and three
config files, so any machine regenerates it byte-for-byte. Row counts are not:
they differ between a developer's populated PostgreSQL and CI's empty one, and a
gate that fails for that reason gets disabled within a week. Offline yields
``not_assessed`` row counts — never ``unfed`` — so the conservative reading is
what gets committed: a declared finding stands unless a measurement retires it,
and offline nothing retires anything.

``--live`` is the other half, and it is the mode that matters operationally: it
opens the database, re-checks every ``closes_when``, and will say a finding has
been retired. It prints; it does not write the checked-in artifact, because a
live render is not reproducible and committing one turns the gate red for the
next person.
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation.benchmark_compare import (
    MAP_DOC,
    POSITION_AHEAD,
    POSITION_PARITY,
    VERDICT_NO_ADAPTATION_NEEDED,
    compare_all,
)
from tools.innovation.icdev_evidence import (
    InventoryError,
    gather_all,
    gather_subsystem_evidence,
    load_inventory,
)
from tools.innovation.subsystem_map import (
    SubsystemMapError,
    load_targets,
    project_label,
)

CLASSIFICATION = "CUI // SP-CTI"

REPORT_PATH = Path("docs") / "research" / "external-benchmark-map.generated.md"

TOOL = "tools/innovation/benchmark_report.py"

SOURCES = (
    "args/xbm_subsystem_inventory.yaml",
    "args/innovation_promoter.yaml",
    "context/genesis/competitors.yaml",
    MAP_DOC,
)

# Passed to the evidence collector as its db_error so it reports `unmeasured`
# rather than opening a connection. It is a mode, not a failure, so the sentence
# d1 builds around it is replaced before rendering — see OFFLINE_LIMIT.
OFFLINE_REASON = "no database was opened (offline render)"

OFFLINE_LIMIT = (
    "row counts were not measured — this report is generated without a database so that it "
    "reproduces byte-for-byte on any machine. Run with --live for the measured half."
)

POSITION_LABELS = {
    "ahead": "Ahead",
    "parity": "Parity",
    "behind": "Behind",
    "unknown": "Not positioned",
}

VERDICT_LABELS = {
    "ahead": "Ahead, with items outstanding",
    "parity": "Parity, with named work",
    "gap": "Gap",
    VERDICT_NO_ADAPTATION_NEEDED: "No adaptation needed",
}

_WHITESPACE = re.compile(r"\s+")


# ── evidence ─────────────────────────────────────────────────────────────


def offline_evidence(
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    base_dir: Path = BASE_DIR,
) -> Dict[str, Any]:
    """Every subsystem's evidence with the database deliberately not opened.

    Module counts are globs over the tree, so they are as real here as in a live
    run; only the row half is withheld. ``db_error`` is what makes d1 report
    ``unmeasured`` instead of connecting — the same path a machine with no
    database takes, reached on purpose rather than by accident.
    """
    inventory = load_inventory() if inventory is None else inventory
    return {
        "subsystems": {
            tag: gather_subsystem_evidence(
                tag, inventory=inventory, base_dir=base_dir, db_error=OFFLINE_REASON
            )
            for tag in sorted(inventory)
        },
        "total_subsystems": len(inventory),
        "db_error": OFFLINE_REASON,
    }


def build_result(
    *,
    live: bool = False,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
) -> Dict[str, Any]:
    """The whole comparison, offline or measured."""
    inventory = load_inventory() if inventory is None else inventory
    evidence = (
        gather_all(inventory=inventory, base_dir=base_dir, conn=conn)
        if live
        else offline_evidence(inventory, base_dir)
    )
    result = compare_all(
        inventory=inventory, targets=targets, evidence=evidence, base_dir=base_dir
    )
    result["live"] = bool(live)
    return result


# ── rendering helpers ────────────────────────────────────────────────────


def _flat(text: Optional[str]) -> str:
    """YAML folded scalars arrive with newlines; markdown table cells cannot hold them."""
    return _WHITESPACE.sub(" ", str(text or "")).strip()


def _cell(text: Optional[str]) -> str:
    """A pipe inside a cell ends the cell, so it has to be escaped, not stripped."""
    return _flat(text).replace("|", "\\|")


def _ordered(result: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Benchmarked subsystems in the map's section order, then the rest by tag.

    The split is the map's own: §1–§10 were benchmarked against named external
    projects, and the other five predate that pass. Presenting them in one list
    would imply the second group was judged and found unremarkable.
    """
    records = list(result["subsystems"].values())
    sectioned = sorted(
        (r for r in records if r.get("benchmark_section") is not None),
        key=lambda r: int(r["benchmark_section"]),
    )
    unsectioned = sorted(
        (r for r in records if r.get("benchmark_section") is None),
        key=lambda r: r["subsystem"],
    )
    return sectioned, unsectioned


def _verdict_label(record: Dict[str, Any]) -> str:
    if record.get("verdict") is None:
        return "Not comparable"
    return VERDICT_LABELS.get(record["verdict"], str(record["verdict"]))


def _heading_label(record: Dict[str, Any]) -> str:
    """The verdict, carrying the position when the two say different things.

    A section heading is the one place the reader gets a single line, and
    collapsing ``position`` into ``verdict`` there would force the choice
    ``benchmark_compare`` refuses to make: §7 is ``position: ahead`` with
    ``verdict: no_adaptation_needed``, and a heading reading only "no adaptation
    needed" loses that ICDEV leads. Everywhere else the two are redundant —
    behind/gap, ahead/ahead, parity/parity — so only this case is qualified,
    which is the same rule the d3 CLI applies.
    """
    if record.get("verdict") == VERDICT_NO_ADAPTATION_NEEDED:
        position = record.get("position")
        if position in (POSITION_AHEAD, POSITION_PARITY):
            return f"{POSITION_LABELS[position]}, no adaptation needed"
    return _verdict_label(record)


def _limits(record: Dict[str, Any]) -> List[str]:
    """Measurement limits with the offline sentinel swapped for a truthful sentence.

    d1 phrases an absent database as "not reachable", which is right when it
    tried and failed and wrong when it was told not to try. The distinction is
    the whole of rule 3 in ``benchmark_compare`` — absence of evidence is not
    evidence — so it survives into the prose.
    """
    return [
        OFFLINE_LIMIT if OFFLINE_REASON in limit else limit
        for limit in record.get("measurement_limits", [])
    ]


def _projects_for(subsystem: str, targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [t for t in targets if (t.get("subsystem") or "").strip() == subsystem]
    return sorted(rows, key=lambda t: project_label(t).lower())


def _project_link(target: Dict[str, Any]) -> str:
    label = project_label(target)
    url = target.get("url") or (
        f"https://github.com/{target['repo']}" if target.get("repo") else None
    )
    return f"[{_cell(label)}]({url})" if url else _cell(label)


# ── rendering ────────────────────────────────────────────────────────────


def _render_header(result: Dict[str, Any], live: bool) -> List[str]:
    counts = result["verdict_counts"]
    return [
        "# External Benchmark Map — generated",
        "",
        CLASSIFICATION,
        "",
        f"<!-- GENERATED FILE. Do not edit by hand: regenerate with `python {TOOL} --write`.",
        "     Edit the sources instead — " + ", ".join(SOURCES) + ". -->",
        "",
        f"Every verdict below is produced by `{TOOL}` from the four sources named at the",
        f"foot of this document. The hand-written companion, [{MAP_DOC}]({Path(MAP_DOC).name}),",
        "stays the human reading that supplies the *declared* half; this file is that reading",
        "re-checked against the tree on every run.",
        "",
        "**What is measured and what is declared.** Module counts are globs over this checkout,",
        "so they move when the tree moves. The external half is *declared* — no tool here clones",
        "Backstage and counts its features — and it is dated by whoever last edited the map. The",
        "measured half can overrule the declared one in **both** directions: a declared gap whose",
        "closing condition is now satisfied is reported retired, and a declared lead that has",
        "fallen below its module floor becomes a gap.",
        "",
        (
            "**Row counts were measured against a live database.** This rendering is therefore"
            " not reproducible on another machine and must not be committed."
            if live
            else "**Row counts were not measured.** This file is generated offline so it"
            " reproduces byte-for-byte anywhere, which is what lets CI diff it. Offline,"
            " nothing retires a declared finding — the conservative reading is the one that"
            " gets committed. `--live` opens the database and re-checks every closing"
            " condition."
        ),
        "",
        "---",
        "",
        "## Summary",
        "",
        f"{result['total_subsystems']} subsystems: "
        + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none comparable")
        + (
            f", not_comparable={len(result['not_comparable'])}"
            if result["not_comparable"]
            else ""
        ),
        "",
    ]


def _render_summary_table(records: List[Dict[str, Any]], numbered: bool) -> List[str]:
    head = "| # | Subsystem | Benchmarked against | Position | Verdict | Modules | Outstanding |"
    lines = [head, "|---|---|---|---|---|---|---|"]
    for record in records:
        section = record.get("benchmark_section")
        against = record["declared"].get("benchmarked_against") or "— not benchmarked —"
        lines.append(
            f"| {section if numbered and section is not None else '—'} "
            f"| {_cell(record['title'])} "
            f"| {_cell(against)} "
            f"| {POSITION_LABELS.get(record['position'], record['position'])} "
            f"| **{_verdict_label(record)}** "
            f"| {record['measured']['module_count']} "
            f"| {len(record['outstanding'])} |"
        )
    lines.append("")
    return lines


def _render_section(record: Dict[str, Any], targets: List[Dict[str, Any]], live: bool) -> List[str]:
    section = record.get("benchmark_section")
    heading = f"## {section}. {record['title']}" if section is not None else f"## {record['title']}"
    lines = [f"{heading} — **{_heading_label(record)}**", ""]

    declared = record["declared"]
    against = declared.get("benchmarked_against")
    lines.append(
        f"**External.** {_flat(against)}"
        if against
        else "**External.** No benchmark pass has named an external counterpart for this subsystem."
    )
    lines.append("")

    projects = _projects_for(record["subsystem"], targets)
    if projects:
        lines += ["| Project | Tracking | What it is |", "|---|---|---|"]
        for target in projects:
            tracking = "manual review" if target.get("trackable") is False else "watched"
            lines.append(
                f"| {_project_link(target)} | {tracking} | {_cell(target.get('notes'))} |"
            )
        lines.append("")

    measured = record["measured"]
    lines.append("**ICDEV today.** " + (_flat(declared.get("finding")) or "No finding declared."))
    lines.append("")
    surface = declared.get("icdev_surface")
    measurement = (
        f"Measured: **{measured['module_count']} modules** "
        f"(floor {measured['module_floor']}) → `{record['code_state']}`"
    )
    if live and measured.get("db_measured"):
        measurement += (
            f"; **{measured['table_row_count']} rows** across {measured['table_count']} tables "
            f"→ `{record['data_state']}`"
        )
    else:
        measurement += f"; rows `{record['data_state']}`"
    if surface:
        measurement += f". Surface: `{_flat(surface)}`"
    lines += [measurement + ".", ""]

    lines.append(
        f"**Verdict.** {_verdict_label(record)}; position "
        f"**{POSITION_LABELS.get(record['position'], record['position'])}** — "
        f"{_flat(record['rationale'])}."
    )
    lines.append("")
    for reason in record.get("basis", []):
        lines.append(f"- {_flat(reason)}")
    if record.get("basis"):
        lines.append("")

    if record.get("finding_retired"):
        lines += [
            "**Retired by measurement.** The declared finding's closing condition is now "
            "satisfied: " + "; ".join(_flat(c) for c in record["retirement"]["conditions"]) + ".",
            "",
        ]

    outstanding = record.get("outstanding") or []
    if outstanding:
        lines.append("**Adapt.**")
        lines.append("")
        for item in outstanding:
            lines.append(f"- {_flat(item['item'])}  *({_flat(item['source'])})*")
        lines.append("")
    else:
        lines += [
            "**Adapt.** Nothing outstanding — neither the benchmark pass nor the watchlist "
            "names an item.",
            "",
        ]

    limits = _limits(record)
    if limits:
        lines.append("**Not measured.**")
        lines.append("")
        for limit in limits:
            lines.append(f"- {_flat(limit)}")
        lines.append("")

    lines += ["---", ""]
    return lines


def _render_leads(result: Dict[str, Any]) -> List[str]:
    """The map's first ranked recommendation, derived rather than restated.

    "Say the ahead parts out loud" is the one recommendation a generator can
    reproduce honestly, because ``position: ahead`` is computed, not asserted.
    The rest of the map's ranked list is editorial judgment and stays there.
    """
    ahead = [r for r in result["subsystems"].values() if r["position"] == POSITION_AHEAD]
    if not ahead:
        return [
            "## Where ICDEV leads",
            "",
            "No subsystem currently holds a declared lead the local evidence still supports.",
            "",
            "---",
            "",
        ]
    lines = ["## Where ICDEV leads", "", "Positioned ahead of the field, measured against this tree:", ""]
    for record in sorted(ahead, key=lambda r: (r.get("benchmark_section") or 99, r["subsystem"])):
        lines.append(
            f"- **{_cell(record['title'])}** — benchmarked against "
            f"{_cell(record['declared'].get('benchmarked_against') or 'nothing named')}; "
            f"{_verdict_label(record).lower()}."
        )
    lines += ["", "---", ""]
    return lines


def _render_outstanding_index(result: Dict[str, Any]) -> List[str]:
    """Everything still to be taken, in one place — the promoter's reading of this document."""
    lines = ["## Everything outstanding", ""]
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for record in result["subsystems"].values():
        for item in record.get("outstanding") or []:
            rows.append((record["title"], item))
    if not rows:
        lines += ["Nothing is outstanding across any subsystem.", "", "---", ""]
        return lines
    carrying = len({title for title, _ in rows})
    needed = len(result["adaptation_needed"])
    lines += [
        f"{len(rows)} item(s) outstanding across {carrying} subsystem(s); "
        f"{needed} subsystem(s) carry `adaptation_needed`."
        + (
            " The two differ because a subsystem with no verdict can still carry a watchlist "
            "candidate — an item to take is not the same thing as a judged deficit."
            if carrying != needed
            else ""
        ),
        "",
        "| Subsystem | Item | Source |",
        "|---|---|---|",
    ]
    for title, item in sorted(rows, key=lambda r: (r[0].lower(), _flat(r[1]["item"]).lower())):
        lines.append(f"| {_cell(title)} | {_cell(item['item'])} | {_cell(item['source'])} |")
    lines += ["", "---", ""]
    return lines


def _render_footer(result: Dict[str, Any]) -> List[str]:
    lines = ["## What this report does not cover", ""]
    if result["not_comparable"]:
        lines += [
            "These subsystems get no verdict. That is the intended outcome, not a failure — a "
            "subsystem nobody has benchmarked is scored against nothing, never against zero.",
            "",
        ]
        for tag in result["not_comparable"]:
            record = result["subsystems"][tag]
            lines.append(f"- **{_cell(record['title'])}** (`{tag}`) — {_flat(record['rationale'])}.")
        lines.append("")
    lines += [
        "The external half of every comparison is a dated human reading, and no run of this "
        "tool refreshes it. When an external project moves, the watchlist and "
        f"[{MAP_DOC}]({Path(MAP_DOC).name}) are edited by hand; this document then re-checks "
        "ICDEV's half against it.",
        "",
        "## Sources",
        "",
    ]
    for source in SOURCES:
        lines.append(f"- `{source}`")
    lines += [
        "",
        f"Generated by `{TOOL}`. This file is checked in, so its as-of date is its git commit "
        "date — no wall-clock stamp is written into the body, because one would make the file "
        "differ on every run and there would be nothing left for CI to diff.",
        "",
        CLASSIFICATION,
        "",
    ]
    return lines


def render_markdown(
    result: Dict[str, Any],
    *,
    targets: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """The comparison as the document, deterministic given its inputs."""
    targets = load_targets() if targets is None else targets
    live = bool(result.get("live"))
    sectioned, unsectioned = _ordered(result)

    lines: List[str] = []
    lines += _render_header(result, live)
    lines += _render_summary_table(sectioned, numbered=True)
    if unsectioned:
        lines += [
            "Subsystems the benchmark map has not covered:",
            "",
        ]
        lines += _render_summary_table(unsectioned, numbered=False)
    lines += ["---", ""]

    for record in sectioned:
        lines += _render_section(record, targets, live)
    if unsectioned:
        lines += [
            "# Subsystems the benchmark map has not benchmarked",
            "",
            "Declared in the inventory and measured here, but no external pass has judged them. "
            "They are listed so their absence from the map is visible rather than silent.",
            "",
            "---",
            "",
        ]
        for record in unsectioned:
            lines += _render_section(record, targets, live)

    lines += _render_leads(result)
    lines += _render_outstanding_index(result)
    lines += _render_footer(result)
    return "\n".join(lines).rstrip("\n") + "\n"


def generate(
    *,
    live: bool = False,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
) -> Tuple[str, Dict[str, Any]]:
    """The markdown and the structured result behind it."""
    targets = load_targets() if targets is None else targets
    result = build_result(
        live=live, inventory=inventory, targets=targets, base_dir=base_dir, conn=conn
    )
    return render_markdown(result, targets=targets), result


# ── write / check ────────────────────────────────────────────────────────


def write_report(path: Path, markdown: str) -> Dict[str, Any]:
    """Write with explicit LF endings.

    ``Path.write_text`` translates to the platform separator, which on Windows
    produces a CRLF file that the next ``--check`` on Linux reports as wholly
    changed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown)
    return {"path": str(path), "bytes": len(markdown.encode("utf-8")),
            "lines": markdown.count("\n")}


def check_report(path: Path, markdown: str) -> Dict[str, Any]:
    """Is the checked-in file what the sources currently produce?

    Compared on universal-newline reads so a checkout with CRLF line endings is
    not reported as a content change — the gate is about content drift, and git
    already owns line endings.
    """
    if not path.exists():
        return {
            "in_sync": False,
            "path": str(path),
            "reason": f"{path} does not exist; run --write",
            "diff": "",
        }
    current = path.read_text(encoding="utf-8")
    if current == markdown:
        return {"in_sync": True, "path": str(path), "reason": None, "diff": ""}
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            markdown.splitlines(keepends=True),
            fromfile=f"{path} (checked in)",
            tofile=f"{path} (regenerated)",
            n=2,
        )
    )
    return {
        "in_sync": False,
        "path": str(path),
        "reason": "the checked-in report is not what its sources now produce",
        "diff": diff,
    }


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the external benchmark comparison as markdown"
    )
    parser.add_argument("--write", action="store_true", help="Write the report to --out")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate and diff against the checked-in file; exit 1 on drift",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Measure row counts against the database (not reproducible; will not be written)",
    )
    parser.add_argument("--out", type=Path, help=f"Output path (default {REPORT_PATH.as_posix()})")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    out = args.out or (BASE_DIR / REPORT_PATH)

    if args.live and args.check:
        parser.error(
            "--live cannot be checked: row counts differ per machine, so a live render "
            "never matches the checked-in file"
        )
    if args.live and args.write and args.out is None:
        parser.error(
            "--live --write would commit a rendering that only reproduces on this machine "
            "and would fail --check everywhere else; pass an explicit --out to keep it aside"
        )

    try:
        markdown, result = generate(live=args.live)
    except (InventoryError, SubsystemMapError, KeyError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(
            json.dumps({"error": message}) if args.json else f"ERROR: {message}",
            file=sys.stderr,
        )
        return 1

    if args.check:
        report = check_report(out, markdown)
        if args.json:
            print(json.dumps(report, indent=2))
        elif report["in_sync"]:
            print(f"  in sync: {report['path']}")
        else:
            print(f"ERROR: {report['reason']}", file=sys.stderr)
            print(f"  regenerate with: python {TOOL} --write", file=sys.stderr)
            if report["diff"]:
                print(report["diff"], file=sys.stderr)
        return 0 if report["in_sync"] else 1

    if args.write:
        written = write_report(out, markdown)
        if args.json:
            print(json.dumps({**written, **{
                "verdict_counts": result["verdict_counts"],
                "not_comparable": result["not_comparable"],
                "live": result["live"],
            }}, indent=2))
        else:
            print(f"  wrote {written['path']} ({written['lines']} lines)")
        return 0

    if args.json:
        print(json.dumps({"markdown": markdown, "result": result}, indent=2, default=str))
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
