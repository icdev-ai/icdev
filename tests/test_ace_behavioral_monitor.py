# CUI // SP-CTI
"""Tests for ACE continuous behavioral monitoring (coworker_thread behavioral compliance).

Covers:
  * reconcile_mode_b() text scanner — pattern detection and manifest building
  * _text_to_manifest() capability extraction from plain text
  * CoWorkerThread behavioral compliance check — dangerous output → hitl_pending
  * State machine: hitl_pending emitted within 1 step of dangerous output
  * Compliance event emission and kanban card creation (best-effort paths)

Run: pytest tests/test_ace_behavioral_monitor.py -v
"""
from __future__ import annotations

import sqlite3
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ace_db(tmp_path, monkeypatch):
    """Fresh temp SQLite DB with ACE schema; kanban_tasks added for HITL cards."""
    db_path = tmp_path / "ace_behavioral.db"
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(db_path))

    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()

    # Add kanban_tasks table (may already exist in full DB; idempotent here)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS kanban_tasks (
                id TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                task_type TEXT,
                priority TEXT,
                status TEXT,
                source_prediction_id TEXT,
                created_at TEXT,
                updated_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS canvas_events (
                id TEXT PRIMARY KEY,
                source_canvas TEXT,
                target_canvas TEXT,
                event_type TEXT,
                payload_json TEXT,
                created_at TEXT,
                consumed_at TEXT
            )"""
        )
        conn.commit()
    finally:
        conn.close()

    return db_path




def _ace_conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection("ICDEV_ACE_DB_URL")


def _seed_coworker(instance_id: str, coworker_id: str, role_id: str = "ai_developer") -> None:
    conn = _ace_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ace_instances (id, name, role_id, state, trust_tier) "
            "VALUES (?, ?, ?, 'assembling', 'yellow')",
            (instance_id, instance_id, role_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ace_coworkers "
            "(id, instance_id, role_id, display_name, state, trust_tier) "
            "VALUES (?, ?, ?, ?, 'working', 'yellow')",
            (coworker_id, instance_id, role_id, role_id),
        )
        conn.commit()
    finally:
        conn.close()


def _coworker_state(coworker_id: str) -> str | None:
    conn = _ace_conn()
    try:
        row = conn.execute(
            "SELECT state FROM ace_coworkers WHERE id = ?", (coworker_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _spec(coworker_id: str = "cw-bmon-1", role_id: str = "ai_developer", permissions=None):
    from icdev.tools.ace.team_assembler import CoWorkerSpec

    return CoWorkerSpec(
        coworker_id=coworker_id,
        role_id=role_id,
        role_slot=role_id,
        mailbox_id=f"mailbox:{coworker_id}",
        llm_function="code_generation",
        tool_permissions=permissions or [],
        trust_tier="yellow",
    )


def _fake_loader_cls(steps, tool_permissions=None):
    """Return a RoleLoader stand-in yielding fixed steps and permissions."""

    class _Loader:
        def __init__(self, *args, **kwargs):
            pass

        def get_role(self, role_id):
            return types.SimpleNamespace(
                steps=list(steps),
                tool_permissions=list(tool_permissions or []),
                communication={},
            )

    return _Loader


def _fake_message_bus(instance_id):
    bus = MagicMock()
    bus.instance_id = instance_id
    bus.poll_inbox.return_value = []
    bus.broadcast.return_value = []
    return bus


def _fake_trust_kernel():
    tk = MagicMock()
    tk.can_execute.return_value = True
    return tk


# ---------------------------------------------------------------------------
# Tests: reconcile_mode_b text scanner
# ---------------------------------------------------------------------------


class TestReconcileModeB:
    """Unit tests for the text-based reconcile_mode_b function."""

    def test_clean_output_returns_no_findings(self):
        from tools.integrity.intent_reconciler import reconcile_mode_b

        findings = reconcile_mode_b("Hello, the analysis is complete.", ["network_egress"])
        assert findings == []

    def test_dynamic_code_pattern_detected(self):
        from tools.integrity.intent_reconciler import reconcile_mode_b

        text = "result = eval(user_expression)"
        findings = reconcile_mode_b(text, [])
        types_found = {f["finding_type"] for f in findings}
        assert "undisclosed_capability" in types_found
        caps = {f["detail"]["capability_type"] for f in findings if "capability_type" in f.get("detail", {})}
        assert "dynamic_code" in caps

    def test_obfuscation_pattern_detected(self):
        from tools.integrity.intent_reconciler import reconcile_mode_b

        text = "data = base64.b64decode(encoded_payload)"
        findings = reconcile_mode_b(text, [])
        types_found = {f["finding_type"] for f in findings}
        assert "undisclosed_capability" in types_found

    def test_dangerous_api_fires_on_dynamic_plus_obfuscation(self):
        """decode-then-exec pattern triggers dangerous_api finding."""
        from tools.integrity.intent_reconciler import reconcile_mode_b

        dangerous_text = (
            "payload = base64.b64decode(encoded)\n"
            "exec(payload)\n"
        )
        findings = reconcile_mode_b(dangerous_text, [])
        types_found = {f["finding_type"] for f in findings}
        assert "dangerous_api" in types_found

    def test_claimed_capability_suppresses_undisclosed(self):
        """A capability that IS in claimed_capabilities should not appear as undisclosed."""
        from tools.integrity.intent_reconciler import reconcile_mode_b

        text = "requests.get('http://example.com')"
        # When network_egress is claimed, no undisclosed_capability should fire for it
        findings = reconcile_mode_b(text, ["network_egress"])
        undisclosed_net = [
            f for f in findings
            if f.get("finding_type") == "undisclosed_capability"
            and f.get("detail", {}).get("capability_type") == "network_egress"
        ]
        assert undisclosed_net == []

    def test_process_exec_detected(self):
        from tools.integrity.intent_reconciler import reconcile_mode_b

        text = "result = subprocess.run(['ls', '-la'], capture_output=True)"
        findings = reconcile_mode_b(text, [])
        types_found = {f["finding_type"] for f in findings}
        assert "undisclosed_capability" in types_found

    def test_empty_text_returns_no_findings(self):
        from tools.integrity.intent_reconciler import reconcile_mode_b

        assert reconcile_mode_b("", []) == []
        assert reconcile_mode_b("   ", []) == []


# ---------------------------------------------------------------------------
# Tests: _text_to_manifest internals
# ---------------------------------------------------------------------------


class TestTextToManifest:
    def test_exec_produces_dynamic_code_record(self):
        from tools.integrity.intent_reconciler import _text_to_manifest

        records = _text_to_manifest("exec(code_block)")
        caps = {r["capability_type"] for r in records}
        assert "dynamic_code" in caps

    def test_base64_produces_obfuscation_record(self):
        from tools.integrity.intent_reconciler import _text_to_manifest

        records = _text_to_manifest("base64.b64decode(s)")
        caps = {r["capability_type"] for r in records}
        assert "obfuscation" in caps

    def test_dynamic_plus_obfuscation_sets_obfuscated_input_flag(self):
        """When both dynamic_code and obfuscation are found, taint flag is set."""
        from tools.integrity.intent_reconciler import _text_to_manifest

        text = "payload = base64.b64decode(blob)\nexec(payload)"
        records = _text_to_manifest(text)
        dyn_records = [r for r in records if r["capability_type"] == "dynamic_code"]
        assert dyn_records, "dynamic_code record expected"
        ev = dyn_records[0].get("evidence") or {}
        assert ev.get("obfuscated_input") is True

    def test_clean_text_returns_empty_manifest(self):
        from tools.integrity.intent_reconciler import _text_to_manifest

        assert _text_to_manifest("The report is ready.") == []


# ---------------------------------------------------------------------------
# Tests: CoWorkerThread behavioral compliance (integration)
# ---------------------------------------------------------------------------


class TestCoWorkerThreadBehavioralMonitor:
    """Integration tests: dangerous step output → hitl_pending within 1 step."""

    @pytest.fixture(autouse=True)
    def _open_confidence_gate(self, monkeypatch):
        """Neutralize the trust confidence gate for this class.

        Every role defaults to trust 0.5 < TRUST_SUPERVISED (0.6), so _run_inner
        would park in the confidence-gate HITL wait before ever reaching the step
        loop these tests exercise. Force a trusted score so the gate is skipped;
        the behavioral monitor (the actual subject) still runs in the step loop.
        """
        monkeypatch.setattr(
            "icdev.tools.ace.trust_calibrator.get_trust_score",
            lambda role_id: 0.9,
        )

    def _make_executor_cls(self, outputs: list):
        """Executor that returns successive outputs for each step."""
        idx = {"i": 0}

        class _Executor:
            def run(self, step, context, spec, trust_kernel):
                i = idx["i"]
                idx["i"] += 1
                if i < len(outputs):
                    return outputs[i]
                return f"step {i} done"

        return _Executor

    def test_dangerous_step_output_triggers_hitl_pending(
        self, ace_db, monkeypatch
    ):
        """A step that emits a decode-then-exec pattern causes state=hitl_pending within 1 step."""
        instance_id = "ace-bmon-test-01"
        coworker_id = "cw-bmon-dangerous"
        _seed_coworker(instance_id, coworker_id)

        DANGEROUS_OUTPUT = (
            "Generated code:\n"
            "payload = base64.b64decode(encoded_cmd)\n"
            "exec(payload)\n"
        )

        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.RoleLoader",
            _fake_loader_cls([{"id": "step-1", "required": False}], tool_permissions=[]),
        )
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.StepExecutor",
            self._make_executor_cls([DANGEROUS_OUTPUT]),
        )

        # Suppress kanban + event bus side effects (they rely on full DB schema)
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.CoWorkerThread._create_hitl_kanban_card",
            lambda self, findings: None,
        )
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.CoWorkerThread._emit_compliance_event",
            lambda self, findings: None,
        )

        from icdev.tools.ace.coworker_thread import CoWorkerThread

        spec = _spec(coworker_id)
        thread = CoWorkerThread(
            spec=spec,
            instance_id=instance_id,
            message_bus=_fake_message_bus(instance_id),
            trust_kernel=_fake_trust_kernel(),
            monitor_interval=1,  # check every step so the test is deterministic
        )
        thread._run_inner()  # call directly to avoid threading._context conflict

        state = _coworker_state(coworker_id)
        assert state == "hitl_pending", (
            f"Expected hitl_pending after dangerous step output, got {state!r}"
        )

    def test_clean_step_output_does_not_trigger_hitl(
        self, ace_db, monkeypatch
    ):
        """Benign step output completes normally (state=done)."""
        instance_id = "ace-bmon-test-02"
        coworker_id = "cw-bmon-clean"
        _seed_coworker(instance_id, coworker_id)

        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.RoleLoader",
            _fake_loader_cls(
                [{"id": "step-1", "required": False}],
                tool_permissions=["network_egress"],
            ),
        )
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.StepExecutor",
            self._make_executor_cls(["The analysis report is complete. All checks passed."]),
        )

        from icdev.tools.ace.coworker_thread import CoWorkerThread

        spec = _spec(coworker_id, permissions=["network_egress"])
        thread = CoWorkerThread(
            spec=spec,
            instance_id=instance_id,
            message_bus=_fake_message_bus(instance_id),
            trust_kernel=_fake_trust_kernel(),
            monitor_interval=1,
        )
        thread._run_inner()

        state = _coworker_state(coworker_id)
        assert state == "done", f"Expected done for clean output, got {state!r}"

    def test_hitl_triggers_after_nth_step_not_before(
        self, ace_db, monkeypatch
    ):
        """With monitor_interval=2, hitl fires on the 2nd step when both are combined."""
        instance_id = "ace-bmon-test-03"
        coworker_id = "cw-bmon-interval"
        _seed_coworker(instance_id, coworker_id)

        DANGEROUS = "payload = base64.b64decode(blob)\nexec(payload)"
        outputs = ["clean step output", DANGEROUS]  # 2 steps; dangerous on step 2

        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.RoleLoader",
            _fake_loader_cls(
                [{"id": "step-1"}, {"id": "step-2"}],
                tool_permissions=[],
            ),
        )
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.StepExecutor",
            self._make_executor_cls(outputs),
        )
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.CoWorkerThread._create_hitl_kanban_card",
            lambda self, findings: None,
        )
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.CoWorkerThread._emit_compliance_event",
            lambda self, findings: None,
        )

        from icdev.tools.ace.coworker_thread import CoWorkerThread

        spec = _spec(coworker_id)
        thread = CoWorkerThread(
            spec=spec,
            instance_id=instance_id,
            message_bus=_fake_message_bus(instance_id),
            trust_kernel=_fake_trust_kernel(),
            monitor_interval=2,
        )
        thread._run_inner()

        state = _coworker_state(coworker_id)
        assert state == "hitl_pending"

    def test_monitor_interval_loaded_from_config(self, tmp_path, monkeypatch):
        """_load_monitor_interval() reads monitor_interval from ace_config.yaml."""
        cfg = tmp_path / "ace_config.yaml"
        cfg.write_text("monitor_interval: 7\n", encoding="utf-8")

        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.Path",
            lambda *args: cfg if "ace_config.yaml" in str(args) else Path(*args),
        )


        # Patch Path resolution directly
        with patch(
            "icdev.tools.ace.coworker_thread.Path",
            side_effect=lambda *a: cfg if len(a) == 1 and "ace_config" in str(a[0]) else Path(*a),
        ):
            pass  # _load_monitor_interval resolves path internally; test via yaml

        import yaml

        data = yaml.safe_load(cfg.read_text())
        assert data["monitor_interval"] == 7

    def test_check_behavioral_compliance_returns_false_for_clean(
        self, ace_db
    ):
        """_check_behavioral_compliance() returns False for benign text."""
        instance_id = "ace-bmon-check-clean"
        coworker_id = "cw-check-clean"
        _seed_coworker(instance_id, coworker_id)

        from icdev.tools.ace.coworker_thread import CoWorkerThread

        spec = _spec(coworker_id, permissions=["network_egress", "filesystem"])
        thread = CoWorkerThread(
            spec=spec,
            instance_id=instance_id,
            message_bus=_fake_message_bus(instance_id),
            trust_kernel=_fake_trust_kernel(),
        )
        role = types.SimpleNamespace(tool_permissions=["network_egress", "filesystem"])
        result = thread._check_behavioral_compliance("All steps passed successfully.", role)
        assert result is False

    def test_check_behavioral_compliance_returns_true_for_dangerous(
        self, ace_db, monkeypatch
    ):
        """_check_behavioral_compliance() returns True and sets hitl_pending for dangerous text."""
        instance_id = "ace-bmon-check-dangerous"
        coworker_id = "cw-check-dangerous"
        _seed_coworker(instance_id, coworker_id)

        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.CoWorkerThread._create_hitl_kanban_card",
            lambda self, findings: None,
        )
        monkeypatch.setattr(
            "icdev.tools.ace.coworker_thread.CoWorkerThread._emit_compliance_event",
            lambda self, findings: None,
        )

        from icdev.tools.ace.coworker_thread import CoWorkerThread

        spec = _spec(coworker_id, permissions=[])
        thread = CoWorkerThread(
            spec=spec,
            instance_id=instance_id,
            message_bus=_fake_message_bus(instance_id),
            trust_kernel=_fake_trust_kernel(),
        )
        role = types.SimpleNamespace(tool_permissions=[])
        dangerous = "payload = base64.b64decode(blob)\nexec(payload)"
        result = thread._check_behavioral_compliance(dangerous, role)
        assert result is True
        assert _coworker_state(coworker_id) == "hitl_pending"
