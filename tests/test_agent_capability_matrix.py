# CUI // SP-CTI
"""exa-bench-03: tools/agents/capability_matrix.py — declared vs actual.

Covers the card's binding acceptance criteria:

  1. a ``--json`` probe reports declared next to actual per adapter across the
     whole fixed capability set,
  2. the matrix is consumable by ``registry.pick_default`` — and leaving the
     new argument unset changes nothing for existing callers,
  3. ``tools/workflow/executor_parity.py`` is untouched and the docstring says
     why the two modules are not the same question,
  4. a declared capability the probe cannot confirm comes back ``unconfirmed``,
     never ``present``.

The probes are offline by construction — no subprocess, no socket, no model
call — so every test here is deterministic on a host that has the backend CLIs
installed and on one that does not.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.agents import capability_matrix as cm
from tools.agents import registry
from tools.agents.adapter_base import AgentResult, AgentSession, NotInstalledError


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_state():
    registry.reset()
    cm.reset_cache()
    yield
    registry.reset()
    cm.reset_cache()


# ── test doubles ────────────────────────────────────────────────────────────
class _BareAdapter:
    """The minimum an adapter can be: five methods, no capabilities."""

    name = "bare"

    def available(self) -> bool:
        return True

    def prepare_prompt(self, session: AgentSession) -> str:
        return session.prompt

    def invoke(self, session: AgentSession) -> AgentResult:
        return AgentResult(task_id=session.task_id, adapter_name=self.name,
                           completed=False)

    def detect_completion(self, output: str) -> bool:
        return False

    def parse_response(self, raw: str):
        return {"content": raw or "", "tool_calls": [], "diff": ""}


class _CapableAdapter(_BareAdapter):
    """An adapter that really does surface the seam-level capabilities."""

    name = "capable"
    CAPABILITIES = {"streaming": True, "sub_agents": True}

    def spawn(self, session: AgentSession):
        raise AssertionError("the probe must never actually spawn anything")

    def delegate(self, session: AgentSession):
        raise AssertionError("the probe must never actually delegate anything")

    def build_argv(self, session: AgentSession):
        meta = session.metadata or {}
        argv = ["exe", "--max-turns", str(session.max_turns)]
        if meta.get("sandbox"):
            argv += ["--sandbox", str(meta["sandbox"])]
        return argv

    def parse_response(self, raw: str):
        return {"content": "recovered", "tool_calls": [{"name": "read"}],
                "diff": "--- a\n+++ b\n"}


class _ExplodingAdapter(_BareAdapter):
    """Every seam method raises. A broken adapter must not crash the probe."""

    name = "exploding"

    def available(self) -> bool:
        raise RuntimeError("boom")

    def parse_response(self, raw: str):
        raise RuntimeError("boom")


def _register(monkeypatch, **adapters):
    """Replace the registry contents with the given doubles."""
    registry._ensure_loaded()
    monkeypatch.setattr(registry, "_REGISTRY", dict(adapters), raising=True)


# ── 1. shape of the probe ───────────────────────────────────────────────────
def test_every_capability_has_a_probe_and_vice_versa():
    assert set(cm.CAPABILITIES) == set(cm._PROBES)
    assert len(cm.CAPABILITIES) == 7
    assert set(cm.CAPABILITIES) == {
        "streaming", "tool_calling", "sub_agents", "interruption",
        "sandbox_passthrough", "context_budget", "structured_output",
    }


def test_json_probe_reports_declared_and_actual_for_every_cell():
    matrix = cm.build_matrix()
    # Round-trips as JSON — this is what --json prints.
    json.loads(json.dumps(matrix, default=str))

    assert set(matrix["adapters"]) == set(registry.list_adapters())
    for name, entry in matrix["adapters"].items():
        assert set(entry["capabilities"]) == set(cm.CAPABILITIES), name
        for cap, cell in entry["capabilities"].items():
            assert isinstance(cell["declared"], bool), (name, cap)
            assert cell["actual"] in (cm.PRESENT, cm.ABSENT, cm.UNCONFIRMED)
            assert cell["method"] in (
                cm.BEHAVIORAL, cm.INTERFACE, cm.SOURCE_EVIDENCE)
            assert cell["evidence"].strip(), (name, cap)
            assert cell["verdict"] == cm._verdict(cell["declared"], cell["actual"])


def test_summary_counts_match_the_cells():
    matrix = cm.build_matrix()
    cells = [
        cell
        for entry in matrix["adapters"].values()
        for cell in entry["capabilities"].values()
    ]
    assert matrix["summary"]["cells"] == len(cells)
    for status in (cm.PRESENT, cm.ABSENT, cm.UNCONFIRMED):
        assert matrix["summary"].get(status, 0) == sum(
            1 for c in cells if c["actual"] == status
        )


# ── 2. unconfirmed is never laundered into present ──────────────────────────
def test_source_evidence_can_only_produce_unconfirmed():
    """grep is a lead, not a measurement — enforce that in the output."""
    matrix = cm.build_matrix()
    for name, entry in matrix["adapters"].items():
        for cap, cell in entry["capabilities"].items():
            if cell["method"] == cm.SOURCE_EVIDENCE:
                assert cell["actual"] == cm.UNCONFIRMED, (name, cap)


def test_declaring_a_capability_does_not_make_the_probe_report_it(monkeypatch):
    """The acceptance criterion: declared-but-unconfirmable != present."""
    bare = _BareAdapter()
    _register(monkeypatch, bare=bare)
    config = {"declared": {"bare": {cap: True for cap in cm.CAPABILITIES}}}

    entry = cm.probe_adapter("bare", config)
    for cap, cell in entry["capabilities"].items():
        assert cell["declared"] is True, cap
        assert cell["actual"] != cm.PRESENT, cap
        assert cell["verdict"] in (cm.OVERCLAIMED, cm.UNVERIFIED), cap


def test_a_real_unconfirmed_cell_exists_and_is_not_absent():
    """local_agent's stop_event contract is the canonical unconfirmed case.

    It is a documented metadata key that nothing on the seam exposes: the probe
    must neither claim it works nor claim it is missing.
    """
    entry = cm.probe_adapter("local_agent")
    cell = entry["capabilities"]["interruption"]
    assert cell["actual"] == cm.UNCONFIRMED
    assert cell["method"] == cm.SOURCE_EVIDENCE
    assert "stop_event" in cell["evidence"]


def test_capable_adapter_is_measured_present_where_it_really_is(monkeypatch):
    capable = _CapableAdapter()
    _register(monkeypatch, capable=capable)
    caps = cm.probe_adapter("capable")["capabilities"]

    assert caps["streaming"]["actual"] == cm.PRESENT
    assert caps["sub_agents"]["actual"] == cm.PRESENT
    assert caps["interruption"]["actual"] == cm.PRESENT   # public spawn()
    assert caps["tool_calling"]["actual"] == cm.PRESENT
    assert caps["structured_output"]["actual"] == cm.PRESENT
    assert caps["sandbox_passthrough"]["actual"] == cm.PRESENT
    assert caps["context_budget"]["actual"] == cm.PRESENT


def test_adapter_owned_declaration_wins_over_the_yaml(monkeypatch):
    _register(monkeypatch, capable=_CapableAdapter())
    decl = cm.declared_for("capable", {"declared": {"capable": {"streaming": False}}})
    assert decl["source"] == "capable.CAPABILITIES"
    assert decl["values"] == {"streaming": True, "sub_agents": True}


def test_a_capability_name_with_no_probe_is_ignored(monkeypatch):
    _register(monkeypatch, bare=_BareAdapter())
    decl = cm.declared_for("bare", {"declared": {"bare": {"telepathy": True}}})
    assert "telepathy" not in decl["values"]
    assert cm.capability_status("bare", "telepathy") == cm.UNCONFIRMED


def test_a_broken_adapter_does_not_crash_the_probe(monkeypatch):
    _register(monkeypatch, exploding=_ExplodingAdapter())
    entry = cm.probe_adapter("exploding")
    assert entry["available"] is False
    assert set(entry["capabilities"]) == set(cm.CAPABILITIES)
    assert entry["capabilities"]["tool_calling"]["actual"] == cm.UNCONFIRMED


# ── 3. the probe must not disturb what it measures ──────────────────────────
def test_probing_never_mutates_the_shared_adapter_singleton():
    """argv probes stub resolve() — on a throwaway instance, never the singleton."""
    adapter = registry.get_adapter("claude_cli")
    before = adapter.__dict__.get("resolve", "<unset>")
    cm.build_matrix()
    assert adapter.__dict__.get("resolve", "<unset>") == before
    assert "resolve" not in adapter.__dict__


def test_argv_probes_do_not_depend_on_the_backend_being_installed():
    """Otherwise the matrix would change shape between a laptop and a runner."""
    entry = cm.probe_adapter("codex_cli")
    cell = entry["capabilities"]["sandbox_passthrough"]
    assert cell["actual"] == cm.PRESENT
    assert cell["method"] == cm.BEHAVIORAL
    # The stubbed executable never leaks a host path into the evidence.
    for argv in (cell["detail"]["baseline"], cell["detail"]["probed"]):
        assert argv[0] == "<executable>"
        assert cm._SENTINEL_EXE not in argv


# ── 4. real findings this probe exists to surface ───────────────────────────
def test_claude_cli_tool_calls_are_not_visible_through_the_seam():
    """parse_response() returns tool_calls=[] unconditionally — declared true."""
    caps = cm.probe_adapter("claude_cli")["capabilities"]
    assert caps["tool_calling"]["actual"] == cm.ABSENT
    assert caps["tool_calling"]["method"] == cm.BEHAVIORAL
    assert caps["tool_calling"]["verdict"] == cm.OVERCLAIMED


def test_claude_cli_permission_posture_is_fixed_by_the_adapter():
    caps = cm.probe_adapter("claude_cli")["capabilities"]
    cell = caps["sandbox_passthrough"]
    assert cell["actual"] == cm.ABSENT
    assert "--dangerously-skip-permissions" in cell["detail"]["baseline"]


def test_the_stub_adapter_measures_capable_of_nothing():
    """A stub must never read as capable — copilot_cli is unimplemented."""
    caps = cm.probe_adapter("copilot_cli")["capabilities"]
    assert all(c["actual"] != cm.PRESENT for c in caps.values())


# ── 5. consumable by pick_default ───────────────────────────────────────────
def test_pick_default_without_require_is_unchanged(monkeypatch):
    monkeypatch.delenv("ICDEV_AGENT_ADAPTER", raising=False)
    _register(monkeypatch, bare=_BareAdapter())
    config = {"enabled_adapters": ["bare"], "fallback_order": ["bare"],
              "per_task_type_preference": {}}
    assert registry.pick_default(config=config).name == "bare"
    # ...and an empty require list is the same thing, not a filter that matches
    # nothing.
    assert registry.pick_default(config=config, require=[]).name == "bare"


def test_pick_default_require_filters_on_measurement(monkeypatch):
    monkeypatch.delenv("ICDEV_AGENT_ADAPTER", raising=False)
    _register(monkeypatch, bare=_BareAdapter(), capable=_CapableAdapter())
    config = {"enabled_adapters": ["bare", "capable"],
              "fallback_order": ["bare", "capable"],
              "per_task_type_preference": {}}

    # bare is first in the fallback order but cannot stream.
    assert registry.pick_default(config=config).name == "bare"
    assert registry.pick_default(
        config=config, require=["streaming"]).name == "capable"


def test_pick_default_require_skips_the_task_type_preference_too(monkeypatch):
    monkeypatch.delenv("ICDEV_AGENT_ADAPTER", raising=False)
    _register(monkeypatch, bare=_BareAdapter(), capable=_CapableAdapter())
    config = {"enabled_adapters": ["bare", "capable"],
              "fallback_order": ["capable"],
              "per_task_type_preference": {"build": "bare"}}
    assert registry.pick_default("build", config=config).name == "bare"
    assert registry.pick_default(
        "build", config=config, require=["sub_agents"]).name == "capable"


def test_unconfirmed_does_not_satisfy_a_requirement():
    """Fail-closed: routing on an unverified claim is the bug being fixed."""
    assert cm.capability_status("local_agent", "interruption") == cm.UNCONFIRMED
    assert cm.supports("local_agent", ["interruption"]) is False
    assert "local_agent" not in cm.adapters_with("interruption")


def test_pick_default_raises_when_nothing_meets_the_requirement(monkeypatch):
    monkeypatch.delenv("ICDEV_AGENT_ADAPTER", raising=False)
    _register(monkeypatch, bare=_BareAdapter())
    config = {"enabled_adapters": ["bare"], "fallback_order": ["bare"],
              "per_task_type_preference": {}}
    with pytest.raises(NotInstalledError) as exc:
        registry.pick_default(config=config, require=["streaming"])
    assert "capability_matrix" in str(exc.value)


def test_forced_adapter_still_wins_over_require(monkeypatch):
    """An operator override is more specific than a capability filter."""
    _register(monkeypatch, bare=_BareAdapter(), capable=_CapableAdapter())
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "bare")
    picked = registry.pick_default(require=["streaming"])
    assert picked.name == "bare"


def test_a_failing_probe_never_promotes_a_candidate(monkeypatch):
    _register(monkeypatch, bare=_BareAdapter())
    monkeypatch.setattr(
        cm, "supports",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("probe down")),
    )
    config = {"enabled_adapters": ["bare"], "fallback_order": ["bare"],
              "per_task_type_preference": {}}
    with pytest.raises(NotInstalledError):
        registry.pick_default(config=config, require=["streaming"])


# ── 6. caching ──────────────────────────────────────────────────────────────
def test_cache_is_only_used_for_the_whole_unfiltered_matrix():
    first = cm.build_matrix(use_cache=True)
    assert cm.build_matrix(use_cache=True) is first
    assert cm.build_matrix(use_cache=True, only=["streaming"]) is not first
    cm.reset_cache()
    assert cm.build_matrix(use_cache=True) is not first


# ── 7. CLI ──────────────────────────────────────────────────────────────────
def test_cli_json_output_parses(capsys):
    assert cm.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["adapters"]) == set(registry.list_adapters())
    assert "overclaimed" in payload


def test_cli_capability_filter_narrows_the_matrix(capsys):
    assert cm.main(["--json", "--capability", "streaming"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload["capabilities"]) == ["streaming"]
    for entry in payload["adapters"].values():
        assert list(entry["capabilities"]) == ["streaming"]


def test_cli_rejects_unknown_names(capsys):
    assert cm.main(["--adapter", "nope"]) == 2
    assert cm.main(["--capability", "telepathy"]) == 2


def test_cli_gate_exits_nonzero_only_on_an_overclaim(capsys):
    """The gate is opt-in and wired to no pipeline; it must still be honest."""
    rc = cm.main(["--gate", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == (1 if payload["overclaimed"] else 0)


# ── 8. the module boundary the card asked to be preserved ───────────────────
def test_executor_parity_is_untouched():
    """A different question: outcome parity on a replayed corpus."""
    changed = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", shell=False,
    )
    if changed.returncode == 0:
        assert "tools/workflow/executor_parity.py" not in changed.stdout


def test_docstring_states_the_relationship_to_executor_parity():
    """So the next session does not merge two modules that answer differently."""
    doc = cm.__doc__ or ""
    assert "executor_parity" in doc
    assert "outcome parity" in doc.lower()
    assert "capability parity" in doc.lower()
