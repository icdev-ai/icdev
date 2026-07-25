# CUI // SP-CTI
"""Tests for `icdev status` fresh-install detection (pkg-docs-01).

The reporting user's install "failed" because nothing told them `icdev init`
existed. A fresh `pip install icdev` user who runs `icdev status` before
`icdev init` used to see an all-off table that looks like a broken install.
`status` now detects a missing .env / .claude and prints the exact init command.

Run: pytest tests/test_pkg_docs01_status_hint.py -v --tb=short
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cli import enable as enable_mod


def test_uninitialized_when_no_env(tmp_path):
    env = tmp_path / ".env"  # does not exist
    assert enable_mod._looks_uninitialized(env) is True


def test_uninitialized_when_env_but_no_claude(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ICDEV_DIC_ENABLED=true\n", encoding="utf-8")
    # no .claude dir
    assert enable_mod._looks_uninitialized(env) is True


def test_initialized_when_env_and_claude_present(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ICDEV_DIC_ENABLED=true\n", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    assert enable_mod._looks_uninitialized(env) is False


def test_init_hint_names_the_exact_command(tmp_path):
    env = tmp_path / ".env"
    hint = enable_mod._init_hint(env)
    assert "icdev init" in hint
    # targets the .env's own directory
    assert str(tmp_path) in hint or "icdev init ." in hint
    assert "airgap-pip-install.md" in hint


def test_status_main_prints_hint_on_fresh_project(tmp_path, capsys):
    env = tmp_path / ".env"  # uninitialized
    rc = enable_mod.main(["status", "--env-file", str(env)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "icdev init" in out
    # A bare status table (the [ON]/[off] rows) must NOT be shown here.
    assert "[off]" not in out and "[ON ]" not in out


def test_status_json_flags_initialized_false(tmp_path, capsys):
    env = tmp_path / ".env"
    rc = enable_mod.main(["status", "--json", "--env-file", str(env)])
    out = capsys.readouterr().out
    assert rc == 0
    import json
    data = json.loads(out)
    assert data["initialized"] is False
    assert "icdev init" in data["init_hint"]


def test_status_normal_table_when_initialized(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("ICDEV_DIC_ENABLED=true\n", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    rc = enable_mod.main(["status", "--env-file", str(env)])
    out = capsys.readouterr().out
    assert rc == 0
    # Real status table is shown (has the Enabled summary line).
    assert "Enabled:" in out
    assert "icdev init" not in out
