# CUI // SP-CTI
"""The ISR/SSR check in ``detect_noncompliance`` only fires where FAR 52.219-9 applies.

Check 4 of ``subcontractor_tracker.detect_noncompliance`` had no applicability
gate, while all three checks above it do — flow-down and cybersecurity filter to
ACTIVE subcontractors, cybersecurity and CMMC additionally to
``subcontract_value > _CYBER_THRESHOLD``. That asymmetry is the tell.

Because the finding is derived from the ABSENCE of a ``cpmp_small_business_plan``
row, an installation that has never used the ISR/SSR feature raises one for every
contract it holds. Measured on the live board 2026-08-13: zero
``cpmp_small_business_plan`` rows exist for any contract, so the check fired on
8 of 8 active contracts — 7 of them with ``total_value`` 0.0, three orders of
magnitude below the $750K at which the obligation begins (FAR 19.702(a)(1); the
repo states the same rule in ``far_dfars_verifier.FAR-19.7``). Every card it has
ever filed told someone to file a federal report in eSRS that is not owed.

Two consequences these tests pin:

  * a HIGH ``isr_ssr`` finding is raised ONLY where the clause actually attaches;
  * ``compliant`` becomes reachable. It is ``len(findings) == 0``, and with no
    ISR/SSR row anywhere on the board no contract could ever satisfy it.

The unknown case is deliberately NOT treated as noncompliant: ``create_contract``
defaults ``total_value`` to 0.0, so an unpopulated value means "nobody said",
and a HIGH severity derived from a field nobody filled in is a guess wearing a
severity. It is reported through ``isr_ssr_applicability`` instead, so a
suppressed check stays visibly suppressed rather than looking like a check that
ran and found nothing.
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
    status          TEXT,
    total_value     REAL
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

# One contract per applicability case. No subcontractors anywhere in this
# fixture, so any finding that appears is the ISR/SSR one and nothing else --
# that is what lets `compliant` be asserted directly.
ABOVE = "aaaaaaaa-0000-0000-0000-000000000001"   # $2M   -> clause applies
BELOW = "bbbbbbbb-0000-0000-0000-000000000002"   # $500k -> below the threshold
UNSET = "cccccccc-0000-0000-0000-000000000003"   # 0.0   -> never populated
EXACT = "dddddddd-0000-0000-0000-000000000004"   # $750k -> boundary, "> $750K"

_CONTRACTS = [
    (ABOVE, "W912-24-C-0001", "Major Services IDIQ", 2_000_000.0),
    (BELOW, "W912-24-C-0002", "Small Task Order", 500_000.0),
    (UNSET, "", "GCPL Seed Contract", 0.0),
    (EXACT, "W912-24-C-0004", "Threshold Contract", 750_000.0),
]


def _storage_conn(raw):
    """Wrap *raw* the way the runtime wraps its connections.

    ``translating`` keeps the runtime's %s -> ? rewrite in front of sqlite3, and
    ``unclosable`` because the tracker close()s every connection it opens while
    an in-memory database dies with its connection.
    """
    conn = translating(raw, unclosable=True)
    conn.set_security_context = lambda _ctx: None
    return conn


@pytest.fixture
def board(monkeypatch):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)
    raw.executemany(
        "INSERT INTO cpmp_contracts (id, contract_number, title, status, total_value) "
        "VALUES (?, ?, ?, 'active', ?)",
        _CONTRACTS,
    )
    raw.commit()

    conn = _storage_conn(raw)

    from tools.db import storage as _storage
    from tools.govcon import subcontractor_tracker as _tracker

    # subcontractor_tracker bound get_connection at import time, so patching only
    # the storage module would leave it on the live database.
    monkeypatch.setattr(_storage, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(_tracker, "get_connection", lambda *a, **kw: conn)

    yield raw
    raw.close()


def _isr_findings(contract_id):
    from tools.govcon.subcontractor_tracker import detect_noncompliance

    result = detect_noncompliance(contract_id)
    return result, [f for f in result["findings"] if f["category"] == "isr_ssr"]


class TestClauseApplies:
    def test_above_threshold_with_no_report_is_a_high_finding(self, board):
        """The case the check exists for. Must keep working."""
        result, isr = _isr_findings(ABOVE)

        assert len(isr) == 1, result["findings"]
        assert isr[0]["severity"] == "high"
        assert isr[0]["description"] == "No ISR/SSR report has been filed for this contract"
        assert result["isr_ssr_applicability"] == "required"


class TestClauseDoesNotApply:
    """Each of these raised a HIGH 'you failed to file' card before the gate."""

    def test_below_threshold_raises_nothing(self, board):
        result, isr = _isr_findings(BELOW)

        assert isr == [], "flagged a missing ISR/SSR on a contract that owes none"
        assert result["isr_ssr_applicability"] == "not_required_below_threshold"

    def test_unpopulated_contract_value_raises_nothing(self, board):
        """total_value 0.0 is create_contract's default: unknown, not zero-dollar.

        7 of the 8 active contracts on the live board are in exactly this state.
        """
        result, isr = _isr_findings(UNSET)

        assert isr == [], "asserted noncompliance from a field nobody populated"
        assert result["isr_ssr_applicability"] == "unknown_contract_value"

    def test_threshold_is_exclusive(self, board):
        """FAR 19.702(a)(1) is 'exceeds $750,000' — at the number it does not apply."""
        result, isr = _isr_findings(EXACT)

        assert isr == []
        assert result["isr_ssr_applicability"] == "not_required_below_threshold"

    def test_compliant_is_reachable(self, board):
        """`compliant` is len(findings) == 0, and the ungated check made it unreachable.

        No contract on the board has an ISR/SSR row, so every one of them was
        permanently non-compliant regardless of its actual posture.
        """
        result, _ = _isr_findings(BELOW)

        assert result["compliant"] is True, result["findings"]


class TestAnExistingReportProvesTheObligation:
    """A filed report is itself evidence the clause attaches — check its age."""

    def _file_report(self, raw, contract_id, created_at):
        raw.execute(
            "INSERT INTO cpmp_small_business_plan "
            "(id, contract_id, reporting_period, report_type, created_at) "
            "VALUES (?, ?, '2024-Q1', 'isr', ?)",
            (f"rep-{contract_id[:8]}", contract_id, created_at),
        )
        raw.commit()

    def test_stale_report_flagged_even_when_value_unpopulated(self, board):
        """Nobody files an ISR in eSRS for a contract with no plan.

        So the value gate must not suppress the currency check on a contract
        that has demonstrably been reporting.
        """
        self._file_report(board, UNSET, "2020-01-01T00:00:00+00:00")

        result, isr = _isr_findings(UNSET)

        assert len(isr) == 1, result["findings"]
        assert isr[0]["severity"] == "medium"
        assert "days old" in isr[0]["description"]
        assert result["isr_ssr_applicability"] == "required"

    def test_current_report_raises_nothing(self, board):
        from datetime import datetime, timezone

        self._file_report(board, ABOVE, datetime.now(timezone.utc).isoformat())

        result, isr = _isr_findings(ABOVE)

        assert isr == []
        assert result["isr_ssr_applicability"] == "required"


class TestTheReflexStopsFilingTheCard:
    """End to end: the false-positive board card is what a human actually saw."""

    @pytest.fixture
    def only_pass_3(self, monkeypatch):
        from tools.govcon import cdrl_generator, cpars_predictor, pmo_ai_advisor

        monkeypatch.setattr(pmo_ai_advisor, "auto_detect_issues", lambda cid: {"issues": []})
        monkeypatch.setattr(cpars_predictor, "predict_cpars", lambda cid: {"predicted_score": 1.0})
        monkeypatch.setattr(cpars_predictor, "get_cpars_trend", lambda cid: {"trend": []})
        monkeypatch.setattr(
            cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0}
        )

    def test_only_the_applicable_contract_gets_a_card(self, board, only_pass_3):
        """Four active contracts, one of which owes a report. Was 4 cards."""
        import tools.genesis.reflexes.cpmp_monitor as rx

        results = rx.run()

        cards = [dict(r) for r in board.execute("SELECT * FROM kanban_tasks").fetchall()]
        isr_cards = [c for c in cards if "ISR/SSR" in c["title"]]

        assert len(isr_cards) == 1, [c["title"] for c in isr_cards]
        # _contract_label prefers contract_number over title.
        assert "W912-24-C-0001" in isr_cards[0]["title"], isr_cards[0]["title"]
        # Scoped to pass 3: this fixture carries no cpmp_deliverables table, so
        # the unrelated pass-0 overdue sweep reports its own (expected) error.
        assert [e for e in results["errors"] if "Subcon" in e] == [], results["errors"]
