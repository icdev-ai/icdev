#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the registry-driven canvas completeness gate (idp-score-05).

CLAUDE.md declares the 8-component new-page rule mandatory: "Never ship a
template without all 7 other components." That rule has two implementations
that enumerate it from opposite directions:

  * ``check_new_page_completeness``  — walks ``tools/dashboard/templates/*/page.html``
  * ``check_canvas_completeness``    — walks ``args/component_registry.yaml``

They disagreed on severity. The filesystem-driven one returned ``fail`` and is
declared ``blocking`` in ``args/security_gates.yaml``; the registry-driven one
returned ``warn``, so a canvas the filesystem walk cannot see (no ``page.html``
under its own key) could ship incomplete and nothing blocked. These tests pin
the reconciliation: both fail, both are declared blocking, and both honour the
same ``args/page_completeness_whitelist.yaml``.

They also pin the ``nav_link`` applicability fix. The registry's ``completeness``
block declares APPLICABILITY — its own convention comment reads "7. nav_link —
applies -> ``nav_link: true``" — but the validator read an explicit
``nav_link: false`` as "declared and missing" rather than "does not apply". That
made ``aiify_compat`` (a 301-redirect alias whose sidebar link is owned by the
real ``aiify`` canvas) the single permanent failure blocking the flip.

Patching is shim-aware: ``tools.*`` and ``icdev.tools.*`` are distinct module
objects, so we resolve the canonical module via ``importlib.import_module`` and
``monkeypatch.setattr`` its globals, then call the function off that same object.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixture builders — a synthetic canvas that satisfies all 8 points on disk
# ---------------------------------------------------------------------------


def _build_canvas_tree(root: Path) -> None:
    """Lay down the files the 8-point validator stats for canvas key 'synth'."""
    pkg = root / "tools" / "synth_canvas"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "blueprint.py").write_text(
        "from flask import Blueprint\n"
        "bp = Blueprint('synth', __name__)\n\n\n"
        "@bp.route('/')\n"
        "def index():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (pkg / "constants.py").write_text("KEY = 'synth'\n", encoding="utf-8")
    (pkg / "engine.py").write_text("def analyze():\n    pass\n", encoding="utf-8")

    (pkg / "db" / "migrations").mkdir(parents=True)
    (pkg / "db" / "migrations" / "001_init.sql").write_text("", encoding="utf-8")

    for prefix in ("", "icdev"):
        tmpl = root.joinpath(prefix, "tools", "dashboard", "templates", "synth")
        tmpl.mkdir(parents=True)
        (tmpl / "page.html").write_text('{% extends "base.html" %}', encoding="utf-8")

    (root / "tools" / "iqe" / "adapters").mkdir(parents=True)
    (root / "tools" / "iqe" / "adapters" / "synth.py").write_text("", encoding="utf-8")
    (root / "context" / "iqe" / "queries" / "synth").mkdir(parents=True)
    (root / "context" / "iqe" / "queries" / "synth" / "default.yaml").write_text("", encoding="utf-8")


def _write_registry(root: Path, completeness: dict, nav: dict | None = None) -> Path:
    """Write a one-component registry whose `completeness` block is the variable."""
    path = root / "args" / "component_registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "components": [
                    {
                        "key": "synth",
                        "kind": "canvas",
                        "cli_name": "synth",
                        "display_name": "Synthetic Canvas",
                        "description": "Test canvas",
                        "env_flag": "ICDEV_SYNTH_ENABLED",
                        "extra_env_flags": [],
                        "default_enabled": False,
                        "module": "tools.synth_canvas.blueprint",
                        "blueprint_attr": "bp",
                        "url_prefix": "/synth",
                        "min_il": "IL2",
                        "default_roles": [],
                        "nav": nav if nav is not None else {},
                        "iqe": {
                            "adapter_module": "tools.iqe.adapters.synth",
                            "collections": ["synth.items"],
                        },
                        "completeness": completeness,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


_BASE_COMPLETENESS = {
    "blueprint": True,
    "template": "tools/dashboard/templates/synth/page.html",
    "constants": "tools/synth_canvas/constants.py",
    "db_migration": "tools/synth_canvas/db/migrations",
    "iqe_adapter": True,
    "seed_queries": "context/iqe/queries/synth",
}


def _validate(root: Path, completeness: dict, nav: dict | None = None):
    from tools.config.component_registry import ComponentRegistry

    registry_path = _write_registry(root, completeness, nav)
    registry = ComponentRegistry(registry_path=registry_path, env={})
    return registry.validate_canvas_completeness("synth", repo_root=root)


def _nav_item(report):
    return next(item for item in report.items if item.point == "nav_link")


@pytest.fixture()
def canvas_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _build_canvas_tree(root)
    return root


# ---------------------------------------------------------------------------
# nav_link applicability — `completeness` declares APPLIES, not PRESENT
# ---------------------------------------------------------------------------


def test_nav_link_true_is_required_and_present(canvas_root: Path):
    report = _validate(canvas_root, {**_BASE_COMPLETENESS, "nav_link": True})
    item = _nav_item(report)
    assert item.required is True
    assert item.present is True
    assert report.passed


def test_nav_link_false_declares_the_point_not_applicable(canvas_root: Path):
    """An explicit `nav_link: false` opts the point out — this is aiify_compat."""
    report = _validate(canvas_root, {**_BASE_COMPLETENESS, "nav_link": False})
    item = _nav_item(report)
    assert item.required is False, "explicit `nav_link: false` must mean 'does not apply'"
    assert item.present is False
    assert report.passed, "a declared-N/A point must not fail the canvas"
    assert "not applicable" in item.message


def test_missing_nav_link_key_still_fails(canvas_root: Path):
    """Opting out must be explicit — a forgotten declaration is still a failure."""
    completeness = dict(_BASE_COMPLETENESS)
    completeness.pop("nav_link", None)
    report = _validate(canvas_root, completeness, nav={})
    item = _nav_item(report)
    assert item.required is True, "a MISSING key must not silently excuse the canvas"
    assert item.present is False
    assert not report.passed


def test_nav_section_satisfies_the_point_without_a_completeness_key(canvas_root: Path):
    completeness = dict(_BASE_COMPLETENESS)
    completeness.pop("nav_link", None)
    report = _validate(canvas_root, completeness, nav={"section": "Canvases", "label": "Synth"})
    item = _nav_item(report)
    assert item.required is True
    assert item.present is True
    assert report.passed


# ---------------------------------------------------------------------------
# Severity — the registry-driven check blocks instead of warning
# ---------------------------------------------------------------------------


class _StubItem:
    def __init__(self, point: str, required: bool, present: bool, message: str = ""):
        self.point = point
        self.required = required
        self.present = present
        self.path = None
        self.message = message


class _StubReport:
    def __init__(self, items):
        self.items = items
        self.passed = all(i.present or not i.required for i in items)


class _StubComponent:
    def __init__(self, key: str):
        self.key = key


class _StubRegistry:
    """Minimal stand-in for ComponentRegistry — keys map to their item lists."""

    def __init__(self, reports: dict):
        self._reports = reports

    def iter_canvases(self):
        return [_StubComponent(k) for k in self._reports]

    def validate_canvas_completeness(self, key: str):
        return _StubReport(self._reports[key])


def _patch_registry(monkeypatch, reports: dict):
    """Patch get_registry where check_canvas_completeness imports it from."""
    registry_mod = importlib.import_module("tools.config.component_registry")
    monkeypatch.setattr(registry_mod, "get_registry", lambda *a, **k: _StubRegistry(reports))


_INCOMPLETE = [_StubItem("iqe_integration", required=True, present=False, message="missing IQE adapter")]
_COMPLETE = [_StubItem("nav_link", required=False, present=False, message="not applicable")]


def test_incomplete_canvas_fails_rather_than_warns(monkeypatch):
    coherence = importlib.import_module("tools.workflow.coherence_checker")
    _patch_registry(monkeypatch, {"broken": _INCOMPLETE})
    monkeypatch.setattr(coherence, "_load_page_completeness_whitelist", set)

    result = coherence.check_canvas_completeness()

    assert result.status == "fail", "the gate must block, not warn — CLAUDE.md calls the rule mandatory"
    assert any("broken" in m for m in result.missing)


def test_complete_canvas_passes(monkeypatch):
    coherence = importlib.import_module("tools.workflow.coherence_checker")
    _patch_registry(monkeypatch, {"ok": _COMPLETE})
    monkeypatch.setattr(coherence, "_load_page_completeness_whitelist", set)

    assert coherence.check_canvas_completeness().status == "pass"


def test_whitelisted_canvas_is_skipped(monkeypatch):
    """Both halves of the gate share one grandfather list."""
    coherence = importlib.import_module("tools.workflow.coherence_checker")
    _patch_registry(monkeypatch, {"broken": _INCOMPLETE})
    monkeypatch.setattr(coherence, "_load_page_completeness_whitelist", lambda: {"broken"})

    result = coherence.check_canvas_completeness()

    assert result.status == "pass"
    assert "broken" in result.extra


# ---------------------------------------------------------------------------
# The two implementations of the rule agree
# ---------------------------------------------------------------------------


def test_both_completeness_gates_are_declared_blocking():
    repo_root = Path(__file__).resolve().parents[1]
    gates = yaml.safe_load((repo_root / "args" / "security_gates.yaml").read_text(encoding="utf-8"))

    for gate in ("new_page_completeness", "canvas_completeness"):
        assert gate in gates, f"{gate} must be declared in args/security_gates.yaml"
        assert "incomplete_canvas_page" in (gates[gate].get("blocking") or []), (
            f"{gate} must block on incomplete_canvas_page — the two halves of the "
            "8-component rule may not disagree on severity"
        )


def test_live_registry_passes_the_gate():
    """Every registered canvas is complete today, so the flip costs nothing."""
    coherence = importlib.import_module("tools.workflow.coherence_checker")
    result = coherence.check_canvas_completeness()
    assert result.status == "pass", result.missing
