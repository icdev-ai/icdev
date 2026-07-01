# CUI // SP-CTI
"""Tests for check_yaml_duplicate_keys coherence check.

Builds synthetic args/*.yaml under tmp_path, points PROJECT_ROOT at them,
runs the check, and asserts expected status + violations.
"""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


def _make_repo(tmp_path: pathlib.Path, files: dict) -> pathlib.Path:
    """Write files dict {rel_path: body} under tmp_path/repo and return root."""
    root = tmp_path / "repo"
    args_dir = root / "args"
    args_dir.mkdir(parents=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_clean_repo_passes(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, {
        "args/clean.yaml": (
            "foo:\n"
            "  a: 1\n"
            "bar:\n"
            "  b: 2\n"
        ),
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_yaml_duplicate_keys()
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.message}"
    assert result.check_id == "yaml_duplicate_keys"
    assert result.missing == []


def test_top_level_duplicate_fails(tmp_path, monkeypatch):
    """Two commits adding the same top-level key (llm_config.yaml agent_loop incident)."""
    repo = _make_repo(tmp_path, {
        "args/llm_config.yaml": (
            "providers:\n"
            "  openai:\n"
            "    model: gpt\n"
            "agent_loop:\n"
            "  max_iterations: 5\n"
            "agent_loop:\n"
            "  max_iterations: 10\n"
        ),
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_yaml_duplicate_keys()
    assert result.status == "fail", f"Expected fail, got {result.status}: {result.message}"
    assert any("agent_loop" in v for v in result.missing)
    assert any("llm_config.yaml" in v for v in result.missing)


def test_nested_duplicate_warns_not_fails(tmp_path, monkeypatch):
    """A duplicate key nested inside a block/list item warns but does not fail the gate."""
    repo = _make_repo(tmp_path, {
        "args/genesis_config.yaml": (
            "reflexes:\n"
            "  gap_tasks_created:\n"
            "    threshold: 0\n"
            "    operator: gte\n"
            "    threshold: 0\n"
            "    operator: gte\n"
        ),
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_yaml_duplicate_keys()
    assert result.status == "warn", f"Expected warn, got {result.status}: {result.message}"
    assert any("threshold" in v for v in result.extra)


def test_parse_error_fails(tmp_path, monkeypatch):
    """A YAML parse error (bad indentation) fails the gate outright."""
    repo = _make_repo(tmp_path, {
        "args/package_exclusions.yaml": (
            "exclusions:\n"
            "  - path: apps/fathomdesk\n"
            "    category: marketplace\n"
            "- path: tools/trading\n"
            "    category: marketplace\n"
        ),
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_yaml_duplicate_keys()
    assert result.status == "fail", f"Expected fail, got {result.status}: {result.message}"
    assert any("parse error" in v for v in result.missing)


def test_multi_file_only_violators_flagged(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, {
        "args/clean.yaml": "foo:\n  a: 1\n",
        "args/dirty.yaml": "foo:\n  a: 1\nfoo:\n  a: 2\n",
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_yaml_duplicate_keys()
    assert result.status == "fail"
    assert any("dirty.yaml" in v for v in result.missing)
    assert not any("clean.yaml" in v for v in result.missing)


def test_scoped_to_changed_files(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, {
        "args/a.yaml": "foo:\n  a: 1\nfoo:\n  a: 2\n",
        "args/b.yaml": "bar:\n  a: 1\nbar:\n  a: 2\n",
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_yaml_duplicate_keys(changed_files=[pathlib.Path("args/a.yaml")])
    assert result.status == "fail"
    assert any("a.yaml" in v for v in result.missing)
    assert not any("b.yaml" in v for v in result.missing)


def test_registry_and_fix_tier():
    assert "yaml_duplicate_keys" in cc.CHECK_REGISTRY
    assert cc._FIX_REGISTRY.get("yaml_duplicate_keys") == "skip"
