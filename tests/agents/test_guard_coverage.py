# CUI // SP-CTI
"""omx-guard-06: the runner admits an adapter by guard COVERAGE, not by "has a native hook".

Covers the card's acceptance criteria:

  1. a codex-shaped adapter (no L1, L2+L3 measured present) is admitted by the
     default policy and dispatched by the runner,
  2. L2 alone is refused by default and admitted with ``l2_only_opt_in``,
  3. L1 alone is admitted, unchanged from omx-guard-02,
  4. the capability matrix and the Omarchy panel record show the live layers.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tools.agents import capability_matrix as cm
from tools.agents import guard_parity as gp
from tools.agents import registry

P, A, U = cm.PRESENT, cm.ABSENT, cm.UNCONFIRMED


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(gp.MODE_ENV, raising=False)
    gp.reset_cache()
    yield
    gp.reset_cache()


def _policy(tmp_path, text):
    path = tmp_path / "guard_coverage.yaml"
    path.write_text(text, encoding="utf-8")
    return gp.load_policy(path)


def _layers(l1, l2, l3):
    return {gp.L1: l1, gp.L2: l2, gp.L3: l3}


# ── the policy ──────────────────────────────────────────────────────────────
def test_shipped_policy_is_l1_or_l2_plus_l3_without_the_opt_in():
    pol = gp.load_policy()
    assert pol["admit"] == [("L1",), ("L2", "L3")]
    assert pol["l2_only_opt_in"] is False and pol["warnings"] == []


def test_unreadable_policy_falls_back_to_the_default(tmp_path):
    pol = gp.load_policy(tmp_path / "missing.yaml")
    assert pol["admit"] == [("L1",), ("L2", "L3")]
    assert pol["warnings"]


def test_an_entry_that_checks_no_command_is_dropped(tmp_path):
    pol = _policy(tmp_path, "admit:\n  - [L3]\n  - [L1]\n")
    assert pol["admit"] == [("L1",)]
    assert any("no shell command" in w for w in pol["warnings"])


# ── the verdict ─────────────────────────────────────────────────────────────
def test_codex_shaped_l2_plus_l3_is_admitted_by_default():
    v = gp.coverage_verdict("codex_cli", _layers(A, P, P))
    assert v.guarded and v.live == ["L2", "L3"]
    assert "L2+L3" in v.reason


def test_l2_only_is_refused_by_default(tmp_path):
    v = gp.coverage_verdict("codex_cli", _layers(A, P, U))
    assert not v.guarded and v.live == ["L2"]
    assert "L3=unconfirmed" in v.reason
    # Listing [L2] without the opt-in is not an opt-in.
    pol = _policy(tmp_path, "admit:\n  - [L1]\n  - [L2]\n")
    assert ("L2",) not in pol["admit"]
    assert not gp.coverage_verdict("codex_cli", _layers(A, P, U), pol).guarded


def test_l2_only_is_admitted_with_the_operator_opt_in(tmp_path):
    pol = _policy(tmp_path, "admit:\n  - [L1]\n  - [L2, L3]\nl2_only_opt_in: true\n")
    assert gp.coverage_verdict("codex_cli", _layers(A, P, U), pol).guarded


def test_l1_alone_is_admitted_unchanged():
    v = gp.coverage_verdict("opencode_cli", _layers(P, A, U))
    assert v.guarded and v.live == ["L1"]


def test_l3_alone_is_refused():
    assert not gp.coverage_verdict("x", _layers(A, A, P)).guarded


# ── measured, through the matrix ────────────────────────────────────────────
def _fake_probe(cells):
    def probe(name, config=None, only=None):
        return {"capabilities": {cap: {"actual": cells[cap], "evidence": "fake"}
                                 for cap in (only or cells)}}
    return probe


def test_verdict_for_measures_each_layer(monkeypatch):
    monkeypatch.setattr(cm, "probe_adapter", _fake_probe(
        {"guard_wired": A, "guard_shell": P, "sandboxed": P}))
    v = gp.verdict_for("codex_cli", use_cache=False)
    assert v.guarded and v.layers == _layers(A, P, P)


def test_a_failed_probe_admits_nothing(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("probe host broken")
    monkeypatch.setattr(cm, "probe_adapter", boom)
    v = gp.verdict_for("codex_cli", use_cache=False)
    assert not v.guarded and "probe host broken" in v.reason


# ── the runner ──────────────────────────────────────────────────────────────
class _Conn:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params=()):
        self.sink.append((sql, params))


@pytest.fixture
def runner(monkeypatch):
    from tools.genesis.reflexes import kanban

    writes = []

    @contextmanager
    def _fake_conn(*_a, **_k):
        yield _Conn(writes)

    monkeypatch.setattr(kanban, "get_connection", _fake_conn)
    monkeypatch.setattr(gp, "_available", lambda _n: True)
    monkeypatch.setattr(registry, "get_adapter", lambda n: SimpleNamespace(name=n))
    return SimpleNamespace(kanban=kanban, writes=writes)


def _layer_table(table):
    return lambda name: (table[name], {})


def test_runner_dispatches_codex_once_l2_and_l3_cover_it(runner, monkeypatch):
    monkeypatch.setenv(gp.MODE_ENV, "enforce")
    monkeypatch.setattr(gp, "layer_status", _layer_table({"codex_cli": _layers(A, P, P)}))
    adapter = SimpleNamespace(name="codex_cli")
    got = runner.kanban._guard_parity_screen(adapter, ["codex_cli"], "omx-test-06", [])
    assert got is adapter and runner.writes == []


def test_runner_refuses_l2_only_codex_and_falls_back(runner, monkeypatch):
    monkeypatch.setenv(gp.MODE_ENV, "enforce")
    monkeypatch.setattr(gp, "layer_status", _layer_table({
        "codex_cli": _layers(A, P, U), "claude_cli": _layers(P, U, U)}))
    outcomes = []
    got = runner.kanban._guard_parity_screen(
        SimpleNamespace(name="codex_cli"), ["codex_cli", "claude_cli"],
        "omx-test-06", outcomes)
    assert got.name == "claude_cli"
    assert "codex_cli" in outcomes[0] and "live: L2" in outcomes[0]


# ── visible: matrix and panel ───────────────────────────────────────────────
def test_matrix_shows_guard_coverage(monkeypatch):
    statuses = {"guard_wired": A, "guard_shell": P, "sandboxed": P}
    for cap, status in statuses.items():
        monkeypatch.setitem(cm._PROBES, cap,
                            lambda _a, s=status: cm.ProbeOutcome(s, cm.BEHAVIORAL, "fake"))
    matrix = cm.build_matrix(adapters=["codex_cli"])
    cov = matrix["adapters"]["codex_cli"]["guard_coverage"]
    assert cov["admitted"] is True and cov["live"] == ["L2", "L3"]
    assert "guard coverage: ADMITTED" in cm._render_text(matrix)


def test_matrix_omits_coverage_when_a_layer_was_not_probed():
    matrix = cm.build_matrix(adapters=["codex_cli"], only=["streaming"])
    assert "guard_coverage" not in matrix["adapters"]["codex_cli"]


def test_panel_tier_label_names_the_live_layers():
    from tools.cli import omarchy_panel as panel

    verdict = gp.coverage_verdict("codex_cli", _layers(A, P, P))
    status = panel.adapter_status(lambda: SimpleNamespace(name="codex_cli"),
                                  lambda _n: verdict)
    assert status["coverage"] == ["L2", "L3"] and status["guard_wired"] is True
    assert panel._tier_label(status) == "codex_cli · guard wired (L2+L3)"
    refused = gp.coverage_verdict("codex_cli", _layers(A, A, U))
    status = panel.adapter_status(lambda: SimpleNamespace(name="codex_cli"),
                                  lambda _n: refused)
    assert panel._tier_label(status) == "codex_cli · guard NOT wired (no layer live)"
