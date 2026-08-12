# CUI // SP-CTI
"""`icdev init` must scaffold the instruction files this repo actually runs on.

BOOTSTRAP_MAP (tools/cli/init.py) copies icdev/data/claude_bootstrap/<x>, NOT
the repo file of the same name. Editing one without the other hands a scaffolded
project different rules than the project develops against.

Shipped twice on 2026-08-12: PR #1552 edited CLAUDE.md and not its packaged copy
(caught only by a CI byte-compare whose offset markers read like CRLF drift), and
AGENTS.md had drifted with nothing checking it at all — the packaged copy was
missing the "Project Cards" section, so every scaffolded project got a weaker
guardrail doc.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
checker = importlib.import_module("tools.workflow.coherence_checker")


def _pairs():
    return checker._load_bootstrap_parity()


def test_declaration_is_present_and_non_empty():
    """A parity gate that loses its config must not look green."""
    assert (REPO_ROOT / "args" / "bootstrap_parity.yaml").is_file()
    assert _pairs(), "args/bootstrap_parity.yaml declares no must_match pairs"


def test_claude_md_and_agents_md_are_both_declared():
    """The two files whose identity IS the intent.

    Pinned by name: the check is only as good as its declaration, and dropping a
    row from the YAML would otherwise silently narrow the gate to nothing.
    """
    targets = {str(p["target"]) for p in _pairs()}
    assert "CLAUDE.md" in targets
    assert "AGENTS.md" in targets


@pytest.mark.parametrize("pair", _pairs(), ids=lambda p: str(p["target"]))
def test_declared_bootstrap_pairs_are_byte_identical(pair):
    target = REPO_ROOT / str(pair["target"])
    source = REPO_ROOT / "icdev" / str(pair["source"])
    assert target.is_file(), f"repo file missing: {pair['target']}"
    assert source.is_file(), f"packaged copy missing: icdev/{pair['source']}"
    assert target.read_bytes() == source.read_bytes(), (
        f"{pair['target']} has drifted from icdev/{pair['source']}. "
        f"The repo file is the authority — copy it over the packaged copy. "
        f"Do not hand-edit the packaged file."
    )


def test_check_passes_on_the_current_tree():
    result = checker.check_bootstrap_parity()
    assert result.status == "pass", f"{result.message} | {result.extra}"


def test_check_reports_warn_rather_than_pass_when_the_declaration_is_gone(monkeypatch):
    """Fail-visible, not fail-silent: no declaration must never read as green."""
    monkeypatch.setattr(checker, "_load_bootstrap_parity", lambda: [])
    result = checker.check_bootstrap_parity()
    assert result.status == "warn"
    assert result.status != "pass"


def test_check_fails_on_drift(tmp_path, monkeypatch):
    """The whole point: a drifted pair must fail, and name the file."""
    monkeypatch.setattr(
        checker,
        "_load_bootstrap_parity",
        lambda: [{"target": "CLAUDE.md", "source": "data/claude_bootstrap/CLAUDE.md"}],
    )
    real_read = Path.read_bytes

    def fake_read(self):
        if self.name == "CLAUDE.md" and "claude_bootstrap" in str(self):
            return b"stale packaged copy"
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read)
    result = checker.check_bootstrap_parity()
    assert result.status == "fail"
    assert "CLAUDE.md" in " ".join(result.extra)


def test_excluded_pairs_are_documented_with_a_reason():
    """Three bootstrap sources are templates and SHOULD differ.

    Each carries a `why`, so the next person does not "fix" a difference that is
    the design — which is how a noisy gate gets switched off.
    """
    import yaml

    data = yaml.safe_load(
        (REPO_ROOT / "args" / "bootstrap_parity.yaml").read_text(encoding="utf-8")
    )
    excluded = data.get("excluded") or []
    assert excluded, "the template-by-design exclusions must stay documented"
    for entry in excluded:
        assert entry.get("why"), f"{entry.get('target')} excluded without a reason"
