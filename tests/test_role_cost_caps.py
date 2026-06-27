# CUI // SP-CTI
"""Tests for per-role cost cap loader."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch


SAMPLE_CAPS = {
    "ai_developer": 2.00,
    "researcher": 5.00,
    "docs_writer": 1.50,
    "default": 3.00,
}


class TestGetCapForRole:
    def test_returns_cap_for_known_role(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value=SAMPLE_CAPS):
            cap = role_cost_caps.get_cap_for_role("ai_developer")
        assert cap == 2.00

    def test_returns_cap_for_researcher(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value=SAMPLE_CAPS):
            cap = role_cost_caps.get_cap_for_role("researcher")
        assert cap == 5.00

    def test_returns_default_for_unknown_role(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value=SAMPLE_CAPS):
            cap = role_cost_caps.get_cap_for_role("unknown_role_xyz")
        assert cap == 3.00

    def test_returns_none_when_no_default_and_role_missing(self):
        from icdev.tools.llm import role_cost_caps
        caps = {"ai_developer": 2.00}  # no 'default' key
        with patch.object(role_cost_caps, "_load_caps", return_value=caps):
            cap = role_cost_caps.get_cap_for_role("other_role")
        assert cap is None

    def test_null_role_falls_through_to_default(self):
        from icdev.tools.llm import role_cost_caps
        caps = {"ai_developer": None, "default": 3.00}
        with patch.object(role_cost_caps, "_load_caps", return_value=caps):
            cap = role_cost_caps.get_cap_for_role("ai_developer")
        # null role entry → skip to default
        assert cap == 3.00

    def test_returns_float_from_string_value(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value={"researcher": "5.00"}):
            cap = role_cost_caps.get_cap_for_role("researcher")
        assert isinstance(cap, float)
        assert cap == 5.00

    def test_missing_file_returns_none(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_CONFIG_PATH", Path("/nonexistent/path.yaml")):
            cap = role_cost_caps.get_cap_for_role("ai_developer")
        assert cap is None

    def test_load_error_returns_empty_dict(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_CONFIG_PATH", Path("/nonexistent/path.yaml")):
            caps = role_cost_caps._load_caps()
        assert caps == {}

    def test_researcher_cap_higher_than_docs(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value=SAMPLE_CAPS):
            r_cap = role_cost_caps.get_cap_for_role("researcher")
            d_cap = role_cost_caps.get_cap_for_role("docs_writer")
        assert r_cap > d_cap

    def test_returns_none_on_all_none_config(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value={}):
            cap = role_cost_caps.get_cap_for_role("ai_developer")
        assert cap is None


class TestListCaps:
    def test_returns_dict_without_default(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value=SAMPLE_CAPS):
            result = role_cost_caps.list_caps()
        assert isinstance(result, dict)
        assert "ai_developer" in result
        assert "researcher" in result
        assert "default" not in result  # default excluded from listing

    def test_handles_none_values(self):
        from icdev.tools.llm import role_cost_caps
        caps = {"ai_developer": None, "researcher": 5.00}
        with patch.object(role_cost_caps, "_load_caps", return_value=caps):
            result = role_cost_caps.list_caps()
        assert result["ai_developer"] is None
        assert result["researcher"] == 5.00

    def test_returns_floats(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value=SAMPLE_CAPS):
            result = role_cost_caps.list_caps()
        for val in result.values():
            assert val is None or isinstance(val, float)

    def test_empty_caps_returns_empty_dict(self):
        from icdev.tools.llm import role_cost_caps
        with patch.object(role_cost_caps, "_load_caps", return_value={}):
            result = role_cost_caps.list_caps()
        assert result == {}
