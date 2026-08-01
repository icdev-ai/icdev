# CUI // SP-CTI
"""Smoke tests for the riskiest ZIG pillar orchestrators + remediation engine.

Scope (shx-test-07): wiring / regression coverage, NOT deep behaviour. Each
test imports the module cleanly and invokes its primary public entry point
against a *scratch* SQLite database, asserting a sane output SHAPE and that no
exception is raised on (a) an empty freshly-initialized DB and (b) one minimal
seeded input row.

Why these three orchestrators (most enforcement surface of the eight
``tools/security_canvas/*_orchestrator.py`` drivers):

  * ``device_pillar_orchestrator``  -- imports SIX enforcement engines
    (mdm/edr/compliance-scanner/attestation/nac/ztna), seeds a managed fleet,
    and runs per-host loops that call ``nac.evaluate_access`` + attestation
    token generation. Largest write + engine footprint.
  * ``user_pillar_orchestrator``    -- imports FOUR enforcement engines
    (mfa/pam/user-risk/identity-governance) and enrolls an account roster.
  * ``app_pillar_orchestrator``     -- imports THREE enforcement engines
    (app-access/dast-gates/continuous-authorization) AND writes activity-
    completion rows (``zig_activity_tracker.set_activity_status``) plus a
    per-application ``aac.evaluate_access`` decision-seeding loop.

The base ``zig_activities`` / ``zig_activity_completions`` tables are created by
the canonical ``db.init_db.init_db()`` initializer; the per-engine tables are
created lazily by each module's ``_ensure_tables`` (idempotency proven by
``tests/test_sdc_lazy_tables.py``). This suite drives the two together.

``remediation.py`` is a pure-function engine (no DB, no LLM, no network); its
primary public entry point is ``generate_remediation_plan``.
"""
from __future__ import annotations

import socket
import tempfile
import uuid
from pathlib import Path

import pytest

from tools.security_canvas import remediation
from tools.security_canvas.db import init_db
from tools.security_canvas import (
    app_pillar_orchestrator,
    device_pillar_orchestrator,
    user_pillar_orchestrator,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def sdc_scratch_db(monkeypatch):
    """Point the SDC engines at a throwaway SQLite DB and initialize its schema.

    Patches ``init_db.DB_PATH`` + ``init_db._SC_BACKEND`` (both read at call time
    by ``get_connection``) so every ``get_connection()`` in every pillar-engine
    module resolves to the scratch file, then runs the canonical initializer to
    create + seed the base ``zig_*`` tables.
    """
    scratch = Path(tempfile.gettempdir()) / f"sdc_smoke_{uuid.uuid4().hex[:12]}.db"
    if scratch.exists():
        scratch.unlink()
    monkeypatch.setattr(init_db, "DB_PATH", scratch)
    monkeypatch.setattr(init_db, "_SC_BACKEND", "sqlite")
    monkeypatch.setenv("SC_STORAGE_BACKEND", "sqlite")
    init_db.init_db()
    try:
        yield scratch
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(scratch) + suffix)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Fail loudly if any orchestrator attempts network egress.

    The modules are expected to be fully local (SQLite only, no LLM/HTTP). This
    guard turns any outbound ``socket.connect`` into an assertion failure so a
    future egress regression cannot pass silently.
    """
    real_connect = socket.socket.connect

    def _no_egress(self, address, *args, **kwargs):  # noqa: ANN001
        raise AssertionError(f"unexpected network egress attempt to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_egress)
    yield
    monkeypatch.setattr(socket.socket, "connect", real_connect)


def _assert_assessment_shape(result: dict) -> None:
    """Every orchestrator returns a dict carrying an ``assessment`` sub-dict."""
    assert isinstance(result, dict)
    assert "assessment" in result, f"missing 'assessment' key: {sorted(result)}"
    assessment = result["assessment"]
    assert isinstance(assessment, dict)
    assert "aggregate_score" in assessment
    # aggregate_score is a rounded percentage float (or None if no pillars).
    assert assessment["aggregate_score"] is None or isinstance(
        assessment["aggregate_score"], (int, float)
    )


# ---------------------------------------------------------------------------
# Device pillar orchestrator -- 6 enforcement engines
# ---------------------------------------------------------------------------
_DEVICE_KEYS = {"mdm", "edr", "compliance", "attestation", "nac", "ztna", "assessment"}


def test_device_orchestrator_empty_db(sdc_scratch_db):
    """deploy_all() with the default fleet against a freshly-initialized DB."""
    result = device_pillar_orchestrator.deploy_all()
    assert _DEVICE_KEYS.issubset(result.keys()), sorted(result)
    _assert_assessment_shape(result)
    # Attestation sub-result reports counts as ints.
    assert isinstance(result["attestation"], dict)
    assert isinstance(result["attestation"].get("issued"), int)


def test_device_orchestrator_minimal_seeded_fleet(sdc_scratch_db):
    """deploy_all() with one trivially-constructible managed device."""
    result = device_pillar_orchestrator.deploy_all(
        [{"hostname": "smoke-host-01", "os_platform": "linux"}]
    )
    assert _DEVICE_KEYS.issubset(result.keys()), sorted(result)
    _assert_assessment_shape(result)
    assert result["attestation"]["issued"] == 1


# ---------------------------------------------------------------------------
# User pillar orchestrator -- 4 enforcement engines
# ---------------------------------------------------------------------------
_USER_KEYS = {"mfa", "pam", "risk", "governance", "assessment"}


def test_user_orchestrator_empty_db(sdc_scratch_db):
    """deploy_all() with the default account roster."""
    result = user_pillar_orchestrator.deploy_all()
    assert _USER_KEYS.issubset(result.keys()), sorted(result)
    _assert_assessment_shape(result)
    assert isinstance(result["mfa"], dict)


def test_user_orchestrator_minimal_seeded_roster(sdc_scratch_db):
    """deploy_all() with one trivially-constructible standard account."""
    result = user_pillar_orchestrator.deploy_all(
        [{"username": "smoke-user-01", "account_class": "standard", "authenticator": "totp"}]
    )
    assert _USER_KEYS.issubset(result.keys()), sorted(result)
    _assert_assessment_shape(result)


# ---------------------------------------------------------------------------
# Application pillar orchestrator -- 3 engines + activity/decision writes
# ---------------------------------------------------------------------------
_APP_KEYS = {"dast", "access_control", "continuous_ato", "assessment"}


def test_app_orchestrator_empty_db(sdc_scratch_db):
    """deploy_all() with the default application set."""
    result = app_pillar_orchestrator.deploy_all()
    assert _APP_KEYS.issubset(result.keys()), sorted(result)
    _assert_assessment_shape(result)


def test_app_orchestrator_minimal_seeded_app(sdc_scratch_db):
    """deploy_all() with one trivially-constructible application."""
    result = app_pillar_orchestrator.deploy_all(["smoke-app"])
    assert _APP_KEYS.issubset(result.keys()), sorted(result)
    _assert_assessment_shape(result)


# ---------------------------------------------------------------------------
# Remediation engine -- pure functions (no DB / LLM / network)
# ---------------------------------------------------------------------------
def test_remediation_plan_empty_findings():
    """generate_remediation_plan with no findings -> empty, well-formed plan."""
    plan = remediation.generate_remediation_plan({"findings": []}, {})
    assert isinstance(plan, dict)
    for key in ("phases", "summary", "total_actions", "overall_risk", "estimated_effort"):
        assert key in plan, f"missing '{key}': {sorted(plan)}"
    assert plan["phases"] == []
    assert plan["total_actions"] == 0
    assert isinstance(plan["summary"], str)


def test_remediation_plan_one_seeded_finding():
    """generate_remediation_plan with one minimal CAT1 finding -> one action."""
    assessment = {
        "design_id": "smoke-design",
        "findings": [
            {
                "rule_id": "siem_present",
                "severity": "CAT1",
                "title": "SIEM not deployed",
                "affected_entity": "design",
            }
        ],
        "_rules": [],
        "risk_score": 10,
        "posture_grade": "F",
    }
    plan = remediation.generate_remediation_plan(assessment, {})
    assert plan["total_actions"] == 1
    assert plan["phases"], "expected at least one non-empty phase"
    action = plan["phases"][0]["actions"][0]
    for key in ("id", "finding_id", "severity", "remediation_step", "effort_hours", "status"):
        assert key in action, f"missing action key '{key}': {sorted(action)}"
    assert action["severity"] == "CAT1"


def test_estimate_effort_shapes():
    """estimate_effort returns a human-readable string across magnitudes."""
    assert remediation.estimate_effort([]) == "0 hours"
    assert remediation.estimate_effort([{"effort_hours": 4}]) == "4 hours"
    # 8h -> days branch, 200h -> weeks branch; assert type + unit, not exact value.
    assert remediation.estimate_effort([{"effort_hours": 8}]).endswith("days")
    assert remediation.estimate_effort([{"effort_hours": 200}]).endswith("weeks")


def test_generate_poam_entries_shape():
    """generate_poam_entries maps a phase's actions to OMB POA&M entries.

    ``generate_remediation_plan`` emits ``phases`` as a LIST of phase dicts,
    which is the shape every consumer (POA&M/SAR artifact generators, blueprint
    export routes) reads. This test asserts the POA&M entry shape via that same
    LIST contract, so the mapping stays covered. The end-to-end chaining of the
    two functions is verified by ``test_generate_poam_entries_chained_end_to_end``.
    """
    list_shaped_plan = {
        "phases": [
            {
                "phase": 1,
                "name": "Phase 1: Critical (0-48h)",
                "deadline": "2026-01-01T00:00:00+00:00",
                "actions": [
                    {
                        "finding_id": "siem_present",
                        "title": "SIEM not deployed",
                        "remediation_step": "Deploy a SIEM.",
                        "effort_hours": 24,
                        "auto_fixable": False,
                        "severity": "CAT1",
                        "affected_entity": "design",
                    }
                ],
            }
        ]
    }
    entries = remediation.generate_poam_entries(list_shaped_plan)
    assert isinstance(entries, list)
    assert len(entries) == 1
    entry = entries[0]
    for key in ("poam_id", "weakness_id", "weakness_name", "severity", "status", "risk_level"):
        assert key in entry, f"missing POA&M key '{key}': {sorted(entry)}"
    assert entry["risk_level"] == "High"  # CAT1 -> High
    assert entry["milestone"].startswith("Phase 1:")

    # Empty plan -> empty entry list (no exception), for both list and dict defaults.
    assert remediation.generate_poam_entries({"phases": []}) == []
    assert remediation.generate_poam_entries({}) == []


def test_generate_poam_entries_chained_end_to_end():
    """generate_poam_entries(generate_remediation_plan(...)) works end-to-end.

    Reconciled contract (shx-hyg-06): ``generate_remediation_plan`` emits a LIST
    of phases and ``generate_poam_entries`` consumes that same LIST shape, so the
    two functions chain without raising. Regression guard against the prior
    list/dict mismatch (AttributeError on ``phases.items()``).
    """
    assessment = {
        "design_id": "smoke-design",
        "findings": [
            {
                "rule_id": "siem_present",
                "severity": "CAT1",
                "title": "SIEM not deployed",
                "affected_entity": "design",
            },
            {
                "rule_id": "encryption_at_rest",
                "severity": "CAT2",
                "title": "Encryption at rest missing",
                "affected_entity": "datastore",
            },
        ],
        "_rules": [],
        "risk_score": 10,
        "posture_grade": "F",
    }

    plan = remediation.generate_remediation_plan(assessment, {})
    assert isinstance(plan["phases"], list)

    entries = remediation.generate_poam_entries(plan)
    assert isinstance(entries, list)
    # One POA&M entry per action across all phases.
    assert len(entries) == plan["total_actions"] == 2
    poam_ids = [e["poam_id"] for e in entries]
    assert poam_ids == ["POAM-0001", "POAM-0002"]
    severities = {e["severity"] for e in entries}
    assert severities == {"CAT1", "CAT2"}
    for entry in entries:
        for key in ("poam_id", "weakness_id", "milestone", "scheduled_completion", "status"):
            assert key in entry, f"missing POA&M key '{key}': {sorted(entry)}"
