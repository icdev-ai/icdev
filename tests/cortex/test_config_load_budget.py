# CUI // SP-CTI
"""How many times ONE governed Cortex call loads args/cortex_config.yaml.

ctx-perf-01, part 2. Part 1 (PR #1641) memoized ``resolve_cortex_config_path()``
so the parent-directory walk stopped being repaid per call — that is asserted in
tests/cortex/test_hot_path_cost.py. It deliberately did NOT collapse the CALLS
themselves: ``load_cortex_config()`` still ran once per gate, each one a
``path.stat()`` before the mtime memo could answer, and its own docstring said so.

This file measures the calls. A governed call reached ``load_cortex_config()``
through eight independent sites — ``cache.is_enabled``, ``cache.cacheable``,
``cache._ttl_for``, ``resolve_profile``, ``resolve_fail_closed`` (up to three
times), ``skip_grounding_for_plain_complete`` and ``_content_grounding_floor``
(twice) — none of which knew the others had already read the same file a
microsecond earlier.

Counted, not timed: a timing assertion on a shared runner is a flake, and the
defect is "how many times", not "how slow".

The budget is a CEILING, not a target. Lower it when a call site is collapsed;
never raise it to get a commit through — that is how this cost grew back the
first time.
"""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

config = importlib.import_module("tools.cortex.config")
api = importlib.import_module("tools.cortex.api")
gov = importlib.import_module("tools.cortex.governance")
cache = importlib.import_module("tools.cortex.cache")
from tools.cortex.schemas import CortexContext  # noqa: E402

#: One full config load per governed call: the api wrapper reads it once and
#: threads it through the cache decision and every governance gate.
MAX_LOADS_PER_GOVERNED_CALL = 1


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Governance sinks -> in-memory. No DB, no gateway, no provider."""
    cache.reset()
    monkeypatch.setattr(gov, "_gate_record_audit", lambda p: None)
    monkeypatch.setattr(gov, "_gate_register_provenance", lambda t, c, o, r: "scr")
    monkeypatch.setattr(gov, "_gate_check_text",
                        lambda t: {"allowed": True, "warnings": [], "blocked_reason": None})
    monkeypatch.setattr(gov, "_gate_redact_input", lambda t, c: (t, 0))
    monkeypatch.setattr(gov, "_gate_redact_output", lambda t: (t, []))
    monkeypatch.setattr(
        api, "_invoke",
        lambda function, request, context: SimpleNamespace(
            content="answer", provider="p", model_id="m", cost_usd=0.0, duration_ms=1
        ),
    )
    yield
    cache.reset()


def _count_config_loads(monkeypatch) -> list:
    """Count load_cortex_config() through EVERY module object that holds it.

    ``api`` binds the function at import time
    (``from .config import load_cortex_config``), so patching only
    ``tools.cortex.config`` would leave its reference invisible. ``cache`` and
    the ``config`` readers resolve the name on the config module at call time,
    so the config-module patch catches those. ``governance`` is listed because
    it USED to bind it and a regression would bind it again — the loop patches
    whatever is actually there.
    """
    real = config.load_cortex_config
    calls: list = []

    def _counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    patched = 0
    for name in ("tools.cortex.config", "tools.cortex.governance", "tools.cortex.api",
                 "icdev.tools.cortex.config", "icdev.tools.cortex.governance",
                 "icdev.tools.cortex.api"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "load_cortex_config", None) is not None:
            monkeypatch.setattr(module, "load_cortex_config", _counted)
            patched += 1
    assert sys.modules["tools.cortex.config"].load_cortex_config is _counted, (
        "the config module was not patched — every reader resolves the name "
        "there, so an unpatched config module makes a low count meaningless"
    )
    assert patched >= 2, (
        f"only {patched} module(s) exposed load_cortex_config — the counter is "
        "not wired to the call sites, so a zero here would mean nothing"
    )
    return calls


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------
def test_one_governed_call_loads_the_config_once(monkeypatch):
    calls = _count_config_loads(monkeypatch)

    result = api.complete("hello", ctx=CortexContext(tenant_id="t-a"))

    assert result.text == "answer"  # the call really ran; a no-op scores 0 loads
    assert len(calls) <= MAX_LOADS_PER_GOVERNED_CALL, (
        f"one governed cortex.complete loaded args/cortex_config.yaml "
        f"{len(calls)} times (budget {MAX_LOADS_PER_GOVERNED_CALL}). Each load "
        "stat()s the file before its mtime memo can answer, so this is paid "
        "per gate on every Cortex call whether or not the cache is on."
    )


def test_the_budget_holds_for_a_retrieval_backed_call(monkeypatch):
    """The grounding gates are the sites that read config most — measure them.

    A retrieval call runs citation grounding AND content grounding; the latter
    read the floor twice and re-read the whole config inside
    ``_gate_ground_content``. Those sites are skipped entirely by a plain
    ``complete()``, so the cheap path alone would not prove the fix.
    """
    calls = _count_config_loads(monkeypatch)

    sources = [SimpleNamespace(source_id="1", content="Ada Lovelace wrote the first program.")]
    result = api.complete(
        "who wrote the first program?",
        ctx=CortexContext(tenant_id="t-a"),
        context_sources=sources,
    )

    assert result.governance is not None
    assert "content_grounding" in result.governance.outcomes, (
        "the retrieval path did not run the grounding gates — this test would "
        "then be measuring the same cheap path as the one above"
    )
    assert len(calls) <= MAX_LOADS_PER_GOVERNED_CALL, (
        f"a retrieval-backed governed call loaded the config {len(calls)} times "
        f"(budget {MAX_LOADS_PER_GOVERNED_CALL})"
    )


def test_the_path_is_resolved_at_most_once_per_call(monkeypatch):
    """The syscall side of the same defect: resolution is memoized, not free."""
    calls = _count_config_loads(monkeypatch)
    resolutions: list = []
    real_resolve = config.resolve_cortex_config_path

    def _counted_resolve():
        resolutions.append(1)
        return real_resolve()

    monkeypatch.setattr(config, "resolve_cortex_config_path", _counted_resolve)

    api.complete("hello", ctx=CortexContext(tenant_id="t-a"))

    assert len(resolutions) <= MAX_LOADS_PER_GOVERNED_CALL, (
        f"resolve_cortex_config_path() ran {len(resolutions)} times for one "
        "governed call"
    )
    assert len(calls) <= MAX_LOADS_PER_GOVERNED_CALL


# ---------------------------------------------------------------------------
# The invalidation that must survive the collapse
# ---------------------------------------------------------------------------
def test_a_config_edit_is_still_picked_up(tmp_path, monkeypatch):
    """mtime invalidation must survive: an edited file wins on the NEXT load."""
    path = tmp_path / "cortex_config.yaml"
    path.write_text("governance:\n  fail_closed: false\n", encoding="utf-8")
    monkeypatch.setenv(config.CORTEX_CONFIG_ENV_VAR, str(path))
    config.reset_path_cache()

    assert config.load_cortex_config()["governance"]["fail_closed"] is False

    path.write_text("governance:\n  fail_closed: true\n", encoding="utf-8")
    # Force a distinct mtime: a same-second rewrite is exactly the edit a
    # coarse-grained memo would swallow.
    stat = path.stat()
    import os
    os.utime(path, (stat.st_atime + 10, stat.st_mtime + 10))

    assert config.load_cortex_config()["governance"]["fail_closed"] is True, (
        "the mtime memo did not notice an edited cortex_config.yaml — the "
        "per-call load was collapsed at the cost of the invalidation signal"
    )
    config.reset_path_cache()


def test_an_edit_between_two_governed_calls_is_picked_up(tmp_path, monkeypatch):
    """The snapshot is per-CALL, not per-process.

    Threading one config through a call is only safe if the next call re-reads.
    A snapshot that outlived the call would make an operator's edit invisible
    until restart — the failure this test exists to catch.
    """
    import os

    path = tmp_path / "cortex_config.yaml"
    path.write_text(
        "governance:\n  profiles:\n    narrow:\n"
        "      gates: [operation, output_redaction, provenance]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(config.CORTEX_CONFIG_ENV_VAR, str(path))
    config.reset_path_cache()

    assert "narrow" in gov.load_governance_profiles()

    path.write_text(
        "governance:\n  profiles:\n    widened:\n"
        "      gates: [operation, output_redaction, provenance]\n",
        encoding="utf-8",
    )
    stat = path.stat()
    os.utime(path, (stat.st_atime + 10, stat.st_mtime + 10))

    profiles = gov.load_governance_profiles()
    assert "widened" in profiles and "narrow" not in profiles, (
        "a governance profile edit was not visible to the next call"
    )
    config.reset_path_cache()
