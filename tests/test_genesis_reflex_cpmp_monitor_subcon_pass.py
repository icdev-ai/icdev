# CUI // SP-CTI
"""Pass 3 of tools/genesis/reflexes/cpmp_monitor.py actually reaches the board.

``subcontractor_tracker.detect_noncompliance()`` returns its findings under the
key ``findings``. The reflex read ``nc.get("noncompliance", [])`` — a key that
function has never returned — so the list was empty on every cycle, for every
contract, since the pass was written. ``subcon_alerts`` reported 0 and the
reflex reported ``status: ok``: the pass was declared, registered and inert.

Nothing caught it because every existing test stubbed the dependency with
``{"noncompliance": []}``, i.e. the fake encoded the caller's misreading rather
than the real function's return shape, and then asserted the no-op it had
created itself.

So these tests do NOT stub ``detect_noncompliance``. They run the real function
against a real (in-memory) board, which is the only arrangement that can pin
the contract between the two modules: renaming the key on either side fails
here. The per-finding keys are pinned the same way — the reflex read
``issue_type``/``subcontractor_name``, and the findings carry
``category``/``company_name``, so even with the container key fixed every card
would have been titled "Noncompliance" and named no subcontractor.

Live evidence (2026-08-13): the board carried
"[CPMP] Untitled Contract: Subcontractor Compliance" — "1 subcontractor(s) have
incomplete flow-down or cybersecurity gaps", raised by Pass 1, which counts
noncompliant subs with its own SQL. The Pass 3 card that would have named WHICH
subcontractor and WHICH gap was never created.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests._sql_compat import translating

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
    total_value     REAL,
    status          TEXT
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

CONTRACT_ID = "c0ffee00-1111-2222-3333-444455556666"

# Two active subs, each noncompliant in a DIFFERENT way, plus (by omission of
# any cpmp_small_business_plan row) a contract-level ISR/SSR finding. All three
# are high/critical, which is the severity band the reflex files cards for.
_SUBS = [
    # flow_down_complete = 0 -> 'flowdown'. Value > $100k makes it high, not medium.
    ("sub-acme", "Acme Defense LLC", 250_000.0, 0, 1, 2),
    # cybersecurity_compliant = 0 above threshold -> 'cybersecurity', critical.
    ("sub-bolt", "Bolt Systems Inc", 500_000.0, 1, 0, 3),
]


def _storage_conn(raw):
    """Wrap *raw* the way the runtime wraps its connections.

    ``translating`` keeps the runtime's %s -> ? rewrite in front of sqlite3; a
    bare sqlite3 connection here would make every query the reflex and the
    tracker run raise ``near "%": syntax error`` inside the caller's except, and
    the test would assert against a no-op it caused itself — which is the exact
    failure mode this file exists to catch one layer up.

    ``unclosable`` because both modules close() every connection they open, and
    an in-memory database dies with its connection.
    """
    conn = translating(raw, unclosable=True)
    # Both modules call set_security_context() on the connections they open;
    # TranslatingConnection would delegate that to sqlite3 and raise AttributeError.
    conn.set_security_context = lambda _ctx: None
    return conn


@pytest.fixture
def board(monkeypatch):
    """One in-memory board shared by the reflex AND the real tracker.

    Both modules must be pointed at it: the reflex resolves
    ``tools.db.storage.get_connection`` lazily per call, while
    subcontractor_tracker bound it at import time, so patching only the storage
    module would leave the tracker on the live database.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)

    conn = _storage_conn(raw)

    # total_value is ABOVE the FAR 19.702(a)(1) subcontracting-plan threshold, so
    # this contract genuinely owes an ISR/SSR and the missing-report finding below
    # is a real one. Before _isr_ssr_applicability the value was irrelevant — the
    # check fired on every contract regardless — which is exactly why this fixture
    # never had to declare one.
    raw.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, total_value, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (CONTRACT_ID, "W912-24-C-0001", "Untitled Contract", 5_000_000.0, "active"),
    )
    for sub_id, name, value, flowdown, cyber, cmmc in _SUBS:
        raw.execute(
            "INSERT INTO cpmp_subcontractors (id, contract_id, company_name, cage_code, uei, "
            "business_size, subcontract_value, flow_down_complete, cybersecurity_compliant, "
            "cmmc_level, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
            (sub_id, CONTRACT_ID, name, "1ABC2", "UEI123456789", "small", value, flowdown, cyber, cmmc),
        )
    raw.commit()

    from tools.db import storage as _storage
    from tools.govcon import subcontractor_tracker as _tracker

    monkeypatch.setattr(_storage, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(_tracker, "get_connection", lambda *a, **kw: conn)

    yield raw
    raw.close()


@pytest.fixture
def only_pass_3(monkeypatch):
    """Silence passes 1, 2 and 4 so a card on the board came from pass 3."""
    from tools.govcon import cdrl_generator, cpars_predictor, pmo_ai_advisor

    monkeypatch.setattr(pmo_ai_advisor, "auto_detect_issues", lambda cid: {"issues": []})
    monkeypatch.setattr(cpars_predictor, "predict_cpars", lambda cid: {"predicted_score": 1.0})
    monkeypatch.setattr(cpars_predictor, "get_cpars_trend", lambda cid: {"trend": []})
    monkeypatch.setattr(cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0})


@pytest.fixture
def reflex():
    import tools.genesis.reflexes.cpmp_monitor as rx

    return rx


def _cards(raw):
    return [dict(r) for r in raw.execute("SELECT * FROM kanban_tasks ORDER BY title").fetchall()]


class TestTrackerReturnShape:
    """Pin the shape the reflex has to read. These are the contract itself."""

    def test_findings_are_returned_under_findings(self, board):
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        result = detect_noncompliance(CONTRACT_ID)
        assert result["findings"], "fixture should produce noncompliance findings"

    def test_there_is_no_noncompliance_key(self, board):
        """The key the reflex used to read. Its absence is why pass 3 was inert."""
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        assert "noncompliance" not in detect_noncompliance(CONTRACT_ID)

    def test_findings_carry_category_and_company_name(self, board):
        """NOT issue_type/subcontractor_name, which the reflex used to read."""
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        for finding in detect_noncompliance(CONTRACT_ID)["findings"]:
            assert "category" in finding
            assert "company_name" in finding
            assert "issue_type" not in finding
            assert "subcontractor_name" not in finding


class TestPassThreeReachesTheBoard:
    def test_a_noncompliant_subcontractor_produces_a_card(self, board, only_pass_3, reflex):
        """The whole point of the pass. Was 0 cards on every cycle."""
        results = reflex.run()

        assert results["status"] == "ok"
        assert results["subcon_alerts"] > 0, (
            "pass 3 filed no card for two noncompliant subcontractors — "
            f"errors: {results['errors']}"
        )

    def test_each_finding_gets_its_own_card(self, board, only_pass_3, reflex):
        """Two subs noncompliant in different ways + a missing ISR/SSR report.

        A dedup key that omitted the subcontractor would collapse these onto one
        card, which is the failure mode this reflex has already shipped twice.
        """
        reflex.run()

        cards = _cards(board)
        assert len(cards) == 3, [c["title"] for c in cards]
        assert len({c["id"] for c in cards}) == 3
        assert len({c["title"] for c in cards}) == 3

    def test_card_names_the_subcontractor_and_the_gap(self, board, only_pass_3, reflex):
        """A card that cannot say WHICH sub or WHICH gap is not actionable."""
        reflex.run()

        cards = _cards(board)
        flowdown = [c for c in cards if "Acme Defense LLC" in c["description"]]
        assert len(flowdown) == 1, [c["title"] for c in cards]

        card = flowdown[0]
        assert "Flow-Down" in card["title"], card["title"]
        assert "Noncompliance" not in card["title"], (
            "generic fallback title — the finding's category was not read"
        )
        assert "N/A" not in card["description"], (
            "subcontractor name fell back to its default — the finding's "
            "company_name was not read"
        )

    def test_cybersecurity_finding_is_titled_for_its_category(self, board, only_pass_3, reflex):
        reflex.run()

        cards = _cards(board)
        cyber = [c for c in cards if "Bolt Systems Inc" in c["description"]]
        assert len(cyber) == 1, [c["title"] for c in cards]
        assert "Cybersecurity" in cyber[0]["title"], cyber[0]["title"]

    def test_rerun_does_not_duplicate(self, board, only_pass_3, reflex):
        """Dedup is keyed on the card id, so a second cycle is a no-op."""
        reflex.run()
        before = _cards(board)
        second = reflex.run()

        assert _cards(board) == before
        assert second["subcon_alerts"] == 0, "re-filed a card already on the board"

    def test_alert_count_matches_cards_written(self, board, only_pass_3, reflex):
        """subcon_alerts counts writes, not attempts — it is what gets logged."""
        results = reflex.run()
        assert results["subcon_alerts"] == len(_cards(board))
