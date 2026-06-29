"""Tests for STIG marker injector."""
import importlib
import pathlib


def _load():
    src = pathlib.Path(__file__).parent / "step1_starter.py"
    spec = importlib.util.spec_from_file_location("stig_injector", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stig_mappings_have_v_ids():
    mod = _load()
    for key, comment in mod.STIG_MAPPINGS.items():
        assert "V-" in comment, f"STIG mapping {key} missing V-ID"


def test_pattern_keywords_cover_all_mappings():
    mod = _load()
    assert set(mod.STIG_MAPPINGS.keys()) == set(mod.PATTERN_KEYWORDS.keys())


def test_find_functions_returns_list():
    mod = _load()
    result = mod.find_functions_needing_markers(pathlib.Path(__file__))
    assert isinstance(result, list)
