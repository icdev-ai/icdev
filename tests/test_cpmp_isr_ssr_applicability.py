# CUI // SP-CTI
"""The "no ISR/SSR filed" finding must be gated on FAR 52.219-9 actually applying.

`detect_noncompliance` check #4 raised a HIGH finding — "No ISR/SSR report has
been filed for this contract" — for ANY contract with no `cpmp_small_business_plan`
row. Nothing gated it on whether the contract owes a report at all.

Checks #1-#3 are all scoped: flow-down and cybersecurity filter `status = 'active'`
subcontractors, CMMC additionally filters on subcontract value. Check #4 was scoped
to nothing, and that asymmetry is the defect: on the live board every one of the
seven active contracts had zero ACTIVE subcontractors, so checks #1-#3 correctly
produced nothing while check #4 fired HIGH on all seven. The cpmp_monitor reflex
turned each into a kanban card and two were dispatched to sessions, instructing
them to "File the outstanding ISR/SSR in eSRS" for a $0 contract with no
subcontractors. The pass had never produced a true positive and could not.

FAR 19.702(a) requires a subcontracting plan only where the contract exceeds the
threshold AND subcontracting possibilities exist; the ISR/SSR obligation in
FAR 52.219-9(d)(10) flows from that plan. No plan, no report, no finding.

The gate deliberately does NOT cover the staleness branch. A report on file is
itself proof the obligation exists, so an overdue report stays a finding
regardless of the contract's current value or subcontractor roster — otherwise
deactivating the last subcontractor would silently retire a live reporting gap.

Verifies:
1. No active subcontractors -> no finding, at any contract value.
2. Below the FAR 19.702 threshold -> no finding, however many subcontractors.
3. Active subcontractors at or above the threshold -> the finding IS raised, so
   the gate narrows the check rather than killing it.
4. The staleness branch is ungated — an existing overdue report still flags.
5. The applicability decision and its reason are REPORTED, not silently dropped.
6. The threshold matches FAR 19.702(a)(1).
"""
from unittest.mock import patch

import pytest

_CONTRACT_ID = "ctr-isr-0001"

# FAR 19.702(a)(1) — subcontracting plan threshold.
_FAR_THRESHOLD = 750000.0


class _FakeConn:
    """Answers the four queries detect_noncompliance runs after check #1/#2."""

    def __init__(self, *, total_value=0.0, active_subs=0, latest_report=None,
                 funded_value=0.0, ceiling_value=None):
        self.contract = {
            "total_value": total_value,
            "funded_value": funded_value,
            "ceiling_value": ceiling_value,
        }
        self.active_subs = active_subs
        self.latest_report = latest_report
        self.queries = []

    def set_security_context(self, _ctx):
        pass

    def execute(self, sql, params=None):
        self.queries.append(sql)
        return _FakeCursor(self._result_for(sql))

    def _result_for(self, sql):
        if "FROM cpmp_small_business_plan" in sql:
            return ("one", self.latest_report)
        if "FROM cpmp_contracts" in sql:
            return ("one", dict(self.contract))
        if "COUNT(*)" in sql and "cpmp_subcontractors" in sql:
            return ("one", {"n": self.active_subs})
        if "cmmc_level IS NULL" in sql:
            return ("all", [])
        return ("all", [])

    def commit(self):
        pass

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, result):
        self.kind, self.value = result

    def fetchone(self):
        return self.value if self.kind == "one" else None

    def fetchall(self):
        return self.value if self.kind == "all" else []


def _detect(**conn_kwargs):
    """Run detect_noncompliance with checks #1/#2 stubbed clear."""
    from tools.govcon import subcontractor_tracker

    conn = _FakeConn(**conn_kwargs)
    with patch.object(subcontractor_tracker, "_get_db", return_value=conn):
        with patch.object(subcontractor_tracker, "check_flowdown", return_value={"non_compliant": []}):
            with patch.object(
                subcontractor_tracker, "check_cybersecurity", return_value={"non_compliant": []}
            ):
                result = subcontractor_tracker.detect_noncompliance(_CONTRACT_ID)
    return result, conn


def _isr_findings(result):
    return [f for f in result["findings"] if f["category"] == "isr_ssr"]


# ---------------------------------------------------------------------------
# Tests: no subcontracting -> no obligation
# ---------------------------------------------------------------------------


class TestNoActiveSubcontractorsRaisesNoFinding:
    """The live-board shape: active contract, no active subs, no plan on file."""

    def test_zero_value_zero_subs_is_clean(self):
        result, _ = _detect(total_value=0.0, active_subs=0)
        assert _isr_findings(result) == []

    def test_large_contract_with_no_subcontractors_is_clean(self):
        """Over threshold is not enough — FAR 19.702 also needs subcontracting."""
        result, _ = _detect(total_value=5_000_000.0, active_subs=0)
        assert _isr_findings(result) == []

    def test_contract_reports_compliant_overall(self):
        result, _ = _detect(total_value=0.0, active_subs=0)
        assert result["compliant"] is True
        assert result["total_findings"] == 0

    def test_no_high_severity_is_manufactured(self):
        """The card that reached the board was priority=high off this severity."""
        result, _ = _detect(total_value=0.0, active_subs=0)
        assert result["severity_counts"].get("high", 0) == 0


# ---------------------------------------------------------------------------
# Tests: below the dollar threshold -> no obligation
# ---------------------------------------------------------------------------


class TestBelowThresholdRaisesNoFinding:
    @pytest.mark.parametrize("value", [0.0, 1.0, 100_000.0, 749_999.0])
    def test_under_threshold_is_clean(self, value):
        result, _ = _detect(total_value=value, active_subs=3)
        assert _isr_findings(result) == []

    def test_none_valued_contract_is_clean(self):
        """total_value is nullable; None must not crash or count as over."""
        result, _ = _detect(total_value=None, active_subs=3)
        assert _isr_findings(result) == []


# ---------------------------------------------------------------------------
# Tests: the check is narrowed, NOT killed
# ---------------------------------------------------------------------------


class TestObligatedContractStillFlags:
    """A gate that can never fire is the same defect one door over."""

    def test_subs_over_threshold_raises_the_finding(self):
        result, _ = _detect(total_value=5_000_000.0, active_subs=2)
        assert len(_isr_findings(result)) == 1

    def test_finding_is_high_severity(self):
        result, _ = _detect(total_value=5_000_000.0, active_subs=2)
        assert _isr_findings(result)[0]["severity"] == "high"

    def test_exactly_at_threshold_is_obligated(self):
        """FAR 19.702 applies to contracts that EXCEED the threshold amount."""
        result, _ = _detect(total_value=_FAR_THRESHOLD, active_subs=1)
        assert len(_isr_findings(result)) == 1

    def test_ceiling_value_alone_can_obligate(self):
        """An IDIQ may carry its dollars on ceiling rather than total."""
        result, _ = _detect(total_value=0.0, ceiling_value=9_000_000.0, active_subs=1)
        assert len(_isr_findings(result)) == 1

    def test_funded_value_alone_can_obligate(self):
        result, _ = _detect(total_value=0.0, funded_value=9_000_000.0, active_subs=1)
        assert len(_isr_findings(result)) == 1


# ---------------------------------------------------------------------------
# Tests: the staleness branch is NOT gated
# ---------------------------------------------------------------------------


class TestStalenessBranchIsUngated:
    """A report on file proves the obligation — it outlives the roster."""

    @staticmethod
    def _old_report():
        return {
            "created_at": "2020-01-01T00:00:00+00:00",
            "reporting_period": "2019-Q4",
            "report_type": "isr",
        }

    def test_overdue_report_flags_even_with_no_active_subs(self):
        result, _ = _detect(total_value=0.0, active_subs=0, latest_report=self._old_report())
        assert len(_isr_findings(result)) == 1

    def test_overdue_report_is_medium_severity(self):
        result, _ = _detect(total_value=0.0, active_subs=0, latest_report=self._old_report())
        assert _isr_findings(result)[0]["severity"] == "medium"

    def test_current_report_raises_nothing(self):
        from datetime import datetime, timezone

        fresh = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reporting_period": "2026-Q2",
            "report_type": "ssr",
        }
        result, _ = _detect(total_value=5_000_000.0, active_subs=2, latest_report=fresh)
        assert _isr_findings(result) == []


# ---------------------------------------------------------------------------
# Tests: the decision is reported, not silent
# ---------------------------------------------------------------------------


class TestApplicabilityIsReported:
    """A silently skipped check is indistinguishable from a passing one."""

    def test_result_reports_not_applicable(self):
        result, _ = _detect(total_value=0.0, active_subs=0)
        assert result["isr_ssr_applicable"] is False

    def test_result_reports_applicable(self):
        result, _ = _detect(total_value=5_000_000.0, active_subs=2)
        assert result["isr_ssr_applicable"] is True

    def test_skip_reason_names_the_missing_subcontractors(self):
        result, _ = _detect(total_value=5_000_000.0, active_subs=0)
        assert "subcontractor" in result["isr_ssr_not_applicable_reason"].lower()

    def test_skip_reason_names_the_threshold(self):
        result, _ = _detect(total_value=1000.0, active_subs=2)
        assert "threshold" in result["isr_ssr_not_applicable_reason"].lower()

    def test_applicable_contract_has_no_skip_reason(self):
        result, _ = _detect(total_value=5_000_000.0, active_subs=2)
        assert result["isr_ssr_not_applicable_reason"] is None


# ---------------------------------------------------------------------------
# Tests: the gate is scoped like its siblings
# ---------------------------------------------------------------------------


class TestGateScoping:
    def test_threshold_matches_far_19_702(self):
        from tools.govcon.subcontractor_tracker import _SUBCONTRACTING_PLAN_THRESHOLD

        assert _SUBCONTRACTING_PLAN_THRESHOLD == _FAR_THRESHOLD

    def test_subcontractor_count_filters_active(self):
        """Siblings #1-#3 all filter status='active'; this must match them."""
        _, conn = _detect(total_value=5_000_000.0, active_subs=1)
        counts = [q for q in conn.queries if "COUNT(*)" in q and "cpmp_subcontractors" in q]
        assert counts, "no active-subcontractor count query was issued"
        assert "status = 'active'" in counts[0]

    def test_gate_queries_the_contract_value(self):
        _, conn = _detect(total_value=5_000_000.0, active_subs=1)
        assert any("FROM cpmp_contracts" in q for q in conn.queries)
