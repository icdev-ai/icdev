#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the registry-driven `canvas_completeness` coherence check.

Background (idp-score-05): CLAUDE.md declares the 8-point new-dashboard-page
gate mandatory — "Never ship a template without all 7 other components." Two
checks implement that one rule from opposite directions:

  * ``check_new_page_completeness`` walks ``tools/dashboard/templates/*/page.html``
    (filesystem-driven). It has always returned ``fail`` and is declared blocking
    in ``args/security_gates.yaml``.
  * ``check_canvas_completeness`` walks ``args/component_registry.yaml`` and runs
    ``component_registry.validate_canvas_completeness`` per canvas
    (registry-driven). It returned ``warn``, so it never blocked anything — the
    "mandatory" rule was advisory for every canvas the filesystem check could
    not see (a registered canvas whose templates are not named ``page.html``).

These tests pin the reconciliation: same severity, same grandfather list, and an
explicit ``completeness.nav_link: false`` waiver honoured rather than reported as
an unmet requirement.

Patching is shim-aware: we import the canonical ``tools.workflow.coherence_checker``
module object and ``monkeypatch.setattr`` its globals, then call the function off
that same object (the ``tools.*`` and ``icdev.tools.*`` shims are distinct
objects, so a string-form patch target can miss).
"""

import importlib

import pytest
import yaml

from tools.config.component_registry import ComponentRegistry


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _registry_with(tmp_path, completeness, nav=None, key="synth"):
    """Write a one-canvas registry whose files do NOT exist on disk.

    Every filesystem-backed point therefore reports absent; only the declaration
    driven points (nav_link) can pass. That is exactly what we want when the
    subject under test is severity and waiver handling, not path resolution.
    """
    registry_yaml = tmp_path / "component_registry.yaml"
    registry_yaml.write_text(
        yaml.safe_dump(
            {
                "components": [
                    {
                        "key": key,
                        "kind": "canvas",
                        "display_name": "Synthetic Canvas",
                        "description": "Test canvas",
                        "env_flag": "ICDEV_SYNTH_ENABLED",
                        "default_enabled": False,
                        "module": "tools.synth_canvas.blueprint",
                        "blueprint_attr": "bp",
                        "url_prefix": "/synth",
                        "min_il": "IL2",
                        "default_roles": [],
                        "nav": nav or {},
                        "completeness": completeness,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return ComponentRegistry(registry_path=registry_yaml, env={})


@pytest.fixture
def checker():
    return importlib.import_module("tools.workflow.coherence_checker")


def _run(checker, monkeypatch, registry, grandfathered=frozenset()):
    """Run check_canvas_completeness against a synthetic registry."""
    registry_mod = importlib.import_module("tools.config.component_registry")
    monkeypatch.setattr(registry_mod, "get_registry", lambda *a, **k: registry)
    monkeypatch.setattr(
        checker, "_load_grandfathered_canvases", lambda: set(grandfathered)
    )
    return checker.check_canvas_completeness()


# ---------------------------------------------------------------------------
# Severity — the point of idp-score-05
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_incomplete_canvas_fails_and_does_not_warn(
        self, checker, tmp_path, monkeypatch
    ):
        """An incomplete registered canvas must BLOCK, not warn."""
        registry = _registry_with(
            tmp_path,
            {"blueprint": True, "nav_link": True, "template": "nope/page.html"},
        )
        result = _run(checker, monkeypatch, registry)

        assert result.status == "fail", result.message
        assert result.status != "warn"
        assert result.missing, "a failing gate must name the missing point(s)"

    def test_message_points_at_the_three_legitimate_remedies(
        self, checker, tmp_path, monkeypatch
    ):
        """Ship it, waive the point in the registry, or grandfather the canvas."""
        registry = _registry_with(
            tmp_path,
            {"blueprint": True, "nav_link": True, "template": "nope/page.html"},
        )
        result = _run(checker, monkeypatch, registry)

        assert "page_completeness_whitelist.yaml" in result.message
        assert "completeness" in result.message

    def test_complete_canvas_still_passes(self, checker, monkeypatch):
        """The live registry passes — flipping warn->fail costs nothing today."""
        result = checker.check_canvas_completeness()
        assert result.status == "pass", result.missing

    def test_both_completeness_checks_agree_on_severity(self, checker):
        """The registry-driven and filesystem-driven gates are one rule."""
        registry_driven = checker.check_canvas_completeness()
        filesystem_driven = checker.check_new_page_completeness()
        assert registry_driven.status == filesystem_driven.status == "pass"

    def test_both_completeness_checks_declared_blocking(self, checker):
        """args/security_gates.yaml declares the pair at the same severity."""
        gates_path = checker.PROJECT_ROOT / "args" / "security_gates.yaml"
        gates = yaml.safe_load(gates_path.read_text(encoding="utf-8"))
        assert gates["canvas_completeness"]["blocking"]
        assert gates["new_page_completeness"]["blocking"]


# ---------------------------------------------------------------------------
# Grandfather list — shared with the filesystem-driven sibling
# ---------------------------------------------------------------------------


class TestGrandfathering:
    def test_whitelisted_canvas_is_skipped_and_reported(
        self, checker, tmp_path, monkeypatch
    ):
        registry = _registry_with(
            tmp_path,
            {"blueprint": True, "nav_link": True, "template": "nope/page.html"},
        )
        result = _run(checker, monkeypatch, registry, grandfathered={"synth"})

        assert result.status == "pass"
        assert "synth" in result.extra, "a skipped canvas must stay visible"

    def test_grandfather_list_reads_the_shared_whitelist_file(self, checker):
        """Both gates read args/page_completeness_whitelist.yaml."""
        assert (
            checker._load_grandfathered_canvases()
            <= checker._load_page_completeness_whitelist()
        )

    def test_live_whitelist_is_still_empty(self, checker):
        """cvx-tch-01/02/03 retired the legacy list; keep it retired."""
        assert checker._load_grandfathered_canvases() == set()


# ---------------------------------------------------------------------------
# nav_link waiver — how aiify_compat is fixed rather than whitelisted
# ---------------------------------------------------------------------------


class TestNavLinkWaiver:
    def test_explicit_false_waives_the_point(self, tmp_path):
        """`nav_link: false` is a declaration, not an unmet requirement."""
        registry = _registry_with(tmp_path, {"blueprint": True, "nav_link": False})
        report = registry.validate_canvas_completeness("synth", repo_root=tmp_path)
        nav = next(i for i in report.items if i.point == "nav_link")

        assert nav.required is False
        assert nav.present is False
        assert "waived" in nav.message

    def test_absent_declaration_still_requires_a_nav_link(self, tmp_path):
        """Omitting the key must NOT be read as a waiver."""
        registry = _registry_with(tmp_path, {"blueprint": True})
        report = registry.validate_canvas_completeness("synth", repo_root=tmp_path)
        nav = next(i for i in report.items if i.point == "nav_link")

        assert nav.required is True
        assert nav.present is False

    def test_nav_section_satisfies_the_point_without_a_declaration(self, tmp_path):
        registry = _registry_with(
            tmp_path, {"blueprint": True}, nav={"section": "Canvases"}
        )
        report = registry.validate_canvas_completeness("synth", repo_root=tmp_path)
        nav = next(i for i in report.items if i.point == "nav_link")

        assert nav.required is True
        assert nav.present is True

    def test_aiify_compat_passes_the_live_gate(self):
        """The 301-redirect alias is excused by its registry declaration."""
        from tools.config.component_registry import get_registry

        report = get_registry().validate_canvas_completeness("aiify_compat")
        assert report.passed, [
            (i.point, i.message) for i in report.items if i.required and not i.present
        ]
