# CUI // SP-CTI
"""A code-level reflex finding files ONE card, not one per affected row.

THE MEASURED CASE (2026-08-14)
==============================
`cpmp_monitor` pass 3 filed a `[SUBCON] … ISR/SSR` card per contract: SEVEN
cards for ONE code-level defect. `detect_noncompliance` check #4 had no FAR
19.702(a) applicability gate, so it fired HIGH on all seven active contracts
while its three siblings — which all scope to active subcontractors — correctly
found nothing. All 7 were false positives.

Four sessions then fixed the same bug independently. #1628 landed; #1629, #1633
and #1635 were closed as redundant, and two of those three had created the SAME
test file path, so they conflicted with each other as well as with main. The
cost of the extra six cards was six branches, six PRs and mutually conflicting
work a human had to adjudicate.

WHAT IS PINNED HERE
===================
Both directions, because either one alone is easy and useless:

  * a finding whose remedy is ONE code change produces exactly ONE card, with
    every affected row carried as evidence — `TestCodeLevelAggregates`,
    `TestTheMeasuredCase`;
  * a data finding still produces one card per row — `TestDataLevelStaysPerRow`,
    `TestDataLevelSurvivesTheReflex`.

And the mechanism, because the obvious implementation is the wrong one:

  * card identity is a DETERMINISTIC key, never a title — `TestIdentityIsNotATitle`.
    Title dedup has already shipped in this codebase and it DROPS distinct
    findings that happen to share a title (PR #1504: five contracts with
    noncompliant subcontractors showed one card). Aggregating is not deduping;
    `test_aggregation_never_drops_a_finding` pins that every input finding is
    still accounted for on the output, in both directions.
"""
from __future__ import annotations

import sqlite3
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests._sql_compat import translating
from tools.genesis import finding_scope as fs

SOURCE = "unit_test_source"

# Inference is opt-in per source (args/finding_scope.yaml). These unit tests
# supply their own config so they pin the RULE rather than today's opt-in list.
_INFER = {"defaults": {"infer_code_scope": True, "min_population": 3}}
_NO_INFER = {"defaults": {"infer_code_scope": False}}


def _finding(subject, category="isr_ssr", signature="file the report", **kw):
    return fs.Finding(
        subject=subject,
        category=category,
        dedup_key=kw.pop("dedup_key", f"{subject}:{category}:"),
        signature=signature,
        evidence=kw.pop("evidence", f"contract {subject}"),
        payload=kw.pop("payload", {}),
        declared_scope=kw.pop("declared_scope", None),
    )


def _uniform(n, category="isr_ssr"):
    """n subjects, one identical remedy — the shape a broken check has."""
    return [_finding(f"c-{i}", category=category) for i in range(n)]


def _distinct(n, category="flowdown"):
    """n subjects, each naming its own thing to fix — a real data finding."""
    return [
        _finding(f"c-{i}", category=category, signature=f"chase sub-{i}")
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# The discriminator itself — source-agnostic, no reflex, no board
# ---------------------------------------------------------------------------


class TestCodeLevelAggregates:
    def test_a_check_declaring_code_scope_files_one_card(self):
        """Declaration is the primary mechanism and wins over everything."""
        findings = [
            _finding(f"c-{i}", declared_scope=fs.CODE) for i in range(7)
        ]
        specs = fs.group(SOURCE, findings, population=7, config=_NO_INFER)

        assert len(specs) == 1
        assert specs[0].scope == fs.CODE
        assert len(specs[0].findings) == 7

    def test_declaration_beats_a_population_too_small_to_infer_from(self):
        """A declared code defect is one card even at n=1 — inference is only
        the fallback for a check nobody has classified yet."""
        specs = fs.group(
            SOURCE, [_finding("c-0", declared_scope=fs.CODE)], population=1,
            config=_NO_INFER,
        )
        assert len(specs) == 1 and specs[0].scope == fs.CODE

    def test_config_can_declare_a_category_code_level(self):
        """The operator's lever: one line in args/finding_scope.yaml collapses a
        defective check's next cycle without a code deploy."""
        config = {"sources": {SOURCE: {"categories": {"isr_ssr": "code"}}}}
        specs = fs.group(SOURCE, _uniform(7), population=7, config=config)

        assert len(specs) == 1
        assert "finding_scope.yaml" in specs[0].reason

    def test_saturated_uniform_findings_are_inferred_code_level(self):
        """A check that fired on 100% of what it examined, with an identical
        remedy every time, is describing itself rather than the population."""
        specs = fs.group(SOURCE, _uniform(7), population=7, config=_INFER)

        assert len(specs) == 1
        assert specs[0].scope == fs.CODE
        assert "7/7" in specs[0].reason

    def test_every_affected_row_is_carried_on_the_one_card(self):
        specs = fs.group(SOURCE, _uniform(7), population=7, config=_INFER)

        assert {f.subject for f in specs[0].findings} == {f"c-{i}" for i in range(7)}


class TestDataLevelStaysPerRow:
    def test_default_scope_is_one_card_per_row(self):
        specs = fs.group(SOURCE, _uniform(7), population=7, config=_NO_INFER)

        assert len(specs) == 7
        assert all(s.scope == fs.DATA for s in specs)

    def test_inference_is_off_unless_the_source_opts_in(self):
        """Built-in default, no config at all. Aggregating a source nobody has
        looked at is not a cost to impose by default."""
        specs = fs.group(SOURCE, _uniform(7), population=7, config={})

        assert len(specs) == 7

    def test_a_check_that_skipped_some_rows_stays_per_row(self):
        """Asymmetry is the tell that the check HAS an applicability gate and it
        is working: it examined 7 rows and deliberately passed on 4."""
        specs = fs.group(SOURCE, _uniform(3), population=7, config=_INFER)

        assert len(specs) == 3
        assert "3/7" in specs[0].reason

    def test_distinct_remedies_stay_per_row_even_at_full_saturation(self):
        """Seven contracts, seven different subcontractors to chase. Saturation
        alone must never aggregate — this is the case a naive "it fired on
        everything" rule gets wrong, and it is a genuine data finding."""
        specs = fs.group(SOURCE, _distinct(7), population=7, config=_INFER)

        assert len(specs) == 7
        assert all(s.scope == fs.DATA for s in specs)
        assert "distinct remedies" in specs[0].reason

    def test_a_population_too_small_to_mean_anything_stays_per_row(self):
        """Two of two is a coincidence; seven of seven is a pattern."""
        specs = fs.group(SOURCE, _uniform(2), population=2, config=_INFER)

        assert len(specs) == 2

    def test_a_broken_check_does_not_drag_its_sound_siblings_with_it(self):
        """Categories are scoped independently — exactly the situation on the
        live board, where #4 was broken and #1-#3 were not."""
        findings = _uniform(7, category="isr_ssr") + _distinct(7, category="flowdown")
        specs = fs.group(SOURCE, findings, population=7, config=_INFER)

        by_scope = {}
        for spec in specs:
            by_scope.setdefault(spec.scope, []).append(spec)

        assert len(by_scope[fs.CODE]) == 1
        assert by_scope[fs.CODE][0].category == "isr_ssr"
        assert len(by_scope[fs.DATA]) == 7


class TestIdentityIsNotATitle:
    """Card identity is a deterministic key. Never a title.

    Title dedup fails in the direction that loses work: unrelated findings whose
    titles collide are treated as duplicates and all but the first are discarded
    (PR #1504). Nothing here reads a title.
    """

    def test_the_code_level_key_is_stable_across_calls(self):
        first = fs.group(SOURCE, _uniform(7), population=7, config=_INFER)[0]
        second = fs.group(SOURCE, _uniform(7), population=7, config=_INFER)[0]

        assert first.dedup_key == second.dedup_key

    def test_the_code_level_key_does_not_move_with_the_population(self):
        """Seven contracts today and five tomorrow are the same defect and must
        be the same card — a key containing a subject would file a second one."""
        seven = fs.group(SOURCE, _uniform(7), population=7, config=_INFER)[0]
        five = fs.group(SOURCE, _uniform(5), population=5, config=_INFER)[0]

        assert seven.dedup_key == five.dedup_key
        assert "c-0" not in seven.dedup_key

    def test_data_level_keys_are_the_callers_own_keys(self):
        """So an existing board keeps colliding with its existing cards rather
        than re-filing all of them the first time this seam ships."""
        findings = _distinct(3)
        specs = fs.group(SOURCE, findings, population=7, config=_INFER)

        assert {s.dedup_key for s in specs} == {f.dedup_key for f in findings}

    def test_two_categories_never_share_a_code_level_key(self):
        findings = _uniform(7, category="isr_ssr") + _uniform(7, category="cmmc")
        specs = fs.group(SOURCE, findings, population=7, config=_INFER)

        assert len({s.dedup_key for s in specs}) == 2

    @pytest.mark.parametrize("config", [_INFER, _NO_INFER])
    def test_aggregation_never_drops_a_finding(self, config):
        """The difference between aggregating and deduping, in one assertion.

        Whatever the scope decision, every input finding is still accounted for
        on some output card. A title dedup cannot pass this: it discards.
        """
        findings = _uniform(7, category="isr_ssr") + _distinct(4, category="flowdown")
        specs = fs.group(SOURCE, findings, population=7, config=config)

        carried = [f for spec in specs for f in spec.findings]
        assert len(carried) == len(findings)
        assert {id(f) for f in carried} == {id(f) for f in findings}


class TestEvidence:
    def test_the_aggregated_card_lists_every_affected_row(self):
        spec = fs.group(SOURCE, _uniform(7), population=7, config=_INFER)[0]
        block = fs.evidence_block(spec, population=7, source=SOURCE, config=_INFER)

        for i in range(7):
            assert f"contract c-{i}" in block

    def test_the_evidence_says_why_it_was_aggregated(self):
        spec = fs.group(SOURCE, _uniform(7), population=7, config=_INFER)[0]
        block = fs.evidence_block(spec, population=7, source=SOURCE, config=_INFER)

        assert "7/7" in block
        assert "applicability gate" in block

    def test_truncation_is_reported_not_silent(self):
        """A cap nobody mentions reads as 'that was all of them'."""
        config = {
            "defaults": {
                "infer_code_scope": True, "min_population": 3, "max_evidence_rows": 4,
            }
        }
        spec = fs.group(SOURCE, _uniform(9), population=9, config=config)[0]
        block = fs.evidence_block(spec, population=9, source=SOURCE, config=config)

        assert "and 5 more" in block
        assert "context data" in block

    def test_a_data_level_card_has_no_evidence_block(self):
        """It already names its own row."""
        spec = fs.group(SOURCE, _distinct(3), population=7, config=_INFER)[0]

        assert fs.evidence_block(spec, population=7, source=SOURCE) == ""


class TestConfigFileIsReadAndComplete:
    def test_the_shipped_config_loads(self):
        config = fs.load_config()
        assert config.get("defaults"), "args/finding_scope.yaml did not load"

    def test_the_measured_source_is_opted_in(self):
        """cpmp_monitor pass 3 is the instance this was built for."""
        config = fs.load_config()
        assert fs._setting(config, "cpmp_monitor_subcon", "infer_code_scope") is True

    def test_every_key_in_the_file_is_one_the_module_reads(self):
        """The failure this platform ships most is a declared thing nothing
        consumes — including a config key (see #ctx-enf-03)."""
        config = fs.load_config()
        readable = set(fs._BUILTIN_DEFAULTS)

        assert set(config.get("defaults", {})) <= readable
        for name, block in (config.get("sources") or {}).items():
            assert set(block) <= readable | {"categories"}, name

    def test_the_icdev_mirror_reads_the_same_file(self):
        """`tools/` and `icdev/tools/` are two module objects, not one file.

        A repo-root-relative path here resolves to `icdev/args/` in the mirror —
        which holds 19 of the repo's 309 args files — so the mirrored copy would
        fall back to built-in defaults and quietly file seven cards while the
        root copy filed one. Nothing would error.
        """
        from icdev.tools.genesis import finding_scope as mirrored

        assert mirrored._config_path() == fs._config_path()
        assert mirrored.load_config() == fs.load_config()

    def test_an_unreadable_config_degrades_to_todays_behaviour(self):
        """A reflex must not lose its findings because a config file moved."""
        config = fs.load_config(path="/nonexistent/finding_scope.yaml")
        specs = fs.group(SOURCE, _uniform(7), population=7, config=config)

        assert len(specs) == 7


# ---------------------------------------------------------------------------
# The measured case, replayed end-to-end through the reflex and a real board
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE kanban_tasks (
    id              TEXT PRIMARY KEY,
    task_type       TEXT,
    title           TEXT,
    description     TEXT,
    status          TEXT,
    priority        TEXT,
    tags            TEXT,
    dispatch_source TEXT,
    created_at      TEXT,
    updated_at      TEXT
);
CREATE TABLE cpmp_contracts (
    id              TEXT PRIMARY KEY,
    contract_number TEXT,
    title           TEXT,
    status          TEXT,
    total_value     REAL,
    funded_value    REAL,
    ceiling_value   REAL
);
CREATE TABLE cpmp_subcontractors (
    id                      TEXT PRIMARY KEY,
    contract_id             TEXT,
    company_name            TEXT,
    cage_code               TEXT,
    uei                     TEXT,
    business_size           TEXT,
    subcontract_value       REAL,
    flow_down_complete      INTEGER,
    cybersecurity_compliant INTEGER,
    cmmc_level              INTEGER,
    status                  TEXT
);
CREATE TABLE cpmp_small_business_plan (
    id               TEXT PRIMARY KEY,
    contract_id      TEXT,
    reporting_period TEXT,
    report_type      TEXT,
    created_at       TEXT
);
"""

# The live board on 2026-08-14: seven active contracts.
_N_CONTRACTS = 7


def _cid(family, i):
    """A uuid-shaped contract id, because the card's evidence abbreviates it to
    eight characters — an id whose first eight characters were shared would make
    the evidence assertions pass without discriminating anything."""
    return f"{family}{i}a1b2-1111-2222-3333-4444555566{i}{i}"


def _number(family, i):
    return f"W912-24-C-{family}{i}"


class _Board:
    """Seven active contracts, over the FAR 19.702(a)(1) threshold, each with a
    fully COMPLIANT active subcontractor and no ISR/SSR on file.

    That is the measured shape exactly: checks #1-#3 find nothing on any of
    them, and #4 fires HIGH on all seven with byte-identical text.
    """

    def __init__(self, raw, conn):
        self.raw = raw
        self.conn = conn

    def add_contract(self, cid, number, flow_down=1, value=2_000_000.0):
        self.raw.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status, total_value) "
            "VALUES (?, ?, ?, 'active', ?)",
            (cid, number, "Untitled Contract", value),
        )
        self.raw.execute(
            "INSERT INTO cpmp_subcontractors (id, contract_id, company_name, cage_code, "
            "uei, business_size, subcontract_value, flow_down_complete, "
            "cybersecurity_compliant, cmmc_level, status) "
            "VALUES (?, ?, ?, '1ABC2', 'UEI123456789', 'small', ?, ?, 1, 2, 'active')",
            (f"sub-{cid[:8]}", cid, f"Supplier for {number}", 250_000.0, flow_down),
        )
        self.raw.commit()

    def drop_contract(self, cid):
        self.raw.execute("DELETE FROM cpmp_contracts WHERE id = ?", (cid,))
        self.raw.execute("DELETE FROM cpmp_subcontractors WHERE contract_id = ?", (cid,))
        self.raw.commit()

    def cards(self):
        return [
            dict(r)
            for r in self.raw.execute(
                "SELECT * FROM kanban_tasks ORDER BY id"
            ).fetchall()
        ]

    def subcon_cards(self):
        return [c for c in self.cards() if c["dispatch_source"] == "cpmp_monitor_subcon"]


def _storage_conn(raw):
    """Wrap *raw* the way the runtime wraps its connections.

    `translating` keeps the runtime's %s -> ? rewrite in front of sqlite3; a bare
    sqlite3 connection here would make every query the reflex and the tracker run
    raise `near "%": syntax error` inside the caller's except, and the test would
    assert against a no-op it caused itself. `unclosable` because both modules
    close() every connection they open and an in-memory database dies with its
    connection.
    """
    conn = translating(raw, unclosable=True)
    # Both modules call set_security_context(); TranslatingConnection would
    # delegate that to sqlite3 and raise AttributeError.
    conn.set_security_context = lambda _ctx: None
    return conn


@pytest.fixture
def board(monkeypatch):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)

    conn = _storage_conn(raw)

    from tools.db import storage as _storage
    from tools.govcon import subcontractor_tracker as _tracker

    # Both must be pointed at the same board: the reflex resolves
    # get_connection lazily per call, the tracker bound it at import.
    monkeypatch.setattr(_storage, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(_tracker, "get_connection", lambda *a, **kw: conn)

    yield _Board(raw, conn)
    raw.close()


@pytest.fixture
def run_reflex():
    """Run the reflex with passes 0, 1, 2 and 4 silenced, so a card is pass 3's."""

    def _run():
        from tools.genesis.reflexes.cpmp_monitor import run

        with ExitStack() as stack:
            stack.enter_context(patch(
                "tools.govcon.contract_manager.compute_overdue_deliverables",
                return_value={"overdue_count": 0, "days_refreshed": 0}))
            stack.enter_context(patch(
                "tools.govcon.pmo_ai_advisor.auto_detect_issues",
                return_value={"issues": []}))
            stack.enter_context(patch(
                "tools.govcon.cpars_predictor.predict_cpars",
                return_value={"predicted_score": 1.0}))
            stack.enter_context(patch(
                "tools.govcon.cpars_predictor.get_cpars_trend", return_value={"trend": []}))
            stack.enter_context(patch(
                "tools.govcon.cdrl_generator.generate_all_due", return_value={"generated": 0}))
            stack.enter_context(patch("tools.memory.memory_write.write_to_db", return_value=None))
            return run()

    return _run


class TestTheMeasuredCase:
    """7 contracts failing one ISR/SSR code defect yields 1 card."""

    @pytest.fixture(autouse=True)
    def seven_contracts(self, board):
        for i in range(_N_CONTRACTS):
            board.add_contract(_cid("aa", i), _number("aa", i))

    def test_the_check_really_does_fire_on_all_seven(self, board):
        """Pin the premise. If the fixture stopped reproducing the defect these
        tests would assert 1 card against 1 finding and prove nothing."""
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        by_category = {}
        for i in range(_N_CONTRACTS):
            for f in detect_noncompliance(_cid("aa", i))["findings"]:
                by_category.setdefault(f["category"], []).append(f)

        assert len(by_category["isr_ssr"]) == _N_CONTRACTS
        assert len({f["description"] for f in by_category["isr_ssr"]}) == 1, (
            "remedy text must be identical on every hit"
        )
        # The measured asymmetry: #1-#3 scope to active subcontractors and
        # correctly find nothing here, while #4 fires on every contract.
        assert set(by_category) == {"isr_ssr"}, sorted(by_category)

    def test_seven_contracts_one_defect_is_one_card(self, board, run_reflex):
        """Was SEVEN cards, and four sessions fixing the same bug."""
        results = run_reflex()

        cards = board.subcon_cards()
        assert len(cards) == 1, [c["title"] for c in cards]
        assert results["code_level_cards"] == 1
        assert results["subcon_alerts"] == 1

    def test_the_card_names_every_affected_contract(self, board, run_reflex):
        """Aggregating must not lose what the other six cards would have said."""
        run_reflex()

        description = board.subcon_cards()[0]["description"]
        for i in range(_N_CONTRACTS):
            assert _number("aa", i) in description, description

    def test_the_card_says_it_was_aggregated_and_why(self, board, run_reflex):
        run_reflex()

        card = board.subcon_cards()[0]
        assert "7/7" in card["description"]
        assert "applicability gate" in card["description"]
        assert "identical on all 7 contracts" in card["title"]

    def test_the_full_row_list_is_in_the_cards_context_data(self, board, run_reflex):
        import json

        run_reflex()
        tags = json.loads(board.subcon_cards()[0]["tags"])

        assert tags["scope"] == "code"
        assert len(tags["affected"]) == _N_CONTRACTS

    def test_a_second_cycle_files_nothing(self, board, run_reflex):
        """The id is deterministic, so re-detecting is a primary-key collision."""
        run_reflex()
        before = board.cards()
        second = run_reflex()

        assert board.cards() == before
        assert second["subcon_alerts"] == 0

    def test_a_shrinking_population_does_not_file_a_second_card(self, board, run_reflex):
        """Two contracts close; the defect is the same defect and the same card.
        A key containing a contract id would file a fresh one here."""
        run_reflex()
        board.drop_contract(_cid("aa", 5))
        board.drop_contract(_cid("aa", 6))
        run_reflex()

        assert len(board.subcon_cards()) == 1

    def test_a_scoping_failure_falls_back_to_one_card_per_row(
        self, board, run_reflex, monkeypatch
    ):
        """Fail-soft in the SAFE direction.

        A redundant card costs a session; a swallowed exception costs the whole
        pass and reports the silence as a clean sweep — which is how this reflex
        was inert for every cycle since it was written.
        """
        import tools.genesis.finding_scope as scope

        def _boom(*a, **kw):
            raise RuntimeError("scoping is down")

        monkeypatch.setattr(scope, "group", _boom)

        results = run_reflex()

        assert len(board.subcon_cards()) == _N_CONTRACTS
        assert results["code_level_cards"] == 0
        assert any("scoping is down" in e for e in results["errors"])

    def test_dedup_is_not_the_title(self, board, run_reflex):
        """A foreign card carrying the same title must not suppress the finding,
        and must not be mistaken for one of ours."""
        run_reflex()
        title = board.subcon_cards()[0]["title"]
        board.raw.execute("DELETE FROM kanban_tasks")
        board.raw.execute(
            "INSERT INTO kanban_tasks (id, title, status, dispatch_source) "
            "VALUES ('someone-elses-card', ?, 'backlog', 'human')",
            (title,),
        )
        board.raw.commit()

        run_reflex()

        assert len(board.subcon_cards()) == 1, "title collision suppressed the finding"


class TestDataLevelSurvivesTheReflex:
    """The other direction, on the same reflex and the same board."""

    def test_seven_distinct_subcontractor_gaps_are_seven_cards(self, board, run_reflex):
        """Every contract has flow-down incomplete — 7 of 7, fully saturated —
        but each names a DIFFERENT subcontractor, so there are seven things to
        chase and seven cards. Saturation alone must never aggregate."""
        for i in range(_N_CONTRACTS):
            board.add_contract(_cid("bb", i), _number("bb", i), flow_down=0)

        results = run_reflex()

        flowdown = [c for c in board.subcon_cards() if "Flow-Down" in c["title"]]
        assert len(flowdown) == _N_CONTRACTS, [c["title"] for c in flowdown]
        assert len({c["id"] for c in flowdown}) == _N_CONTRACTS
        assert len({c["title"] for c in flowdown}) == _N_CONTRACTS
        assert results["code_level_cards"] == 1, "only the ISR/SSR category aggregates"

    def test_a_partial_hit_rate_is_one_card_per_row(self, board, run_reflex):
        """Three of seven — the check skipped four, so its gate is working."""
        for i in range(_N_CONTRACTS):
            board.add_contract(
                _cid("cc", i), _number("cc", i), flow_down=0 if i < 3 else 1
            )

        run_reflex()

        flowdown = [c for c in board.subcon_cards() if "Flow-Down" in c["title"]]
        assert len(flowdown) == 3

    def test_a_data_card_still_names_its_own_contract_and_sub(self, board, run_reflex):
        for i in range(3):
            board.add_contract(
                _cid("dd", i), _number("dd", i), flow_down=0 if i == 0 else 1
            )

        run_reflex()

        flowdown = [c for c in board.subcon_cards() if "Flow-Down" in c["title"]]
        assert len(flowdown) == 1
        assert _number("dd", 0) in flowdown[0]["title"], flowdown[0]["title"]
        assert f"Supplier for {_number('dd', 0)}" in flowdown[0]["description"]
