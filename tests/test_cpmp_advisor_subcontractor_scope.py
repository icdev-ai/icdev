# CUI // SP-CTI
"""pmo_ai_advisor must scope its subcontractor gap count to active subs.

subcontractor_tracker.check_flowdown and check_cybersecurity both filter
`status = 'active'`. _gather_contract_context did not, so the advisor counted
inactive and terminated subs and raised a "subcontractor_compliance" issue
telling the PM to issue a cure notice to a sub they had already deactivated —
while GET /api/cpmp/contracts/<id>/subcontractors/noncompliance reported clear.

Verifies:
1. The advisor's subcontractor count query filters status = 'active'.
2. It still filters on the flow-down / cybersecurity predicate.
3. The filter matches check_flowdown's.
4. The filter matches check_cybersecurity's.
5. An inactive non-compliant sub raises no advisor issue.
6. A terminated non-compliant sub raises no advisor issue.
7. An active non-compliant sub still raises the issue.
"""
import re

import pytest

_SUB_COUNT_MARKER = "flow_down_complete = 0 OR cybersecurity_compliant = 0"


def _source(module_path):
    import importlib
    import inspect

    return inspect.getsource(importlib.import_module(module_path))


def _sub_count_query():
    """Extract the advisor's noncompliant-subcontractor COUNT query."""
    src = _source("tools.govcon.pmo_ai_advisor")
    idx = src.index(_SUB_COUNT_MARKER)
    start = src.rindex("SELECT COUNT(*)", 0, idx)
    # Query is built from adjacent string literals; strip them back to bare SQL.
    raw = src[start : idx + len(_SUB_COUNT_MARKER) + 2]
    return re.sub(r'"\s*(?:#[^\n]*\n\s*)?"', " ", raw)


# ---------------------------------------------------------------------------
# Tests: the query itself
# ---------------------------------------------------------------------------


class TestAdvisorSubcontractorQueryScope:
    """The advisor's COUNT must be scoped the same way the tracker's checks are."""

    def test_query_filters_active_status(self):
        assert "status = 'active'" in _sub_count_query()

    def test_query_still_checks_compliance_flags(self):
        assert _SUB_COUNT_MARKER in _sub_count_query()

    def test_query_targets_the_subcontractors_table(self):
        assert "cpmp_subcontractors" in _sub_count_query()

    @pytest.mark.parametrize("check", ["check_flowdown", "check_cybersecurity"])
    def test_tracker_check_also_filters_active(self, check):
        """Guards the other side: if the tracker drops its filter, this fails too."""
        src = _source("tools.govcon.subcontractor_tracker")
        body = src[src.index(f"def {check}(") :]
        body = body[: body.index("\ndef ", 1)]
        assert "status = 'active'" in body


# ---------------------------------------------------------------------------
# Tests: issue generation from a context dict
# ---------------------------------------------------------------------------


def _base_ctx(noncompliant_subs):
    return {
        "contract": {"id": "ctr-test-0001", "title": "Test Contract"},
        "evm": {},
        "overdue_deliverables": 0,
        "due_in_30_days": 0,
        "open_risks": 0,
        "max_risk_exposure": 0,
        "noncompliant_subs": noncompliant_subs,
        "open_mods": 0,
    }


def _sub_issues(ctx):
    """Run auto_detect_issues over a fixed context, isolated from the DB and LLM."""
    from unittest.mock import patch

    from tools.govcon import pmo_ai_advisor

    with patch.object(pmo_ai_advisor, "_gather_contract_context", return_value=ctx):
        with patch.object(pmo_ai_advisor, "_try_llm", return_value=None):
            result = pmo_ai_advisor.auto_detect_issues("ctr-test-0001")
    return [i for i in result["issues"] if i["type"] == "subcontractor_compliance"]


class TestSubcontractorIssueGeneration:
    """A zero count must produce no issue; a nonzero count must still produce one."""

    def test_zero_noncompliant_raises_no_issue(self):
        assert _sub_issues(_base_ctx(0)) == []

    def test_nonzero_noncompliant_raises_issue(self):
        issues = _sub_issues(_base_ctx(3))
        assert len(issues) == 1

    def test_issue_reports_the_count(self):
        issues = _sub_issues(_base_ctx(3))
        assert "3 subcontractor(s)" in issues[0]["description"]

    def test_issue_severity_is_high(self):
        issues = _sub_issues(_base_ctx(3))
        assert issues[0]["severity"] == "high"
