"""Tests for the ACE pipeline runner."""
import importlib
import pathlib


def _load(tmp_path=None):
    src = pathlib.Path(__file__).parent / "step2_starter.py"
    spec = importlib.util.spec_from_file_location("step2_starter", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pipeline_request_has_3_stages(tmp_path):
    mod = _load(tmp_path)
    assert len(mod.PIPELINE_REQUEST["pipeline"]) >= 3, "Pipeline needs at least 3 stages"


def test_all_stages_have_role_and_task(tmp_path):
    mod = _load(tmp_path)
    valid_roles = {
        "ai_developer", "agent_developer", "security_analyst",
        "data_engineer", "devops_engineer", "compliance_officer",
    }
    for stage in mod.PIPELINE_REQUEST["pipeline"]:
        assert "role" in stage, f"Stage missing 'role': {stage}"
        assert "task" in stage, f"Stage missing 'task': {stage}"
        assert stage["role"] in valid_roles, f"Unknown role: {stage['role']}"


def test_run_pipeline_defined(tmp_path):
    mod = _load(tmp_path)
    assert callable(getattr(mod, "run_pipeline", None)), "run_pipeline() function must be defined"
