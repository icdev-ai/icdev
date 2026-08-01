# CUI // SP-CTI
"""Scope-locked app red team (oss-redteam-01/02).

Two things are load-bearing and tested as such: the scope-lock has no bypass,
and a raised probe is a LEAD — never a finding — until oss-poc-01 confirms it
with a discriminating reproduction.
"""
from __future__ import annotations

import pytest

from tools.security import app_red_team as art
from tools.security.redteam_scope import (
    RedTeamScope,
    ScopeViolation,
    assert_in_scope,
    assert_no_sensitive_fields,
    public_summary,
    record_authorization,
)


@pytest.fixture
def loopback_scope():
    return RedTeamScope(allowed_hosts=("localhost", "127.0.0.1"), require_authorization=True)


# ── Scope-lock has no bypass (oss-redteam-02) ────────────────────────────────


def test_loopback_is_allowed(loopback_scope):
    assert assert_in_scope("http://localhost:5050/x", loopback_scope) == "localhost:5050"


@pytest.mark.parametrize("url", [
    "https://production.example.gov/admin",
    "http://10.0.0.5/internal",
    "https://a-customer.example.com",
])
def test_non_allowlisted_host_is_refused_outright(url, loopback_scope):
    """No warn-and-continue path — a warning that can be ignored is not a control."""
    with pytest.raises(ScopeViolation):
        assert_in_scope(url, loopback_scope)


def test_a_url_with_no_host_is_refused(loopback_scope):
    with pytest.raises(ScopeViolation):
        assert_in_scope("not-a-url", loopback_scope)


def test_non_loopback_needs_a_written_authorization(tmp_path, monkeypatch):
    """Owning the host is not enough; someone must have recorded the decision."""
    from tools.security import redteam_scope

    monkeypatch.setattr(redteam_scope, "_AUTH_DIR", tmp_path / "auth")
    scope = RedTeamScope(allowed_hosts=("staging.internal",), require_authorization=True)

    with pytest.raises(ScopeViolation, match="no written authorization"):
        assert_in_scope("https://staging.internal/x", scope)

    record_authorization("staging.internal", authorized_by="secteam", reason="scheduled self-test")
    assert assert_in_scope("https://staging.internal/x", scope) == "staging.internal"


def test_authorization_requires_who_and_why(tmp_path, monkeypatch):
    from tools.security import redteam_scope

    monkeypatch.setattr(redteam_scope, "_AUTH_DIR", tmp_path / "auth")
    with pytest.raises(ValueError):
        record_authorization("h", authorized_by="", reason="x")
    with pytest.raises(ValueError):
        record_authorization("h", authorized_by="x", reason="")


def test_broken_scope_config_fails_to_loopback_only(tmp_path, monkeypatch):
    from tools.security import redteam_scope

    bad = tmp_path / "redteam_scope.yaml"
    bad.write_text("redteam_scope: [not: valid: yaml", encoding="utf-8")
    scope = redteam_scope.load_scope(bad)
    assert scope.allowed_hosts == redteam_scope.DEFAULT_ALLOWED_HOSTS


def test_run_refuses_before_any_probe_executes(loopback_scope):
    """The scope check is the choke point — it bites before the observer runs."""
    called = {"n": 0}

    def observer(probe):
        called["n"] += 1
        return {"status": 200}

    with pytest.raises(ScopeViolation):
        art.run("https://evil.test", observer=observer, scope=loopback_scope)
    assert called["n"] == 0, "an observation ran against a refused target"


# ── Detectors fire on the seeded defects ─────────────────────────────────────


def test_authz_probe_fires_when_a_privileged_route_serves_anon(loopback_scope):
    probes = [p for p in art.load_catalog() if p.id == "authz-anon-privileged-route"]
    result = art.run(
        "http://localhost:5050",
        observer=lambda p: {"status": 200},          # served — the defect
        catalog=probes,
        scope=loopback_scope,
    )
    assert len(result["leads"]) == 1


def test_authz_probe_is_silent_when_route_refuses_anon(loopback_scope):
    probes = [p for p in art.load_catalog() if p.id == "authz-anon-privileged-route"]
    result = art.run(
        "http://localhost:5050",
        observer=lambda p: {"status": 403},          # correctly refused
        catalog=probes,
        scope=loopback_scope,
    )
    assert result["leads"] == []


def test_tenant_crossing_detector_fires_on_foreign_rows():
    probe = next(p for p in art.load_catalog() if p.family == "tenant_isolation")
    leaked = {"status": 200, "tenant": "acme", "rows": [{"tenant": "acme"}, {"tenant": "globex"}]}
    clean = {"status": 200, "tenant": "acme", "rows": [{"tenant": "acme"}]}
    assert art.evaluate(probe, leaked) is True
    assert art.evaluate(probe, clean) is False


def test_classification_detector_fires_on_read_up():
    probe = next(p for p in art.load_catalog() if p.family == "classification")
    up = {"clearance": "CUI", "rows": [{"classification": "SECRET"}]}
    ok = {"clearance": "CUI", "rows": [{"classification": "CUI"}]}
    assert art.evaluate(probe, up) is True
    assert art.evaluate(probe, ok) is False


# ── Leads are not findings — the oss-poc-01 seam ─────────────────────────────


def test_a_raised_probe_is_a_lead_not_a_confirmed_finding(loopback_scope):
    """The naming is the guardrail: run() produces leads, never findings."""
    result = art.run(
        "http://localhost:5050",
        observer=lambda p: {"status": 200},
        catalog=[p for p in art.load_catalog() if p.severity == "high"][:1],
        scope=loopback_scope,
    )
    assert "leads" in result
    assert all(isinstance(x, art.ProbeLead) for x in result["leads"])
    # nothing in the result claims a confirmed/gate-blocking finding
    assert "confirmed" not in result


def test_a_lead_only_becomes_a_finding_through_discriminating_replay(loopback_scope):
    """End to end with oss-poc-01: lead -> discriminating replay -> finding.

    Skips until oss-poc-01 (PR #840) is on main — the red team does not depend
    on reproduction.py at import time, only this cross-check does.
    """
    repro = pytest.importorskip(
        "tools.security.reproduction",
        reason="oss-poc-01 (reproduction.py) not merged to main yet",
    )
    Finding, Reproduction, verify_discriminates = (
        repro.Finding, repro.Reproduction, repro.verify_discriminates
    )

    result = art.run(
        "http://localhost:5050",
        observer=lambda p: {"status": 200},
        catalog=[p for p in art.load_catalog() if p.id == "authz-anon-privileged-route"],
        scope=loopback_scope,
    )
    lead = result["leads"][0]

    state = {"fixed": False}
    verdict = verify_discriminates(
        replay=lambda: not state["fixed"],           # fires until fixed
        apply_fix=lambda: state.update(fixed=True),
    )
    finding = Finding(
        title=lead.family,
        severity=lead.severity,
        reproduction=Reproduction(kind="http", steps=[{"probe": lead.probe_id}],
                                  expectation=lead.detail),
    )
    assert finding.confirm(verdict["discriminates"]) == "confirmed"
    assert finding.blocks_gate is True


def test_gate_exit_is_one_on_a_high_lead(loopback_scope):
    result = art.run(
        "http://localhost:5050",
        observer=lambda p: {"status": 200},
        catalog=[p for p in art.load_catalog() if p.severity == "high"][:1],
        scope=loopback_scope,
    )
    assert art.gate_exit_code(result) == 1


def test_gate_exit_is_zero_with_no_high_leads(loopback_scope):
    result = art.run(
        "http://localhost:5050",
        observer=lambda p: {"status": 403},
        scope=loopback_scope,
    )
    assert art.gate_exit_code(result) == 0


# ── Public-repo redaction (oss-redteam-02) ───────────────────────────────────


def test_public_summary_carries_counts_not_locations(loopback_scope):
    result = art.run(
        "http://localhost:5050",
        observer=lambda p: {"status": 200},          # everything fires
        scope=loopback_scope,
    )
    summary = result["public_summary"]
    # Check the DATA carries no locators, not the human-readable note (which
    # legitimately says the word "payloads" while explaining that they are
    # withheld). Serialise everything except that note.
    checked = {k: v for k, v in summary.items() if k != "note"}
    blob = str(checked).lower()
    for forbidden in ("localhost:5050", "expectation", "/admin", "' or 1=1"):
        assert forbidden not in blob, f"public summary leaked {forbidden!r}"
    assert summary["total_findings"] >= 1


def test_lead_public_row_omits_the_detail():
    lead = art.ProbeLead(
        probe_id="x", family="idor", owasp="A01", severity="high", failed=True,
        detail="GET /api/canvas/42 owned by tenant globex returned 200",
    )
    row = lead.public_row()
    assert "detail" not in row
    assert "42" not in str(row)
    assert row["severity"] == "high"


def test_redaction_guard_blocks_a_serialized_finding():
    import json

    finding = json.dumps({"payload": "' OR 1=1--", "path": "/api/x", "severity": "high"})
    with pytest.raises(ValueError, match="public surface"):
        assert_no_sensitive_fields(finding)


def test_redaction_guard_allows_a_clean_summary():
    import json

    assert_no_sensitive_fields(json.dumps(public_summary([{"severity": "high"}])))
