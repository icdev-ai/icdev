# CUI // SP-CTI
"""DAST/runtime gate must be evidence-gated (fail-closed).

Regression tests for the defect where ``run_dast_scan`` defaulted every OWASP
check to ``True`` when no findings were supplied, so ``deploy_dast_gates()`` scored
100%, returned ``gate_status="pass"``, and marked ZIG activity p2-21 complete
without any scan having run. Those rows are consumed by
``continuous_authorization._resolve_dast_signal`` to compute the cATO posture, so
the fabricated pass propagated into an ongoing-authorization decision.

The invariant under test: **no observation → never ``pass``.**
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

drg = importlib.import_module("tools.security_canvas.dast_runtime_gates")
cont = importlib.import_module("tools.security_canvas.continuous_authorization")
tracker = importlib.import_module("tools.security_canvas.zig_activity_tracker")

APP = "test-app"


# ---------------------------------------------------------------------------
# Fixtures — a real StorageConnection over a temp sqlite file so the module's
# `%s` placeholders go through the same translator they use at runtime. A raw
# sqlite3 connection would raise `near "%": syntax error`; :memory: would lose
# state because every call closes its connection.
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn_factory(tmp_path):
    from tools.db.storage import StorageConnection

    db_path = tmp_path / "security_canvas_test.db"

    def factory():
        raw = sqlite3.connect(str(db_path))
        raw.row_factory = sqlite3.Row
        return StorageConnection(raw, "sqlite")

    return factory


@pytest.fixture(autouse=True)
def patch_conn(monkeypatch, conn_factory):
    monkeypatch.setattr(drg, "get_connection", conn_factory)


@pytest.fixture()
def captured_activity(monkeypatch):
    """Capture set_activity_status calls instead of writing ZIG completion rows."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        tracker, "set_activity_status",
        lambda *a, **kw: calls.append((a, kw)) or {"status": a[1] if len(a) > 1 else None},
    )
    return calls


def _all_dast_pass() -> dict[str, bool]:
    return {cid: True for cid in drg.DAST_CHECKS}


def _all_runtime_pass() -> dict[str, bool]:
    return {cid: True for cid in drg.RUNTIME_CHECKS}


def _scan_rows(conn_factory, application: str) -> int:
    conn = conn_factory()
    try:
        drg._ensure_tables(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM zig_dast_scans WHERE application=%s", (application,)
        ).fetchone()
        return row["cnt"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The core invariant
# ---------------------------------------------------------------------------

def test_no_evidence_never_passes():
    """The original defect: no findings used to mean 'baseline-clean app'."""
    result = drg.evaluate_gate(APP)

    assert result["gate_status"] == drg.GATE_UNKNOWN
    assert result["gate_status"] != drg.GATE_PASS
    assert result["dast_score"] is None
    assert result["runtime_score"] is None
    assert result["combined_score"] is None
    assert "no DAST scan evidence" in result["evidence_gaps"]
    assert "no runtime posture evidence" in result["evidence_gaps"]


def test_no_evidence_records_no_scan_rows(conn_factory):
    """zig_dast_scans must not be populated with imagined passes."""
    drg.run_dast_scan(APP)
    drg.run_runtime_check(APP)

    assert _scan_rows(conn_factory, APP) == 0


def test_unevaluated_checks_report_unknown_not_pass():
    scan = drg.run_dast_scan(APP)

    assert scan["scan_status"] == drg.SCAN_NOT_RUN
    assert scan["evaluated"] == 0
    assert len(scan["unknown_checks"]) == len(drg.DAST_CHECKS)
    assert set(scan["results"].values()) == {drg.CHECK_UNKNOWN}


def test_partial_coverage_never_passes():
    """A thin-but-perfect scan must not promote on a 1.0 score."""
    result = drg.evaluate_gate(
        APP,
        dast_findings={"A01-broken-access": True},
        runtime_posture=_all_runtime_pass(),
        target_url="http://localhost:5050",
    )

    assert result["dast_scan_status"] == drg.SCAN_PARTIAL
    assert result["dast_score"] == 1.0          # score over observed weight only
    assert result["gate_status"] == drg.GATE_UNKNOWN   # ...but coverage blocks it
    assert any("unevaluated" in gap for gap in result["evidence_gaps"])


def test_findings_without_target_url_is_an_evidence_gap():
    """A finding set with no target cannot be reproduced, so it cannot promote."""
    result = drg.evaluate_gate(
        APP, dast_findings=_all_dast_pass(), runtime_posture=_all_runtime_pass()
    )

    assert result["gate_status"] == drg.GATE_UNKNOWN
    assert "DAST findings supplied without a target_url" in result["evidence_gaps"]


# ---------------------------------------------------------------------------
# Real evidence still works — the gate is fail-closed, not broken
# ---------------------------------------------------------------------------

def test_full_clean_evidence_passes(conn_factory):
    result = drg.evaluate_gate(
        APP,
        dast_findings=_all_dast_pass(),
        runtime_posture=_all_runtime_pass(),
        target_url="http://localhost:5050",
    )

    assert result["gate_status"] == drg.GATE_PASS
    assert result["dast_score"] == 1.0
    assert result["runtime_score"] == 1.0
    assert result["combined_score"] == 1.0
    assert result["evidence_gaps"] == []
    assert _scan_rows(conn_factory, APP) == len(drg.DAST_CHECKS) + len(drg.RUNTIME_CHECKS)


def test_cat1_failure_blocks():
    findings = _all_dast_pass()
    findings["A03-injection"] = False    # CAT-I

    result = drg.evaluate_gate(
        APP, dast_findings=findings, runtime_posture=_all_runtime_pass(),
        target_url="http://localhost:5050",
    )

    assert result["gate_status"] == drg.GATE_BLOCKED
    assert result["cat1_failures"] == 1
    assert any("CAT-I" in b for b in result["blockers"])


def test_real_failure_outranks_missing_evidence():
    """An observed CAT-I failure is more actionable than an evidence gap."""
    result = drg.evaluate_gate(APP, dast_findings={"A03-injection": False})

    assert result["gate_status"] == drg.GATE_BLOCKED
    assert result["evidence_gaps"]      # gaps still recorded for the operator


def test_unknown_check_ids_are_ignored():
    scan = drg.run_dast_scan(APP, target_url="http://x", findings={"not-a-check": True})

    assert scan["evaluated"] == 0
    assert scan["scan_status"] == drg.SCAN_NOT_RUN


# ---------------------------------------------------------------------------
# ZIG activity honesty
# ---------------------------------------------------------------------------

def test_deploy_does_not_claim_complete_without_a_scanner(captured_activity):
    summary = drg.deploy_dast_gates(["app-a", "app-b"])

    assert summary["unknown"] == 2
    assert summary["passing"] == 0
    assert summary["activity_status"] == "in_progress"

    (args, _kwargs) = captured_activity[-1]
    assert args[0] == "zig-act-p2-21"
    assert args[1] == "in_progress"
    assert "NOT YET OPERATIONAL" in args[2]


def test_deploy_marks_complete_with_full_evidence(captured_activity):
    evidence = {
        "dast": _all_dast_pass(),
        "runtime": _all_runtime_pass(),
        "target_url": "http://localhost:5050",
    }
    summary = drg.deploy_dast_gates(
        ["app-a", "app-b"], findings_by_app={"app-a": evidence, "app-b": evidence}
    )

    assert summary["unknown"] == 0
    assert summary["passing"] == 2
    assert summary["activity_status"] == "complete"
    assert captured_activity[-1][0][1] == "complete"


def test_gate_summary_counts_unknown(captured_activity):
    drg.deploy_dast_gates(["app-a"])

    assert drg.get_gate_summary().get(drg.GATE_UNKNOWN) == 1


# ---------------------------------------------------------------------------
# cATO must not take credit for an evidence-free gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "gate_status,dast_score,runtime_score,expected",
    [
        ("unknown", None, None, 0.6),   # no evidence → neutral, same as no run
        ("unknown", 1.0, 1.0, 0.6),     # scores present but coverage incomplete
        ("blocked", 0.4, 0.5, 0.3),
        ("pass", 1.0, 1.0, 1.0),
    ],
)
def test_cato_signal_gives_no_credit_for_unknown(
    conn_factory, gate_status, dast_score, runtime_score, expected
):
    conn = conn_factory()
    try:
        drg._ensure_tables(conn)
        conn.execute(
            "INSERT INTO zig_dast_gate_results "
            "(application, gate_status, dast_score, runtime_score, cat1_failures, "
            "blockers_json, evaluated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (APP, gate_status, dast_score, runtime_score, 0, "[]", "2026-07-25T00:00:00+00:00"),
        )
        conn.commit()

        assert cont._resolve_dast_signal(conn, APP) == expected
    finally:
        conn.close()


def test_cato_signal_neutral_when_no_gate_row(conn_factory):
    conn = conn_factory()
    try:
        drg._ensure_tables(conn)
        assert cont._resolve_dast_signal(conn, "never-evaluated") == 0.6
    finally:
        conn.close()
