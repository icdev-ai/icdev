"""Tests for PNA runner."""
import importlib
import pathlib


def _load():
    src = pathlib.Path(__file__).parent / "step2_starter.py"
    spec = importlib.util.spec_from_file_location("pna_runner", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_pna_analysis_returns_list():
    mod = _load()
    result = mod.run_pna_analysis()
    assert isinstance(result, list), "run_pna_analysis() must return a list"


def test_function_defined():
    mod = _load()
    assert callable(getattr(mod, "run_pna_analysis", None))
