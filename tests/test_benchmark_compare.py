#!/usr/bin/env python3
# CUI // SP-CTI
"""Guards for the benchmark verdict engine (xbm-cmp-01-d3).

``tools/innovation/benchmark_compare.py`` turns d1's measurements and d2's join
into one of four verdicts. What these pin, and why each is here rather than
being obvious:

1. **All four verdicts are reachable, including on the shipped configs.** The
   acceptance criterion for this task is that the engine emits ``ahead``,
   ``parity``, ``gap`` AND ``no_adaptation_needed`` with correct logic for the
   known cases in ``docs/research/external-benchmark-map.md``. A comparator that
   can only produce deficits is the specific failure this task exists to
   prevent, so ``no_adaptation_needed`` is asserted against the real tree and
   not only against a fixture that could be tuned until it passed.

2. **The map's own conclusions are the fixture.** §2 observability, §6
   compliance and §7 delivery are the three the map found ICDEV ahead on; §1,
   §4, §8 and §9 are the gaps; §3 and §5 are parity. Each is asserted by name,
   so a config edit that quietly flips one fails here rather than in a report
   nobody re-derives.

3. **The verdict runs in both directions.** A declared lead whose modules have
   fallen below the floor must read ``gap``/``behind``, and a declared gap whose
   closing condition is now satisfied must stop being reported as one. A
   benchmark that can only ratchet one way is a stale document with a CLI.

4. **Blindness never becomes a finding.** An unreachable database must yield
   ``not_assessed``, never ``unfed`` — otherwise a cold worktree manufactures
   the exact gap §8 reports for real.

The pure-decision tests use literal fixtures and no database. The shipped-config
tests read the real YAML; the ones that need measurements build the evidence
dict directly rather than opening a connection, so the whole file runs offline.
"""

import doctest
import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import benchmark_compare as bc

INVENTORY = BASE_DIR / "args" / "xbm_subsystem_inventory.yaml"
PROMOTER = BASE_DIR / "args" / "innovation_promoter.yaml"
WATCHLIST = BASE_DIR / "context" / "genesis" / "competitors.yaml"


# ── fixtures ─────────────────────────────────────────────────────────────


def evidence(
    subsystem="observability",
    *,
    module_count=31,
    row_count=954,
    tables=None,
    table_count=None,
    db_measured=True,
    db_error=None,
):
    """A d1 evidence dict, built literally so no test needs a database."""
    if table_count is None:
        table_count = len(tables) if tables else (1 if row_count else 0)
    record = {
        "subsystem": subsystem,
        "title": subsystem,
        "benchmark_section": None,
        "module_count": module_count,
        "modules": {},
        "table_count": table_count,
        "table_row_count": row_count,
        "tables": tables or {},
        "status": "live",
        "db_measured": db_measured,
        "measured_at": "2026-08-07T00:00:00+00:00",
    }
    if db_error:
        record["db_error"] = db_error
    return record


@pytest.fixture(scope="module")
def shipped_inventory():
    return yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))["subsystems"]


@pytest.fixture(scope="module")
def shipped_benchmark():
    return yaml.safe_load(PROMOTER.read_text(encoding="utf-8"))["benchmark_subsystems"]


@pytest.fixture(scope="module")
def shipped_targets():
    return yaml.safe_load(WATCHLIST.read_text(encoding="utf-8"))["targets"]


# ── the four verdicts, in isolation ──────────────────────────────────────


def test_ahead_is_emitted_when_icdev_leads_with_work_left():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_AHEAD,
        outstanding_count=1,
    )
    assert result["verdict"] == bc.VERDICT_AHEAD
    assert result["position"] == bc.POSITION_AHEAD
    assert result["adaptation_needed"] is True


def test_parity_is_emitted_when_the_floor_is_cleared_with_named_gaps():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_PARITY,
        outstanding_count=3,
    )
    assert result["verdict"] == bc.VERDICT_PARITY
    assert result["position"] == bc.POSITION_PARITY


def test_gap_is_emitted_for_a_standing_declared_finding():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_BEHIND,
        finding_retired=False,
        outstanding_count=1,
    )
    assert result["verdict"] == bc.VERDICT_GAP
    assert result["position"] == bc.POSITION_BEHIND


def test_no_adaptation_needed_is_emitted_when_nothing_is_outstanding():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_AHEAD,
        outstanding_count=0,
    )
    assert result["verdict"] == bc.VERDICT_NO_ADAPTATION_NEEDED
    assert result["adaptation_needed"] is False
    # The position survives the verdict: "nothing to adapt" must not erase the
    # fact that ICDEV leads, which is the half of the result that gets used for
    # positioning rather than for planning.
    assert result["position"] == bc.POSITION_AHEAD


def test_no_adaptation_needed_also_covers_parity_matched():
    """"Matched" licenses it too — the criterion is matched OR exceeded."""
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_PARITY,
        outstanding_count=0,
    )
    assert result["verdict"] == bc.VERDICT_NO_ADAPTATION_NEEDED
    assert result["position"] == bc.POSITION_PARITY


def test_every_verdict_constant_is_reachable_from_the_decision_function():
    """No constant in the vocabulary is decorative."""
    emitted = {
        bc.decide_verdict(
            code_state=code,
            data_state=bc.DATA_POPULATED,
            declared_position=position,
            finding_retired=retired,
            outstanding_count=count,
        )["verdict"]
        for code, position, retired, count in (
            (bc.CODE_THIN, bc.POSITION_PARITY, None, 0),
            (bc.CODE_BUILT, bc.POSITION_AHEAD, None, 1),
            (bc.CODE_BUILT, bc.POSITION_PARITY, None, 1),
            (bc.CODE_BUILT, bc.POSITION_AHEAD, None, 0),
        )
    }
    assert emitted == set(bc.VERDICTS)


# ── both directions ──────────────────────────────────────────────────────


def test_a_declared_lead_is_overruled_when_the_code_falls_below_the_floor():
    result = bc.decide_verdict(
        code_state=bc.CODE_THIN,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_AHEAD,
        outstanding_count=0,
    )
    assert result["verdict"] == bc.VERDICT_GAP
    assert result["position"] == bc.POSITION_BEHIND
    assert any("no longer supports the declared" in line for line in result["basis"])


def test_built_but_unfed_is_a_gap_even_when_parity_was_declared():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_UNFED,
        declared_position=bc.POSITION_PARITY,
        outstanding_count=0,
    )
    assert result["verdict"] == bc.VERDICT_GAP
    assert "nothing is running through it" in result["rationale"]


def test_a_retired_finding_stops_being_reported_as_a_gap():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_BEHIND,
        finding_retired=True,
        outstanding_count=1,
    )
    assert result["verdict"] == bc.VERDICT_PARITY
    assert result["position"] == bc.POSITION_PARITY


def test_a_retired_finding_with_nothing_queued_reaches_no_adaptation_needed():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_BEHIND,
        finding_retired=True,
        outstanding_count=0,
    )
    assert result["verdict"] == bc.VERDICT_NO_ADAPTATION_NEEDED


# ── refusals: what the engine declines to judge ──────────────────────────


def test_an_unowned_subsystem_gets_no_verdict():
    result = bc.decide_verdict(
        code_state=bc.CODE_ABSENT,
        data_state=bc.DATA_NOT_EXPECTED,
        declared_position=None,
        owned=False,
    )
    assert result["verdict"] is None
    assert result["comparable"] is False
    assert "intentional" in result["rationale"]


def test_an_unbenchmarked_subsystem_gets_no_verdict():
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=None,
        assessed=False,
        outstanding_count=0,
    )
    assert result["verdict"] is None
    assert result["comparable"] is False


def test_a_declared_lead_without_a_declared_outstanding_list_gets_no_verdict():
    """Rule 4: "nothing outstanding" is asserted, never inferred from silence.

    The position still stands — it was declared — but no_adaptation_needed is
    withheld, because nobody wrote down that there is nothing to take.
    """
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=bc.POSITION_AHEAD,
        assessed=False,
        outstanding_count=0,
    )
    assert result["verdict"] is None
    assert result["position"] == bc.POSITION_AHEAD
    assert result["comparable"] is False


def test_an_unbenchmarked_subsystem_with_a_queued_adaptation_is_judged():
    """A pending candidate IS an external reading, even without a benchmark pass."""
    result = bc.decide_verdict(
        code_state=bc.CODE_BUILT,
        data_state=bc.DATA_POPULATED,
        declared_position=None,
        assessed=False,
        outstanding_count=1,
    )
    assert result["verdict"] == bc.VERDICT_PARITY
    assert result["position"] == bc.POSITION_UNKNOWN


def test_an_unreachable_database_is_not_assessed_never_unfed():
    assert bc.classify_data(0, db_measured=False) == bc.DATA_NOT_ASSESSED
    record = bc.compare_subsystem(
        "data_lineage",
        inventory=yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))["subsystems"],
        benchmark={},
        targets=[],
        evidence=evidence("data_lineage", module_count=18, row_count=0, db_measured=False,
                          db_error="could not connect"),
    )
    assert record["data_state"] == bc.DATA_NOT_ASSESSED
    assert any("not reachable" in limit for limit in record["measurement_limits"])


def test_a_finding_with_no_closing_condition_says_so():
    record = bc.compare_subsystem(
        "security_ops",
        inventory=yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))["subsystems"],
        benchmark={"security_ops": {"verdict": "gap"}},
        targets=[],
        evidence=evidence("security_ops", module_count=153, row_count=13),
    )
    assert record["verdict"] == bc.VERDICT_GAP
    assert record["retirement"]["declared"] is False
    assert any("no measurable closing condition" in limit for limit in record["measurement_limits"])


# ── closing conditions ───────────────────────────────────────────────────


def test_closes_when_is_unmet_while_the_cited_table_is_empty():
    report = bc.evaluate_closes_when(
        {"tables": {"developer_scorecards": 1}},
        evidence(tables={"developer_scorecards": 0}),
    )
    assert report["satisfied"] is False


def test_closes_when_is_unmet_when_the_cited_table_was_never_counted():
    """A finding is not retired by a table nobody measured."""
    report = bc.evaluate_closes_when({"tables": {"kg_edges": 1}}, evidence(tables={}))
    assert report["satisfied"] is False
    assert "was not counted" in report["conditions"][0]


def test_closes_when_is_undecidable_without_a_database():
    report = bc.evaluate_closes_when(
        {"tables": {"kg_edges": 1}}, evidence(db_measured=False)
    )
    assert report["satisfied"] is None


# ── watchlist adaptations ────────────────────────────────────────────────


def test_a_closed_adaptation_stops_counting_as_outstanding():
    targets = [
        {"repo": "a/open", "subsystem": "observability",
         "adaptation": {"title": "still open", "status": "pending"}},
        {"repo": "a/taken", "subsystem": "observability",
         "adaptation": {"title": "already taken", "status": "absorbed"}},
    ]
    assert [a["item"] for a in bc.open_adaptations("observability", targets)] == ["still open"]


def test_registering_a_candidate_moves_the_verdict_without_a_config_edit(shipped_inventory):
    """The watchlist is a live input, not a snapshot compiled into the inventory."""
    common = dict(
        inventory=shipped_inventory,
        benchmark={"compliance_ato": {"verdict": "ahead"}},
        evidence=evidence("compliance_ato", module_count=94, row_count=3974),
    )
    clean = bc.compare_subsystem("compliance_ato", targets=[], **common)
    queued = bc.compare_subsystem(
        "compliance_ato",
        targets=[{"repo": "new/thing", "subsystem": "compliance_ato",
                  "adaptation": {"title": "something to take", "status": "pending"}}],
        **common,
    )
    assert clean["verdict"] == bc.VERDICT_NO_ADAPTATION_NEEDED
    assert queued["verdict"] == bc.VERDICT_AHEAD


# ── the known cases from external-benchmark-map.md ───────────────────────


# Section, tag, and the direction the map recorded. Measurements are the ones
# the map itself quotes, so these assert the ENGINE's logic against a published
# reading rather than against whatever the tree happens to hold today.
MAP_CASES = [
    (1, "developer_portal", 48, 194, {"developer_scorecards": 0}, bc.VERDICT_GAP,
     bc.POSITION_BEHIND),
    (2, "observability", 31, 954, {}, bc.VERDICT_NO_ADAPTATION_NEEDED, bc.POSITION_AHEAD),
    (3, "agent_runtime", 117, 389, {}, bc.VERDICT_PARITY, bc.POSITION_PARITY),
    (4, "security_ops", 153, 13, {}, bc.VERDICT_GAP, bc.POSITION_BEHIND),
    (5, "rag_knowledge_graph", 61, 24295, {"kg_edges": 0}, bc.VERDICT_PARITY,
     bc.POSITION_PARITY),
    (6, "compliance_ato", 94, 3974, {}, bc.VERDICT_NO_ADAPTATION_NEEDED, bc.POSITION_AHEAD),
    (7, "delivery_pipeline", 105, 1231, {}, bc.VERDICT_NO_ADAPTATION_NEEDED,
     bc.POSITION_AHEAD),
    (8, "data_lineage", 18, 12, {"dm_domains": 4, "dm_data_products": 8}, bc.VERDICT_GAP,
     bc.POSITION_BEHIND),
    (9, "evaluation", 11, 1041, {}, bc.VERDICT_GAP, bc.POSITION_BEHIND),
    (10, "iac", 45, 57, {}, bc.VERDICT_NO_ADAPTATION_NEEDED, bc.POSITION_PARITY),
]


@pytest.mark.parametrize(
    "section,tag,modules,rows,tables,expected_verdict,expected_position",
    MAP_CASES,
    ids=[f"section{c[0]}_{c[1]}" for c in MAP_CASES],
)
def test_map_sections_reproduce_their_recorded_reading(
    section, tag, modules, rows, tables, expected_verdict, expected_position,
    shipped_inventory, shipped_benchmark,
):
    """Each §1–§10 verdict, from the map's own numbers and the shipped configs.

    The watchlist is held empty here so a candidate registered after the map was
    written cannot silently rewrite what the map concluded; the live-tree test
    below covers the merged case.
    """
    record = bc.compare_subsystem(
        tag,
        inventory=shipped_inventory,
        benchmark=shipped_benchmark,
        targets=[],
        evidence=evidence(tag, module_count=modules, row_count=rows, tables=tables),
    )
    assert record["verdict"] == expected_verdict, record["basis"]
    assert record["position"] == expected_position
    assert record["benchmark_section"] == section


def test_the_three_ahead_subsystems_are_exactly_the_ones_the_map_named(
    shipped_inventory, shipped_benchmark
):
    """§1 of the map's recommendations: compliance/ATO, delivery, OTel telemetry."""
    ahead = {
        tag
        for section, tag, modules, rows, tables, _v, position in MAP_CASES
        if position == bc.POSITION_AHEAD
    }
    assert ahead == {"compliance_ato", "delivery_pipeline", "observability"}


def test_section_5_named_gap_retires_once_the_edges_exist(
    shipped_inventory, shipped_benchmark
):
    """kg_edges was 0 the whole time §5 was written; it is not any more."""
    record = bc.compare_subsystem(
        "rag_knowledge_graph",
        inventory=shipped_inventory,
        benchmark=shipped_benchmark,
        targets=[],
        evidence=evidence("rag_knowledge_graph", module_count=61, row_count=24295,
                          tables={"kg_edges": 4314}),
    )
    assert record["finding_retired"] is True
    # Still parity, because §5's adaptation (GraphRAG community detection) was
    # sequenced BEHIND the edges existing — retiring the finding unblocks it
    # rather than completing it.
    assert record["verdict"] == bc.VERDICT_PARITY


def test_section_2_reports_no_adaptation_needed_verbatim_from_the_map(
    shipped_inventory
):
    """§2's verdict sentence is "No adaptation needed." — the engine must say it."""
    assert shipped_inventory["observability"]["benchmark"]["outstanding"] == []


# ── the shipped configuration ────────────────────────────────────────────


def test_every_declared_verdict_folds_onto_a_direction(shipped_benchmark):
    """A verdict string the engine cannot fold would silently read as unbenchmarked."""
    unmapped = [
        tag
        for tag, spec in shipped_benchmark.items()
        if bc.normalize_position(spec.get("verdict")) is None
    ]
    assert unmapped == []


def test_outstanding_is_declared_for_every_benchmarked_subsystem(
    shipped_inventory, shipped_benchmark
):
    """A declared verdict without a declared outstanding list cannot reach a verdict
    of no_adaptation_needed honestly, so the pair must be kept in step."""
    missing = [
        tag
        for tag in shipped_benchmark
        if not bc.benchmark_spec(tag, shipped_inventory)["assessed"]
    ]
    assert missing == []


def test_embedded_is_the_only_unowned_subsystem(shipped_inventory):
    unowned = [
        tag for tag in shipped_inventory if not bc.benchmark_spec(tag, shipped_inventory)["owned"]
    ]
    assert unowned == ["embedded"]


def test_a_closes_when_only_names_tables_the_evidence_can_count(shipped_inventory):
    """A condition citing an unmatched table can never be satisfied — it would
    read as a permanently open finding caused by a config typo."""
    import fnmatch

    for tag, spec in shipped_inventory.items():
        closes_when = ((spec or {}).get("benchmark") or {}).get("closes_when") or {}
        patterns = (spec or {}).get("tables") or []
        for table in closes_when.get("tables") or {}:
            assert any(fnmatch.fnmatch(table, pattern) for pattern in patterns), (
                f"{tag}: closes_when cites {table}, which no table pattern matches"
            )


def test_an_unknown_subsystem_raises_the_same_way_d1_and_d2_do(shipped_inventory):
    with pytest.raises(KeyError):
        bc.benchmark_spec("not_a_subsystem", shipped_inventory)


# ── the shipped tree, end to end ─────────────────────────────────────────


@pytest.fixture(scope="module")
def shipped_sweep(shipped_inventory):
    """compare_all over the real configs and the real module tree, no database.

    Deliberately DB-free. Every test process here forces SQLite, and the SQLite
    file in a fresh worktree is empty — measuring it would make these assertions
    depend on whichever database the runner happened to have lying around. What
    is left is still the whole engine: the shipped inventory, the shipped
    promoter verdicts, the live watchlist and the actual files on disk.

    It doubles as the regression test for rule 3 in the module docstring — with
    every subsystem's data unassessed, not one of them may turn into a gap for
    that reason.
    """
    from tools.innovation.icdev_evidence import gather_subsystem_evidence

    return bc.compare_all(
        inventory=shipped_inventory,
        evidence={
            "subsystems": {
                tag: gather_subsystem_evidence(
                    tag, inventory=shipped_inventory, db_error="not opened for this test"
                )
                for tag in shipped_inventory
            }
        },
    )


@pytest.mark.parametrize("verdict", bc.VERDICTS)
def test_all_four_verdicts_are_emitted_against_the_shipped_tree(verdict, shipped_sweep):
    """The acceptance criterion, derived rather than fixtured.

    This is the test that fails if the engine ever degenerates into a gap
    scanner: it re-derives every subsystem from the shipped configs and the real
    module counts and requires all four verdicts to appear.
    """
    assert verdict in shipped_sweep["verdict_counts"], (
        f"{verdict} was not emitted for any subsystem: {shipped_sweep['verdict_counts']}"
    )


def test_the_shipped_tree_reports_icdev_ahead_where_the_map_said_so(shipped_sweep):
    assert {"compliance_ato", "delivery_pipeline", "observability"} <= set(shipped_sweep["ahead"])


def test_no_adaptation_needed_survives_a_whole_sweep(shipped_sweep):
    assert shipped_sweep["no_adaptation_needed"], (
        "not one subsystem cleared the field — a deficit-only report is the failure "
        "mode this task exists to prevent"
    )


def test_an_unassessed_database_produces_no_gaps_by_itself(shipped_sweep):
    """Rule 3: blindness may not manufacture a finding."""
    for tag, record in shipped_sweep["subsystems"].items():
        if record["verdict"] != bc.VERDICT_GAP:
            continue
        assert record["data_state"] != bc.DATA_UNFED, tag
        assert not any("data_state=unfed" in line for line in record["basis"]), tag


def test_not_comparable_subsystems_are_counted_apart_from_the_verdicts(shipped_sweep):
    assert "embedded" in shipped_sweep["not_comparable"]
    assert sum(shipped_sweep["verdict_counts"].values()) + len(
        shipped_sweep["not_comparable"]
    ) == shipped_sweep["total_subsystems"]


# ── doctests ─────────────────────────────────────────────────────────────


def test_module_doctests_pass():
    failures, _ = doctest.testmod(bc, verbose=False)
    assert failures == 0
