# CUI // SP-CTI
"""The ISR/SSR check asks whether a report exists, never whether one is owed.

``subcontractor_tracker.detect_noncompliance()`` raises a HIGH finding — "No
ISR/SSR report has been filed for this contract" — for any contract with no row
in ``cpmp_small_business_plan``. It never asks whether FAR 52.219-9(d) reporting
attaches to that contract at all.

FAR 19.702(a) requires a subcontracting plan, and therefore the ISR/SSR
reporting that flows from it, only where subcontracting possibilities exist. A
contract that has subcontracted nothing owes no report, so its missing report is
not a compliance gap.

Live board, 2026-08-13: all EIGHT active contracts carry $0 of recorded
subcontract dollars and zero active subcontractors, ``cpmp_small_business_plan``
holds zero rows board-wide, and every one of the eight raises this finding —
100% of them false, 0 true positives ever. Five had already reached the kanban
board as high-priority cards ("[SUBCON] contract 6d67ff20: ISR/SSR" and
siblings) and a sixth, cpmp-841f3ee111, was dispatched to a session. That card
is unsatisfiable by construction: its remedy is filing a report in eSRS, an
external government system, for a $0 contract that owes none.

The dollars, not the subcontractor's current status, are what decides. An ISR
reports subcontracting DOLLARS, so a contract whose subcontractors have all been
terminated still owes a report for what it awarded them — gating on "has an
active subcontractor" would drop that genuine obligation.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests._sql_compat import translating

_DDL = """
CREATE TABLE cpmp_contracts (
    id              TEXT PRIMARY KEY,
    contract_number TEXT,
    title           TEXT,
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

CONTRACT_ID = "1a2b3c4d-0000-1111-2222-333344445555"


@pytest.fixture
def board(monkeypatch):
    """One in-memory board, shared by the tracker and its helpers.

    ``subcontractor_tracker`` bound ``get_connection`` at import time, so the
    storage module alone is not enough to redirect it — patching only there
    leaves the tracker reading the LIVE board.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)
    raw.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, status) VALUES (?, ?, ?, ?)",
        (CONTRACT_ID, "", "Untitled Contract", "active"),
    )
    raw.commit()

    conn = translating(raw, unclosable=True)
    conn.set_security_context = lambda _ctx: None

    from tools.db import storage as _storage
    from tools.govcon import subcontractor_tracker as _tracker

    monkeypatch.setattr(_storage, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(_tracker, "get_connection", lambda *a, **kw: conn)

    yield raw
    raw.close()


def _add_sub(raw, sub_id, value, status="active"):
    raw.execute(
        "INSERT INTO cpmp_subcontractors (id, contract_id, company_name, cage_code, uei, "
        "business_size, subcontract_value, flow_down_complete, cybersecurity_compliant, "
        "cmmc_level, status) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 3, ?)",
        (sub_id, CONTRACT_ID, "Acme Defense LLC", "1ABC2", "UEI123456789", "small", value, status),
    )
    raw.commit()


def _isr_findings(result):
    return [f for f in result["findings"] if f["category"] == "isr_ssr"]


class TestNoObligationNoFinding:
    """The live-board case: nothing subcontracted, so nothing to report."""

    def test_contract_with_no_subcontractors_raises_no_isr_finding(self, board):
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        assert _isr_findings(detect_noncompliance(CONTRACT_ID)) == []

    def test_zero_dollar_subcontractor_raises_no_isr_finding(self, board):
        """df32ba49 exactly: one subcontractor row, $0, and no report."""
        _add_sub(board, "sub-zero", 0.0, status="inactive")
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        assert _isr_findings(detect_noncompliance(CONTRACT_ID)) == []

    def test_contract_reads_compliant_rather_than_high_severity(self, board):
        """The card this suppresses was filed 'high' and was unsatisfiable."""
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        result = detect_noncompliance(CONTRACT_ID)
        assert result["compliant"] is True, result["findings"]
        assert result["severity_counts"].get("high", 0) == 0


class TestSuppressionIsReportedNotSilent:
    """A check that quietly stops running is the defect one door over."""

    def test_result_says_the_obligation_does_not_apply_and_why(self, board):
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        isr = detect_noncompliance(CONTRACT_ID)["isr_ssr"]
        assert isr["applicable"] is False
        assert isr["subcontracted_dollars"] == 0.0
        assert isr["reason"], "a suppressed check must say why"

    def test_result_says_the_obligation_applies_when_it_does(self, board):
        _add_sub(board, "sub-acme", 250_000.0)
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        isr = detect_noncompliance(CONTRACT_ID)["isr_ssr"]
        assert isr["applicable"] is True
        assert isr["subcontracted_dollars"] == 250_000.0


class TestGenuineObligationStillFires:
    """The finding must survive where FAR 52.219-9(d) actually attaches."""

    def test_subcontracted_dollars_with_no_report_is_still_high(self, board):
        _add_sub(board, "sub-acme", 250_000.0)
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        findings = _isr_findings(detect_noncompliance(CONTRACT_ID))
        assert len(findings) == 1
        assert findings[0]["severity"] == "high"
        assert "No ISR/SSR report" in findings[0]["description"]

    def test_terminated_subcontractor_still_owes_a_report(self, board):
        """Dollars were awarded; the sub going inactive does not retire the ISR."""
        _add_sub(board, "sub-gone", 500_000.0, status="terminated")
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        assert len(_isr_findings(detect_noncompliance(CONTRACT_ID))) == 1

    def test_a_filed_report_is_not_re_flagged_as_missing(self, board):
        _add_sub(board, "sub-acme", 250_000.0)
        board.execute(
            "INSERT INTO cpmp_small_business_plan (id, contract_id, reporting_period, "
            "report_type, created_at) VALUES (?, ?, ?, ?, ?)",
            ("rep-1", CONTRACT_ID, "2026-Q2", "isr", "2026-08-01T00:00:00+00:00"),
        )
        board.commit()
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        assert _isr_findings(detect_noncompliance(CONTRACT_ID)) == []


class TestStaleReportBranchIsUnchanged:
    """Once a report exists the obligation is established — do not over-gate."""

    def test_stale_report_is_flagged_even_with_no_recorded_dollars(self, board):
        board.execute(
            "INSERT INTO cpmp_small_business_plan (id, contract_id, reporting_period, "
            "report_type, created_at) VALUES (?, ?, ?, ?, ?)",
            ("rep-old", CONTRACT_ID, "2020-Q1", "ssr", "2020-01-01T00:00:00+00:00"),
        )
        board.commit()
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        findings = _isr_findings(detect_noncompliance(CONTRACT_ID))
        assert len(findings) == 1
        assert findings[0]["severity"] == "medium"
        assert "days old" in findings[0]["description"]
