# CUI // SP-CTI
"""Tests for tools/builder/template_engine.py."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.builder.template_engine import load_manifest, render_tree, _resolve_variables


# Child-app generator helpers (overlay/refactor tests)
from tools.builder.child_app_generator import (
    _build_template_variables,
    _overlay_template,
    _resolve_template_dir,
)


MINIMAL_TEMPLATE = BASE_DIR / "data" / "templates" / "canvases" / "minimal"
INFO_OPS_TEMPLATE = BASE_DIR / "data" / "templates" / "canvases" / "info_ops"
CHILD_APP_FLAVORS = BASE_DIR / "data" / "templates" / "child_apps"


class TestLoadManifest:
    def test_load_minimal_manifest(self):
        manifest = load_manifest(MINIMAL_TEMPLATE)
        assert manifest["name"] == "minimal"
        assert manifest["kind"] == "canvas"
        assert "variables" in manifest
        assert "files" in manifest
        assert "validators" in manifest

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path)


class TestResolveVariables:
    def test_required_variable_missing(self):
        with pytest.raises(ValueError, match="Missing required template variable"):
            _resolve_variables({"key": {"type": "string", "required": True}}, {})

    def test_default_used_when_not_provided(self):
        result = _resolve_variables(
            {"key": {"type": "string", "required": True}, "suffix": {"type": "string", "default": "_dev"}},
            {"key": "demo"},
        )
        assert result == {"key": "demo", "suffix": "_dev"}

    def test_boolean_parsing(self):
        result = _resolve_variables({"enabled": {"type": "boolean", "default": False}}, {"enabled": "true"})
        assert result["enabled"] is True


class TestRenderTree:
    def test_minimal_canvas_generation(self, tmp_path):
        out = tmp_path / "out"
        result = render_tree(
            MINIMAL_TEMPLATE,
            out,
            {
                "key": "demo",
                "display_name": "Demo Canvas",
                "env_flag": "ICDEV_DEMO_ENABLED",
                "url_prefix": "/demo",
            },
        )

        assert result["success"]
        assert not result["errors"]
        assert not result["validation_failures"]

        blueprint = out / "tools" / "demo_canvas" / "blueprint.py"
        constants = out / "tools" / "demo_canvas" / "constants.py"
        page = out / "tools" / "dashboard" / "templates" / "demo" / "page.html"

        assert blueprint.exists()
        assert constants.exists()
        assert page.exists()

        blueprint_text = blueprint.read_text(encoding="utf-8")
        assert 'Blueprint("demo", __name__' in blueprint_text
        assert '"/demo"' in blueprint_text
        assert "ICDEV_DEMO_ENABLED" in blueprint_text

        constants_text = constants.read_text(encoding="utf-8")
        assert 'CANVAS_KEY = "demo"' in constants_text

        page_text = page.read_text(encoding="utf-8")
        assert '{% extends "base.html" %}' in page_text
        assert "Demo Canvas" in page_text

    def test_skip_existing(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        target = out / "tools" / "demo_canvas" / "blueprint.py"
        target.parent.mkdir(parents=True)
        target.write_text("existing", encoding="utf-8")

        result = render_tree(
            MINIMAL_TEMPLATE,
            out,
            {
                "key": "demo",
                "display_name": "Demo Canvas",
                "env_flag": "ICDEV_DEMO_ENABLED",
                "url_prefix": "/demo",
            },
            skip_existing=True,
        )

        assert target.read_text(encoding="utf-8") == "existing"
        assert "tools/demo_canvas/blueprint.py" in result["skipped_files"]


class TestInfoOpsTemplate:
    def test_info_ops_template_renders_importable_blueprint(self, tmp_path):
        out = tmp_path / "out"
        result = render_tree(
            INFO_OPS_TEMPLATE,
            out,
            {
                "key": "info_ops",
                "display_name": "Info Ops Canvas",
                "url_prefix": "/info-ops",
            },
        )

        assert result["success"]
        blueprint_path = out / "tools" / "info_ops" / "blueprint.py"
        assert blueprint_path.exists()

        blueprint_text = blueprint_path.read_text(encoding="utf-8")
        assert 'Blueprint("info_ops", __name__' in blueprint_text
        assert "create_info_ops_blueprint" in blueprint_text
        assert "ICDEV_INFO_OPS_ENABLED" in blueprint_text
        assert "/info-ops/" in blueprint_text

        # Ensure the generated file is syntactically valid Python.
        ast.parse(blueprint_text)

    def test_info_ops_template_generates_forge_compliant_project(self, tmp_path):
        out = tmp_path / "out"
        result = render_tree(
            INFO_OPS_TEMPLATE,
            out,
            {
                "key": "info_ops",
                "display_name": "Info Ops Canvas",
                "url_prefix": "/info-ops",
            },
        )
        assert result["success"]
        assert not result["errors"]
        assert not result["validation_failures"]

        from tools.builder.forge_validator import CHECK_REGISTRY, validate

        # Run all FORGE layer checks except coherence. The coherence checker
        # is not shipped with the generated canvas; in a standalone run it is
        # skipped. Inside the ICDEV test harness it can import the repo's
        # coherence checker and report unrelated repo-level issues, so we
        # validate the generated project structure independently.
        checks = [c for c in CHECK_REGISTRY if c != "coherence"]
        report = validate(out, checks=checks)

        assert report.overall_pass, report.to_dict()["checks"]
        layer_status = report.to_dict()["layer_summary"]
        assert layer_status.get("goals") == "pass"
        assert layer_status.get("tools") == "pass"
        assert layer_status.get("args") in ("pass", "warn")
        assert layer_status.get("context") == "pass"
        assert layer_status.get("hardprompts") == "pass"
        assert layer_status.get("meta") in ("pass", "warn")


class TestChildAppTemplate:
    def test_minimal_child_app_generation(self, tmp_path):
        template = BASE_DIR / "data" / "templates" / "child_apps" / "minimal"
        out = tmp_path / "out"
        result = render_tree(
            template,
            out,
            {
                "key": "my_app",
                "display_name": "My App",
                "env_flag": "ICDEV_MY_APP_ENABLED",
                "url_prefix": "/my-app",
            },
        )

        assert result["success"]
        assert not result["errors"]
        assert not result["validation_failures"]

        init = out / "apps" / "my_app" / "__init__.py"
        blueprint = out / "apps" / "my_app" / "blueprint.py"
        page = out / "tools" / "dashboard" / "templates" / "my_app" / "index.html"

        assert init.exists()
        assert blueprint.exists()
        assert page.exists()

        blueprint_text = blueprint.read_text(encoding="utf-8")
        assert 'Blueprint("my_app", __name__' in blueprint_text
        assert "/my-app" in blueprint_text
        assert "ICDEV_MY_APP_ENABLED" in blueprint_text
        assert "My App" in blueprint_text

        # Ensure syntactically valid Python.
        ast.parse(blueprint_text)


class TestChildAppFlavors:
    """Validate built-in child-app flavor templates render FORGE-compliant skeletons."""

    @pytest.mark.parametrize("flavor", ["minimal", "compliance", "ai-lab", "govcon"])
    def test_flavor_renders_and_passes_forge(self, tmp_path, flavor):
        template = CHILD_APP_FLAVORS / flavor
        out = tmp_path / flavor
        result = render_tree(
            template,
            out,
            {"key": f"{flavor}_demo", "display_name": f"{flavor} Demo"},
        )

        assert result["success"], f"{flavor} render errors: {result.get('errors')}"
        assert not result["validation_failures"], f"{flavor} validation failures: {result.get('validation_failures')}"

        from tools.builder.forge_validator import CHECK_REGISTRY, validate

        checks = [c for c in CHECK_REGISTRY if c != "coherence"]
        report = validate(out, checks=checks)

        assert report.score >= 0.92, (
            f"{flavor} FORGE score too low: {report.score}"
        )
        # All three flavors share the same expected warning: no explicit agent
        # cards, only the template-generated CLAUDE.md.
        failed = [c.to_dict() for c in report.checks if c.status == "fail"]
        assert not failed, f"{flavor} has unexpected FORGE failures: {failed}"


class TestChildAppGeneratorOverlay:
    """Unit tests for the template-based composition added to child_app_generator."""

    def test_resolve_template_dir_builtin_flavor(self):
        resolved = _resolve_template_dir(None, "compliance", BASE_DIR)
        assert resolved == BASE_DIR / "data" / "templates" / "child_apps" / "compliance"

    def test_resolve_template_dir_explicit_path(self, tmp_path):
        custom = tmp_path / "custom_template"
        custom.mkdir()
        resolved = _resolve_template_dir(str(custom), None, BASE_DIR)
        assert resolved == custom

    def test_resolve_template_dir_missing_flavor_raises(self):
        with pytest.raises(FileNotFoundError):
            _resolve_template_dir(None, "not_a_flavor", BASE_DIR)

    def test_build_template_variables(self):
        blueprint = {
            "classification": "CUI",
            "impact_level": "IL5",
            "display_name": "My Demo App",
        }
        vars_ = _build_template_variables(blueprint, "my-demo-app")

        assert vars_["key"] == "my-demo-app"
        assert vars_["display_name"] == "My Demo App"
        assert vars_["classification"] == "CUI"
        assert vars_["impact_level"] == "IL5"
        assert vars_["env_flag"] == "ICDEV_MY_DEMO_APP_ENABLED"
        assert vars_["url_prefix"] == "/my-demo-app"
        assert vars_["module_package"] == "apps.my-demo-app"

    def test_build_template_variables_defaults_display_name(self):
        vars_ = _build_template_variables({}, "acme_lab")
        assert vars_["display_name"] == "Acme Lab"

    def test_overlay_template_specializes_child_root(self, tmp_path):
        """A flavor template overlays its files onto an existing child root.

        The baseline README is replaced by the template README, while files not
        listed in the template manifest are preserved.
        """
        child_root = tmp_path / "child"
        child_root.mkdir()
        baseline = child_root / "baseline.txt"
        baseline.write_text("parent baseline", encoding="utf-8")
        baseline_readme = child_root / "README.md"
        baseline_readme.write_text("# Baseline", encoding="utf-8")

        blueprint = {
            "classification": "CUI",
            "impact_level": "IL4",
            "display_name": "Overlay App",
        }
        result = _overlay_template(
            child_root,
            CHILD_APP_FLAVORS / "compliance",
            blueprint,
            "overlay_app",
        )

        assert result["success"]
        assert "README.md" in result["rendered_files"]
        # Baseline file outside the template should still exist.
        assert baseline.exists()
        # Template README should have replaced the baseline README.
        readme_text = (child_root / "README.md").read_text(encoding="utf-8")
        assert "Overlay App" in readme_text
        # Compliance-specific files were overlaid.
        assert (child_root / "args" / "security_gates.yaml").exists()
        blueprint_path = child_root / "apps" / "overlay_app" / "blueprint.py"
        assert blueprint_path.exists()
        ast.parse(blueprint_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Core extension template tests
# ---------------------------------------------------------------------------
CORE_EXT_TEMPLATES = BASE_DIR / "data" / "templates" / "core_extensions"


class TestCoreExtensionTemplate:
    """Validate the standard core_extension template generates valid Python."""

    def test_standard_renders_successfully(self, tmp_path):
        template = CORE_EXT_TEMPLATES / "standard"
        out = tmp_path / "out"
        result = render_tree(
            template,
            out,
            {
                "key": "notify_hub",
                "display_name": "Notification Hub",
                "env_flag": "ICDEV_NOTIFY_HUB_ENABLED",
            },
        )
        assert result["success"], f"Errors: {result.get('errors')}"
        assert not result["validation_failures"]

    def test_standard_generates_blueprint(self, tmp_path):
        template = CORE_EXT_TEMPLATES / "standard"
        out = tmp_path / "out"
        render_tree(
            template,
            out,
            {
                "key": "notify_hub",
                "display_name": "Notification Hub",
                "env_flag": "ICDEV_NOTIFY_HUB_ENABLED",
            },
        )
        bp = out / "tools" / "notify_hub" / "blueprint.py"
        assert bp.exists()
        text = bp.read_text(encoding="utf-8")
        assert "create_notify_hub_blueprint" in text
        assert "ICDEV_NOTIFY_HUB_ENABLED" in text
        ast.parse(text)

    def test_standard_generates_constants(self, tmp_path):
        template = CORE_EXT_TEMPLATES / "standard"
        out = tmp_path / "out"
        render_tree(
            template,
            out,
            {
                "key": "notify_hub",
                "display_name": "Notification Hub",
                "env_flag": "ICDEV_NOTIFY_HUB_ENABLED",
            },
        )
        c = out / "tools" / "notify_hub" / "constants.py"
        assert c.exists()
        ast.parse(c.read_text(encoding="utf-8"))

    def test_standard_iqe_skipped_by_default(self, tmp_path):
        template = CORE_EXT_TEMPLATES / "standard"
        out = tmp_path / "out"
        result = render_tree(
            template,
            out,
            {
                "key": "notify_hub",
                "display_name": "Notification Hub",
                "env_flag": "ICDEV_NOTIFY_HUB_ENABLED",
                "include_iqe": "false",
            },
        )
        iqe = out / "tools" / "iqe" / "adapters" / "notify_hub.py"
        assert not iqe.exists(), "IQE adapter should be skipped when include_iqe=false"
        assert "tools/iqe/adapters/notify_hub.py" in result["skipped_files"]

    def test_standard_iqe_generated_when_requested(self, tmp_path):
        template = CORE_EXT_TEMPLATES / "standard"
        out = tmp_path / "out"
        render_tree(
            template,
            out,
            {
                "key": "notify_hub",
                "display_name": "Notification Hub",
                "env_flag": "ICDEV_NOTIFY_HUB_ENABLED",
                "include_iqe": "true",
            },
        )
        iqe = out / "tools" / "iqe" / "adapters" / "notify_hub.py"
        assert iqe.exists()
        ast.parse(iqe.read_text(encoding="utf-8"))


class TestScaffoldCLI:
    """Tests for the scaffold CLI (canvas, child-app, core, list-templates)."""

    def test_list_templates_returns_kinds(self):
        from tools.cli.scaffold import _list_templates
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _list_templates(emit_json=False)
        assert rc == 0
        output = buf.getvalue()
        assert "canvas:" in output
        assert "child-app:" in output
        assert "core:" in output
        assert "standard" in output

    def test_list_templates_json(self):
        from tools.cli.scaffold import _list_templates
        import io, contextlib, json as _json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _list_templates(emit_json=True)
        assert rc == 0
        data = _json.loads(buf.getvalue())
        assert "canvases" in data
        assert "child_apps" in data
        assert "core_extensions" in data
        assert "standard" in data["core_extensions"]

    def test_core_dry_run(self, tmp_path):
        from tools.cli.scaffold import main
        rc = main([
            "core", "my_ext",
            "--display-name", "My Ext",
            "--env-flag", "ICDEV_MY_EXT_ENABLED",
            "--dry-run",
            "--no-register",
            "--json",
            "--out", str(tmp_path / "out"),
        ])
        assert rc == 0
        # No files written in dry-run
        assert not (tmp_path / "out").exists() or not list((tmp_path / "out").rglob("*"))

    def test_core_scaffold_generates_files(self, tmp_path):
        from tools.cli.scaffold import main
        out = tmp_path / "out"
        rc = main([
            "core", "my_ext",
            "--display-name", "My Ext",
            "--env-flag", "ICDEV_MY_EXT_ENABLED",
            "--no-register",
            "--out", str(out),
        ])
        assert rc == 0
        assert (out / "tools" / "my_ext" / "__init__.py").exists()
        assert (out / "tools" / "my_ext" / "blueprint.py").exists()
        assert (out / "tools" / "my_ext" / "constants.py").exists()
