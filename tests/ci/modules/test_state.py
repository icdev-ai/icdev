# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/modules/state.py.

These exercise the public contract documented in
docs/rewrite/adw/specs/tools/ci/modules/state.md so the rewrite stays
verifiable against the spec rather than against any implementation.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.modules import state as state_mod  # noqa: E402
from tools.ci.modules.state import CORE_FIELDS, ICDevState  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# CORE_FIELDS contract
# ────────────────────────────────────────────────────────────────────────────


def test_core_fields_minimum_set():
    for field in (
        "run_id", "issue_number", "branch_name",
        "plan_file", "issue_class", "platform", "project_id",
    ):
        assert field in CORE_FIELDS, f"missing core field: {field}"


# ────────────────────────────────────────────────────────────────────────────
# Construction + paths
# ────────────────────────────────────────────────────────────────────────────


def test_state_dir_under_repo_agents(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    s = ICDevState("R1")
    assert s.state_dir == tmp_path / "R1"
    assert s.state_file == tmp_path / "R1" / "icdev_state.json"


# ────────────────────────────────────────────────────────────────────────────
# update() — whitelisting + None drop
# ────────────────────────────────────────────────────────────────────────────


def test_update_keeps_only_core_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    s = ICDevState("R1")
    s.update(branch_name="feat-1", random_unknown="x", project_id="P")
    assert s.get("branch_name") == "feat-1"
    assert s.get("project_id") == "P"
    assert s.get("random_unknown") is None


def test_update_drops_none_values(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    s = ICDevState("R1")
    s.update(branch_name="feat-1")
    s.update(branch_name=None)  # explicit None must NOT clobber
    assert s.get("branch_name") == "feat-1"


# ────────────────────────────────────────────────────────────────────────────
# save / load roundtrip
# ────────────────────────────────────────────────────────────────────────────


def test_save_then_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    s = ICDevState("R-roundtrip")
    s.update(
        branch_name="feat-x",
        issue_number="42",
        plan_file="plan.md",
        platform="github",
    )
    s.save("plan")
    assert s.state_file.exists()

    loaded = ICDevState.load("R-roundtrip")
    assert loaded.get("branch_name") == "feat-x"
    assert loaded.get("issue_number") == "42"
    assert loaded.get("platform") == "github"
    assert loaded.run_id == "R-roundtrip"


def test_load_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    s = ICDevState.load("R-no-such")
    assert s.run_id == "R-no-such"
    assert s.get("branch_name") is None


def test_load_corrupt_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    target = tmp_path / "R-bad" / "icdev_state.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    s = ICDevState.load("R-bad")
    assert s.run_id == "R-bad"
    assert s.get("branch_name") is None


def test_load_non_object_payload_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    target = tmp_path / "R-list" / "icdev_state.json"
    target.parent.mkdir(parents=True)
    target.write_text('["not","an","object"]', encoding="utf-8")
    s = ICDevState.load("R-list")
    assert s.get("anything") is None


# ────────────────────────────────────────────────────────────────────────────
# stdin/stdout pipe transport
# ────────────────────────────────────────────────────────────────────────────


def test_to_stdout_emits_filtered_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    s = ICDevState("R1")
    s.update(branch_name="feat-1", platform="github")
    s._store["non_core"] = "leak-me-not"  # internal storage allows extras
    s.to_stdout()
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["branch_name"] == "feat-1"
    assert payload["platform"] == "github"
    assert payload["run_id"] == "R1"
    assert "non_core" not in payload


def test_from_stdin_returns_none_on_tty(monkeypatch):
    class _TTY:
        def isatty(self):
            return True

        def read(self):
            return ""

    monkeypatch.setattr(sys, "stdin", _TTY())
    assert ICDevState.from_stdin() is None


def test_from_stdin_parses_piped_payload(monkeypatch):
    payload = json.dumps({
        "run_id": "R-piped",
        "branch_name": "feat-z",
        "platform": "github",
    })
    fake = io.StringIO(payload)
    fake.isatty = lambda: False
    monkeypatch.setattr(sys, "stdin", fake)
    s = ICDevState.from_stdin()
    assert s is not None
    assert s.run_id == "R-piped"
    assert s.get("branch_name") == "feat-z"


def test_from_stdin_returns_none_on_invalid_json(monkeypatch):
    fake = io.StringIO("{not json")
    fake.isatty = lambda: False
    monkeypatch.setattr(sys, "stdin", fake)
    assert ICDevState.from_stdin() is None


def test_from_stdin_returns_none_when_run_id_missing(monkeypatch):
    fake = io.StringIO('{"branch_name":"feat-x"}')
    fake.isatty = lambda: False
    monkeypatch.setattr(sys, "stdin", fake)
    assert ICDevState.from_stdin() is None


def test_from_stdin_returns_none_when_payload_empty(monkeypatch):
    fake = io.StringIO("")
    fake.isatty = lambda: False
    monkeypatch.setattr(sys, "stdin", fake)
    assert ICDevState.from_stdin() is None


# ────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ────────────────────────────────────────────────────────────────────────────


def test_to_dict_returns_copy():
    s = ICDevState("R")
    s.update(branch_name="b")
    d = s.to_dict()
    d["leak"] = "x"
    assert s.get("leak") is None


def test_repr_includes_run_id():
    s = ICDevState("R1")
    assert "R1" in repr(s)


def test_no_db_imports():
    """Spec rule: this module must NOT touch the DB or LLM stack."""
    src = pathlib.Path(state_mod.__file__).read_text(encoding="utf-8")
    assert "tools.db" not in src
    assert "tools.llm" not in src
    assert "psycopg2" not in src
