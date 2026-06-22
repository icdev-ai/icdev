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


MINIMAL_TEMPLATE = BASE_DIR / "data" / "templates" / "canvases" / "minimal"
INFO_OPS_TEMPLATE = BASE_DIR / "data" / "templates" / "canvases" / "info_ops"


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
