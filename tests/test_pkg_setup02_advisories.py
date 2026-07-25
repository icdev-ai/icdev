# CUI // SP-CTI
"""Tests for `icdev setup` min_il + dependency advisories (pkg-setup-02).

Toggling blindly is how an operator enables a canvas its impact level forbids,
or one whose backing component is off. The TUI now:
  - marks components whose declared min_il exceeds the configured impact level
    and requires an explicit confirm to enable them, and
  - warns when enabling a component whose registry-declared prerequisite is off,
    offering to enable it too.

Advisory, never blocking; everything derives from the registry (min_il +
depends_on), not hardcoded pairs. Run:
    pytest tests/test_pkg_setup02_advisories.py -v --tb=short
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cli.setup import (
    SetupState,
    _configured_impact_level,
    _il_rank,
    render,
    run_plain,
)
from tools.config.component_registry import get_registry


def _state(tmp_path, env_text: str = "") -> SetupState:
    env = tmp_path / ".env"
    env.write_text(env_text, encoding="utf-8")
    return SetupState(get_registry(), env)


# ── impact-level ordering + resolution ─────────────────────────────────────

def test_il_rank_order():
    assert _il_rank("IL2") < _il_rank("IL4") < _il_rank("IL5") < _il_rank("IL6")
    assert _il_rank("") == _il_rank("IL2") == 0  # blank is most permissive


def test_impact_level_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ICDEV_IMPACT_LEVEL=IL5\n", encoding="utf-8")
    assert _configured_impact_level(env) == "IL5"


def test_impact_level_defaults_to_il2(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# nothing\n", encoding="utf-8")
    assert _configured_impact_level(env) == "IL2"


# ── restriction marking ────────────────────────────────────────────────────

def test_high_il_component_restricted_at_il2(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL2\n")
    # sdc_demo is declared min_il IL4 in the registry.
    sdc_demo = next(r for r in st.rows if r.key == "sdc_demo")
    assert sdc_demo.min_il == "IL4"
    assert st.is_restricted(sdc_demo) is True


def test_high_il_component_not_restricted_at_matching_level(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL4\n")
    sdc_demo = next(r for r in st.rows if r.key == "sdc_demo")
    assert st.is_restricted(sdc_demo) is False


# ── dependency declaration is registry-driven ──────────────────────────────

def test_sdc_demo_declares_dependency_on_sdc(tmp_path):
    st = _state(tmp_path)
    sdc_demo = next(r for r in st.rows if r.key == "sdc_demo")
    assert sdc_demo.depends_on == ["sdc"]


def test_unmet_dependencies_lists_off_prereqs(tmp_path):
    st = _state(tmp_path)  # everything off
    sdc_demo = next(r for r in st.rows if r.key == "sdc_demo")
    unmet = st.unmet_dependencies(sdc_demo)
    assert [r.key for r in unmet] == ["sdc"]


def test_unmet_dependencies_empty_when_prereq_on(tmp_path):
    reg = get_registry()
    sdc = reg.get("sdc")
    st = _state(tmp_path, f"ICDEV_IMPACT_LEVEL=IL6\n{sdc.env_flag}=true\n"
                          "ICDEV_SECURITY_ENABLED=true\n")
    sdc_demo = next(r for r in st.rows if r.key == "sdc_demo")
    assert st.unmet_dependencies(sdc_demo) == []


# ── toggle_with_advisories: min_il gate ────────────────────────────────────

def test_confirm_restricted_true_enables(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL2\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == "sdc_demo")
    res = st.toggle_with_advisories(
        idx, confirm_restricted=lambda r: True, confirm_deps=lambda r, d: False)
    assert res["action"] == "enabled"
    assert st.rows[idx].enabled is True


def test_confirm_restricted_false_leaves_off(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL2\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == "sdc_demo")
    res = st.toggle_with_advisories(idx, confirm_restricted=lambda r: False)
    assert res["action"] == "skipped_restricted"
    assert st.rows[idx].enabled is False


def test_not_restricted_component_needs_no_confirm(tmp_path):
    # A default IL2 canvas at IL2 is not restricted; enabling just works.
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL2\n")
    row = next(r for r in st.rows if r.min_il in ("", "IL2") and not r.depends_on)
    idx = st.rows.index(row)
    called = {"n": 0}

    def _boom(r):
        called["n"] += 1
        return True

    res = st.toggle_with_advisories(idx, confirm_restricted=_boom)
    assert res["action"] == "enabled"
    assert called["n"] == 0  # confirm never invoked for a non-restricted row


# ── toggle_with_advisories: dependency offer ───────────────────────────────

def test_dependency_enabled_when_offered_yes(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL6\n")  # not restricted at IL6
    idx = next(i for i, r in enumerate(st.rows) if r.key == "sdc_demo")
    res = st.toggle_with_advisories(idx, confirm_deps=lambda r, d: True)
    assert res["enabled_deps"] == ["sdc"]
    assert next(r for r in st.rows if r.key == "sdc").enabled is True


def test_dependency_not_enabled_when_offered_no(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL6\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == "sdc_demo")
    res = st.toggle_with_advisories(idx, confirm_deps=lambda r, d: False)
    assert res["unmet_deps"] == ["sdc"]
    assert res["enabled_deps"] == []
    assert next(r for r in st.rows if r.key == "sdc").enabled is False


def test_disable_is_immediate_no_prompts(tmp_path):
    reg = get_registry()
    sdc = reg.get("sdc")
    st = _state(tmp_path, f"{sdc.env_flag}=true\nICDEV_SECURITY_ENABLED=true\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == "sdc")
    res = st.toggle_with_advisories(idx, confirm_restricted=lambda r: False)
    assert res["action"] == "disabled"
    assert st.rows[idx].enabled is False


# ── rendering surfaces the advisories ───────────────────────────────────────

def test_render_shows_impact_level_and_markers(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL2\n")
    out = render(st, cursor=0)
    assert "impact level: IL2" in out
    assert "(!)" in out            # at least one restricted component marked
    assert "needs:sdc" in out      # sdc_demo's declared dependency shown


# ── plain mode drives the same advisories via stdin ────────────────────────

def test_plain_mode_confirms_restricted_and_deps(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL2\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == "sdc_demo")
    # toggle sdc_demo (restricted + dep): answer "y" (enable anyway), "y" (deps), then quit
    inp = io.StringIO(f"{idx + 1}\ny\ny\nq\n")
    run_plain(st, in_stream=inp, out_stream=io.StringIO())
    assert st.rows[idx].enabled is True
    assert next(r for r in st.rows if r.key == "sdc").enabled is True


def test_plain_mode_decline_restricted(tmp_path):
    st = _state(tmp_path, "ICDEV_IMPACT_LEVEL=IL2\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == "sdc_demo")
    inp = io.StringIO(f"{idx + 1}\nn\nq\n")  # decline the min_il confirm
    run_plain(st, in_stream=inp, out_stream=io.StringIO())
    assert st.rows[idx].enabled is False
