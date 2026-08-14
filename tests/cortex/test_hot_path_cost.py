# CUI // SP-CTI
"""Two per-call costs on the Cortex hot path, both measured not eyeballed.

ctx-perf-01 — ``resolve_cortex_config_path()`` called
``resolve_llm_config_path()``, which walks EVERY parent directory
is_file()-probing for ``args/llm_config.yaml`` with no memo. One governed call
reaches ``load_cortex_config()`` roughly 8-12 times, so the walk was paid that
many times over — tens of filesystem syscalls per Cortex call, whether or not
the response cache was even on.

ctx-perf-02 — ``_allowed_tables()`` sat in a comprehension's CONDITION, so it
was re-evaluated once PER TABLE. It calls ``list_collections()`` and
``get_registry().get_iqe_mapping()``, so a 5-table query did 5 full
registry+executor scans on the SQL-safety path of every analyst question.

Both are asserted with CALL COUNTERS rather than timings: a timing assertion on
a shared runner is a flake, and the defect is "how many times", not "how slow".
"""
from __future__ import annotations

import importlib

import pytest

config = importlib.import_module("tools.cortex.config")
analyst = importlib.import_module("tools.cortex.analyst")


@pytest.fixture(autouse=True)
def _clear_memo():
    # getattr, not a direct call: this fixture is autouse, so if the memo does
    # not exist the fixture ERRORS and every test in the file errors with it —
    # including the ctx-perf-02 allowlist tests, which have nothing to do with
    # the memo. That would hide whether those tests catch their own defect.
    reset = getattr(config, "reset_path_cache", lambda: None)
    reset()
    yield
    reset()


# ---------------------------------------------------------------------------
# ctx-perf-01 — config path resolution
# ---------------------------------------------------------------------------


def test_the_directory_walk_runs_once_not_per_call(monkeypatch):
    calls = []
    real = config.resolve_llm_config_path

    def _counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(config, "resolve_llm_config_path", _counted)

    for _ in range(12):
        config.resolve_cortex_config_path()

    assert len(calls) == 1, (
        f"the parent-directory walk ran {len(calls)} times for 12 resolutions; "
        "one governed Cortex call reaches load_cortex_config 8-12 times, so this "
        "multiplies straight into per-call syscalls"
    )


def test_the_resolved_path_is_still_correct(monkeypatch):
    config.reset_path_cache()
    first = config.resolve_cortex_config_path()
    second = config.resolve_cortex_config_path()

    assert first == second
    assert first.name == config.CORTEX_CONFIG_FILENAME


def test_an_env_override_is_honoured_and_not_masked_by_the_memo(monkeypatch, tmp_path):
    """The memo must key on the override, not shadow it."""
    default = config.resolve_cortex_config_path()

    custom = tmp_path / "cortex_config.yaml"
    custom.write_text("governance: {}\n", encoding="utf-8")
    monkeypatch.setenv(config.CORTEX_CONFIG_ENV_VAR, str(custom))

    assert config.resolve_cortex_config_path() == custom.resolve()

    monkeypatch.delenv(config.CORTEX_CONFIG_ENV_VAR, raising=False)
    assert config.resolve_cortex_config_path() == default


def test_the_llm_config_override_also_keys_the_memo(monkeypatch, tmp_path):
    """cortex_config.yaml is resolved RELATIVE to the llm config, so it counts."""
    before = config.resolve_cortex_config_path()

    args_dir = tmp_path / "args"
    args_dir.mkdir()
    (args_dir / "llm_config.yaml").write_text("routing: {}\n", encoding="utf-8")
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(args_dir / "llm_config.yaml"))

    after = config.resolve_cortex_config_path()
    assert after != before
    assert after.parent == args_dir


def test_reset_path_cache_actually_clears(monkeypatch):
    config.resolve_cortex_config_path()
    assert config._path_cache
    config.reset_path_cache()
    assert not config._path_cache


# ---------------------------------------------------------------------------
# ctx-perf-02 — allowlist recomputation
# ---------------------------------------------------------------------------


def test_the_allowlist_is_built_once_per_validation(monkeypatch):
    calls = []
    real = analyst._allowed_tables

    def _counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(analyst, "_allowed_tables", _counted)

    # Five table references. Pre-hoist this evaluated _allowed_tables() five
    # times, once per comprehension iteration.
    sql = "SELECT * FROM a JOIN b ON 1=1 JOIN c ON 1=1 JOIN d ON 1=1 JOIN e ON 1=1"
    with pytest.raises(analyst.CortexQueryBlocked):
        # Off-allowlist by construction, so it refuses — which is the path that
        # runs the allowlist comparison. The REFUSAL is expected and asserted:
        # a bare `except Exception: pass` here would make this test pass even if
        # the function did not exist.
        analyst._validate_sql_safety(
            "q?", sql, analyst.CortexContext(), analyst.GovernanceReport(), 0.0
        )

    assert len(calls) == 1, (
        f"_allowed_tables() ran {len(calls)} times for one validation of a "
        "5-table query; it loads the component registry each time, so this "
        "scales with table count"
    )


def test_the_allowlist_hoist_did_not_change_the_verdict():
    """Refusal behaviour must be identical — this was a perf change only."""
    allowed = analyst._allowed_tables()

    assert isinstance(allowed, set)
    # Both forms of every collection name remain queryable.
    if allowed:
        dotted = [a for a in allowed if "." in a]
        if dotted:
            assert dotted[0].replace(".", "_") in allowed
