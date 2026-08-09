# CUI // SP-CTI
"""hgx-obs-02: ORANGE-tier reflexes must produce a reviewable artifact.

``GenesisDaemon._run_reflex_impl_inner`` used to do this::

    if trust.requires_human_approval(risk_tier):
        return True, 0.0, {"status": "awaiting_human_approval", ...}

**before** the reflex module was ever imported.  So ``evolve`` and
``experiment`` — the only two ORANGE reflexes — never executed their mutation
code at all.  They produced nothing, and "produced nothing" reads identically to
"ran and found nothing" on the dashboard and in ``genesis_audit``.

The early return was also self-defeating: both reflexes are propose-only by
construction.  ``evolve`` ends at ``_export_mutation_proposal`` and returns
``status: proposed_for_human_review``; ``experiment`` exports GKPs for
promotion.  Neither merges anything on its own.  The guard was blocking exactly
the artifact the ORANGE tier exists to produce.

The decision implemented and tested here: an ORANGE reflex **runs**, behind a
proposal-mode config overlay, and its outcome is staged as an
``orange_proposal`` GKP at ``pending_review`` — on the existing Genesis staging
surface (``genesis_gkp`` + ``tools/genesis/promoter.py``), not a new one.
``orange_proposal`` is absent from ``promoter.auto_promote`` and listed under
``human_approve``, so ``auto_promote_eligible()`` never matches it — a human must
act on it.  It does have an ``_import_to_v1x`` handler, but that handler writes
to no v1.x store; it exists only so a reviewer's Promote click is audited rather
than erroring.  Both halves of that invariant are asserted below.
"""
from __future__ import annotations

import contextlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.daemon.base import parse_schedule  # noqa: E402
from tools.genesis.daemon import GenesisDaemon  # noqa: E402

# The two ORANGE reflexes the daemon actually ships.
ORANGE_REFLEXES = ("evolve", "experiment")


@pytest.fixture
def daemon() -> GenesisDaemon:
    """A daemon with an empty config — __init__ touches no DB."""
    return GenesisDaemon({})


@pytest.fixture
def staged(monkeypatch):
    """Capture export_gkp calls; stub the DB-touching reflex connection scope."""
    from tools.genesis import daemon as daemon_mod
    from tools.genesis import promoter as promoter_mod

    captured: list[dict] = []

    def fake_export_gkp(reflex, artifact_type, payload, confidence=0.0, evidence=None):
        captured.append(
            {
                "reflex": reflex,
                "artifact_type": artifact_type,
                "payload": payload,
                "confidence": confidence,
                "evidence": evidence,
            }
        )
        return {"status": "exported", "gkp_id": f"gkp-{len(captured)}"}

    # export_gkp is imported *inside* _stage_orange_gkp, so patching the module
    # attribute is what takes effect at call time.
    monkeypatch.setattr(promoter_mod, "export_gkp", fake_export_gkp)
    monkeypatch.setattr(daemon_mod, "reflex_connection_scope", contextlib.nullcontext)
    return captured


def _install_fake_reflex(monkeypatch, name: str, result: dict, calls: list) -> None:
    """Register a fake reflex module so importlib.import_module resolves it."""
    module = types.ModuleType(f"tools.genesis.reflexes.{name}")

    def run(config, trust):
        calls.append(config)
        return result

    module.run = run
    monkeypatch.setitem(sys.modules, f"tools.genesis.reflexes.{name}", module)


# ---------------------------------------------------------------------------
# The proposal-mode overlay
# ---------------------------------------------------------------------------
def test_proposal_overlay_forbids_applying_changes(daemon):
    """Defensive, not just documentation.

    The two ORANGE reflexes shipped today never merge, but a future one must not
    be able to apply a change merely because the daemon now runs it.
    """
    overlaid = daemon._orange_proposal_config(
        {"risk_tier": "orange", "max_files_per_cycle": 1}
    )

    assert overlaid["proposal_only"] is True
    assert overlaid["require_human_merge"] is True
    assert overlaid["auto_apply"] is False
    assert overlaid["dry_run"] is True, "proposal mode must default to dry_run"
    # Non-safety keys pass through untouched.
    assert overlaid["max_files_per_cycle"] == 1
    assert overlaid["risk_tier"] == "orange"


def test_proposal_overlay_does_not_clobber_explicit_dry_run(daemon):
    """setdefault, not overwrite — a reflex that must really run says so."""
    assert daemon._orange_proposal_config({"dry_run": False})["dry_run"] is False


def test_proposal_overlay_does_not_mutate_the_caller_config(daemon):
    """The daemon reuses the config dict across cycles; the overlay must copy."""
    original = {"risk_tier": "orange"}
    daemon._orange_proposal_config(original)
    assert original == {"risk_tier": "orange"}


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------
def test_orange_reflex_module_is_actually_invoked(daemon, monkeypatch, staged):
    """THE regression guard: run() is called, not skipped before import."""
    calls: list = []
    _install_fake_reflex(
        monkeypatch,
        "fake_orange_runs",
        {"success": True, "metric_value": 0.72, "details": {"target_file": "tools/x.py"}},
        calls,
    )

    success, metric, details = daemon._run_orange_proposal(
        "fake_orange_runs", {"risk_tier": "orange"}, trust=object(), risk_tier="orange"
    )

    assert calls, "ORANGE reflex module was never invoked — the early return is back"
    assert calls[0]["proposal_only"] is True, "module must run under the proposal overlay"
    assert success is True
    assert metric == pytest.approx(0.72)
    assert details["status"] == "proposal_staged"
    assert details["reflex_result"] == {"target_file": "tools/x.py"}
    # The human-review signal is preserved, not replaced.
    assert details["awaiting_human_approval"] is True


def test_orange_run_stages_a_reviewable_gkp(daemon, monkeypatch, staged):
    """Acceptance criterion: an ORANGE run leaves a reviewable artifact behind."""
    _install_fake_reflex(
        monkeypatch,
        "fake_orange_gkp",
        {"success": True, "metric_value": 0.6, "details": {"improvement": "extract helper"}},
        [],
    )

    _, _, details = daemon._run_orange_proposal(
        "fake_orange_gkp", {"risk_tier": "orange"}, trust=object(), risk_tier="orange"
    )

    assert len(staged) == 1, "exactly one proposal artifact should be staged"
    artifact = staged[0]
    assert artifact["artifact_type"] == "orange_proposal"
    assert artifact["reflex"] == "fake_orange_gkp"
    assert artifact["payload"]["proposal_mode"] is True
    assert artifact["payload"]["risk_tier"] == "orange"
    assert artifact["payload"]["details"] == {"improvement": "extract helper"}
    assert details["gkp_id"] == "gkp-1"


def test_failing_orange_reflex_still_stages_its_outcome(daemon, monkeypatch, staged):
    """A failed proposal is still reviewable evidence — and still reported failed."""
    _install_fake_reflex(
        monkeypatch,
        "fake_orange_fails",
        {"success": False, "metric_value": 0.0, "details": {"error": "tests failed"}},
        [],
    )

    success, _, details = daemon._run_orange_proposal(
        "fake_orange_fails", {"risk_tier": "orange"}, trust=object(), risk_tier="orange"
    )

    assert success is False, "a failed reflex must not be reported as a success"
    assert len(staged) == 1
    assert staged[0]["payload"]["reflex_success"] is False
    assert details["reflex_result"] == {"error": "tests failed"}


def test_dispatch_path_routes_orange_to_proposal_staging(daemon, monkeypatch):
    """_run_reflex_impl_inner must reach the proposal helper for an ORANGE tier."""
    seen: dict = {}

    def fake_proposal(name, config, trust, risk_tier):
        seen["name"] = name
        seen["risk_tier"] = risk_tier
        return True, 0.0, {"status": "proposal_staged"}

    class ApprovalTrust:
        def requires_human_approval(self, risk_tier):
            return True

    monkeypatch.setattr(daemon, "_run_orange_proposal", fake_proposal)
    success, _, details = daemon._run_reflex_impl_inner(
        "evolve", {"risk_tier": "orange"}, ApprovalTrust()
    )

    assert seen == {"name": "evolve", "risk_tier": "orange"}
    assert details["status"] == "proposal_staged"
    assert success is True


# ---------------------------------------------------------------------------
# Guardrails on the new path
# ---------------------------------------------------------------------------
def test_orange_proposal_is_a_registered_artifact_type():
    """export_gkp refuses unknown artifact types, so staging would silently no-op."""
    from tools.genesis.promoter import ARTIFACT_TYPES

    assert "orange_proposal" in ARTIFACT_TYPES


def test_acknowledging_an_orange_proposal_writes_to_no_knowledge_store():
    """Promoting an ``orange_proposal`` records a human decision, nothing more.

    The payload is the record of a run already performed in proposal mode, not a
    change to apply — whatever the reflex wants merged travels as its own GKP
    (``evolve`` exports a ``code_patch``) and is reviewed separately. The handler
    exists only so a reviewer's Promote click succeeds and is audited instead of
    erroring with "No import handler"; an artifact that can only ever be rejected
    is half a review surface.
    """
    from tools.genesis.promoter import _import_to_v1x

    result = _import_to_v1x("orange_proposal", {"reflex": "evolve"}, 1.0)
    assert result["success"] is True
    assert result["table"] is None, (
        "acknowledging an ORANGE proposal must not write into a v1.x knowledge store"
    )


def test_orange_proposal_can_never_auto_promote():
    """The real guarantee lives in config, not in a missing handler.

    ``auto_promote_eligible()`` matches pending GKPs on artifact_type / reflex /
    source. Keeping ``orange_proposal`` out of ``promoter.auto_promote`` — and
    naming it under ``human_approve`` — is what stops an ORANGE reflex from
    approving its own proposal. Enforcing this by leaving the import handler
    broken would be silently undone the day someone adds one.
    """
    import yaml

    config_path = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
    with open(config_path, "r", encoding="utf-8") as fh:
        promoter_cfg = (yaml.safe_load(fh) or {}).get("promoter", {})

    auto_rules = promoter_cfg.get("auto_promote", [])
    assert "orange_proposal" not in {r.get("artifact_type") for r in auto_rules}, (
        "orange_proposal is in promoter.auto_promote — an ORANGE reflex could approve itself"
    )
    # A rule keyed on the reflex name would match just as well as one keyed on
    # the artifact type, so the ORANGE reflexes must be absent from there too.
    auto_reflexes = {r.get("reflex") for r in auto_rules}
    assert not (set(ORANGE_REFLEXES) & auto_reflexes), (
        f"ORANGE reflex(es) in promoter.auto_promote: {sorted(set(ORANGE_REFLEXES) & auto_reflexes)}"
    )
    assert "orange_proposal" in {
        r.get("artifact_type") for r in promoter_cfg.get("human_approve", [])
    }


def test_kill_switch_restores_the_early_return(daemon, monkeypatch, staged):
    """Operators keep an escape hatch if proposal mode misbehaves in production."""
    calls: list = []
    _install_fake_reflex(monkeypatch, "fake_orange_off", {"success": True}, calls)
    monkeypatch.setenv("ICDEV_GENESIS_ORANGE_PROPOSALS", "0")

    success, metric, details = daemon._run_orange_proposal(
        "fake_orange_off", {"risk_tier": "orange"}, trust=object(), risk_tier="orange"
    )

    assert details["status"] == "awaiting_human_approval"
    assert success is True and metric == 0.0
    assert not calls, "kill switch must prevent the module from running"
    assert not staged


def test_missing_module_falls_back_to_old_behaviour(daemon, staged):
    """Nothing to propose from => preserve the historical signal, don't crash."""
    _, _, details = daemon._run_orange_proposal(
        "definitely_not_a_reflex_hgx_obs_02",
        {"risk_tier": "orange"},
        trust=object(),
        risk_tier="orange",
    )
    assert details["status"] == "awaiting_human_approval"


def test_staging_failure_does_not_lose_the_reflex_result(daemon, monkeypatch):
    """GKP staging is best-effort; the reflex already ran either way."""
    from tools.genesis import daemon as daemon_mod
    from tools.genesis import promoter as promoter_mod

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(promoter_mod, "export_gkp", boom)
    monkeypatch.setattr(daemon_mod, "reflex_connection_scope", contextlib.nullcontext)
    _install_fake_reflex(
        monkeypatch,
        "fake_orange_stage_fail",
        {"success": True, "metric_value": 0.5, "details": {"ok": True}},
        [],
    )

    success, metric, details = daemon._run_orange_proposal(
        "fake_orange_stage_fail", {"risk_tier": "orange"}, trust=object(), risk_tier="orange"
    )

    assert success is True and metric == pytest.approx(0.5)
    assert details["gkp_id"] == ""
    assert details["status"] == "proposal_run"
    assert details["reflex_result"] == {"ok": True}


# ---------------------------------------------------------------------------
# Premise guard — these are the reflexes the ORANGE path serves
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ORANGE_REFLEXES)
def test_orange_reflexes_are_configured_orange_and_schedulable(name):
    """If either stops being ORANGE, this file is testing a dead branch."""
    import yaml

    config_path = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
    with open(config_path, "r", encoding="utf-8") as fh:
        reflexes = (yaml.safe_load(fh) or {}).get("reflexes", {})

    cfg = reflexes[name]
    assert cfg.get("risk_tier", "").lower() == "orange"
    assert cfg.get("enabled") is True
    assert parse_schedule(cfg.get("schedule", "")) is not None


@pytest.mark.parametrize("name", ORANGE_REFLEXES)
def test_orange_reflex_modules_exist_to_be_proposed_from(name):
    """The proposal path imports the module; it must be there."""
    module_path = (
        Path(__file__).resolve().parents[1] / "tools" / "genesis" / "reflexes" / f"{name}.py"
    )
    assert module_path.is_file(), f"ORANGE reflex '{name}' has no module at {module_path}"
