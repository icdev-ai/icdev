#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for cross-phase workflow composer (Phase 54, D343)."""

import json
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "args" / "workflow_templates"


# ---------------------------------------------------------------------------
# Template Validation Tests
# ---------------------------------------------------------------------------

class TestTemplates:
    """Validate all workflow templates."""

    def test_template_dir_exists(self):
        assert TEMPLATE_DIR.exists(), "Workflow templates directory must exist"

    def test_four_templates_present(self):
        templates = list(TEMPLATE_DIR.glob("*.yaml"))
        assert len(templates) >= 4, f"Expected >= 4 templates, found {len(templates)}"

    def test_ato_acceleration_template(self):
        f = TEMPLATE_DIR / "ato_acceleration.yaml"
        assert f.exists()
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "steps" in data
        assert len(data["steps"]) >= 5
        step_ids = [s["id"] for s in data["steps"]]
        assert "categorize" in step_ids
        assert "ssp" in step_ids
        assert "sbom" in step_ids

    def test_security_hardening_template(self):
        f = TEMPLATE_DIR / "security_hardening.yaml"
        assert f.exists()
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "steps" in data
        step_ids = [s["id"] for s in data["steps"]]
        assert "sast" in step_ids
        assert "deps" in step_ids

    def test_full_compliance_template(self):
        f = TEMPLATE_DIR / "full_compliance.yaml"
        assert f.exists()
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "steps" in data
        step_ids = [s["id"] for s in data["steps"]]
        assert "detect" in step_ids
        assert "assess" in step_ids

    def test_build_deploy_template(self):
        f = TEMPLATE_DIR / "build_deploy.yaml"
        assert f.exists()
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "steps" in data
        assert data.get("category") == "build"

    def test_all_templates_have_description(self):
        for f in TEMPLATE_DIR.glob("*.yaml"):
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert "description" in data, f"Template {f.name} missing description"

    def test_no_circular_dependencies(self):
        from tools.orchestration.workflow_composer import _resolve_dag
        for f in TEMPLATE_DIR.glob("*.yaml"):
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            # Should not raise CycleError
            order = _resolve_dag(data["steps"])
            assert len(order) == len(data["steps"]), f"Template {f.name}: DAG order length mismatch"


# ---------------------------------------------------------------------------
# Workflow Composer Logic Tests
# ---------------------------------------------------------------------------

class TestWorkflowComposer:
    """Test workflow composition logic."""

    def test_list_templates(self):
        from tools.orchestration.workflow_composer import list_templates
        templates = list_templates()
        assert len(templates) >= 4
        names = [t["name"] for t in templates]
        assert "ato_acceleration" in names
        assert "security_hardening" in names

    def test_compose_workflow(self):
        from tools.orchestration.workflow_composer import compose_workflow
        plan = compose_workflow("ato_acceleration", "proj-test")
        assert plan["template"] == "ato_acceleration"
        assert plan["project_id"] == "proj-test"
        assert plan["total_steps"] >= 5
        assert "workflow_id" in plan
        assert plan["workflow_id"].startswith("wf-")

    def test_compose_respects_dag_order(self):
        from tools.orchestration.workflow_composer import compose_workflow
        plan = compose_workflow("ato_acceleration", "proj-test")
        step_ids = [s["id"] for s in plan["steps"]]
        # categorize must come before assess
        assert step_ids.index("categorize") < step_ids.index("assess")
        # assess must come before ssp
        assert step_ids.index("assess") < step_ids.index("ssp")

    def test_compose_with_overrides(self):
        from tools.orchestration.workflow_composer import compose_workflow
        overrides = {"categorize": {"method": "cnssi_1253"}}
        plan = compose_workflow("ato_acceleration", "proj-test", overrides=overrides)
        cat_step = next(s for s in plan["steps"] if s["id"] == "categorize")
        assert "--method" in " ".join(cat_step["command"])

    def test_execute_dry_run(self):
        from tools.orchestration.workflow_composer import compose_workflow, execute_workflow
        plan = compose_workflow("ato_acceleration", "proj-test")
        result = execute_workflow(plan, dry_run=True)
        assert result["dry_run"] is True
        assert result["overall_status"] == "pass"
        for step in result["steps"]:
            assert step["status"] == "dry_run"

    def test_missing_template_raises(self):
        from tools.orchestration.workflow_composer import compose_workflow
        with pytest.raises(FileNotFoundError):
            compose_workflow("nonexistent_template", "proj-test")


# ---------------------------------------------------------------------------
# DAG Resolution Tests
# ---------------------------------------------------------------------------

class TestDAGResolution:
    """Test topological sort and DAG handling."""

    def test_simple_dag(self):
        from tools.orchestration.workflow_composer import _resolve_dag
        steps = [
            {"id": "a", "depends_on": []},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]
        order = _resolve_dag(steps)
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_parallel_dag(self):
        from tools.orchestration.workflow_composer import _resolve_dag
        steps = [
            {"id": "a"},
            {"id": "b"},
            {"id": "c", "depends_on": ["a", "b"]},
        ]
        order = _resolve_dag(steps)
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("c")

    def test_cycle_detection(self):
        from graphlib import CycleError
        from tools.orchestration.workflow_composer import _resolve_dag
        steps = [
            {"id": "a", "depends_on": ["c"]},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]
        with pytest.raises(CycleError):
            _resolve_dag(steps)
