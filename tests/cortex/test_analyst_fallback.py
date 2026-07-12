# CUI // SP-CTI
"""Tests for the Cortex Analyst NLQ fallback + shared safety layer (ctx-analyst-02).

Covers the three ``mode`` behaviors (auto/iqe/nlq), the transparent fallback
from IQE to the NL→SQL pipeline, and the pre-execution safety layer: raw
question injection screening, SELECT-only enforcement, multi-statement
rejection, and the registered-collection table allowlist — each rejection
audited via ``log_nlq_query(status="blocked")``.

The dashboard NLQ pipeline and the LLM gateway are stubbed at their module
attributes (the analyst lazy-imports them at call time), so no LLM call or
real DB write ever happens here.
"""
from __future__ import annotations

import pytest

from tools.cortex.analyst import CortexAnalystError, CortexQueryBlocked, ask
from tools.cortex.schemas import CortexContext
from tools.dashboard import nlq_processor as _nlq
from tools.iqe import executor as _executor
from tools.iqe.executor import register_collection
from tools.llm import gateway as _gateway

_SATELLITES = [
    {"id": 1, "name": "alpha", "status": "active"},
    {"id": 2, "name": "bravo", "status": "retired"},
]

_NLQ_RESULTS = {
    "columns": ["id", "name"],
    "rows": [{"id": 1, "name": "alpha"}],
    "row_count": 1,
    "truncated": False,
    "max_rows": 500,
}

_GATEWAY_OK = {
    "pre_invoke": {
        "allowed": True,
        "warnings": [],
        "blocked_reason": None,
        "injection_score": 0.0,
        "pii_labels": [],
        "request_id": "gw_test",
    },
    "post_invoke": {},
}


class StubConn:
    def set_security_context(self, ctx):
        self.security_context = ctx


@pytest.fixture()
def audit_log():
    """Captured log_nlq_query() calls: list of (question, sql, status, error)."""
    return []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, audit_log):
    """Fake collections + stubbed NLQ pipeline, gateway, and audit sink."""
    saved = dict(_executor._default._registry)
    register_collection("satellites", lambda conn: list(_SATELLITES))

    monkeypatch.setattr(_nlq, "extract_schema", lambda db_path=None: {})
    monkeypatch.setattr(
        _nlq,
        "generate_sql_via_bedrock",
        lambda query, schema, exclude_model_ids=None: pytest.fail(
            "NLQ generation should not run in this test"
        ),
    )
    # ctx-expose-03: the NLQ fallback now executes through the analyst's own
    # RLS-context-threading seam (_execute_nlq_readonly), NOT the dashboard's
    # context-free execute_safely. Stub the seam the analyst actually calls.
    monkeypatch.setattr(
        "tools.cortex.analyst._execute_nlq_readonly",
        lambda sql, ctx: pytest.fail("NLQ execution should not run in this test"),
    )
    monkeypatch.setattr(
        _nlq,
        "log_nlq_query",
        lambda q, sql, n, ms, actor, status, error_message=None: audit_log.append(
            (q, sql, status, error_message)
        ),
    )
    monkeypatch.setattr(_gateway, "check_text", lambda text: dict(_GATEWAY_OK))

    yield
    _executor._default._registry.clear()
    _executor._default._registry.update(saved)


def _enable_nlq(monkeypatch, sql: str, results: dict = _NLQ_RESULTS):
    monkeypatch.setattr(
        _nlq, "generate_sql_via_bedrock",
        lambda query, schema, exclude_model_ids=None: sql,
    )
    monkeypatch.setattr(
        "tools.cortex.analyst._execute_nlq_readonly", lambda s, ctx: dict(results)
    )


# ---------------------------------------------------------------------------
# mode= behavior + fallback triggers
# ---------------------------------------------------------------------------
def test_auto_iqe_primary_no_fallback():
    result = ask("show all satellites", conn=StubConn())
    assert result.provider == "iqe"
    assert result.data["row_count"] == 2


def test_parse_failure_falls_back_to_nlq(monkeypatch):
    monkeypatch.setattr(
        "tools.cortex.analyst.nl_to_iqe",
        lambda q, colls: {"iqe": "@@ not parseable", "explanation": ""},
    )
    _enable_nlq(monkeypatch, "SELECT id, name FROM satellites")

    result = ask("show all satellites", conn=StubConn())
    assert result.provider == "nlq"
    assert result.grounded is True
    assert result.data["sql"] == "SELECT id, name FROM satellites"
    assert result.data["rows"] == _NLQ_RESULTS["rows"]
    g = result.governance
    assert g.outcomes["iqe_translation"] == "fail"  # recorded as history
    assert g.outcomes["nlq_translation"] == "pass"
    assert g.outcomes["sql_readonly"] == "pass"
    assert g.outcomes["table_allowlist"] == "pass"
    assert g.outcomes["nlq_execution"] == "pass"
    assert g.blocked is False  # a degrade, not a refusal
    assert result.citations[0].source_table == "satellites"
    # ctx-analyst-03: the snippet is the row-summary evidence text; the
    # executed SQL stays in data["sql"].
    assert result.citations[0].snippet == "1 row; id=1, name='alpha'"


def test_no_matching_collection_falls_back_to_nlq(monkeypatch):
    _enable_nlq(monkeypatch, "SELECT id, name FROM satellites")
    result = ask("count the frobnicators", conn=StubConn())
    assert result.provider == "nlq"
    assert result.governance.outcomes["collection_resolution"] == "fail"
    assert result.governance.outcomes["nlq_execution"] == "pass"


def test_fallback_untranslatable_question_raises(monkeypatch):
    monkeypatch.setattr(
        _nlq, "generate_sql_via_bedrock",
        lambda query, schema, exclude_model_ids=None: None,
    )
    with pytest.raises(CortexAnalystError) as exc_info:
        ask("count the frobnicators", conn=StubConn())
    err = exc_info.value
    assert not isinstance(err, CortexQueryBlocked)
    assert err.governance.outcomes["nlq_translation"] == "fail"


def test_mode_nlq_skips_iqe(monkeypatch):
    monkeypatch.setattr(
        "tools.cortex.analyst.nl_to_iqe",
        lambda q, colls: pytest.fail("IQE path should not run in mode='nlq'"),
    )
    _enable_nlq(monkeypatch, "SELECT id, name FROM satellites")
    result = ask("show all satellites", mode="nlq")
    assert result.provider == "nlq"
    assert "collection_resolution" not in result.governance.outcomes


def test_mode_iqe_never_falls_back():
    with pytest.raises(CortexAnalystError) as exc_info:
        ask("count the frobnicators", mode="iqe", conn=StubConn())
    # generate_sql_via_bedrock stub would pytest.fail() if the fallback ran
    assert exc_info.value.governance.outcomes["collection_resolution"] == "fail"


def test_explicit_collections_never_fall_back():
    with pytest.raises(CortexAnalystError) as exc_info:
        ask("show all unicorns", collections=["unicorns"], conn=StubConn())
    assert exc_info.value.collection == "unicorns"


def test_iqe_execution_failure_does_not_fall_back():
    def _broken(conn):
        raise RuntimeError("adapter exploded")

    register_collection("outages", _broken)
    with pytest.raises(CortexAnalystError) as exc_info:
        ask("show all outages", conn=StubConn())
    assert exc_info.value.governance.outcomes["iqe_execution"] == "fail"


def test_invalid_mode_raises():
    with pytest.raises(CortexAnalystError):
        ask("show all satellites", mode="quantum", conn=StubConn())


# ---------------------------------------------------------------------------
# Safety layer: injection screening on the raw question (both paths)
# ---------------------------------------------------------------------------
def test_stacked_statement_question_blocked(audit_log):
    with pytest.raises(CortexQueryBlocked) as exc_info:
        ask("'; DROP TABLE users; --", conn=StubConn())
    err = exc_info.value
    assert err.governance.outcomes["safety_screen"] == "fail"
    assert err.governance.blocked is True
    assert audit_log and audit_log[0][2] == "blocked"


def test_union_exfil_question_blocked(audit_log):
    with pytest.raises(CortexQueryBlocked):
        ask("show satellites UNION SELECT password FROM users", conn=StubConn())
    assert audit_log[0][2] == "blocked"
    assert "UNION" in audit_log[0][3]


def test_injection_blocked_in_nlq_mode_before_generation():
    # generate_sql_via_bedrock stub would pytest.fail() if generation ran
    with pytest.raises(CortexQueryBlocked):
        ask("nothing important; DELETE FROM audit_trail", mode="nlq")


def test_gateway_block_refuses_question(monkeypatch, audit_log):
    verdict = {"pre_invoke": {"allowed": False, "blocked_reason": "Prompt injection detected"}}
    monkeypatch.setattr(_gateway, "check_text", lambda text: verdict)
    with pytest.raises(CortexQueryBlocked) as exc_info:
        ask("show all satellites", conn=StubConn())
    assert "LLM gateway" in str(exc_info.value)
    assert audit_log[0][2] == "blocked"


def test_gateway_outage_fails_open_by_default(monkeypatch):
    def _boom(text):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(_gateway, "check_text", _boom)
    result = ask("show all satellites", conn=StubConn())
    assert result.provider == "iqe"


def test_gateway_outage_fails_closed_when_ctx_says_so(monkeypatch):
    def _boom(text):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(_gateway, "check_text", _boom)
    ctx = CortexContext(fail_closed=True)
    with pytest.raises(CortexQueryBlocked):
        ask("show all satellites", ctx=ctx, conn=StubConn())


# ---------------------------------------------------------------------------
# Safety layer: generated-SQL gates (read-only + allowlist)
# ---------------------------------------------------------------------------
def test_non_select_sql_blocked(monkeypatch, audit_log):
    _enable_nlq(monkeypatch, "DELETE FROM satellites")
    with pytest.raises(CortexQueryBlocked) as exc_info:
        ask("clean up old satellites please", mode="nlq")
    assert exc_info.value.governance.outcomes["sql_readonly"] == "fail"
    assert audit_log[0][1] == "DELETE FROM satellites"
    assert audit_log[0][2] == "blocked"


def test_multi_statement_sql_blocked(monkeypatch, audit_log):
    _enable_nlq(monkeypatch, "SELECT * FROM satellites; DROP TABLE satellites")
    with pytest.raises(CortexQueryBlocked):
        ask("show satellites the fancy way", mode="nlq")
    assert audit_log[0][2] == "blocked"


def test_off_allowlist_table_blocked(monkeypatch, audit_log):
    _enable_nlq(monkeypatch, "SELECT * FROM tenant_secrets ORDER BY id")
    with pytest.raises(CortexQueryBlocked) as exc_info:
        ask("show me the interesting stuff", mode="nlq")
    assert exc_info.value.governance.outcomes["table_allowlist"] == "fail"
    assert "tenant_secrets" in audit_log[0][3]


def test_registry_collection_table_passes_allowlist(monkeypatch):
    # infra.resources is declared in args/component_registry.yaml iqe: blocks,
    # so its dots→underscores table form is queryable via get_iqe_mapping().
    _enable_nlq(monkeypatch, "SELECT id FROM infra_resources LIMIT 10")
    result = ask("how many infra resources", mode="nlq")
    assert result.provider == "nlq"
    assert result.governance.outcomes["table_allowlist"] == "pass"
    assert result.citations[0].source_table == "infra_resources"


def test_cte_names_are_not_allowlist_checked(monkeypatch):
    _enable_nlq(
        monkeypatch,
        "WITH s AS (SELECT * FROM satellites) SELECT s.id FROM s",
    )
    result = ask("show satellite ids via cte", mode="nlq")
    assert result.governance.outcomes["table_allowlist"] == "pass"
    assert result.data["collections"] == ["satellites"]


def test_nlq_success_is_audited(monkeypatch, audit_log):
    _enable_nlq(monkeypatch, "SELECT id, name FROM satellites")
    ask("show all satellites", mode="nlq")
    assert audit_log[-1][2] == "success"
    assert audit_log[-1][1] == "SELECT id, name FROM satellites"


# ---------------------------------------------------------------------------
# Air-gap: the NL->SQL generation call must honor per-call air-gap exclusions
# the same way cortex.api._invoke does for every other Cortex LLM call.
# ---------------------------------------------------------------------------
def test_nlq_generation_receives_airgap_exclusions(monkeypatch):
    seen = {}

    def _capture(query, schema, exclude_model_ids=None):
        seen["exclude_model_ids"] = exclude_model_ids
        return "SELECT id, name FROM satellites"

    monkeypatch.setattr(_nlq, "generate_sql_via_bedrock", _capture)
    monkeypatch.setattr(
        "tools.cortex.analyst._execute_nlq_readonly", lambda s, ctx: dict(_NLQ_RESULTS)
    )
    # Force airgap_exclusions to return a sentinel regardless of config so the
    # test pins the wiring, not the air-gap detection logic.
    monkeypatch.setattr(
        "tools.cortex.config.airgap_exclusions", lambda ctx=None, config_path=None: ["cloud-model-x"]
    )
    result = ask("show all satellites", mode="nlq", ctx=CortexContext(air_gap=True))
    assert result.provider == "nlq"
    assert seen["exclude_model_ids"] == ["cloud-model-x"]


def test_nlq_generation_no_exclusions_when_not_airgapped(monkeypatch):
    seen = {}

    def _capture(query, schema, exclude_model_ids=None):
        seen["exclude_model_ids"] = exclude_model_ids
        return "SELECT id, name FROM satellites"

    monkeypatch.setattr(_nlq, "generate_sql_via_bedrock", _capture)
    monkeypatch.setattr(
        "tools.cortex.analyst._execute_nlq_readonly", lambda s, ctx: dict(_NLQ_RESULTS)
    )
    monkeypatch.setattr(
        "tools.cortex.config.airgap_exclusions", lambda ctx=None, config_path=None: None
    )
    ask("show all satellites", mode="nlq")
    assert seen["exclude_model_ids"] is None


def test_blocked_error_exposed_from_package_and_api():
    from tools.cortex import CortexQueryBlocked as pkg_blocked
    from tools.cortex.api import CortexQueryBlocked as api_blocked

    assert pkg_blocked is CortexQueryBlocked and api_blocked is CortexQueryBlocked
    assert issubclass(CortexQueryBlocked, CortexAnalystError)
