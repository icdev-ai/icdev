# CUI // SP-CTI
"""Tests: child apps must inherit anti-hallucination grounding (trust-cite-05).

Covers:
    - child_app_generator.DIRECTORY_TREE includes tools/quality
    - forge_validator FORGE-03c grounding-presence check (pass/fail)
"""

import importlib

cag = importlib.import_module("tools.builder.child_app_generator")
fv = importlib.import_module("tools.builder.forge_validator")


def test_directory_tree_includes_quality():
    assert "tools/quality" in cag.DIRECTORY_TREE


def _grounding_check(checks):
    return next((c for c in checks if c.check_id == "FORGE-03c"), None)


def test_validator_passes_when_grounding_present(tmp_path):
    q = tmp_path / "tools" / "quality"
    q.mkdir(parents=True)
    (tmp_path / "tools" / "db").mkdir(parents=True)
    (q / "content_grounding.py").write_text("# stub\n", encoding="utf-8")
    (q / "citation_grounding.py").write_text("# stub\n", encoding="utf-8")
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "pass"


def test_validator_fails_when_grounding_missing(tmp_path):
    # tools/ exists with some scripts but no grounding modules
    (tmp_path / "tools" / "db").mkdir(parents=True)
    (tmp_path / "tools" / "db" / "x.py").write_text("# stub\n", encoding="utf-8")
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "citation_grounding.py" in check.actual
