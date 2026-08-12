# [CUI // SP-CTI]
"""Unit tests for tools.network.remediation_simulator.simulate_remediation."""

import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── In-memory DB fixture ──────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nc_advisories (
    id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS nc_remediation_actions (
    id              TEXT PRIMARY KEY,
    advisory_id     TEXT,
    device_id       TEXT,
    device_name     TEXT,
    action_type     TEXT NOT NULL DEFAULT 'patch',
    current_version TEXT,
    target_version  TEXT,
    project_id      TEXT,
    status          TEXT DEFAULT 'pending',
    performed_by    TEXT,
    notes           TEXT,
    result          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT
);
"""


@pytest.fixture
def mem_db():
    """A connection matching the contract of `tools.network.db.init_db.get_connection`.

    That function ALWAYS returns a `StorageConnection` — on PostgreSQL directly,
    and on the SQLite fallback by wrapping the raw sqlite3 connection, precisely
    "so NC callers' PG-native %s placeholders translate to ? on the SQLite path
    too". `remediation_simulator` is one of those callers: its SQL is authored
    for PostgreSQL, as CLAUDE.md requires of runtime call sites.

    So the fixture must wrap too. Handing the module a bare sqlite3.Connection
    stubs out the translation layer that production always supplies, and every
    query dies with `near "%": syntax error` — a defect in the seam, not the code.
    """
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.executescript(_SCHEMA)
    return StorageConnection(raw, "sqlite")


def _insert_action(conn, **kwargs) -> str:
    row_id = kwargs.pop("id", str(uuid.uuid4()))
    fields = {
        "device_name": "router-01",
        "action_type": "patch",
        "current_version": "15.2",
        "target_version": "15.6",
        "project_id": None,
        "status": "pending",
    }
    fields.update(kwargs)
    cols = ", ".join(["id"] + list(fields))
    placeholders = ", ".join(["?"] * (1 + len(fields)))
    conn.execute(
        f"INSERT INTO nc_remediation_actions ({cols}) VALUES ({placeholders})",
        [row_id] + list(fields.values()),
    )
    conn.commit()
    return row_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pass_twin_result(**overrides):
    base = {
        "verdict": "pass",
        "simulation_id": "sim-001",
        "intent_results": [],
        "compliance_findings": [],
        "impacted_systems": [],
    }
    base.update(overrides)
    return base


def _patch_conn(mem_db):
    """Context-manager patcher for _nc_conn."""
    return patch(
        "tools.network.remediation_simulator._nc_conn",
        return_value=mem_db,
    )


def _patch_intent_validate(return_value):
    return patch(
        "tools.network.twin.intent_validate",
        return_value=return_value,
    )


def _patch_nqe_skipped():
    return patch(
        "tools.network.remediation_simulator._run_nqe_layer",
        return_value={"verdict": "skipped", "findings": []},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSimulateRemediationLoadsRow:
    def test_loads_row_by_id_returns_action_id(self, mem_db):
        row_id = _insert_action(mem_db, device_name="core-sw-01", action_type="patch")
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with _patch_intent_validate(_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["action_id"] == row_id

    def test_loads_device_name_from_row(self, mem_db):
        row_id = _insert_action(mem_db, device_name="edge-fw-02", action_type="patch")
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with _patch_intent_validate(_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["device_name"] == "edge-fw-02"

    def test_loads_action_type_from_row(self, mem_db):
        row_id = _insert_action(mem_db, device_name="sw-01", action_type="firmware_update")
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with _patch_intent_validate(_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["action_type"] == "firmware_update"

    def test_not_found_returns_error_dict(self, mem_db):
        with _patch_conn(mem_db), _patch_nqe_skipped():
            from tools.network.remediation_simulator import simulate_remediation
            result = simulate_remediation("nonexistent-id-9999")
        assert result["error"] == "not_found"
        assert result["remediation_action_id"] == "nonexistent-id-9999"


class TestTwinIntentValidateCallArgs:
    def test_intent_validate_called_with_correct_delta(self, mem_db):
        row_id = _insert_action(
            mem_db,
            device_name="rtr-nyc-01",
            action_type="patch",
            current_version="15.2",
            target_version="15.6",
            project_id=None,
        )
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=_pass_twin_result()) as mock_iv:
                from tools.network.remediation_simulator import simulate_remediation
                simulate_remediation(row_id)
        mock_iv.assert_called_once()
        call_kwargs = mock_iv.call_args
        delta = call_kwargs.kwargs.get("delta") or call_kwargs.args[0]
        assert delta["device"] == "rtr-nyc-01"
        assert delta["action"] == "patch"
        assert delta["from_version"] == "15.2"
        assert delta["to_version"] == "15.6"

    def test_intent_validate_receives_project_id(self, mem_db):
        row_id = _insert_action(
            mem_db,
            device_name="rtr-01",
            action_type="replace",
            project_id="proj-abc",
        )
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=_pass_twin_result()) as mock_iv:
                from tools.network.remediation_simulator import simulate_remediation
                simulate_remediation(row_id)
        call_kwargs = mock_iv.call_args
        project_id = call_kwargs.kwargs.get("project_id") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        assert project_id == "proj-abc"


class TestTwinVerdictMapping:
    def test_twin_verdict_pass_when_no_violations(self, mem_db):
        row_id = _insert_action(mem_db)
        twin_result = _pass_twin_result(
            verdict="pass",
            intent_results=[{"rule_id": "r1", "label": "Rule 1", "passed": True, "detail": None}],
        )
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=twin_result):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["twin_verdict"] == "pass"
        assert result["policy_violations"] == []

    def test_twin_verdict_fail_when_policy_violation_present(self, mem_db):
        row_id = _insert_action(mem_db)
        twin_result = _pass_twin_result(
            verdict="fail",
            intent_results=[
                {"rule_id": "no-direct-internet", "label": "No Direct Internet", "passed": False, "detail": "Node exposed"},
                {"rule_id": "acl-compliance", "label": "ACL", "passed": False, "detail": "Permissive rule"},
            ],
        )
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=twin_result):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["twin_verdict"] == "fail"
        assert len(result["policy_violations"]) == 2
        rule_ids = {v["rule_id"] for v in result["policy_violations"]}
        assert "no-direct-internet" in rule_ids

    def test_twin_verdict_warn_when_soft_warning_present(self, mem_db):
        row_id = _insert_action(mem_db)
        twin_result = _pass_twin_result(
            verdict="warn",
            intent_results=[
                {"rule_id": "redundancy", "label": "Redundancy", "passed": False, "detail": "Single path"},
            ],
        )
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=twin_result):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["twin_verdict"] == "warn"


class TestBlastRadius:
    def test_blast_radius_populated_from_twin_impacted_systems(self, mem_db):
        row_id = _insert_action(mem_db)
        twin_result = _pass_twin_result(
            impacted_systems=[
                {"id": "db-01", "title": "Primary DB", "impact_type": "data-loss", "severity": "critical"},
                {"id": "lb-01", "title": "Load Balancer", "impact_type": "connectivity", "severity": "high"},
            ],
        )
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=twin_result):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert len(result["blast_radius"]) == 2
        devices = {r["device"] for r in result["blast_radius"]}
        assert "Primary DB" in devices
        assert "Load Balancer" in devices

    def test_blast_radius_uses_id_when_title_absent(self, mem_db):
        row_id = _insert_action(mem_db)
        twin_result = _pass_twin_result(
            impacted_systems=[{"id": "node-42", "impact_type": "connectivity", "severity": "medium"}],
        )
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=twin_result):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["blast_radius"][0]["device"] == "node-42"

    def test_blast_radius_empty_when_no_impacted_systems(self, mem_db):
        row_id = _insert_action(mem_db)
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["blast_radius"] == []


class TestForwardNetworksDegradation:
    def test_fwd_twin_verdict_unavailable_returned_gracefully(self, mem_db):
        """intent_validate raises ConnectionError → twin_verdict='warn', no exception propagated."""
        row_id = _insert_action(mem_db, device_name="rtr-01", action_type="patch")
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch(
                "tools.network.twin.intent_validate",
                side_effect=ConnectionError("FWD unreachable"),
            ):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert "error" not in result
        assert result["twin_verdict"] == "warn"

    def test_fwd_unreachable_does_not_raise_exception(self, mem_db):
        """simulate_remediation must not propagate any exception when FWD is down."""
        row_id = _insert_action(mem_db)
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch(
                "tools.network.twin.intent_validate",
                side_effect=OSError("network timeout"),
            ):
                from tools.network.remediation_simulator import simulate_remediation
                try:
                    result = simulate_remediation(row_id)
                except Exception as exc:
                    pytest.fail(f"simulate_remediation raised unexpectedly: {exc}")
        assert result is not None


class TestRollbackSteps:
    def test_rollback_steps_present_for_patch_action(self, mem_db):
        row_id = _insert_action(mem_db, action_type="patch")
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert isinstance(result["rollback_steps"], list)
        assert len(result["rollback_steps"]) > 0

    def test_rollback_steps_include_device_name(self, mem_db):
        row_id = _insert_action(mem_db, device_name="core-rtr-99", action_type="patch")
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        first_step = result["rollback_steps"][0]
        assert "core-rtr-99" in first_step


class TestConnectionSeamContract:
    """Pin the invariant the whole file rests on.

    These tests fail with `near "%": syntax error` the moment `_nc_conn` is
    handed a connection that does not translate. There are two ways to make
    that green, and only one is correct: fix the fixture (done), or weaken the
    module's SQL to SQLite `?` placeholders — which CLAUDE.md forbids of runtime
    call sites and which would break the PostgreSQL path this canvas runs on in
    production. This test rejects the second one.
    """

    def test_module_sql_is_pg_native(self):
        import inspect

        from tools.network import remediation_simulator

        src = inspect.getsource(remediation_simulator.simulate_remediation)
        assert "WHERE id = %s" in src, (
            "simulate_remediation must author PostgreSQL-native placeholders; "
            "translation to SQLite is StorageConnection's job, not the call site's"
        )
        assert "WHERE id = ?" not in src

    def test_raw_sqlite_connection_is_not_the_seam(self, mem_db):
        """A bare sqlite3.Connection cannot satisfy the contract — prove it."""
        row_id = _insert_action(mem_db)
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.executescript(_SCHEMA)
        with patch("tools.network.remediation_simulator._nc_conn", return_value=raw):
            with _patch_nqe_skipped():
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        assert result["error"] == "db_error"


class TestReturnShape:
    def test_result_contains_all_required_keys(self, mem_db):
        row_id = _insert_action(mem_db)
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        required = {
            "action_id", "device_name", "action_type",
            "twin_verdict", "blast_radius", "policy_violations",
            "nqe_verdict", "nqe_findings",
            "rollback_steps", "simulation_id", "simulated_at",
        }
        assert required.issubset(result.keys())

    def test_simulated_at_is_iso_string(self, mem_db):
        row_id = _insert_action(mem_db)
        with _patch_conn(mem_db), _patch_nqe_skipped():
            with patch("tools.network.twin.intent_validate", return_value=_pass_twin_result()):
                from tools.network.remediation_simulator import simulate_remediation
                result = simulate_remediation(row_id)
        from datetime import datetime
        dt = datetime.fromisoformat(result["simulated_at"])
        assert dt is not None
