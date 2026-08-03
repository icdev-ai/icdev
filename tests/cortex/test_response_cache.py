# CUI // SP-CTI
"""Tests for the opt-in Cortex response cache (tools/cortex/cache.py).

Focus on the security-load-bearing behaviors: the key folds the full
tenant/classification/domain/air_gap boundary (a hit never crosses it), a hit is
still audited, and a blocked result is never cached.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.cortex import api
from tools.cortex import cache as rc
from tools.cortex import governance as gov
from tools.cortex.schemas import CortexContext


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    rc.reset()
    # Governance sinks -> in-memory (no DB audit/provenance, no heavy gateway).
    monkeypatch.setattr(gov, "_gate_record_audit", lambda p: None)
    monkeypatch.setattr(gov, "_gate_register_provenance", lambda t, c, o, r: "scr")
    monkeypatch.setattr(gov, "_gate_check_text",
                        lambda t: {"allowed": True, "warnings": [], "blocked_reason": None})
    monkeypatch.setattr(gov, "_gate_redact_input", lambda t, c: (t, 0))
    monkeypatch.setattr(gov, "_gate_redact_output", lambda t: (t, []))
    yield
    rc.reset()


def _enable(monkeypatch, **over):
    cfg = {
        "enabled": True, "max_entries": 8,
        "operations": ["cortex.complete", "cortex.search", "cortex.ask",
                       "cortex.classify", "cortex.extract"],
        "ttl_seconds": {"default": 300, "cortex.complete": 900,
                        "cortex.classify": 600, "cortex.extract": 900},
    }
    cfg.update(over)
    monkeypatch.setattr(rc, "_cache_cfg", lambda: cfg)
    rc.reset()


def _count_invoke(monkeypatch, text="answer"):
    calls = {"n": 0}

    def _fake(function, request, context):
        calls["n"] += 1
        return SimpleNamespace(content=text, provider="p", model_id="m",
                               cost_usd=0.0, duration_ms=1)

    monkeypatch.setattr(api, "_invoke", _fake)
    return calls


# --------------------------------------------------------------------------- #
# make_key — the security boundary
# --------------------------------------------------------------------------- #
def test_key_folds_full_boundary():
    base = CortexContext(tenant_id="t-a", classification="CUI", domain="proposal")
    k = rc.make_key("cortex.complete", "hi", base, {})
    # Identical inputs -> identical key.
    assert k == rc.make_key("cortex.complete", "hi", base, {})
    # Each boundary dimension changes the key.
    assert k != rc.make_key("cortex.complete", "hi", CortexContext(tenant_id="t-b", classification="CUI", domain="proposal"), {})
    assert k != rc.make_key("cortex.complete", "hi", CortexContext(tenant_id="t-a", classification="SECRET", domain="proposal"), {})
    assert k != rc.make_key("cortex.complete", "hi", CortexContext(tenant_id="t-a", classification="CUI", domain="network"), {})
    assert k != rc.make_key("cortex.complete", "hi", CortexContext(tenant_id="t-a", classification="CUI", domain="proposal", air_gap=True), {})
    # Different prompt or extra args -> different key.
    assert k != rc.make_key("cortex.complete", "bye", base, {})
    assert k != rc.make_key("cortex.complete", "hi", base, {"temperature": 0.9})
    # user_id intentionally does NOT partition (same tenant+classification share).
    assert k == rc.make_key("cortex.complete", "hi",
                            CortexContext(tenant_id="t-a", classification="CUI", domain="proposal", user_id="u9"), {})


# --------------------------------------------------------------------------- #
# _TTLCache unit
# --------------------------------------------------------------------------- #
def test_ttlcache_expiry_and_lru(monkeypatch):
    import tools.cortex.cache as cmod
    clock = {"t": 1000.0}
    monkeypatch.setattr(cmod.time, "monotonic", lambda: clock["t"])
    c = cmod._TTLCache(max_entries=2)
    c.put("a", 1, ttl=10)
    assert c.get("a") == 1
    clock["t"] = 1011.0  # past ttl
    assert c.get("a") is None
    # LRU eviction at capacity 2.
    clock["t"] = 2000.0
    c.put("x", 1, ttl=100); c.put("y", 2, ttl=100)
    c.get("x")  # touch x -> y is now LRU
    c.put("z", 3, ttl=100)  # evicts y
    assert c.get("y") is None
    assert c.get("x") == 1 and c.get("z") == 3


# --------------------------------------------------------------------------- #
# Facade integration
# --------------------------------------------------------------------------- #
def test_disabled_by_default_no_caching(monkeypatch):
    monkeypatch.setattr(rc, "_cache_cfg", lambda: {"enabled": False})
    calls = _count_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.complete("same prompt", ctx=ctx)
    api.complete("same prompt", ctx=ctx)
    assert calls["n"] == 2  # no cache -> both ran


def test_enabled_serves_identical_call_from_cache(monkeypatch):
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a", domain="proposal")
    r1 = api.complete("draft it", ctx=ctx, system_prompt="terse")
    r2 = api.complete("draft it", ctx=ctx, system_prompt="terse")
    assert calls["n"] == 1  # second served from cache
    assert r1.text == r2.text


def test_cache_isolated_across_tenants(monkeypatch):
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    api.complete("secret question", ctx=CortexContext(tenant_id="t-a"))
    api.complete("secret question", ctx=CortexContext(tenant_id="t-b"))
    assert calls["n"] == 2  # different tenant -> never shared


def test_cache_isolated_across_classification_and_domain(monkeypatch):
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    api.complete("q", ctx=CortexContext(tenant_id="t", classification="CUI"))
    api.complete("q", ctx=CortexContext(tenant_id="t", classification="SECRET"))
    api.complete("q", ctx=CortexContext(tenant_id="t", classification="CUI", domain="network"))
    assert calls["n"] == 3


def test_different_system_prompt_not_shared(monkeypatch):
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.complete("q", ctx=ctx, system_prompt="style A")
    api.complete("q", ctx=ctx, system_prompt="style B")
    assert calls["n"] == 2


def test_cache_hit_is_audited(monkeypatch):
    _enable(monkeypatch)
    _count_invoke(monkeypatch)
    audits = []
    import importlib
    # db/__init__.py re-exports the init_db FUNCTION under the same dotted name,
    # so import the module explicitly to patch record_audit on it.
    initdb = importlib.import_module("tools.cortex.db.init_db")
    monkeypatch.setattr(initdb, "record_audit", lambda payload, conn=None: audits.append(payload) or "id")
    ctx = CortexContext(tenant_id="t-a")
    api.complete("q", ctx=ctx)      # miss -> stores
    api.complete("q", ctx=ctx)      # hit -> audited
    assert any(a.get("cache_hit") for a in audits), "cache hit must write an audit row"
    hit = next(a for a in audits if a.get("cache_hit"))
    assert hit["cost_usd"] == 0.0   # a hit incurs no new spend
    assert hit["tenant_id"] == "t-a"


def test_blocked_result_not_cached(monkeypatch):
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    # Gateway denies -> pre_check blocks -> complete raises, nothing cached.
    monkeypatch.setattr(gov, "_gate_check_text",
                        lambda t: {"allowed": False, "warnings": [], "blocked_reason": "injection"})
    from tools.cortex.api import CortexQueryBlocked
    from tools.cortex.governance import GovernanceBlockedError
    ctx = CortexContext(tenant_id="t-a")
    with pytest.raises((GovernanceBlockedError, CortexQueryBlocked)):
        api.complete("bad", ctx=ctx)
    # Restore gateway; the same prompt must now actually run (was not cached).
    monkeypatch.setattr(gov, "_gate_check_text",
                        lambda t: {"allowed": True, "warnings": [], "blocked_reason": None})
    api.complete("bad", ctx=ctx)
    assert calls["n"] == 1  # only the post-unblock call ran


# --------------------------------------------------------------------------- #
# classify / extract — the two deterministic, high-repeat operations (cxo-perf-06)
# --------------------------------------------------------------------------- #
def _count_extract_invoke(monkeypatch, payload=None):
    """_invoke double for extract(): the impl reads response.structured_output."""
    calls = {"n": 0}

    def _fake(function, request, context):
        calls["n"] += 1
        return SimpleNamespace(content="{}", structured_output=payload or {"k": "v"},
                               provider="p", model_id="m", cost_usd=0.0, duration_ms=1)

    monkeypatch.setattr(api, "_invoke", _fake)
    return calls


def test_shipped_config_caches_classify_and_extract():
    """The SHIPPED args/cortex_config.yaml opts both ops in — and stays disabled."""
    from tools.cortex.config import load_cortex_config

    cache_cfg = load_cortex_config().get("cache") or {}
    assert cache_cfg.get("enabled") is False, "caching must stay opt-in platform-wide"
    ops = cache_cfg.get("operations") or []
    assert "cortex.classify" in ops and "cortex.extract" in ops
    ttls = cache_cfg.get("ttl_seconds") or {}
    # Both carry an explicit, finite TTL rather than silently inheriting default.
    assert float(ttls["cortex.classify"]) > 0
    assert float(ttls["cortex.extract"]) > 0
    # classify degrades to a heuristic label on router failure and that result is
    # cacheable, so its TTL must not exceed complete's.
    assert float(ttls["cortex.classify"]) <= float(ttls["cortex.complete"])


def test_classify_and_extract_are_cacheable(monkeypatch):
    _enable(monkeypatch)
    assert rc.cacheable("cortex.classify")
    assert rc.cacheable("cortex.extract")


def test_classify_repeat_call_hits_cache(monkeypatch):
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch, text="alpha")
    ctx = CortexContext(tenant_id="t-a", domain="proposal")
    r1 = api.classify("some text", ["alpha", "beta"], ctx=ctx)
    r2 = api.classify("some text", ["alpha", "beta"], ctx=ctx)
    assert calls["n"] == 1  # second served from cache
    assert r1.text == r2.text == "alpha"


def test_extract_repeat_call_hits_cache(monkeypatch):
    _enable(monkeypatch)
    calls = _count_extract_invoke(monkeypatch, payload={"total": 7})
    ctx = CortexContext(tenant_id="t-a")
    schema = {"type": "object"}
    r1 = api.extract("line items", schema, ctx=ctx)
    r2 = api.extract("line items", schema, ctx=ctx)
    assert calls["n"] == 1
    assert r1.text == r2.text


def test_classify_isolated_across_tenant_and_classification(monkeypatch):
    """The boundary the security note turns on: no cross-tenant/-classification reuse."""
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch, text="alpha")
    labels = ["alpha", "beta"]
    api.classify("q", labels, ctx=CortexContext(tenant_id="t-a", classification="CUI"))
    api.classify("q", labels, ctx=CortexContext(tenant_id="t-b", classification="CUI"))
    api.classify("q", labels, ctx=CortexContext(tenant_id="t-a", classification="SECRET"))
    api.classify("q", labels, ctx=CortexContext(tenant_id="t-a", classification="CUI",
                                                domain="network"))
    api.classify("q", labels, ctx=CortexContext(tenant_id="t-a", classification="CUI",
                                                air_gap=True))
    assert calls["n"] == 5  # every boundary dimension partitions the cache


def test_extract_isolated_across_tenant_and_classification(monkeypatch):
    _enable(monkeypatch)
    calls = _count_extract_invoke(monkeypatch)
    schema = {"type": "object"}
    api.extract("doc", schema, ctx=CortexContext(tenant_id="t-a", classification="CUI"))
    api.extract("doc", schema, ctx=CortexContext(tenant_id="t-b", classification="CUI"))
    api.extract("doc", schema, ctx=CortexContext(tenant_id="t-a", classification="SECRET"))
    api.extract("doc", schema, ctx=CortexContext(tenant_id="t-a", classification="CUI",
                                                 domain="document"))
    api.extract("doc", schema, ctx=CortexContext(tenant_id="t-a", classification="CUI",
                                                 air_gap=True))
    assert calls["n"] == 5


def test_classify_different_label_set_not_shared(monkeypatch):
    """labels is output-affecting -> it must partition, or a hit answers the
    wrong question with a label the caller never offered."""
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch, text="alpha")
    ctx = CortexContext(tenant_id="t-a")
    api.classify("q", ["alpha", "beta"], ctx=ctx)
    api.classify("q", ["alpha", "gamma"], ctx=ctx)   # different set
    api.classify("q", ["beta", "alpha"], ctx=ctx)    # same set, different order
    assert calls["n"] == 3


def test_extract_different_schema_not_shared(monkeypatch):
    """schema is output-affecting -> a hit must never return a payload shaped
    for a different schema."""
    _enable(monkeypatch)
    calls = _count_extract_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.extract("doc", {"type": "object", "properties": {"a": {"type": "string"}}}, ctx=ctx)
    api.extract("doc", {"type": "object", "properties": {"b": {"type": "string"}}}, ctx=ctx)
    assert calls["n"] == 2


def test_classify_hit_is_audited(monkeypatch):
    _enable(monkeypatch)
    _count_invoke(monkeypatch, text="alpha")
    audits = []
    import importlib
    initdb = importlib.import_module("tools.cortex.db.init_db")
    monkeypatch.setattr(initdb, "record_audit",
                        lambda payload, conn=None: audits.append(payload) or "id")
    ctx = CortexContext(tenant_id="t-a")
    api.classify("q", ["alpha"], ctx=ctx)   # miss -> stores
    api.classify("q", ["alpha"], ctx=ctx)   # hit -> audited
    hit = next(a for a in audits if a.get("cache_hit"))
    assert hit["operation"] == "cortex.classify"
    assert hit["cost_usd"] == 0.0
    assert hit["tenant_id"] == "t-a"


def test_extract_can_be_opted_out_without_touching_code(monkeypatch):
    """operations is the whole switch — dropping an op from it disables it."""
    _enable(monkeypatch, operations=["cortex.complete"])
    calls = _count_extract_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.extract("doc", {"type": "object"}, ctx=ctx)
    api.extract("doc", {"type": "object"}, ctx=ctx)
    assert calls["n"] == 2


def test_new_op_ttls_come_from_module_defaults_not_the_generic_default(monkeypatch):
    """A config with no ttl_seconds must still resolve both ops to their own TTL.

    Asserts the module fallback table, not the fixture: if _DEFAULT_TTL lacked
    the two new keys they would silently inherit default=300.
    """
    monkeypatch.setattr(rc, "_cache_cfg", lambda: {"enabled": True})
    rc.reset()
    assert rc._ttl_for("cortex.classify") == 600.0
    assert rc._ttl_for("cortex.extract") == 900.0
    assert rc._ttl_for("cortex.unknown") == 300.0  # unknown op -> generic default


def test_module_default_operations_cover_the_new_ops(monkeypatch):
    """cacheable() with no configured operations falls back to _DEFAULT_OPERATIONS.

    That fallback is what applies when args/cortex_config.yaml is unreadable, so
    it must not drift from the shipped file.
    """
    monkeypatch.setattr(rc, "_cache_cfg", lambda: {"enabled": True})
    assert rc.cacheable("cortex.classify")
    assert rc.cacheable("cortex.extract")
    assert not rc.cacheable("cortex.govern")
