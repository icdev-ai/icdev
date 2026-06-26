"""Tests for AgentSHAP runner."""
import importlib
import pathlib


def _load():
    src = pathlib.Path(__file__).parent / "step2_starter.py"
    spec = importlib.util.spec_from_file_location("shap_runner", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_get_recent_traces_returns_list():
    mod = _load()
    result = mod.get_recent_traces(3)
    assert isinstance(result, list)


def test_run_attribution_defined():
    mod = _load()
    assert callable(getattr(mod, "run_attribution_report", None))
