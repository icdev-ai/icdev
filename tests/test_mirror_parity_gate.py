# CUI // SP-CTI
"""`tools/` and `icdev/tools/` are two modules; a stale mirror runs old code.

PR #1542 fixed the kanban executor degrade in tools/ and did not mirror it.
Three and a half hours after that fix "deployed", exa-policy-07 and
exa-bench-02 were quarantined carrying the exact hardcoded error string #1542
had deleted, and the widened auto-revive never fired for them. The mirror was
43 KB behind.

tools/dx/mirror_parity.py already computed all of this and already had --gate.
It was wired into nothing.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
checker = importlib.import_module("tools.workflow.coherence_checker")


def test_gate_declaration_exists_and_parses():
    assert (REPO_ROOT / "args" / "mirror_parity_gate.yaml").is_file()
    gate = checker._load_mirror_gate()
    assert gate is not None
    assert isinstance(gate.get("budget"), int)
    assert gate.get("extensions"), "at least one executing extension must be gated"


def test_py_is_gated_and_md_is_not():
    """Manifest shards are merge=union documentation; .py silently runs."""
    gate = checker._load_mirror_gate()
    assert ".py" in gate["extensions"]
    assert ".md" not in gate["extensions"]
    excluded = {e["ext"] for e in gate.get("excluded_extensions") or []}
    assert ".md" in excluded


def test_every_exclusion_carries_a_reason():
    """A gate that fires on a correct difference gets switched off."""
    gate = checker._load_mirror_gate()
    for entry in gate.get("excluded_extensions") or []:
        assert entry.get("why"), f"{entry.get('ext')} excluded with no reason"


def test_check_passes_on_the_current_tree():
    result = checker.check_mirror_parity()
    assert result.status == "pass", f"{result.message} | {result.extra[:5]}"


def test_missing_declaration_warns_rather_than_passes(monkeypatch):
    """A drift gate that vanishes with its config is the bug it exists for."""
    monkeypatch.setattr(checker, "_load_mirror_gate", lambda: None)
    result = checker.check_mirror_parity()
    assert result.status == "warn"
    assert result.status != "pass"


def test_exceeding_the_budget_fails(monkeypatch):
    monkeypatch.setattr(checker, "_load_mirror_gate", lambda: {"extensions": [".py"], "budget": 0})
    monkeypatch.setattr(
        checker, "_mirror_drifted_files", lambda exts: ["tools/foo/bar.py"]
    )
    result = checker.check_mirror_parity()
    assert result.status == "fail"
    assert "budget" in result.message


def test_a_file_you_changed_is_never_grandfathered(monkeypatch):
    """THE RULE THAT CATCHES A #1542.

    Budget 999 forgives the whole backlog, and the changed file must still fail:
    the budget grandfathers what was already broken, it is not permission to add
    more.
    """
    monkeypatch.setattr(
        checker, "_load_mirror_gate", lambda: {"extensions": [".py"], "budget": 999}
    )
    monkeypatch.setattr(
        checker,
        "_mirror_drifted_files",
        lambda exts: ["tools/genesis/reflexes/kanban.py"],
    )
    result = checker.check_mirror_parity(
        changed_files=[Path("tools/genesis/reflexes/kanban.py")]
    )
    assert result.status == "fail", "drift in a file you changed must fail regardless of budget"
    assert "tools/genesis/reflexes/kanban.py" in " ".join(result.extra)


def test_unchanged_drift_within_budget_still_passes(monkeypatch):
    """The grandfathered backlog must not block work that did not touch it."""
    monkeypatch.setattr(
        checker, "_load_mirror_gate", lambda: {"extensions": [".py"], "budget": 999}
    )
    monkeypatch.setattr(
        checker, "_mirror_drifted_files", lambda exts: ["tools/other/thing.py"]
    )
    result = checker.check_mirror_parity(changed_files=[Path("tools/mine/file.py")])
    assert result.status == "pass"


class TestIntentionalShimsAreNotDrift:
    """A re-export shim is the OPPOSITE of the defect this gate exists for.

    The gate's premise is that `tools/x` and `icdev/tools/x` are separate module
    objects, so a fix in one leaves the other silently running old code. For a
    shim they are the SAME object — `tools/llm/agent_loop.py` is 98 lines that
    `import *` from the 2,672-line canonical module — so there is no stale half.
    Byte-comparing them reports permanent drift whose only "fix" is copying the
    body back, recreating the physically-separate stale copy the shim was
    written to delete.

    `_is_mirror_shim` existed and its docstring already named this exact file as
    one that "must never be flagged as drift" — but only `check_mirror_drift`
    (report-only) applied it, so touching the CANONICAL module failed the GATE
    on its shim, via the suffix match in rule 2 (rem-cap-04).
    """

    def test_the_canonical_shim_is_recognised_as_one(self):
        assert checker._is_mirror_shim(REPO_ROOT / "tools" / "llm" / "agent_loop.py")

    def test_a_real_mirrored_module_is_not_mistaken_for_a_shim(self):
        """The predicate must not launder a genuine stale copy into an exemption."""
        assert not checker._is_mirror_shim(
            REPO_ROOT / "tools" / "workflow" / "coherence_checker.py"
        )

    def test_the_shim_is_excluded_from_the_gate_feed(self):
        drifted = checker._mirror_drifted_files([".py"])
        assert "tools/llm/agent_loop.py" not in drifted

    def test_changing_the_canonical_module_does_not_fail_on_its_shim(self):
        """The end-to-end case: rule 2 matches by suffix, so `icdev/tools/llm/
        agent_loop.py` matched the drifted `tools/llm/agent_loop.py`."""
        result = checker.check_mirror_parity(
            changed_files=[Path("icdev/tools/llm/agent_loop.py")]
        )
        assert result.status == "pass", f"{result.message} | {result.extra[:5]}"

    def test_a_genuinely_drifted_changed_file_still_fails(self, monkeypatch):
        """The exemption must not blunt the rule that catches a #1542."""
        monkeypatch.setattr(
            checker, "_load_mirror_gate", lambda: {"extensions": [".py"], "budget": 999}
        )
        monkeypatch.setattr(
            checker, "_mirror_drifted_files", lambda exts: ["tools/genesis/reflexes/kanban.py"]
        )
        result = checker.check_mirror_parity(
            changed_files=[Path("tools/genesis/reflexes/kanban.py")]
        )
        assert result.status == "fail"


def test_the_budget_matches_what_the_tree_actually_carries():
    """The ratchet only ratchets if the ceiling tracks reality.

    A budget far above the real count silently re-opens the backlog: drift can
    grow for free until it reaches the ceiling, which is the state this gate was
    written to end. Lower it when drift is reconciled; never raise it.
    """
    gate = checker._load_mirror_gate()
    actual = len(checker._mirror_drifted_files(gate["extensions"]))
    assert actual <= gate["budget"], (
        f"{actual} drifted files exceeds the declared budget {gate['budget']}"
    )
    assert gate["budget"] - actual <= 2, (
        f"budget {gate['budget']} is {gate['budget'] - actual} above the actual "
        f"{actual}; lower it to match — headroom is drift you have pre-approved"
    )


def test_registered_in_the_check_registry():
    """Otherwise the gate is itself declared-but-unconsumed — the whole point."""
    assert checker.CHECK_REGISTRY["mirror_parity"] is checker.check_mirror_parity


@pytest.mark.parametrize("ext", [".py"])
def test_gated_extensions_are_executable_code(ext):
    """Only file kinds whose staleness changes BEHAVIOUR belong in the budget."""
    assert ext in checker._load_mirror_gate()["extensions"]
