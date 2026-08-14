# CUI // SP-CTI
"""The FAR 52.219-9(d) ISR/SSR check only fires where the obligation exists.

``detect_noncompliance`` runs four checks. Three are scoped to the rows they can
be true of — flow-down and cybersecurity walk the contract's ACTIVE
subcontractors, CMMC adds a subcontract_value floor. The fourth had no gate of
any kind: it read the ABSENCE of a cpmp_small_business_plan row as a HIGH
FAR 52.219-9(d) violation, on every contract, unconditionally. That asymmetry
with its three siblings is the tell.

An applicability check with no applicability gate does not report a 100%
violation rate. It reports nothing, 100% of the time.

Live evidence (2026-08-13, PostgreSQL board): cpmp_small_business_plan held
0 rows platform-wide, so ``if not latest_report`` was true for all 7 active
contracts and the board carried 7 identical HIGH cards —

    [SUBCON] probe: ISR/SSR                 in_progress
    [SUBCON] bypass probe: ISR/SSR          done
    [SUBCON] GCPL Seed Contract: ISR/SSR    pr_opened
    [SUBCON] contract df32ba49: ISR/SSR     scheduled
    [SUBCON] contract 719d5e59: ISR/SSR     done
    [SUBCON] contract 8143e17a: ISR/SSR     backlog
    [SUBCON] contract 6d67ff20: ISR/SSR     backlog

— two of them (719d5e59, 6d67ff20) for contracts with ZERO subcontractors, and
every one for a contract whose total_value is 0. Each dispatched an autonomous
session to "File the outstanding ISR/SSR in eSRS", which is not an action this
platform can take.

The gate has THREE states, not two. 'undetermined' is the one that matters:
create_contract() defaults total_value to 0.0 exactly as it defaults
contract_number to '', so a 0 means "nobody filled this in" far more often than
"this contract is worth nothing". Folding 0 into below-threshold would silence a
genuine obligation on an unpopulated contract — the same defect with the sign
flipped and no longer visible. It is reported instead, at a severity below the
band cpmp_monitor files cards for.
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

# One contract per applicability state. All three are fully compliant on checks
# 1-3 (no subcontractors at all), so ANY finding they produce came from check 4 —
# which is what makes the counts below unambiguous.
ABOVE = "c0000000-0000-0000-0000-00000000aaaa"   # $5,000,000 -> required
BELOW = "c0000000-0000-0000-0000-00000000bbbb"   # $100,000   -> not_required
UNSET = "c0000000-0000-0000-0000-00000000cccc"   # $0.0       -> undetermined

_CONTRACTS = [
    (ABOVE, "W912-24-C-0001", "Above Threshold Co", 5_000_000.0),
    (BELOW, "W912-24-C-0002", "Below Threshold Co", 100_000.0),
    # The live-board shape: created through create_contract() and never given a
    # value, so total_value sits at its 0.0 default.
    (UNSET, "", "probe", 0.0),
]


def _storage_conn(raw):
    """Wrap *raw* the way the runtime wraps its connections.

    ``translating`` keeps the runtime's %s -> ? rewrite in front of sqlite3, and
    ``unclosable`` survives the close() both modules call on every connection
    they open — an in-memory database dies with its connection.
    """
    conn = translating(raw, unclosable=True)
    conn.set_security_context = lambda _ctx: None
    return conn


@pytest.fixture
def board(monkeypatch):
    """One in-memory board shared by the reflex AND the real tracker.

    Both are pointed at it: the reflex resolves ``get_connection`` lazily per
    call, while subcontractor_tracker bound it at import time, so patching only
    the storage module would leave the tracker on the live database.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)

    for cid, number, title, value in _CONTRACTS:
        raw.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, total_value, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (cid, number, title, value),
        )
    raw.commit()

    conn = _storage_conn(raw)

    from tools.db import storage as _storage
    from tools.govcon import subcontractor_tracker as _tracker

    monkeypatch.setattr(_storage, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(_tracker, "get_connection", lambda *a, **kw: conn)

    yield raw
    raw.close()


def _isr_findings(contract_id):
    from tools.govcon.subcontractor_tracker import detect_noncompliance

    return [
        f
        for f in detect_noncompliance(contract_id)["findings"]
        if str(f["category"]).startswith("isr_ssr")
    ]


class TestApplicabilityGate:
    """The gate itself — one contract per state."""

    def test_above_threshold_with_no_report_is_still_a_violation(self, board):
        """The check must keep working where the obligation is real.

        A gate that suppressed the true positives along with the false ones
        would be a regression dressed as a fix.
        """
        findings = _isr_findings(ABOVE)

        assert len(findings) == 1, findings
        assert findings[0]["category"] == "isr_ssr"
        assert findings[0]["severity"] == "high"

    def test_below_threshold_produces_no_finding(self, board):
        """No subcontracting plan is owed, so no report can be outstanding."""
        assert _isr_findings(BELOW) == []

    def test_unpopulated_value_does_not_produce_a_violation(self, board):
        """The live-board repro. Was a HIGH 'No ISR/SSR report has been filed'."""
        violations = [f for f in _isr_findings(UNSET) if f["category"] == "isr_ssr"]

        assert violations == [], (
            "asserted a FAR 52.219-9(d) violation against a contract whose "
            "applicability was never established"
        )

    def test_unpopulated_value_is_reported_not_swallowed(self, board):
        """'undetermined' is a state, not silence.

        Reading 0.0 as below-threshold would suppress a genuine obligation on an
        unpopulated contract — the same defect with the sign flipped, and no
        longer visible to anyone.
        """
        findings = _isr_findings(UNSET)

        assert len(findings) == 1, findings
        assert findings[0]["category"] == "isr_ssr_applicability"
        assert "total_value" in findings[0]["description"]

    def test_applicability_state_is_in_the_return(self, board):
        """A suppressed finding is indistinguishable from a clean contract
        unless the gate says which one it was."""
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        assert detect_noncompliance(ABOVE)["isr_ssr_applicability"]["state"] == "required"
        assert detect_noncompliance(BELOW)["isr_ssr_applicability"]["state"] == "not_required"
        assert detect_noncompliance(UNSET)["isr_ssr_applicability"]["state"] == "undetermined"

    def test_unknown_contract_is_undetermined_not_a_violation(self, board):
        """Fail-open on identity, fail-closed on the accusation."""
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        result = detect_noncompliance("no-such-contract")

        assert result["isr_ssr_applicability"]["state"] == "undetermined"
        assert [f for f in result["findings"] if f["category"] == "isr_ssr"] == []

    def test_a_filed_report_clears_an_applicable_contract(self, board):
        """The currency check still runs once applicability is established."""
        board.execute(
            "INSERT INTO cpmp_small_business_plan "
            "(id, contract_id, reporting_period, report_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sbp-1", ABOVE, "2026-Q2", "isr", "2026-08-01T00:00:00+00:00"),
        )
        board.commit()

        assert _isr_findings(ABOVE) == []

    def test_threshold_is_the_far_19_702_figure(self):
        """Pinned so it cannot be quietly moved to make a board go green."""
        from tools.govcon.subcontractor_tracker import _SB_PLAN_THRESHOLD

        assert _SB_PLAN_THRESHOLD == 750000.0


class TestNoCardForAnUnestablishedObligation:
    """End to end: what actually reached the board."""

    @pytest.fixture
    def only_pass_3(self, monkeypatch):
        from tools.govcon import cdrl_generator, cpars_predictor, pmo_ai_advisor

        monkeypatch.setattr(pmo_ai_advisor, "auto_detect_issues", lambda cid: {"issues": []})
        monkeypatch.setattr(cpars_predictor, "predict_cpars", lambda cid: {"predicted_score": 1.0})
        monkeypatch.setattr(cpars_predictor, "get_cpars_trend", lambda cid: {"trend": []})
        monkeypatch.setattr(
            cdrl_generator, "generate_all_due", lambda cid, days_ahead=14: {"generated": 0}
        )

    @pytest.fixture
    def reflex(self, monkeypatch):
        import tools.genesis.reflexes.cpmp_monitor as rx

        monkeypatch.setattr(rx, "_write_memory_log", lambda results: None)
        return rx

    def _titles(self, raw):
        return [dict(r)["title"] for r in raw.execute("SELECT title FROM kanban_tasks").fetchall()]

    def test_only_the_applicable_contract_gets_a_card(self, board, only_pass_3, reflex):
        """Three active contracts, one real obligation, one card.

        Was three — one per contract, all HIGH, all identical but for the label.
        """
        result = reflex.run()

        # Labelled by contract_number — _contract_label prefers it over title.
        titles = self._titles(board)
        assert titles == ["[SUBCON] W912-24-C-0001: ISR/SSR"], titles
        assert result["subcon_alerts"] == 1

    def test_the_probe_contract_files_no_card(self, board, only_pass_3, reflex):
        """cpmp-2f71eadacd, by name. A $0 contract titled 'probe' was dispatched
        to a session with the instruction to file in eSRS."""
        reflex.run()

        assert not [t for t in self._titles(board) if "probe" in t]

    def test_the_undetermined_finding_stays_below_the_card_band(self, board, only_pass_3, reflex):
        """cpmp_monitor files cards for high/critical only.

        The applicability gap is reported to the CPMP dashboard without paging a
        session — which is the whole reason it is MEDIUM rather than HIGH.
        """
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        finding = detect_noncompliance(UNSET)["findings"][0]
        assert finding["severity"] not in ("high", "critical")

        reflex.run()
        assert not [t for t in self._titles(board) if "probe" in t]
