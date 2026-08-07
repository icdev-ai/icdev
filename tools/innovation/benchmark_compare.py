#!/usr/bin/env python3
# CUI // SP-CTI
"""Is ICDEV ahead, at parity, or behind — and is there anything to adapt? (xbm-cmp-01-d3)

d1 measured ICDEV's own half of a benchmark comparison (``icdev_evidence.py``).
d2 joined each external project to the subsystem it benchmarks
(``subsystem_map.py``). Neither answers the only question a scouting finding
exists to raise: *given this finding, does ICDEV have work to do?* This module
is that verdict engine.

It emits four verdicts, and the fourth is the reason this is not a gap scanner:

    ahead                  ICDEV leads, and something external is still
                           outstanding — a lead with residual items to take.
    parity                 ICDEV's side clears its floor; named work remains.
    gap                    ICDEV is behind — measurably, or by a standing
                           finding no measurement has retired.
    no_adaptation_needed   ICDEV matched or exceeded the field and NOTHING is
                           outstanding. Emitted, never implied by silence.

``no_adaptation_needed`` is a first-class outcome. A comparator that can only
report deficits misrepresents the platform and stops being read the first time
someone notices — the benchmark map says so in its own method note ("a benchmark
that only finds deficits is not a benchmark"). On the shipped configs it lands on
compliance_ato and delivery_pipeline, and ``position: ahead`` lands on those two
plus observability: the three areas §1 of the map's recommendations says ICDEV
leads the commercial field and nobody had written down.

**verdict and position are different questions, so they are different fields.**
``position`` (ahead / parity / behind / unknown) is where ICDEV stands.
``verdict`` is what to do about it. compliance_ato is ``position: ahead`` with
``verdict: no_adaptation_needed`` — collapsing those into one field would force a
choice between saying ICDEV leads and saying there is nothing to take, and both
are true.

Four rules that are bugs if reversed:

1. **The external half is declared; the local half is measured.** This module
   cannot clone Backstage and count its features, so "Backstage has ownership
   fields ICDEV lacks" arrives from ``args/innovation_promoter.yaml`` and
   ``docs/research/external-benchmark-map.md`` as a dated human reading. What it
   *can* do is re-measure ICDEV every run and overrule that reading in either
   direction: a declared gap whose ``closes_when`` is now satisfied is reported
   as retired, and a declared lead whose modules have fallen below the floor is
   reported as a gap. Presenting the declared half as if it were measured would
   be the lie; presenting the measured half as unable to move it would be the
   waste.

2. **A finding with no measurable closing condition stays open.** §4's gap is
   "79 analyzer-shaped modules share no contract" — no row count retires that.
   Such subsystems declare no ``closes_when``, the verdict stands, and
   ``measurement_limits`` says the tool could not re-check it. Inventing a
   threshold so the number moves is how a stale finding gets laundered into a
   fixed one.

3. **Absence of evidence is not evidence.** An unreachable database yields
   ``not_assessed``, never ``unfed``, so a cold worktree cannot manufacture a
   "built but nothing running through it" gap out of its own blindness. A
   subsystem nobody has benchmarked reports ``verdict: null`` with
   ``comparable: false`` rather than being scored against zero — and ``embedded``
   is ``owned: false``, because SparkPilot living in another repository is an
   intentional absence, not a deficit.

4. **"Nothing outstanding" must be asserted, not inferred.** An explicit
   ``outstanding: []`` in the inventory is a pass that looked and found nothing;
   an absent key is nobody having looked. Only the first licenses
   ``no_adaptation_needed``. Open ``adaptation`` blocks on the watchlist are
   merged in on top, so registering a new adaptation candidate moves the verdict
   without anyone editing this file.

Usage:
    python tools/innovation/benchmark_compare.py --subsystem observability --json
    python tools/innovation/benchmark_compare.py --project langfuse --json
    python tools/innovation/benchmark_compare.py --all --json
    python tools/innovation/benchmark_compare.py --all --verdict gap
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation.icdev_evidence import (
    InventoryError,
    gather_all,
    gather_subsystem_evidence,
    load_inventory,
)
from tools.innovation.subsystem_map import (
    AmbiguousProjectError,
    SubsystemMapError,
    icdev_counterpart,
    load_benchmark_subsystems,
    load_targets,
    project_label,
    subsystem_for_project,
)

# ── vocabulary ───────────────────────────────────────────────────────────

VERDICT_AHEAD = "ahead"
VERDICT_PARITY = "parity"
VERDICT_GAP = "gap"
VERDICT_NO_ADAPTATION_NEEDED = "no_adaptation_needed"

VERDICTS = (VERDICT_AHEAD, VERDICT_PARITY, VERDICT_GAP, VERDICT_NO_ADAPTATION_NEEDED)

POSITION_AHEAD = "ahead"
POSITION_PARITY = "parity"
POSITION_BEHIND = "behind"
POSITION_UNKNOWN = "unknown"

# The promoter config's verdict vocabulary folded onto a direction. Both
# qualified forms keep their direction: §7's "ahead on concept, weak on hygiene"
# is still ahead, and §3's "parity with named gaps" is still parity — the
# qualification is carried by the outstanding list, which is where it can be
# acted on.
DECLARED_POSITIONS = {
    "ahead": POSITION_AHEAD,
    "ahead_weak_hygiene": POSITION_AHEAD,
    "parity": POSITION_PARITY,
    "parity_with_named_gaps": POSITION_PARITY,
    "gap": POSITION_BEHIND,
}

CODE_ABSENT = "absent"
CODE_THIN = "thin"
CODE_BUILT = "built"

DATA_POPULATED = "populated"
DATA_UNFED = "unfed"
DATA_NOT_EXPECTED = "not_expected"
DATA_NOT_ASSESSED = "not_assessed"

# A smoke floor: evidence that something is there. Being above it proves only
# existence, never quality — the quality reading is the declared finding.
DEFAULT_MODULE_FLOOR = 5
DEFAULT_ROW_FLOOR = 1

# An adaptation in one of these states is no longer outstanding. `pending` — the
# status every shipped candidate carries — is not among them.
CLOSED_ADAPTATION_STATUSES = frozenset(
    {"absorbed", "adopted", "complete", "completed", "done", "implemented", "rejected", "declined"}
)

MAP_DOC = "docs/research/external-benchmark-map.md"


# ── config ───────────────────────────────────────────────────────────────


def benchmark_spec(subsystem: str, inventory: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A subsystem's ``benchmark`` block with defaults applied.

    ``assessed`` records whether a benchmark pass declared an outstanding list at
    all, which is a different fact from that list being empty.

    >>> spec = benchmark_spec("x", {"x": {"benchmark": {"outstanding": []}}})
    >>> spec["module_floor"], spec["assessed"], spec["outstanding"]
    (5, True, [])
    >>> benchmark_spec("y", {"y": {}})["assessed"]
    False
    >>> benchmark_spec("z", {"z": {"benchmark": {"owned": False}}})["owned"]
    False
    """
    inventory = load_inventory() if inventory is None else inventory
    if subsystem not in inventory:
        raise KeyError(
            f"subsystem {subsystem!r} is not in the inventory; "
            f"known: {', '.join(sorted(inventory))}"
        )
    spec = inventory[subsystem] or {}
    block = spec.get("benchmark") or {}
    return {
        "module_floor": int(block.get("module_floor", DEFAULT_MODULE_FLOOR)),
        "row_floor": int(block.get("row_floor", DEFAULT_ROW_FLOOR)),
        "data_expected": block.get("data_expected", True) is not False,
        "owned": block.get("owned", True) is not False,
        "finding": block.get("finding"),
        "closes_when": block.get("closes_when") or None,
        "outstanding": list(block.get("outstanding") or []),
        "assessed": "outstanding" in block,
        "declares_tables": bool(spec.get("tables")),
    }


# ── the local half: measured ─────────────────────────────────────────────


def classify_code(module_count: int, floor: int = DEFAULT_MODULE_FLOOR) -> str:
    """Module count against the floor.

    >>> classify_code(0), classify_code(2), classify_code(31)
    ('absent', 'thin', 'built')
    """
    if module_count <= 0:
        return CODE_ABSENT
    return CODE_BUILT if module_count >= floor else CODE_THIN


def classify_data(
    row_count: int,
    floor: int = DEFAULT_ROW_FLOOR,
    *,
    data_expected: bool = True,
    db_measured: bool = True,
    declares_tables: bool = True,
    tables_found: Optional[int] = None,
) -> str:
    """Row count against the floor, with blindness kept distinct from emptiness.

    Two different kinds of blindness are both ``not_assessed``, and neither may
    be reported as ``unfed`` — that would let a cold worktree invent the exact
    finding §8 of the benchmark map reports for real:

    * the database could not be opened at all, and
    * it opened but holds none of this subsystem's tables. A schema that was
      never migrated into *this* database is a fact about the database. Every
      test run here forces SQLite, where most tables do not exist; scoring that
      as "built with nothing running through it" turns a dev instance into
      eleven findings.

    >>> classify_data(954, tables_found=15)
    'populated'
    >>> classify_data(0, tables_found=15)
    'unfed'
    >>> classify_data(0, tables_found=0)
    'not_assessed'
    >>> classify_data(0, db_measured=False)
    'not_assessed'
    >>> classify_data(0, data_expected=False)
    'not_expected'
    >>> classify_data(0, declares_tables=False)
    'not_expected'
    """
    if not declares_tables or not data_expected:
        return DATA_NOT_EXPECTED
    if not db_measured:
        return DATA_NOT_ASSESSED
    if tables_found is not None and tables_found <= 0:
        return DATA_NOT_ASSESSED
    return DATA_POPULATED if row_count >= floor else DATA_UNFED


def evaluate_closes_when(
    closes_when: Optional[Dict[str, Any]], evidence: Dict[str, Any]
) -> Dict[str, Any]:
    """Has the declared finding's closing condition been met?

    ``satisfied`` is None when the condition exists but could not be checked —
    an unreachable database must not read as "condition unmet", which would be a
    measurement failure reported as a finding.

    >>> ev = {"tables": {"kg_edges": 4314}, "db_measured": True}
    >>> evaluate_closes_when({"tables": {"kg_edges": 1}}, ev)["satisfied"]
    True
    >>> ev0 = {"tables": {"developer_scorecards": 0}, "db_measured": True}
    >>> report = evaluate_closes_when({"tables": {"developer_scorecards": 1}}, ev0)
    >>> report["satisfied"], report["conditions"]
    (False, ['developer_scorecards has 0 rows, needs 1'])
    >>> evaluate_closes_when(None, ev)["declared"]
    False
    >>> evaluate_closes_when({"tables": {"kg_edges": 1}}, {"db_measured": False})["satisfied"] is None
    True
    """
    if not closes_when:
        return {"declared": False, "satisfied": None, "conditions": []}
    if not evidence.get("db_measured"):
        return {
            "declared": True,
            "satisfied": None,
            "conditions": ["the database was not reachable, so the condition could not be checked"],
        }

    counts = evidence.get("tables") or {}
    conditions: List[str] = []
    met = True
    for table, minimum in sorted((closes_when.get("tables") or {}).items()):
        actual = counts.get(table)
        if actual is None:
            # Not in the catalog, or not matched by this subsystem's table
            # patterns. Either way the condition is uncheckable as written, and
            # a finding is not retired by a table nobody counted.
            conditions.append(f"{table} was not counted for this subsystem")
            met = False
            continue
        if actual < int(minimum):
            conditions.append(f"{table} has {actual} rows, needs {minimum}")
            met = False
        else:
            conditions.append(f"{table} has {actual} rows, needs {minimum} — met")
    return {"declared": True, "satisfied": met, "conditions": conditions}


# ── the external half: declared ──────────────────────────────────────────


def normalize_position(declared_verdict: Optional[str]) -> Optional[str]:
    """The promoter config's verdict string folded onto a direction.

    >>> normalize_position("ahead_weak_hygiene")
    'ahead'
    >>> normalize_position("parity_with_named_gaps")
    'parity'
    >>> normalize_position("gap")
    'behind'
    >>> normalize_position(None) is None
    True
    """
    if not declared_verdict:
        return None
    return DECLARED_POSITIONS.get(str(declared_verdict).strip().lower())


def open_adaptations(
    subsystem: str, targets: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Watchlist adaptation candidates for a tag that are not yet closed.

    Registering a candidate in ``context/genesis/competitors.yaml`` therefore
    moves the verdict without this module or the inventory being edited.

    >>> targets = [
    ...     {"repo": "yeet-src/httpinspect", "subsystem": "observability",
    ...      "adaptation": {"title": "httpinspect — eBPF HTTP monitor", "status": "pending"}},
    ...     {"repo": "old/absorbed", "subsystem": "observability",
    ...      "adaptation": {"title": "already taken", "status": "absorbed"}},
    ...     {"repo": "langfuse/langfuse", "subsystem": "observability"},
    ... ]
    >>> [a["item"] for a in open_adaptations("observability", targets)]
    ['httpinspect — eBPF HTTP monitor']
    >>> open_adaptations("compliance_ato", targets)
    []
    """
    targets = load_targets() if targets is None else targets
    found: List[Dict[str, Any]] = []
    for target in targets:
        if (target.get("subsystem") or "").strip() != subsystem:
            continue
        adaptation = target.get("adaptation")
        if not isinstance(adaptation, dict):
            continue
        status = str(adaptation.get("status") or "pending").strip().lower()
        if status in CLOSED_ADAPTATION_STATUSES:
            continue
        label = project_label(target)
        found.append(
            {
                "item": adaptation.get("title") or label,
                "source": f"watchlist:{label}",
                "status": status,
                "target": adaptation.get("target"),
            }
        )
    return sorted(found, key=lambda a: a["source"].lower())


def outstanding_items(
    subsystem: str,
    spec: Dict[str, Any],
    targets: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Everything still to be taken from outside: the map's list plus the watchlist's."""
    declared = [
        {"item": item, "source": f"{MAP_DOC}", "status": "declared", "target": None}
        for item in spec.get("outstanding") or []
    ]
    return declared + open_adaptations(subsystem, targets)


# ── the verdict ──────────────────────────────────────────────────────────


def decide_verdict(
    *,
    code_state: str,
    data_state: str,
    declared_position: Optional[str],
    finding_retired: Optional[bool] = None,
    outstanding_count: int = 0,
    assessed: bool = True,
    owned: bool = True,
) -> Dict[str, Any]:
    """The whole decision, as data, from measurements and declarations only.

    Split out and kept free of config and database access so the rules can be
    read in one screen and tested one branch at a time.

    Args:
        code_state: absent / thin / built — measured.
        data_state: populated / unfed / not_expected / not_assessed — measured.
        declared_position: ahead / parity / behind, from the benchmark pass, or
            None if no pass has judged this subsystem.
        finding_retired: whether the declared finding's ``closes_when`` is now
            satisfied. None means no condition was declared or it could not be
            checked, which leaves the finding standing.
        outstanding_count: items still to adapt from outside.
        assessed: a pass declared an outstanding list at all.
        owned: ICDEV holds this subsystem in this tree.

    ICDEV ahead, with one adaptation still open:

    >>> v = decide_verdict(code_state="built", data_state="populated",
    ...                    declared_position="ahead", outstanding_count=1)
    >>> v["verdict"], v["position"]
    ('ahead', 'ahead')

    Ahead with nothing left to take — the map's §2 and §6 conclusion:

    >>> v = decide_verdict(code_state="built", data_state="populated",
    ...                    declared_position="ahead", outstanding_count=0)
    >>> v["verdict"], v["position"], v["adaptation_needed"]
    ('no_adaptation_needed', 'ahead', False)

    A standing gap no measurement retired:

    >>> decide_verdict(code_state="built", data_state="populated",
    ...                declared_position="behind")["verdict"]
    'gap'

    The same gap once its closing condition is met, with work still named:

    >>> v = decide_verdict(code_state="built", data_state="populated",
    ...                    declared_position="behind", finding_retired=True,
    ...                    outstanding_count=1)
    >>> v["verdict"], v["position"]
    ('parity', 'parity')

    A declared lead the local evidence no longer supports — the comparison runs
    in this direction too:

    >>> v = decide_verdict(code_state="thin", data_state="populated",
    ...                    declared_position="ahead", outstanding_count=0)
    >>> v["verdict"], v["position"]
    ('gap', 'behind')

    Nobody has looked, and nothing is queued: no verdict rather than a flattering one.

    >>> v = decide_verdict(code_state="built", data_state="populated",
    ...                    declared_position=None, assessed=False)
    >>> v["verdict"] is None, v["comparable"], v["position"]
    (True, False, 'unknown')

    A declared lead with no declared outstanding list cannot reach
    no_adaptation_needed either — the position survives, the verdict does not:

    >>> v = decide_verdict(code_state="built", data_state="populated",
    ...                    declared_position="ahead", assessed=False)
    >>> v["verdict"] is None, v["position"]
    (True, 'ahead')
    """
    basis: List[str] = []

    if not owned:
        return {
            "verdict": None,
            "position": POSITION_UNKNOWN,
            "adaptation_needed": None,
            "comparable": False,
            "rationale": "not owned in this tree, so its absence is intentional, not a deficit",
            "basis": ["inventory declares owned: false"],
        }

    if not assessed and outstanding_count == 0:
        # Not "nothing is outstanding" — nobody declared an outstanding list and
        # nothing is queued, so there is no basis for saying so. This is the
        # branch that stops an unexamined subsystem reading as a clean sheet,
        # and it fires even when a verdict IS declared: a position without a
        # list of what to take cannot reach no_adaptation_needed honestly.
        return {
            "verdict": None,
            "position": declared_position or POSITION_UNKNOWN,
            "adaptation_needed": None,
            "comparable": False,
            "rationale": (
                "no benchmark pass has declared what is outstanding here and nothing is "
                "queued against it, so there is no external reading to compare against"
            ),
            "basis": ["no declared outstanding list and no open adaptation candidate"],
        }

    if declared_position:
        basis.append(f"benchmark pass declared {declared_position}")

    if code_state in (CODE_ABSENT, CODE_THIN):
        basis.append(f"code_state={code_state} — below the module floor")
        if declared_position in (POSITION_AHEAD, POSITION_PARITY):
            basis.append(
                f"local evidence no longer supports the declared {declared_position} reading"
            )
        return {
            "verdict": VERDICT_GAP,
            "position": POSITION_BEHIND,
            "adaptation_needed": True,
            "comparable": True,
            "rationale": f"ICDEV's own side is {code_state} — measured, not declared",
            "basis": basis,
        }

    if data_state == DATA_UNFED:
        basis.append("data_state=unfed — tables exist and hold nothing")
        if declared_position in (POSITION_AHEAD, POSITION_PARITY):
            basis.append(
                f"local evidence no longer supports the declared {declared_position} reading"
            )
        return {
            "verdict": VERDICT_GAP,
            "position": POSITION_BEHIND,
            "adaptation_needed": True,
            "comparable": True,
            "rationale": "the machinery is built and nothing is running through it",
            "basis": basis,
        }

    basis.append(f"code_state={code_state}, data_state={data_state}")

    position = declared_position or POSITION_UNKNOWN
    if declared_position == POSITION_BEHIND:
        if not finding_retired:
            basis.append("the declared finding has not been retired by measurement")
            return {
                "verdict": VERDICT_GAP,
                "position": POSITION_BEHIND,
                "adaptation_needed": True,
                "comparable": True,
                "rationale": "the benchmark pass found ICDEV behind and nothing has retired that",
                "basis": basis,
            }
        basis.append("the declared finding's closing condition is now satisfied")
        position = POSITION_PARITY

    if outstanding_count:
        verdict = VERDICT_AHEAD if position == POSITION_AHEAD else VERDICT_PARITY
        basis.append(f"{outstanding_count} item(s) still outstanding")
        return {
            "verdict": verdict,
            "position": position,
            "adaptation_needed": True,
            "comparable": True,
            "rationale": (
                "ICDEV leads and there is still something worth taking"
                if verdict == VERDICT_AHEAD
                else "ICDEV's side clears its floor with named work outstanding"
            ),
            "basis": basis,
        }

    basis.append("nothing outstanding — neither the benchmark pass nor the watchlist names an item")
    return {
        "verdict": VERDICT_NO_ADAPTATION_NEEDED,
        "position": position,
        "adaptation_needed": False,
        "comparable": True,
        "rationale": "ICDEV matched or exceeded the field and there is nothing to adapt",
        "basis": basis,
    }


def _measurement_limits(
    *,
    evidence: Dict[str, Any],
    spec: Dict[str, Any],
    declared_position: Optional[str],
    section: Optional[Any],
    retirement: Dict[str, Any],
    data_state: str,
) -> List[str]:
    """What this run could not check. Stated, so a limit is never read as a result."""
    limits: List[str] = []
    if evidence.get("db_error"):
        limits.append(
            f"the database was not reachable ({evidence['db_error']}), so row counts are "
            "not_assessed rather than zero"
        )
    elif data_state == DATA_NOT_ASSESSED and spec["declares_tables"] and spec["data_expected"]:
        limits.append(
            "none of this subsystem's tables exist in the database that was measured, so its "
            "data was not assessed — that is a fact about this instance, not about the subsystem"
        )
    where = f"{MAP_DOC}" + (f" §{section}" if section else "")
    if declared_position == POSITION_BEHIND and not retirement["declared"]:
        limits.append(
            "the declared finding has no measurable closing condition, so this tool cannot "
            f"retire it — re-reading {where} is a human step"
        )
    if retirement["declared"] and retirement["satisfied"] is None and not evidence.get("db_error"):
        limits.append("the declared closing condition could not be evaluated")
    if not spec["assessed"] and declared_position:
        limits.append(
            f"a verdict is declared for this subsystem but no outstanding list is, so "
            f"'nothing outstanding' cannot be asserted from {where}"
        )
    return limits


def compare_subsystem(
    subsystem: str,
    *,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    benchmark: Optional[Dict[str, Dict[str, Any]]] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
    catalog: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """One subsystem's full comparison: measured evidence, declared reading, verdict.

    Args:
        subsystem: A tag from args/xbm_subsystem_inventory.yaml.
        evidence: Pre-gathered d1 evidence. Pass one to reuse a sweep's
            measurements instead of re-opening a connection per subsystem.

    Raises:
        KeyError: the tag is not in the inventory — the same contract d1 and d2
            use, so a typo fails identically everywhere in the chain.
    """
    inventory = load_inventory() if inventory is None else inventory
    benchmark = load_benchmark_subsystems() if benchmark is None else benchmark
    targets = load_targets() if targets is None else targets
    spec = benchmark_spec(subsystem, inventory)

    if evidence is None:
        evidence = gather_subsystem_evidence(
            subsystem, inventory=inventory, base_dir=base_dir, conn=conn, catalog=catalog
        )

    counterpart = icdev_counterpart(subsystem, inventory=inventory, benchmark=benchmark)
    declared_position = normalize_position(counterpart["declared_verdict"])

    code_state = classify_code(evidence["module_count"], spec["module_floor"])
    data_state = classify_data(
        evidence["table_row_count"],
        spec["row_floor"],
        data_expected=spec["data_expected"],
        db_measured=evidence.get("db_measured", False),
        declares_tables=spec["declares_tables"],
        tables_found=evidence.get("table_count"),
    )
    retirement = evaluate_closes_when(spec["closes_when"], evidence)
    outstanding = outstanding_items(subsystem, spec, targets)

    decision = decide_verdict(
        code_state=code_state,
        data_state=data_state,
        declared_position=declared_position,
        finding_retired=retirement["satisfied"],
        outstanding_count=len(outstanding),
        assessed=spec["assessed"],
        owned=spec["owned"],
    )

    section = counterpart.get("benchmark_section")
    return {
        "subsystem": subsystem,
        "title": counterpart["title"],
        "benchmark_section": section,
        "verdict": decision["verdict"],
        "position": decision["position"],
        "adaptation_needed": decision["adaptation_needed"],
        "comparable": decision["comparable"],
        "rationale": decision["rationale"],
        "basis": decision["basis"],
        "code_state": code_state,
        "data_state": data_state,
        "measured": {
            "module_count": evidence["module_count"],
            "module_floor": spec["module_floor"],
            "table_count": evidence["table_count"],
            "table_row_count": evidence["table_row_count"],
            "row_floor": spec["row_floor"],
            "tables": evidence.get("tables") or {},
            "db_measured": evidence.get("db_measured", False),
            "measured_at": evidence.get("measured_at"),
        },
        "declared": {
            "verdict": counterpart["declared_verdict"],
            "position": declared_position,
            "benchmarked_against": counterpart["benchmarked_against"],
            "icdev_surface": counterpart["icdev_surface"],
            "finding": spec["finding"],
            "source": f"{MAP_DOC}" + (f" §{section}" if section else ""),
        },
        "finding_retired": retirement["satisfied"] is True,
        "retirement": retirement,
        "outstanding": outstanding,
        "external_projects": sorted(
            project_label(t)
            for t in targets
            if (t.get("subsystem") or "").strip() == subsystem
        ),
        "measurement_limits": _measurement_limits(
            evidence=evidence,
            spec=spec,
            declared_position=declared_position,
            section=section,
            retirement=retirement,
            data_state=data_state,
        ),
    }


def compare_all(
    *,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    benchmark: Optional[Dict[str, Dict[str, Any]]] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
) -> Dict[str, Any]:
    """Every subsystem, over one sweep of the evidence collector.

    ``verdict_counts`` counts only comparable subsystems; the rest are counted
    separately as ``not_comparable`` so a refusal to judge never inflates a
    verdict tally.
    """
    inventory = load_inventory() if inventory is None else inventory
    benchmark = load_benchmark_subsystems() if benchmark is None else benchmark
    targets = load_targets() if targets is None else targets
    if evidence is None:
        evidence = gather_all(inventory=inventory, base_dir=base_dir, conn=conn)

    per_subsystem = evidence.get("subsystems", {})
    subsystems = {
        tag: compare_subsystem(
            tag,
            inventory=inventory,
            benchmark=benchmark,
            targets=targets,
            evidence=per_subsystem.get(tag),
            base_dir=base_dir,
        )
        for tag in sorted(inventory)
    }

    counts: Dict[str, int] = {}
    not_comparable: List[str] = []
    for tag, record in subsystems.items():
        if record["verdict"] is None:
            not_comparable.append(tag)
            continue
        counts[record["verdict"]] = counts.get(record["verdict"], 0) + 1

    return {
        "subsystems": subsystems,
        "total_subsystems": len(subsystems),
        "verdict_counts": counts,
        "not_comparable": sorted(not_comparable),
        "ahead": sorted(t for t, r in subsystems.items() if r["position"] == POSITION_AHEAD),
        "adaptation_needed": sorted(
            t for t, r in subsystems.items() if r["adaptation_needed"] is True
        ),
        "no_adaptation_needed": sorted(
            t for t, r in subsystems.items() if r["verdict"] == VERDICT_NO_ADAPTATION_NEEDED
        ),
        "db_error": evidence.get("db_error"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def compare_project(
    identifier: str,
    *,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    benchmark: Optional[Dict[str, Dict[str, Any]]] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
) -> Dict[str, Any]:
    """What a finding about one external project means for ICDEV.

    The entry point a scouting finding actually arrives at: a repo slug in, a
    verdict out. An identifier nobody tracks returns ``verdict: None`` with the
    reason — an unroutable finding is a finding about the watchlist, not nothing.

    Raises:
        AmbiguousProjectError: the identifier names projects in two subsystems.
    """
    targets = load_targets() if targets is None else targets
    subsystem = subsystem_for_project(identifier, targets=targets)
    if subsystem is None:
        return {
            "project": identifier,
            "subsystem": None,
            "verdict": None,
            "comparable": False,
            "rationale": (
                "not on the scout watchlist, so no subsystem owns a finding about it"
            ),
        }
    record = compare_subsystem(
        subsystem,
        inventory=inventory,
        benchmark=benchmark,
        targets=targets,
        evidence=evidence,
        base_dir=base_dir,
        conn=conn,
    )
    return {"project": identifier, **record}


# ── CLI ──────────────────────────────────────────────────────────────────


def _verdict_label(record: Dict[str, Any]) -> str:
    if record.get("verdict") is None:
        return "not comparable"
    if record["verdict"] == VERDICT_NO_ADAPTATION_NEEDED:
        position = record.get("position")
        prefix = f"{position} — " if position in (POSITION_AHEAD, POSITION_PARITY) else ""
        return f"{prefix}no adaptation needed"
    return str(record["verdict"])


def _render_one(record: Dict[str, Any]) -> None:
    if record.get("project"):
        print(f"  {record['project']} benchmarks {record.get('subsystem') or '(unrouted)'}")
    if record.get("subsystem"):
        print(f"  [{_verdict_label(record)}] {record['subsystem']} — {record.get('title', '')}")
    print(f"    {record.get('rationale', '')}")
    for line in record.get("basis", []):
        print(f"      · {line}")
    if record.get("finding_retired"):
        print(f"    retired by measurement: {record['declared']['finding']}")
    for item in record.get("outstanding", []):
        print(f"    outstanding: {item['item']}  ({item['source']})")
    for limit in record.get("measurement_limits", []):
        print(f"    not measured: {limit}")


def _render_all(result: Dict[str, Any]) -> None:
    if result.get("filtered_to_verdict"):
        # The counts below describe the whole sweep, not the filtered list.
        print(f"  (showing verdict={result['filtered_to_verdict']} only)")
    for record in result["subsystems"].values():
        print(
            f"  [{_verdict_label(record):<29}] {record['subsystem']:<22}"
            f" modules={record['measured']['module_count']:<4}"
            f" rows={record['measured']['table_row_count']:<7}"
            f" outstanding={len(record['outstanding'])}"
        )
    print(
        f"\n{result['total_subsystems']} subsystems: "
        + ", ".join(f"{k}={v}" for k, v in sorted(result["verdict_counts"].items()))
        + (f", not_comparable={len(result['not_comparable'])}" if result["not_comparable"] else "")
    )
    if result["ahead"]:
        print(f"  ICDEV ahead: {', '.join(result['ahead'])}")
    if result["no_adaptation_needed"]:
        print(f"  no adaptation needed: {', '.join(result['no_adaptation_needed'])}")
    if result.get("db_error"):
        print(f"\n  DB not measured: {result['db_error']}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verdict engine: is ICDEV ahead, at parity, or behind on a subsystem?"
    )
    parser.add_argument("--subsystem", help="Subsystem tag to compare")
    parser.add_argument("--project", help="External project — repo slug, name or GitHub URL")
    parser.add_argument("--all", action="store_true", help="Every declared subsystem")
    parser.add_argument("--verdict", choices=VERDICTS, help="With --all, keep only this verdict")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    try:
        if args.all:
            result = compare_all()
            if args.verdict:
                result["subsystems"] = {
                    tag: record
                    for tag, record in result["subsystems"].items()
                    if record["verdict"] == args.verdict
                }
                result["filtered_to_verdict"] = args.verdict
        elif args.subsystem:
            result = compare_subsystem(args.subsystem)
        elif args.project:
            result = compare_project(args.project)
        else:
            parser.error("use --subsystem <tag>, --project <id>, or --all")
    except (InventoryError, SubsystemMapError, AmbiguousProjectError, KeyError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(
            json.dumps({"error": message}) if args.json else f"ERROR: {message}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif "subsystems" in result:
        _render_all(result)
    else:
        _render_one(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
