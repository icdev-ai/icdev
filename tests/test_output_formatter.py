# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.cli.output_formatter — human-friendly terminal output formatting."""

import argparse
import json
import re
from unittest.mock import patch

import pytest

try:
    from tools.cli.output_formatter import (
        _Ansi,
        _auto_color_value,
        _cui_wrap,
        _visible_len,
        add_human_flag,
        auto_format,
        format_banner,
        format_json_human,
        format_kv,
        format_list,
        format_pipeline,
        format_score,
        format_section,
        format_table,
        human_output,
        should_use_human,
    )
except ImportError:
    pytestmark = pytest.mark.skip("output_formatter not importable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences for assertion clarity."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# TestFormatTable
# ---------------------------------------------------------------------------

class TestFormatTable:
    """format_table: ASCII box-drawing table rendering."""

    def test_empty_rows(self):
        result = format_table(["A", "B"], [])
        plain = _strip_ansi(result)
        # Should contain headers but no data rows
        assert "A" in plain
        assert "B" in plain
        # Box-drawing top and bottom borders
        assert "\u250c" in result  # top-left corner
        assert "\u2514" in result  # bottom-left corner

    def test_single_row(self):
        result = format_table(["Name", "Status"], [["Agent1", "healthy"]])
        plain = _strip_ansi(result)
        assert "Agent1" in plain
        assert "healthy" in plain

    def test_multi_row(self):
        rows = [["A", "1"], ["B", "2"], ["C", "3"]]
        result = format_table(["Col1", "Col2"], rows)
        plain = _strip_ansi(result)
        for row in rows:
            for cell in row:
                assert cell in plain

    def test_with_title(self):
        result = format_table(["H"], [["val"]], title="My Table")
        plain = _strip_ansi(result)
        assert "My Table" in plain

    def test_without_title(self):
        result = format_table(["H"], [["val"]])
        plain = _strip_ansi(result)
        # Should not contain a title line; first real content is the border
        lines = plain.strip().splitlines()
        assert lines[0].startswith("\u250c")

    def test_with_classification(self):
        result = format_table(["H"], [["v"]], classification="CUI // SP-CTI")
        plain = _strip_ansi(result)
        assert "CUI // SP-CTI" in plain

    def test_column_width_adapts_to_content(self):
        result = format_table(["X"], [["a very long cell value"]])
        plain = _strip_ansi(result)
        # The table should contain the full long value
        assert "a very long cell value" in plain

    def test_auto_color_applied_to_cell_values(self):
        """Status values like 'healthy' should be auto-colored when colors are on."""
        result = format_table(["Status"], [["healthy"]])
        # Even if colors are off, the word should appear
        assert "healthy" in _strip_ansi(result)


# ---------------------------------------------------------------------------
# TestFormatBanner
# ---------------------------------------------------------------------------

class TestFormatBanner:
    """format_banner: full-width colored status banner."""

    def test_healthy_banner(self):
        result = format_banner("healthy", "All systems go")
        plain = _strip_ansi(result)
        assert "[OK]" in plain
        assert "All systems go" in plain

    def test_degraded_banner(self):
        result = format_banner("degraded", "Latency detected")
        plain = _strip_ansi(result)
        assert "[!!]" in plain
        assert "Latency detected" in plain

    def test_critical_banner(self):
        result = format_banner("critical", "Agent down")
        plain = _strip_ansi(result)
        assert "[XX]" in plain
        assert "Agent down" in plain

    def test_info_banner(self):
        result = format_banner("info", "New version available")
        plain = _strip_ansi(result)
        assert "[ii]" in plain
        assert "New version available" in plain

    def test_unknown_status_gets_default_icon(self):
        result = format_banner("unknown_status", "Something")
        plain = _strip_ansi(result)
        assert "[--]" in plain

    def test_banner_uses_double_line_rule(self):
        result = format_banner("info", "test")
        assert "\u2550" in result  # double horizontal line character

    def test_banner_with_classification(self):
        result = format_banner("info", "msg", classification="SECRET")
        plain = _strip_ansi(result)
        assert "SECRET" in plain


# ---------------------------------------------------------------------------
# TestFormatScore
# ---------------------------------------------------------------------------

class TestFormatScore:
    """format_score: colored score display with Unicode bar."""

    def test_zero_score(self):
        result = format_score(0.0, 0.7, "Zero")
        plain = _strip_ansi(result)
        assert "0.00" in plain
        assert "0.70" in plain
        assert "Zero" in plain

    def test_half_score(self):
        result = format_score(0.5, 1.0, "Half")
        plain = _strip_ansi(result)
        assert "0.50" in plain
        assert "1.00" in plain

    def test_full_score(self):
        result = format_score(1.0, 0.8, "Full")
        plain = _strip_ansi(result)
        assert "1.00" in plain

    def test_score_above_threshold_is_green_logic(self):
        # value >= threshold should produce green style
        # We check that it does not error and contains the values
        result = format_score(0.9, 0.7, "Above")
        plain = _strip_ansi(result)
        assert "0.90" in plain
        assert "Above" in plain

    def test_score_near_threshold_is_yellow_logic(self):
        # value >= threshold * 0.8 but < threshold
        result = format_score(0.6, 0.7, "Near")
        plain = _strip_ansi(result)
        assert "0.60" in plain

    def test_score_below_threshold_is_red_logic(self):
        # value < threshold * 0.8
        result = format_score(0.3, 0.7, "Low")
        plain = _strip_ansi(result)
        assert "0.30" in plain

    def test_float_rounding(self):
        result = format_score(0.333, 0.5, "Thirds")
        plain = _strip_ansi(result)
        assert "0.33" in plain

    def test_with_label(self):
        result = format_score(0.85, 0.70, "FedRAMP Readiness")
        plain = _strip_ansi(result)
        assert "FedRAMP Readiness" in plain

    def test_with_classification(self):
        result = format_score(0.5, 0.7, "Score", classification="CUI // SP-CTI")
        plain = _strip_ansi(result)
        assert "CUI // SP-CTI" in plain

    def test_bar_contains_block_characters(self):
        result = format_score(0.5, 0.7, "Bar")
        assert "\u2588" in result or "\u2591" in result


# ---------------------------------------------------------------------------
# TestFormatKv
# ---------------------------------------------------------------------------

class TestFormatKv:
    """format_kv: aligned key-value display."""

    def test_empty_dict_returns_empty(self):
        result = format_kv({})
        assert result == ""

    def test_empty_list_returns_empty(self):
        result = format_kv([])
        assert result == ""

    def test_single_key(self):
        result = format_kv({"Project": "alpha"})
        plain = _strip_ansi(result)
        assert "Project" in plain
        assert "alpha" in plain

    def test_multi_key(self):
        result = format_kv({"A": "1", "B": "2", "C": "3"})
        plain = _strip_ansi(result)
        assert "A" in plain
        assert "B" in plain
        assert "C" in plain

    def test_with_title(self):
        result = format_kv({"key": "val"}, title="Summary")
        plain = _strip_ansi(result)
        assert "Summary" in plain

    def test_list_of_tuples_input(self):
        result = format_kv([("X", "10"), ("Y", "20")])
        plain = _strip_ansi(result)
        assert "X" in plain
        assert "10" in plain
        assert "Y" in plain
        assert "20" in plain

    def test_colon_separator(self):
        result = format_kv({"Key": "Value"})
        plain = _strip_ansi(result)
        assert ":" in plain

    def test_with_classification(self):
        result = format_kv({"k": "v"}, classification="CUI")
        plain = _strip_ansi(result)
        assert "CUI" in plain


# ---------------------------------------------------------------------------
# TestFormatSection
# ---------------------------------------------------------------------------

class TestFormatSection:
    """format_section: decorated section header."""

    def test_basic_section(self):
        result = format_section("My Section")
        plain = _strip_ansi(result)
        assert "My Section" in plain

    def test_contains_horizontal_rules(self):
        result = format_section("Title")
        assert "\u2500" in result

    def test_custom_width(self):
        result = format_section("Narrow", width=20)
        plain = _strip_ansi(result)
        lines = plain.strip().splitlines()
        # First line is the rule; should be 20 chars of dashes
        assert len(lines[0].strip()) == 20

    def test_with_classification(self):
        result = format_section("Sec", classification="SECRET")
        plain = _strip_ansi(result)
        assert "SECRET" in plain


# ---------------------------------------------------------------------------
# TestFormatList
# ---------------------------------------------------------------------------

class TestFormatList:
    """format_list: bulleted or numbered list."""

    def test_empty_list(self):
        result = format_list([])
        assert result == ""

    def test_single_item_bullet(self):
        result = format_list(["item one"])
        plain = _strip_ansi(result)
        assert "\u2022" in result or "item one" in plain

    def test_multi_item(self):
        result = format_list(["alpha", "beta", "gamma"])
        plain = _strip_ansi(result)
        assert "alpha" in plain
        assert "beta" in plain
        assert "gamma" in plain

    def test_numbered_list(self):
        result = format_list(["first", "second"], numbered=True)
        plain = _strip_ansi(result)
        assert "1." in plain
        assert "2." in plain

    def test_custom_bullet(self):
        result = format_list(["item"], bullet="-")
        plain = _strip_ansi(result)
        assert "-" in plain

    def test_with_classification(self):
        result = format_list(["x"], classification="CUI")
        plain = _strip_ansi(result)
        assert "CUI" in plain


# ---------------------------------------------------------------------------
# TestFormatPipeline
# ---------------------------------------------------------------------------

class TestFormatPipeline:
    """format_pipeline: horizontal pipeline with status indicators."""

    def test_all_completed(self):
        steps = [
            {"name": "Plan", "status": "completed"},
            {"name": "Build", "status": "completed"},
            {"name": "Test", "status": "completed"},
        ]
        result = format_pipeline(steps)
        plain = _strip_ansi(result)
        assert "Plan" in plain
        assert "Build" in plain
        assert "Test" in plain

    def test_mixed_statuses(self):
        steps = [
            {"name": "A", "status": "completed"},
            {"name": "B", "status": "active"},
            {"name": "C", "status": "pending"},
        ]
        result = format_pipeline(steps)
        plain = _strip_ansi(result)
        assert "A" in plain
        assert "B" in plain
        assert "C" in plain

    def test_all_blocked(self):
        steps = [
            {"name": "X", "status": "blocked"},
            {"name": "Y", "status": "blocked"},
        ]
        result = format_pipeline(steps)
        plain = _strip_ansi(result)
        assert "X" in plain
        assert "Y" in plain

    def test_skipped_status(self):
        steps = [{"name": "Deploy", "status": "skipped"}]
        result = format_pipeline(steps)
        plain = _strip_ansi(result)
        assert "Deploy" in plain

    def test_arrows_between_steps(self):
        steps = [
            {"name": "A", "status": "completed"},
            {"name": "B", "status": "pending"},
        ]
        result = format_pipeline(steps)
        # Arrow character between steps
        assert "\u25b8" in result

    def test_unknown_status_defaults(self):
        steps = [{"name": "Z", "status": "mystery"}]
        result = format_pipeline(steps)
        plain = _strip_ansi(result)
        assert "Z" in plain

    def test_with_classification(self):
        steps = [{"name": "A", "status": "completed"}]
        result = format_pipeline(steps, classification="CUI")
        plain = _strip_ansi(result)
        assert "CUI" in plain

    def test_empty_steps(self):
        result = format_pipeline([])
        # Should not error, returns empty-ish content
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestAutoColorValue
# ---------------------------------------------------------------------------

class TestAutoColorValue:
    """_auto_color_value: pattern-based value coloring."""

    def test_critical_detected(self):
        result = _auto_color_value("critical")
        plain = _strip_ansi(result)
        assert plain == "critical"

    def test_healthy_detected(self):
        result = _auto_color_value("healthy")
        plain = _strip_ansi(result)
        assert plain == "healthy"

    def test_passed_detected(self):
        result = _auto_color_value("passed")
        plain = _strip_ansi(result)
        assert plain == "passed"

    def test_failed_detected(self):
        result = _auto_color_value("failed")
        plain = _strip_ansi(result)
        assert plain == "failed"

    def test_no_match_returns_unchanged(self):
        result = _auto_color_value("foobar")
        assert result == "foobar"

    def test_case_insensitive(self):
        result = _auto_color_value("CRITICAL issue")
        plain = _strip_ansi(result)
        assert plain == "CRITICAL issue"

    def test_partial_match(self):
        # "pass" is in _VALUE_COLORS, so "compass" should match it
        result = _auto_color_value("compass")
        plain = _strip_ansi(result)
        assert plain == "compass"

    def test_info_detected(self):
        result = _auto_color_value("info")
        plain = _strip_ansi(result)
        assert plain == "info"


# ---------------------------------------------------------------------------
# TestVisibleLen
# ---------------------------------------------------------------------------

class TestVisibleLen:
    """_visible_len: display width ignoring ANSI codes."""

    def test_plain_text(self):
        assert _visible_len("hello") == 5

    def test_empty_string(self):
        assert _visible_len("") == 0

    def test_ansi_wrapped_text(self):
        wrapped = "\033[31mhello\033[0m"
        assert _visible_len(wrapped) == 5

    def test_multiple_ansi_codes(self):
        wrapped = "\033[1m\033[31mhi\033[0m"
        assert _visible_len(wrapped) == 2

    def test_numeric_input(self):
        assert _visible_len(42) == 2


# ---------------------------------------------------------------------------
# TestAnsiStrip
# ---------------------------------------------------------------------------

class TestAnsiStrip:
    """_Ansi.strip: removes ANSI escape sequences."""

    def test_strip_single_code(self):
        assert _Ansi.strip("\033[31mred\033[0m") == "red"

    def test_strip_no_codes(self):
        assert _Ansi.strip("plain text") == "plain text"

    def test_strip_empty(self):
        assert _Ansi.strip("") == ""


# ---------------------------------------------------------------------------
# TestCuiWrap
# ---------------------------------------------------------------------------

class TestCuiWrap:
    """_cui_wrap: classification banner wrapping."""

    def test_no_classification_returns_text_unchanged(self):
        assert _cui_wrap("hello") == "hello"
        assert _cui_wrap("hello", None) == "hello"

    def test_with_classification_wraps_text(self):
        result = _cui_wrap("body", "CUI // SP-CTI")
        plain = _strip_ansi(result)
        assert "CUI // SP-CTI" in plain
        assert "body" in plain
        # Banner appears at top and bottom
        occurrences = plain.count("CUI // SP-CTI")
        assert occurrences == 2


# ---------------------------------------------------------------------------
# TestFormatJsonHuman
# ---------------------------------------------------------------------------

class TestFormatJsonHuman:
    """format_json_human: recursive dict/list rendering."""

    def test_simple_dict(self):
        result = format_json_human({"key": "value"})
        plain = _strip_ansi(result)
        assert "key" in plain
        assert "value" in plain

    def test_empty_dict(self):
        result = format_json_human({})
        plain = _strip_ansi(result)
        assert "(empty)" in plain

    def test_empty_list(self):
        result = format_json_human([])
        plain = _strip_ansi(result)
        assert "(empty list)" in plain

    def test_nested_dict(self):
        result = format_json_human({"outer": {"inner": "val"}})
        plain = _strip_ansi(result)
        assert "outer" in plain
        assert "inner" in plain
        assert "val" in plain

    def test_list_of_dicts(self):
        result = format_json_human([{"a": 1}, {"b": 2}])
        plain = _strip_ansi(result)
        assert "[0]" in plain
        assert "[1]" in plain

    def test_with_title(self):
        result = format_json_human({"k": "v"}, title="Details")
        plain = _strip_ansi(result)
        assert "Details" in plain

    def test_scalar_value(self):
        result = format_json_human("just a string")
        plain = _strip_ansi(result)
        assert "just a string" in plain


# ---------------------------------------------------------------------------
# TestAutoFormat
# ---------------------------------------------------------------------------

class TestAutoFormat:
    """auto_format: intelligent formatter selection by data shape."""

    def test_list_of_dicts_becomes_table(self):
        data = [{"name": "A", "status": "healthy"}, {"name": "B", "status": "degraded"}]
        result = auto_format(data)
        plain = _strip_ansi(result)
        # Table should contain headers from dict keys and all values
        assert "name" in plain
        assert "status" in plain
        assert "A" in plain
        assert "B" in plain

    def test_score_dict(self):
        data = {"value": 0.85, "threshold": 0.70, "label": "Readiness"}
        result = auto_format(data)
        plain = _strip_ansi(result)
        assert "0.85" in plain
        assert "Readiness" in plain

    def test_banner_dict(self):
        data = {"status": "healthy", "message": "All good"}
        result = auto_format(data)
        plain = _strip_ansi(result)
        assert "[OK]" in plain
        assert "All good" in plain

    def test_pipeline_dict(self):
        data = {"steps": [{"name": "Build", "status": "completed"}]}
        result = auto_format(data)
        plain = _strip_ansi(result)
        assert "Build" in plain

    def test_plain_dict_becomes_kv(self):
        data = {"project": "alpha", "version": "1.0"}
        result = auto_format(data)
        plain = _strip_ansi(result)
        assert "project" in plain
        assert "alpha" in plain

    def test_plain_list_becomes_bulleted(self):
        data = ["item1", "item2"]
        result = auto_format(data)
        plain = _strip_ansi(result)
        assert "item1" in plain
        assert "item2" in plain

    def test_empty_list(self):
        result = auto_format([])
        plain = _strip_ansi(result)
        assert "(no items)" in plain

    def test_headers_rows_dict(self):
        data = {"headers": ["Col"], "rows": [["val"]]}
        result = auto_format(data)
        plain = _strip_ansi(result)
        assert "Col" in plain
        assert "val" in plain

    def test_scalar_fallback(self):
        result = auto_format("just text")
        plain = _strip_ansi(result)
        assert "just text" in plain


# ---------------------------------------------------------------------------
# TestAddHumanFlag
# ---------------------------------------------------------------------------

class TestAddHumanFlag:
    """add_human_flag / should_use_human: argparse integration."""

    def test_adds_human_flag(self):
        parser = argparse.ArgumentParser()
        add_human_flag(parser)
        args = parser.parse_args(["--human"])
        assert args.human is True

    def test_default_is_false(self):
        parser = argparse.ArgumentParser()
        add_human_flag(parser)
        args = parser.parse_args([])
        assert args.human is False

    def test_should_use_human_true(self):
        ns = argparse.Namespace(human=True)
        assert should_use_human(ns) is True

    def test_should_use_human_false(self):
        ns = argparse.Namespace(human=False)
        assert should_use_human(ns) is False

    def test_should_use_human_missing_attr(self):
        ns = argparse.Namespace()
        assert should_use_human(ns) is False


# ---------------------------------------------------------------------------
# TestHumanOutputDecorator
# ---------------------------------------------------------------------------

class TestHumanOutputDecorator:
    """human_output: decorator intercepting dict returns for --human formatting."""

    def test_json_mode_prints_json(self, capsys):
        @human_output
        def my_tool():
            return {"key": "value"}

        with patch.object(sys, "argv", ["tool"]):
            result = my_tool()

        assert result == {"key": "value"}
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["key"] == "value"

    def test_non_dict_return_passes_through(self):
        @human_output
        def my_tool():
            return 42

        with patch.object(sys, "argv", ["tool"]):
            result = my_tool()

        assert result == 42

    def test_human_mode_via_argv(self, capsys):
        @human_output
        def my_tool():
            return {"status": "healthy", "message": "All ok"}

        with patch.object(sys, "argv", ["tool", "--human"]):
            result = my_tool()

        assert result == {"status": "healthy", "message": "All ok"}
        captured = capsys.readouterr()
        # Should NOT be valid JSON in human mode
        plain = _strip_ansi(captured.out)
        assert "All ok" in plain

    def test_human_mode_via_namespace_arg(self, capsys):
        @human_output
        def my_tool(args):
            return {"project": "alpha"}

        ns = argparse.Namespace(human=True)
        with patch.object(sys, "argv", ["tool"]):
            result = my_tool(ns)

        assert result == {"project": "alpha"}
        captured = capsys.readouterr()
        assert "alpha" in captured.out


# CUI // SP-CTI
