"""Tests for icdev.tools.llm.config_opts."""
from __future__ import annotations

import os

import pytest

from icdev.tools.llm.config_opts import (
    _coerce_value,
    apply_opts,
    merge_config_sources,
    opts_from_env,
    parse_opts,
)


# ---------------------------------------------------------------------------
# parse_opts
# ---------------------------------------------------------------------------

class TestParseOpts:
    def test_single_flag_single_value(self):
        result = parse_opts(["--opts", "a.b=1"])
        assert result == {"a.b": "1"}

    def test_single_flag_multiple_values(self):
        result = parse_opts(["--opts", "a.b=1", "c=hello"])
        assert result == {"a.b": "1", "c": "hello"}

    def test_other_flags_before_opts(self):
        result = parse_opts(["--benchmark", "gsm8k", "--opts", "a=1"])
        assert result == {"a": "1"}

    def test_stops_at_next_flag(self):
        result = parse_opts(["--opts", "a=1", "--output", "file.json"])
        assert result == {"a": "1"}

    def test_empty_argv(self):
        assert parse_opts([]) == {}

    def test_no_opts_flag(self):
        assert parse_opts(["--foo", "bar"]) == {}

    def test_multiple_opts_flags(self):
        result = parse_opts(["--opts", "a=1", "--opts", "b=2"])
        assert result == {"a": "1", "b": "2"}

    def test_value_with_equals_sign(self):
        result = parse_opts(["--opts", "url=http://host?foo=bar"])
        assert result == {"url": "http://host?foo=bar"}


# ---------------------------------------------------------------------------
# _coerce_value
# ---------------------------------------------------------------------------

class TestCoerceValue:
    def test_int(self):
        assert _coerce_value("42") == 42
        assert isinstance(_coerce_value("42"), int)

    def test_negative_int(self):
        assert _coerce_value("-7") == -7

    def test_float(self):
        assert _coerce_value("3.14") == pytest.approx(3.14)
        assert isinstance(_coerce_value("3.14"), float)

    def test_true(self):
        assert _coerce_value("true") is True
        assert _coerce_value("True") is True
        assert _coerce_value("TRUE") is True

    def test_false(self):
        assert _coerce_value("false") is False
        assert _coerce_value("False") is False

    def test_string_passthrough(self):
        assert _coerce_value("hello") == "hello"
        assert _coerce_value("kimi-k2") == "kimi-k2"

    def test_zero(self):
        assert _coerce_value("0") == 0
        assert isinstance(_coerce_value("0"), int)


# ---------------------------------------------------------------------------
# apply_opts
# ---------------------------------------------------------------------------

class TestApplyOpts:
    def test_nested_creation(self):
        result = apply_opts({}, {"providers.kimi.model": "kimi-k2"})
        assert result == {"providers": {"kimi": {"model": "kimi-k2"}}}

    def test_top_level_key(self):
        result = apply_opts({}, {"default_function": "chat"})
        assert result == {"default_function": "chat"}

    def test_does_not_mutate_input(self):
        original = {"providers": {}}
        apply_opts(original, {"providers.kimi.model": "kimi-k2"})
        assert original == {"providers": {}}

    def test_merges_into_existing(self):
        base = {"providers": {"existing": {"model": "foo"}}}
        result = apply_opts(base, {"providers.kimi.model": "kimi-k2"})
        assert result["providers"]["existing"]["model"] == "foo"
        assert result["providers"]["kimi"]["model"] == "kimi-k2"

    def test_coerces_int(self):
        result = apply_opts({}, {"providers.kimi.max_tokens": "8192"})
        assert result["providers"]["kimi"]["max_tokens"] == 8192

    def test_overwrites_existing_scalar(self):
        base = {"level": "info"}
        result = apply_opts(base, {"level": "debug"})
        assert result["level"] == "debug"

    def test_empty_opts(self):
        base = {"a": 1}
        result = apply_opts(base, {})
        assert result == {"a": 1}

    def test_replaces_non_dict_intermediate(self):
        # If existing node is not a dict, it gets replaced with a dict
        base = {"providers": "scalar"}
        result = apply_opts(base, {"providers.kimi.model": "x"})
        assert result["providers"]["kimi"]["model"] == "x"


# ---------------------------------------------------------------------------
# merge_config_sources
# ---------------------------------------------------------------------------

class TestMergeConfigSources:
    def test_cli_wins_over_env_wins_over_base(self):
        base = {"level": "info"}
        env_opts = {"level": "warn"}
        cli_opts = {"level": "error"}
        result = merge_config_sources(base, env_opts=env_opts, cli_opts=cli_opts)
        assert result["level"] == "error"

    def test_env_wins_over_base(self):
        base = {"level": "info"}
        env_opts = {"level": "warn"}
        result = merge_config_sources(base, env_opts=env_opts)
        assert result["level"] == "warn"

    def test_none_opts_ignored(self):
        base = {"a": 1}
        result = merge_config_sources(base, file_opts=None, env_opts=None, cli_opts=None)
        assert result == {"a": 1}

    def test_file_opts_lower_precedence_than_env(self):
        base = {}
        file_opts = {"model": "a"}
        env_opts = {"model": "b"}
        result = merge_config_sources(base, file_opts=file_opts, env_opts=env_opts)
        assert result["model"] == "b"

    def test_does_not_mutate_base(self):
        base = {"x": 1}
        merge_config_sources(base, cli_opts={"x": "99"})
        assert base == {"x": 1}


# ---------------------------------------------------------------------------
# opts_from_env
# ---------------------------------------------------------------------------

class TestOptsFromEnv:
    def test_basic(self, monkeypatch):
        monkeypatch.setenv("ICDEV_OPTS_PROVIDERS__KIMI__MODEL", "kimi-k2")
        result = opts_from_env()
        assert result.get("providers.kimi.model") == "kimi-k2"

    def test_custom_prefix(self, monkeypatch):
        monkeypatch.setenv("MY_OPTS_LEVEL", "debug")
        result = opts_from_env(prefix="MY_OPTS_")
        assert result.get("level") == "debug"

    def test_unrelated_env_ignored(self, monkeypatch):
        monkeypatch.setenv("OTHER_VAR", "value")
        result = opts_from_env()
        assert "other_var" not in result
        assert "OTHER_VAR" not in result

    def test_empty_env(self, monkeypatch):
        # Remove all ICDEV_OPTS_* vars
        for k in list(os.environ.keys()):
            if k.startswith("ICDEV_OPTS_"):
                monkeypatch.delenv(k)
        result = opts_from_env()
        assert result == {}
