# CUI // SP-CTI
"""Tests for the opt-in Cortex response cache (tools/cortex/cache.py).

Focus on the security-load-bearing behaviors: the key folds the full
tenant/classification/domain/air_gap boundary (a hit never crosses it), a hit is
still audited, and a blocked result is never cached.

ctx-perf-06 added the two preconditions for shipping it ON: entries are copied in
and out (a caller cannot mutate the stored answer), and there is an explicit
invalidation path — with cortex.ask dropped from the default operations because
no sound in-process invalidation exists for it.
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
        "operations": ["cortex.complete", "cortex.search",
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
    """The SHIPPED args/cortex_config.yaml opts both ops in — and is now ON."""
    from tools.cortex.config import load_cortex_config

    cache_cfg = load_cortex_config().get("cache") or {}
    assert cache_cfg.get("enabled") is True, "ctx-perf-06 flipped the cache on"
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


# --------------------------------------------------------------------------- #
# ctx-perf-06 #1 — a served result is a COPY; a caller cannot poison the entry
# --------------------------------------------------------------------------- #
def test_hit_cannot_be_mutated_into_the_cache(monkeypatch):
    """The acceptance case: mutate a hit, then re-read, and get the original.

    Storing the live object means one caller doing `result.text = trim(...)`
    rewrites the answer every later hit is served, for the whole TTL.
    """
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch, text="original answer")
    ctx = CortexContext(tenant_id="t-a")

    api.complete("q", ctx=ctx)                       # miss -> stores
    hit = api.complete("q", ctx=ctx)                 # hit
    assert calls["n"] == 1

    hit.text = "MUTATED"
    hit.metadata["poison"] = True
    hit.data["poison"] = True
    hit.citations.append("bogus")

    again = api.complete("q", ctx=ctx)               # still a hit
    assert calls["n"] == 1, "must still be served from cache, not re-run"
    assert again.text == "original answer"
    assert "poison" not in again.metadata
    assert "poison" not in again.data
    assert "bogus" not in again.citations


def test_miss_result_cannot_be_mutated_into_the_cache(monkeypatch):
    """Copy-on-READ alone is not enough — the producing caller holds the object
    that was stored, so the entry must be copied on WRITE too."""
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch, text="original answer")
    ctx = CortexContext(tenant_id="t-a")

    miss = api.complete("q", ctx=ctx)                # miss -> stores
    miss.text = "MUTATED BY PRODUCER"
    miss.metadata["poison"] = True

    hit = api.complete("q", ctx=ctx)
    assert calls["n"] == 1
    assert hit.text == "original answer"
    assert "poison" not in hit.metadata


def test_every_caller_gets_a_distinct_object(monkeypatch):
    """Two hits must not alias each other either — otherwise concurrent callers
    share one mutable answer."""
    _enable(monkeypatch)
    _count_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    r1 = api.complete("q", ctx=ctx)
    r2 = api.complete("q", ctx=ctx)
    r3 = api.complete("q", ctx=ctx)
    assert r1 is not r2 and r2 is not r3
    assert r1.metadata is not r2.metadata, "shallow copy would still share metadata"


def test_nested_structured_payload_is_deep_copied():
    """The analyst puts rows under result.data — a shallow copy would leave those
    inner dicts shared even though the CortexResult itself was new."""
    from tools.cortex.schemas import CortexResult

    stored = CortexResult(text="rows", data={"rows": [{"qty": 1}], "row_count": 1})
    rc.put_by_key("k", stored, "cortex.ask")

    hit = rc.get_by_key("k")
    hit.data["rows"][0]["qty"] = 999
    hit.data["row_count"] = 999
    hit.governance.outcomes["poison"] = True   # nested dataclass field

    again = rc.get_by_key("k")
    assert again.data["rows"][0]["qty"] == 1, "nested mutation leaked into the entry"
    assert again.data["row_count"] == 1
    assert "poison" not in again.governance.outcomes
    # ...and the object handed to the producing caller is untouched too.
    assert stored.data["rows"][0]["qty"] == 1


def test_uncopyable_result_is_not_cached_by_reference():
    """If a payload cannot be copied, skip the cache — never fall back to
    storing the live object, which is the exact failure being prevented."""
    class _NoCopy:
        def __deepcopy__(self, memo):
            raise TypeError("not copyable")

    rc.put_by_key("k", _NoCopy(), "cortex.complete")
    assert rc.get_by_key("k") is None, "an uncopyable result must not be cached"


def test_uncopyable_stored_value_is_served_as_a_miss(monkeypatch):
    """Copy failure on READ degrades to a miss (slow but correct), never to
    handing out the stored instance."""
    rc.put_by_key("k", {"a": 1}, "cortex.complete")

    def _boom(value):
        raise TypeError("not copyable")

    monkeypatch.setattr(rc.copy, "deepcopy", _boom)
    assert rc.get_by_key("k") is None


# --------------------------------------------------------------------------- #
# ctx-perf-06 #2 — invalidation: cortex.ask is out, and a purge path exists
# --------------------------------------------------------------------------- #
def test_ask_is_not_cacheable_by_default(monkeypatch):
    """ask is live NL->SQL over the operational DB. Its invalidating writes come
    from other processes, which an in-process cache cannot observe, so it is out
    of the default operations list rather than hooked."""
    monkeypatch.setattr(rc, "_cache_cfg", lambda: {"enabled": True})
    assert not rc.cacheable("cortex.ask")
    assert "cortex.ask" not in rc._DEFAULT_OPERATIONS


def test_shipped_config_does_not_cache_ask():
    from tools.cortex.config import load_cortex_config

    cache_cfg = load_cortex_config().get("cache") or {}
    assert "cortex.ask" not in (cache_cfg.get("operations") or [])


def test_ask_keeps_a_short_ttl_if_an_operator_re_adds_it(monkeypatch):
    """Re-adding ask must be short-bounded, not silently inherit default: 300."""
    from tools.cortex.config import load_cortex_config

    ttls = (load_cortex_config().get("cache") or {}).get("ttl_seconds") or {}
    assert float(ttls["cortex.ask"]) <= 30
    monkeypatch.setattr(rc, "_cache_cfg", lambda: {"enabled": True})
    assert rc._ttl_for("cortex.ask") == 30.0 < rc._ttl_for("cortex.unknown")


def test_ask_is_cacheable_when_explicitly_opted_in(monkeypatch):
    """Dropping it from the DEFAULTS must not remove the operator's choice."""
    _enable(monkeypatch, operations=["cortex.ask"])
    assert rc.cacheable("cortex.ask")
    assert not rc.cacheable("cortex.complete")


def test_invalidate_purges_entries_and_reports_the_count(monkeypatch):
    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.complete("q1", ctx=ctx)
    api.complete("q2", ctx=ctx)
    api.complete("q1", ctx=ctx)          # hit
    assert calls["n"] == 2

    assert rc.invalidate("test") == 2
    api.complete("q1", ctx=ctx)          # purged -> must re-run
    assert calls["n"] == 3


def test_invalidate_is_a_noop_on_a_cold_cache():
    rc.reset()
    assert rc.invalidate("cold") == 0


def test_rag_ingestion_invalidates_the_cache(monkeypatch):
    """The invalidation path has a REAL consumer — a declared-but-unconsumed
    purge hook would be no invalidation story at all."""
    from tools.rag import ingestion_manager as im

    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.complete("q", ctx=ctx)
    api.complete("q", ctx=ctx)
    assert calls["n"] == 1                      # warm

    im.invalidate_cortex_cache("govcon_proposal_section", chunks=5)
    api.complete("q", ctx=ctx)
    assert calls["n"] == 2, "a corpus change must drop stale search-backed answers"


def test_rag_ingestion_does_not_invalidate_when_nothing_was_written(monkeypatch):
    """A dedup-only sweep writes no chunks — it must not throw away warm entries."""
    from tools.rag import ingestion_manager as im

    _enable(monkeypatch)
    calls = _count_invoke(monkeypatch)
    ctx = CortexContext(tenant_id="t-a")
    api.complete("q", ctx=ctx)
    im.invalidate_cortex_cache("govcon_proposal_section", chunks=0)
    api.complete("q", ctx=ctx)
    assert calls["n"] == 1


def test_every_corpus_mutation_path_calls_the_invalidator():
    """Guards the wiring itself: the hook is reachable from the batch ingest, the
    realtime ingest AND the delete path — not just defined next to them. Delete
    is the sharpest case: a cached answer can keep citing a removed source."""
    from tools.mcp import rag_server
    from tools.rag import ingestion_manager as im

    assert "invalidate_cortex_cache" in im.ingest_source.__code__.co_names
    assert "invalidate_cortex_cache" in im.ingest_single_record.__code__.co_names
    assert "invalidate_cortex_cache" in rag_server.handle_rag_delete_source.__code__.co_names


# --------------------------------------------------------------------------- #
# ctx-perf-06 #3 — the flip: shipped config ON, and a hit still audits
# --------------------------------------------------------------------------- #
def test_shipped_config_serves_a_hit_and_still_audits_it(monkeypatch):
    """End-to-end on the SHIPPED config (no _cache_cfg patch): the flip really
    caches, and the NIST-AU trail stays complete because the hit writes its row.
    """
    rc.reset()
    calls = _count_invoke(monkeypatch)
    audits = []
    import importlib
    initdb = importlib.import_module("tools.cortex.db.init_db")
    monkeypatch.setattr(initdb, "record_audit",
                        lambda payload, conn=None: audits.append(payload) or "id")

    ctx = CortexContext(tenant_id="t-shipped", classification="CUI")
    api.complete("shipped prompt", ctx=ctx)
    api.complete("shipped prompt", ctx=ctx)

    assert calls["n"] == 1, "shipped config must actually serve the second call from cache"
    hit = next((a for a in audits if a.get("cache_hit")), None)
    assert hit is not None, "a cache hit must still write its cortex_audit row"
    assert hit["operation"] == "cortex.complete"
    assert hit["tenant_id"] == "t-shipped"
    assert hit["cost_usd"] == 0.0
    assert hit["blocked"] is False
