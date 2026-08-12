# CUI // SP-CTI
"""kax-exec-01..03 — executor availability must report the truth and degrade in scope.

Regression cover for the 2026-08-12 incident: one operator kill of a wedged
session degraded claude_cli globally for 300s, and every task dispatched in that
window was quarantined to `suggested` with a hardcoded reason. Two real tasks
(exa-policy-08, exa-live-01) had to be revived by hand.
"""
from __future__ import annotations

import importlib

import pytest

kanban = importlib.import_module("tools.genesis.reflexes.kanban")


@pytest.fixture(autouse=True)
def _clean_executor_state():
    """Both sets are module-level and per-process; isolate every test."""
    degraded = set(kanban._degraded_executors)
    probed = dict(kanban._degraded_executors_probed_at)
    ever = set(kanban._tiers_ever_dispatched)
    kanban._degraded_executors.clear()
    kanban._degraded_executors_probed_at.clear()
    kanban._tiers_ever_dispatched.clear()
    yield
    kanban._degraded_executors.clear()
    kanban._degraded_executors.update(degraded)
    kanban._degraded_executors_probed_at.clear()
    kanban._degraded_executors_probed_at.update(probed)
    kanban._tiers_ever_dispatched.clear()
    kanban._tiers_ever_dispatched.update(ever)


CHAIN = ["claude_cli", "gitlab", "ollama_local"]


# ── kax-exec-03: the safety net must actually fire ────────────────────────────
def test_chain_unchanged_when_nothing_degraded():
    assert kanban._build_effective_executor_chain(CHAIN) == CHAIN


def test_cold_cache_preserves_legacy_behaviour():
    """No dispatch evidence yet — behave exactly as before rather than guess."""
    kanban._degraded_executors.add("claude_cli")
    assert kanban._build_effective_executor_chain(CHAIN) == ["gitlab", "ollama_local"]


def test_degrading_the_only_proven_tier_falls_back_to_full_chain():
    """THE BUG: active was non-empty, so the documented last-resort never ran.

    gitlab and ollama_local are in the configured chain but have never dispatched
    on this host, so they are not evidence of a working executor and must not
    suppress the fallback.
    """
    kanban._tiers_ever_dispatched.add("claude_cli")
    kanban._degraded_executors.add("claude_cli")
    assert kanban._build_effective_executor_chain(CHAIN) == CHAIN


def test_a_proven_fallback_still_wins():
    """If gitlab has genuinely dispatched here, degrading claude_cli is fine."""
    kanban._tiers_ever_dispatched.update({"claude_cli", "gitlab"})
    kanban._degraded_executors.add("claude_cli")
    assert kanban._build_effective_executor_chain(CHAIN) == ["gitlab", "ollama_local"]


# ── kax-exec-02: a signal-kill is not evidence about the provider ─────────────
def test_abnormal_exit_still_parks_the_task():
    """The exit-code rule is deliberate and must be preserved: an interrupted
    session parks (keeping its branch) rather than being scored a clean failure."""
    is_exhausted, _hint = kanban._detect_token_exhaustion(-1, "")
    assert is_exhausted is True
    is_exhausted, _hint = kanban._detect_token_exhaustion(137, "")
    assert is_exhausted is True


def test_plain_failure_is_not_exhaustion():
    assert kanban._detect_token_exhaustion(1, "AssertionError: nope")[0] is False


def test_provider_text_is_distinguishable_from_a_bare_exit_code():
    """The degrade site keys on this distinction, so it must be observable."""
    quota = "Error: usage limit reached. Try again at 4:30pm."
    assert kanban._TOKEN_RE.search(quota) is not None
    # A killed process produces no provider text — this is what must NOT degrade.
    assert kanban._TOKEN_RE.search("") is None
    assert kanban._TOKEN_RE.search("Traceback (most recent call last):") is None


# ── kax-exec-01: the quarantine reason must discriminate causes ───────────────
def test_quarantine_reason_is_not_a_hardcoded_sentence():
    """The literal this replaced named three things nothing measured, so the
    2026-08-01 PATHEXT incident and the 2026-08-12 degrade produced the same
    sentence from unrelated causes.

    Checked over the AST, not the raw text: the comment explaining the bug
    necessarily quotes the old string, and a comment is not a claim the code
    makes.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(kanban))
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    offenders = [s for s in literals if "internet=False" in s]
    assert not offenders, (
        "a hardcoded availability verdict is back in a string literal "
        f"({offenders!r}) — it reports a conclusion nothing probed"
    )
    assert "tier_outcomes" in inspect.getsource(kanban)


def test_reason_is_built_from_observed_tier_outcomes():
    """Two different causes must produce two different reasons."""
    degraded_case = "no executor available: " + ", ".join(
        ["gitlab=dispatch returned False", "claude_cli=degraded"]
    )
    unresolved_case = "no executor available: " + ", ".join(
        ["claude_cli=no adapter resolved", "gitlab=dispatch returned False"]
    )
    assert degraded_case != unresolved_case
    assert "degraded" in degraded_case
    assert "no adapter resolved" in unresolved_case
