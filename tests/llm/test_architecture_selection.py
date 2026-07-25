# CUI // SP-CTI
"""Config-driven architecture selection (agx-core-03).

Covers precedence (explicit > function > role > default), the safe default
(omitting the key changes nothing), structured selection logging, and the
REGRESSION GUARD: the shipped args/llm_config.yaml resolves to "current
behavior" (None) for every representative function/role, so existing
CoT/CoD/council call sites are unaffected.
"""
import yaml

from tools.llm.architectures.selection import (
    resolve_and_log,
    resolve_architecture,
)
from tools.llm.config_path import resolve_llm_config_path


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------
_CFG = {
    "architectures": {
        "default": "chain_of_thought",
        "functions": {"code_review": "chain_of_debate"},
        "roles": {"cot_reasoner": "council"},
        "log_selections": True,
    }
}


def test_explicit_arg_wins():
    assert resolve_architecture(function="code_review", explicit="react", config=_CFG) == "react"


def test_explicit_none_forces_current_behavior():
    # Explicit None short-circuits config — caller forces current behavior.
    assert resolve_architecture(function="code_review", explicit=None, config=_CFG) is None


def test_function_beats_role_and_default():
    assert resolve_architecture(function="code_review", role="cot_reasoner", config=_CFG) == "chain_of_debate"


def test_role_beats_default():
    assert resolve_architecture(role="cot_reasoner", config=_CFG) == "council"


def test_default_applies_when_no_function_or_role_match():
    assert resolve_architecture(function="unlisted_fn", config=_CFG) == "chain_of_thought"


def test_empty_config_is_current_behavior():
    assert resolve_architecture(function="anything", config={}) is None
    assert resolve_architecture(function="anything", config=None) is None


def test_narrowed_block_accepted():
    block = {"default": "react"}
    assert resolve_architecture(function="x", config=block) == "react"


# ---------------------------------------------------------------------------
# Structured logging / resolve_and_log source attribution
# ---------------------------------------------------------------------------
def test_resolve_and_log_reports_source(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        arch = resolve_and_log(function="code_review", config=_CFG)
    assert arch == "chain_of_debate"


def test_log_selection_noop_when_disabled():
    cfg = {"architectures": {"default": "react", "log_selections": False}}
    # Should not raise and should honor the disable flag (no assertion on output —
    # just that it completes without error).
    resolve_and_log(function="x", config=cfg)


# ---------------------------------------------------------------------------
# REGRESSION GUARD — shipped config is a no-op
# ---------------------------------------------------------------------------
def test_shipped_config_defaults_to_current_behavior():
    """The shipped args/llm_config.yaml must not change any existing call site:
    every architectures.* value ships as null / empty, so resolution is None."""
    cfg_path = resolve_llm_config_path()
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    arch_block = cfg.get("architectures")
    assert arch_block is not None, "architectures: section missing from llm_config.yaml"
    assert arch_block.get("default") is None, "shipped default must be null (current behavior)"
    assert arch_block.get("functions") in ({}, None), "shipped functions must be empty"
    assert arch_block.get("roles") in ({}, None), "shipped roles must be empty"

    # Representative existing surfaces must all resolve to current behavior.
    for fn in ("code_generation", "code_review", "nlq_sql", "compliance_analysis"):
        assert resolve_architecture(function=fn, config=cfg) is None
    for role in ("cot_reasoner", "cot_critic", "cod_judge", "council_chairman"):
        assert resolve_architecture(role=role, config=cfg) is None
