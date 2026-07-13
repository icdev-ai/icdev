#!/usr/bin/env python3
# CUI // SP-CTI
"""prem-p0-05 — P0 is verified ADVERSARIALLY, not by a happy path.

Every test here asks the same question from a different angle: *can I get CUI to a
cloud provider?* A green happy-path suite would prove nothing — the pins it
replaces were "green" for a year while `requirement_extraction` routed proposal
content to kimi-cloud and `redaction.fail_closed` silently did nothing.

Each assertion names the RULE that fired, so a failure says which rung broke.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.llm.provider import LLMRequest  # noqa: E402
from tools.llm.routing_policy import (  # noqa: E402
    RULE_AIRGAP,
    RULE_DECLARED,
    RULE_DEFAULT,
    RULE_DERIVED,
    RULE_FORCE_LOCAL,
    resolve,
)

# A config that mirrors the shipped shape without depending on it.
CONFIG = {
    "routing_policy": {"enabled": True, "local_threshold": "SECRET", "aggregation_guard": True},
    "routing": {
        "proposal_drafting": {"chain": ["qwen3-local"], "force_local": True},
        "requirement_extraction": {"chain": ["kimi-cloud", "qwen3-local"]},
        "default": {"chain": ["kimi-cloud"]},
    },
}


def _req(text: str = "hello", classification: str = "CUI") -> LLMRequest:
    return LLMRequest(messages=[{"role": "user", "content": text}], classification=classification)


@pytest.fixture(autouse=True)
def _not_airgapped():
    """Default every test to a NON-air-gapped host.

    Otherwise rung 1 fires first and masks whatever rung the test meant to check —
    and worse, `is_airgap()` returns True on a merely-offline laptop, so the suite
    would pass for the wrong reason on a plane.
    """
    with patch("tools.llm.routing_policy._is_airgap", return_value=False):
        yield


# ---------------------------------------------------------------------------
# Rung 1 — air-gap beats everything
# ---------------------------------------------------------------------------


def test_airgapped_install_routes_local_regardless_of_everything_else():
    with patch("tools.llm.routing_policy._is_airgap", return_value=True):
        d = resolve("requirement_extraction", _req("public solicitation text", "PUBLIC"), CONFIG)
    assert d.local_only is True
    assert d.rule == RULE_AIRGAP


# ---------------------------------------------------------------------------
# Rung 2 — force_local
# ---------------------------------------------------------------------------


def test_force_local_function_routes_local_even_for_public_content():
    d = resolve("proposal_drafting", _req("nothing sensitive at all", "PUBLIC"), CONFIG)
    assert d.local_only is True
    assert d.rule == RULE_FORCE_LOCAL


def test_force_local_override_still_wins_over_a_permissive_default():
    """The per-function override is belt-and-braces; it must not be overridable."""
    cfg = {**CONFIG, "routing_policy": {"enabled": True, "local_threshold": "TOP SECRET"}}
    d = resolve("proposal_drafting", _req("x", "PUBLIC"), cfg)
    assert d.local_only is True and d.rule == RULE_FORCE_LOCAL


# ---------------------------------------------------------------------------
# Rung 3 — declared classification
# ---------------------------------------------------------------------------


def test_declared_secret_routes_local():
    d = resolve("requirement_extraction", _req("payload", "SECRET"), CONFIG)
    assert d.local_only is True
    assert d.rule == RULE_DECLARED


def test_cui_default_does_NOT_force_every_call_local():
    """The regression that would take the platform down.

    LLMRequest.classification defaults to "CUI". A CUI threshold would route every
    call in the product to a local model. CUI is protected by force_local + masking,
    not by the threshold.
    """
    d = resolve("some_ordinary_function", _req("hello", "CUI"), CONFIG)
    assert d.local_only is False
    assert d.rule == RULE_DEFAULT


def test_unrecognised_classification_fails_CLOSED():
    """A typo must not become a cloud egress.

    classification_manager.get_clearance_order() maps an unknown string to 1 (CUI),
    which is BELOW a SECRET threshold — so "SECRETT" would be waved through. The
    policy overrides that with fail-closed ordering.
    """
    d = resolve("requirement_extraction", _req("payload", "SECRETT"), CONFIG)
    assert d.local_only is True
    assert d.rule == RULE_DECLARED


# ---------------------------------------------------------------------------
# Rung 4 — DERIVED classification (the mosaic effect). The subtle one.
# ---------------------------------------------------------------------------


def test_derived_classification_also_routes_local():
    """No single field is classified; their CO-OCCURRENCE compiles to SECRET.

    If a derived classification did not force local exactly like a declared one,
    the mosaic would leak through the very door we think we closed.
    """
    fired = [{"rule_id": "SCG-AGG-TEST", "derive": "SECRET", "action": "block"}]
    with patch("tools.security.aggregation_guard.evaluate_rules", return_value=fired):
        d = resolve("requirement_extraction", _req("unclassified-looking text", "PUBLIC"), CONFIG)
    assert d.local_only is True
    assert d.rule == RULE_DERIVED
    assert d.derived == "SECRET"
    assert "SCG-AGG-TEST" in d.fired_rules


def test_derived_below_threshold_does_not_force_local():
    """A CUI derivation is below a SECRET threshold — masking is enough."""
    fired = [{"rule_id": "SCG-AGG-LOW", "derive": "CUI", "action": "warn"}]
    with patch("tools.security.aggregation_guard.evaluate_rules", return_value=fired):
        d = resolve("requirement_extraction", _req("text", "PUBLIC"), CONFIG)
    assert d.local_only is False
    assert d.rule == RULE_DEFAULT


def test_aggregation_guard_unavailable_fails_CLOSED():
    """We cannot rule OUT a derived classification, so we must not permit cloud."""
    with patch(
        "tools.security.aggregation_guard.evaluate_rules",
        side_effect=RuntimeError("presidio missing"),
    ):
        d = resolve("requirement_extraction", _req("text", "PUBLIC"), CONFIG)
    assert d.local_only is True
    assert d.rule == RULE_DERIVED


# ---------------------------------------------------------------------------
# Rung 5 — masked content may go cloud
# ---------------------------------------------------------------------------


def test_ordinary_content_is_cloud_permitted():
    d = resolve("requirement_extraction", _req("Section L instructions", "PUBLIC"), CONFIG)
    assert d.local_only is False
    assert d.rule == RULE_DEFAULT


# ---------------------------------------------------------------------------
# The shipped config — the pins it replaced carried a guarantee; keep it
# ---------------------------------------------------------------------------

FORMERLY_PINNED = [
    "proposal_drafting",
    "bid_scoring",
    "color_review",
    "rfi_writer_drafting",
    "rfi_editor_drafting",
    "rfi_reviewer_review",
    "rfi_researcher_knowledge",
    "rfi_compliance_assessment",
]


@pytest.fixture(scope="module")
def shipped():
    from tools.llm.config_path import resolve_llm_config_path

    with open(resolve_llm_config_path(), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.parametrize("fn", FORMERLY_PINNED)
def test_every_formerly_pinned_cui_function_is_force_local(fn, shipped):
    """Replacing the pin must not drop the guarantee the pin carried."""
    route = shipped["routing"][fn]
    assert route.get("force_local") is True, f"{fn} lost its local-only guarantee"


@pytest.mark.parametrize("fn", FORMERLY_PINNED)
def test_every_force_local_chain_can_actually_run_locally(fn, shipped):
    """force_local + an all-cloud chain fails closed at invoke time — correct, but a
    config bug that would break the function. Catch it here, not in production."""
    from tools.llm.cli_bridge.activate import is_local_only_model

    chain = shipped["routing"][fn]["chain"]
    models, providers = shipped.get("models", {}), shipped.get("providers", {})
    assert any(is_local_only_model(m, models, providers) for m in chain), (
        f"{fn} is force_local but no model in {chain} runs on a local-only provider"
    )


def test_routing_policy_is_enabled_in_the_shipped_config(shipped):
    assert shipped["routing_policy"]["enabled"] is True
    assert shipped["routing_policy"]["aggregation_guard"] is True


def test_redaction_fail_closed_is_actually_reachable():
    """prem-p0-03. The flag was documented in redaction_config.yaml but READ from
    llm_config.yaml, which has no such key — so it resolved to False no matter what
    an operator set, and RedactionUnavailableError could never fire.

    A security control that silently does nothing is worse than one that is absent:
    it is believed in.
    """
    from tools.llm.router import _resolve_redaction_fail_closed

    # The shipped llm_config redaction block still carries no fail_closed key...
    from tools.llm.config_path import resolve_llm_config_path

    with open(resolve_llm_config_path(), encoding="utf-8") as fh:
        llm_rd = (yaml.safe_load(fh) or {}).get("redaction", {})

    # ...and it must STILL resolve true, from redaction_config.yaml.
    assert _resolve_redaction_fail_closed(llm_rd) is True
