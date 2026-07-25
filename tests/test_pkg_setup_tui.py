# CUI // SP-CTI
"""Tests for `icdev setup` — the stdlib-only feature-toggle TUI (pkg-setup-01).

`icdev setup` is the PRIMARY, browser-free enable/disable surface for a fresh
air-gap install. It must be registry-driven (never a hand-maintained list),
show sub-pages indented under their parent so a missing page maps obviously to
an off component, and persist changes to .env with an audit event per change.

These tests exercise the pure state model + plain-menu mode (the raw-key loop is
terminal-bound and covered by manual smoke). Run:
    pytest tests/test_pkg_setup_tui.py -v --tb=short
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cli.setup import (
    KIND_ORDER,
    SetupState,
    build_rows,
    render,
    run_plain,
)
from tools.config.component_registry import get_registry


def _state(tmp_path, env_text: str = "") -> SetupState:
    env = tmp_path / ".env"
    env.write_text(env_text, encoding="utf-8")
    return SetupState(get_registry(), env)


# ── registry-driven completeness ───────────────────────────────────────────

def test_every_registry_flag_component_is_a_row(tmp_path):
    reg = get_registry()
    rows = build_rows(reg, tmp_path / ".env")
    row_keys = {r.key for r in rows}
    reg_keys = {c.key for c in reg.list_all() if c.env_flag}
    assert row_keys == reg_keys, "TUI rows must match registry flag components 1:1"


def test_rows_grouped_in_kind_order(tmp_path):
    rows = build_rows(get_registry(), tmp_path / ".env")
    seen_order = []
    for r in rows:
        if r.kind not in seen_order:
            seen_order.append(r.kind)
    # Observed kinds appear in the canonical order (subset-preserving).
    filtered = [k for k in KIND_ORDER if k in seen_order]
    assert seen_order == filtered


# ── sub-page discoverability (the confusion this card fixes) ────────────────

def test_dic_subpages_shown_and_exclude_overview(tmp_path):
    rows = build_rows(get_registry(), tmp_path / ".env")
    dic = next(r for r in rows if r.key == "dic")
    hrefs = {h for _, h in dic.sub_pages}
    # Tech Writer + DocDrift (the pages the reporter could not find) are present.
    assert "/document-intelligence/techwriter" in hrefs
    assert "/document-intelligence/docdrift" in hrefs
    # The overview/parent link is not repeated as a sub-page.
    assert "/document-intelligence/" not in hrefs
    assert "/document-intelligence" not in hrefs


def test_render_indents_subpages_under_parent(tmp_path):
    st = _state(tmp_path)
    out = render(st, cursor=0)
    assert "Tech Writer" in out
    assert "↳" in out  # sub-page marker


# ── enablement reflects .env ───────────────────────────────────────────────

def test_enabled_reflects_env(tmp_path):
    reg = get_registry()
    # pick any component with a single env flag (no extra flags) for a clean test
    comp = next(c for c in reg.list_all() if c.env_flag and not c.extra_env_flags)
    st = _state(tmp_path, f"{comp.env_flag}=true\n")
    row = next(r for r in st.rows if r.key == comp.key)
    assert row.enabled is True
    assert row.original is True
    assert row.dirty is False


def test_multi_flag_component_needs_all_flags_on(tmp_path):
    reg = get_registry()
    comp = next((c for c in reg.list_all() if c.env_flag and c.extra_env_flags), None)
    if comp is None:
        return  # no multi-flag component in registry; nothing to assert
    # only the primary flag on → component considered OFF
    st = _state(tmp_path, f"{comp.env_flag}=true\n")
    row = next(r for r in st.rows if r.key == comp.key)
    assert row.enabled is False


# ── toggling + dirty tracking ──────────────────────────────────────────────

def test_toggle_sets_dirty_and_pending_updates(tmp_path):
    reg = get_registry()
    comp = next(c for c in reg.list_all() if c.env_flag)
    st = _state(tmp_path, f"{comp.env_flag}=false\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == comp.key)
    st.toggle(idx)
    assert st.dirty
    updates = st.pending_updates()
    assert updates[comp.env_flag] == "true"
    for extra in comp.extra_env_flags:
        assert updates[extra] == "true"


# ── profile apply ──────────────────────────────────────────────────────────

def test_apply_profile_sets_membership(tmp_path):
    st = _state(tmp_path)
    res = st.apply_profile("air-gap")
    assert res["ok"]
    from tools.config.core_profile import get_profile
    wanted = set(get_profile("air-gap")["default_enabled_components"])
    on_keys = {r.key for r in st.rows if r.enabled}
    # every enabled row is in the profile; every profile key that matched is on
    assert on_keys == (wanted & {r.key for r in st.rows})
    assert res["unmatched_keys"] == []


def test_apply_unknown_profile_reports_error(tmp_path):
    st = _state(tmp_path)
    res = st.apply_profile("nope")
    assert res["ok"] is False


# ── write persists + logs + clears dirty ───────────────────────────────────

def test_write_persists_and_clears_dirty(tmp_path):
    reg = get_registry()
    comp = next(c for c in reg.list_all() if c.env_flag)
    st = _state(tmp_path, f"{comp.env_flag}=false\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == comp.key)
    st.toggle(idx)
    res = st.write()
    assert res["written"] is True
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"{comp.env_flag}=true" in text
    # dirty cleared after commit
    assert not st.dirty


def test_write_noop_when_no_changes(tmp_path):
    st = _state(tmp_path)
    res = st.write()
    assert res["written"] is False


# ── plain (non-TTY) mode ───────────────────────────────────────────────────

def test_plain_mode_toggle_write_quit(tmp_path):
    reg = get_registry()
    comp = next(c for c in reg.list_all() if c.env_flag and not c.extra_env_flags)
    st = _state(tmp_path, f"{comp.env_flag}=false\n")
    idx = next(i for i, r in enumerate(st.rows) if r.key == comp.key)
    out = io.StringIO()
    inp = io.StringIO(f"{idx + 1}\nw\nq\n")
    rc = run_plain(st, in_stream=inp, out_stream=out)
    assert rc == 0
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"{comp.env_flag}=true" in text


def test_plain_mode_apply_profile(tmp_path):
    st = _state(tmp_path)
    out = io.StringIO()
    inp = io.StringIO("p air-gap\nq\n")
    run_plain(st, in_stream=inp, out_stream=out)
    assert any(r.enabled for r in st.rows)


def test_plain_mode_eof_exits_cleanly(tmp_path):
    st = _state(tmp_path)
    rc = run_plain(st, in_stream=io.StringIO(""), out_stream=io.StringIO())
    assert rc == 0
