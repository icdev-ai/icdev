# CUI // SP-CTI
"""The claim verifier runs on its own, and a cycle that measures nothing says so
(autonomy-act-01).

``tools/awareness/claim_verifier.py`` (rem-hyg-17) was consumed by NOBODY: no
reflex, no scheduler, no daemon imported it. A genesis reflex needs TWO
registrations -- ``daemon.REFLEX_NAMES`` and a config block in
``args/genesis_config.yaml`` -- and missing either makes it silently inert, so
both are pinned here by name rather than left to the generic parity tests.

The behaviour tests are organised around the one thing the card forbids: a
reflex whose clean run and whose no-op look identical. A cycle that verifies
zero claims must report ``unmeasurable`` and metric 0, never ``ok``.
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness.claim_verifier import Claim  # noqa: E402

reflex = importlib.import_module("tools.genesis.reflexes.claim_verifier_reflex")
claims_mod = importlib.import_module("tools.awareness.claims")

REFLEX_NAME = "claim_verifier_reflex"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _boom():
    raise RuntimeError("table missing")


def _claim(claim_id, reported, derived, tier="report"):
    return Claim(
        claim_id=claim_id,
        description=f"{claim_id}: a surface asserted something its evidence does not support",
        reported=lambda: reported() if callable(reported) else reported,
        derived=lambda: derived() if callable(derived) else derived,
        tier=tier,
    )


@pytest.fixture
def registry(monkeypatch):
    """Swap the live REGISTRY for a controlled one; return the setter."""
    def _set(claims):
        monkeypatch.setattr(claims_mod, "REGISTRY", list(claims))
        return claims
    return _set


@pytest.fixture
def captured(monkeypatch):
    """Record what the reflex asks task_factory to create, without a board."""
    created = []

    def _create_tasks(specs, **_kw):
        created.extend(specs)
        return [s["id"] for s in specs]

    tf = importlib.import_module("tools.kanban.task_factory")
    monkeypatch.setattr(tf, "create_tasks", _create_tasks)
    return created


# --------------------------------------------------------------------------- #
# 1. Registered in BOTH places a reflex needs
# --------------------------------------------------------------------------- #
def test_reflex_is_dispatched_by_the_daemon():
    """In REFLEX_NAMES -- the only list run_due_reflexes iterates."""
    from tools.genesis.daemon import REFLEX_NAMES
    from tests.test_reflex_registration import EXEMPT

    assert REFLEX_NAME in REFLEX_NAMES
    assert REFLEX_NAME not in EXEMPT, "must be dispatched, not exempt"


def test_reflex_has_an_enabled_config_block_on_a_parseable_schedule():
    """A name in REFLEX_NAMES with no config block is as dead as an unregistered module."""
    import yaml
    from tools.daemon.base import parse_schedule

    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    block = (cfg.get("reflexes") or {}).get(REFLEX_NAME)
    assert isinstance(block, dict), "no config block in genesis_config.yaml"
    missing = {"enabled", "risk_tier", "schedule", "description", "success_metric"} - set(block)
    assert not missing, f"config block missing keys: {sorted(missing)}"
    assert block["enabled"] is True
    assert parse_schedule(block["schedule"]), "schedule does not parse -- never dispatched"
    assert block["success_metric"]["name"] == reflex.METRIC_NAME, (
        "the declared metric and the one the reflex stamps must be the same name, "
        "or genesis_reflex_state records 0.0 forever"
    )


def test_reflex_honours_the_daemon_envelope():
    """The daemon reads result['success']; run() must take (config, trust)."""
    assert callable(reflex.run)
    assert len(inspect.signature(reflex.run).parameters) >= 2
    assert '"success"' in inspect.getsource(reflex)


# --------------------------------------------------------------------------- #
# 2. A cycle that measures nothing is UNMEASURABLE, never ok
# --------------------------------------------------------------------------- #
def test_an_empty_registry_is_unmeasurable_not_a_clean_run(registry):
    registry([])
    out = reflex.run({}, None)
    assert out["success"] is True, "the reflex RAN; the breaker must not trip on an empty registry"
    assert out["status"] == reflex.STATUS_UNMEASURABLE
    assert out["metric_value"] == 0
    assert out["details"]["status"] == reflex.STATUS_UNMEASURABLE
    assert "no claims" in out["reason"]


def test_every_claim_unmeasurable_is_unmeasurable_not_ok(registry):
    """The fresh-worktree / ephemeral-CI shape: every surface unreadable."""
    registry([_claim("a", _boom, 1), _claim("b", [], [])])
    out = reflex.run({}, None)
    assert out["status"] == reflex.STATUS_UNMEASURABLE
    assert out["claims"] == 2 and out["claims_measured"] == 0
    assert out["metric_value"] == 0
    assert set(out["unmeasurable_detail"]) == {"a", "b"}
    assert out["verdicts"] == {"a": "unmeasurable", "b": "unmeasurable"}


def test_a_partial_cycle_names_what_it_could_not_measure(registry):
    """A truncated sweep reporting only its successes reads as full coverage."""
    registry([_claim("a", 1, 1), _claim("b", _boom, 1)])
    out = reflex.run({}, None)
    assert out["status"] == reflex.STATUS_OK
    assert out["claims_measured"] == 1 and out["metric_value"] == 1.0
    assert out["unmeasurable_claims"] == ["b"]
    assert out["details"]["unmeasurable_claims"] == ["b"]


def test_an_all_agree_cycle_is_ok_and_measures_every_claim(registry, captured):
    registry([_claim("a", 1, 1), _claim("b", False, False, tier="propose")])
    out = reflex.run({}, None)
    assert out["status"] == reflex.STATUS_OK
    assert out["claims_measured"] == 2 and out["metric_value"] == 2.0
    assert out["findings"] == 0 and out["cards_filed"] == 0
    assert captured == []


def test_details_carry_a_flat_verdict_map_for_the_consumption_probe(registry):
    """This map is what the `verified_claim` class reads out of genesis_audit.
    Its shape is a contract, not a convenience."""
    registry([_claim("a", 1, 1), _claim("b", 1, 2), _claim("c", _boom, 1)])
    out = reflex.run({}, None)
    assert out["details"]["verdicts"] == {"a": "agrees", "b": "disagrees", "c": "unmeasurable"}
    assert out["details"]["claims_measured"] == 2


# --------------------------------------------------------------------------- #
# 3. Acting by tier: report states, propose files, restore is deferred
# --------------------------------------------------------------------------- #
def test_a_propose_tier_disagreement_files_one_card_with_both_sides(registry, captured):
    registry([_claim("recovery_counts", 331, 46, tier="propose")])
    out = reflex.run({}, None)
    assert out["status"] == reflex.STATUS_FINDINGS
    assert out["findings"] == 1 and out["cards_filed"] == 1
    assert len(captured) == 1
    card = captured[0]
    assert card["id"] == reflex._card_id("claim-verif-", "recovery_counts")
    assert "331" in card["description"] and "46" in card["description"], (
        "a finding that shows only one side cannot be acted on"
    )
    assert "--claim recovery_counts" in card["description"]
    assert card["status"] == "backlog"


def test_a_report_tier_disagreement_is_reported_and_files_nothing(registry, captured):
    registry([_claim("a", 1, 2, tier="report")])
    out = reflex.run({}, None)
    assert out["status"] == reflex.STATUS_FINDINGS
    assert out["findings"] == 1
    assert out["cards_filed"] == 0 and captured == []
    assert out["finding_detail"][0]["claim_id"] == "a"


def test_a_restore_tier_disagreement_is_deferred_by_name_not_dropped(registry, captured):
    """restore is autonomy-act-03. Until then it is NAMED, never acted on and
    never silently swallowed."""
    registry([_claim("dead_lease", 1, 0, tier="restore")])
    out = reflex.run({}, None)
    assert out["findings"] == 1
    assert out["deferred_restore"] == ["dead_lease"]
    assert out["details"]["deferred_restore"] == ["dead_lease"]
    assert out["cards_filed"] == 0 and captured == []


def test_no_tier_edits_the_claim():
    """The reflex acts on report and propose only -- there is no arm that makes
    the surface agree."""
    assert set(reflex.ACTING_TIERS) == {"report", "propose"}
    src = inspect.getsource(reflex)
    assert "restore" not in reflex.ACTING_TIERS
    assert "autonomy-act-03" in src


def test_dry_run_verifies_but_files_nothing(registry, captured):
    registry([_claim("a", 1, 2, tier="propose")])
    out = reflex.run({"dry_run": True}, None)
    assert out["status"] == reflex.STATUS_FINDINGS
    assert out["findings"] == 1
    assert out["cards_filed"] == 0 and captured == []
    assert out["details"]["dry_run"] is True


def test_card_config_overrides_are_honoured(registry, captured):
    registry([_claim("a", 1, 2, tier="propose")])
    reflex.run({"card": {"id_prefix": "cv-", "priority": "medium"}}, None)
    assert captured[0]["id"].startswith("cv-")
    assert captured[0]["priority"] == "medium"


# --------------------------------------------------------------------------- #
# 4. Dedupe and card-id hygiene
# --------------------------------------------------------------------------- #
def test_card_id_is_deterministic_in_the_claim_id():
    a = reflex._card_id("claim-verif-", "x")
    assert a == reflex._card_id("claim-verif-", "x")
    assert a != reflex._card_id("claim-verif-", "y")
    assert a.startswith("claim-verif-")


def test_card_id_is_not_gate_shaped():
    """`<card>-gate-<n>` makes promote_backlog_to_scheduled drop the row forever."""
    from tools.kanban.gates import is_manual_gate

    for claim_id in ("posture_score_needs_evidence", "gate", "x-gate-01"):
        cid = reflex._card_id("claim-verif-", claim_id)
        title = f"Surface claim disagrees with its evidence: {claim_id}"
        assert not is_manual_gate(cid, title)


# --------------------------------------------------------------------------- #
# 5. Failure never takes the daemon down, and never reads as clean
# --------------------------------------------------------------------------- #
def test_a_card_write_failure_never_breaks_the_daemon(registry, monkeypatch):
    registry([_claim("a", 1, 2, tier="propose")])
    tf = importlib.import_module("tools.kanban.task_factory")
    monkeypatch.setattr(
        tf, "create_tasks", lambda specs, **kw: (_ for _ in ()).throw(RuntimeError("board down")),
    )
    out = reflex.run({}, None)
    assert out["success"] is True
    assert out["findings"] == 1 and out["cards_filed"] == 0


def test_a_verifier_that_raises_is_an_error_not_a_clean_run(monkeypatch, registry):
    registry([_claim("a", 1, 1)])
    cv = importlib.import_module("tools.awareness.claim_verifier")
    monkeypatch.setattr(cv, "verify_all", lambda claims: (_ for _ in ()).throw(RuntimeError("x")))
    out = reflex.run({}, None)
    assert out["success"] is False
    assert out["status"] == reflex.STATUS_ERROR
    assert out["metric_value"] == 0
