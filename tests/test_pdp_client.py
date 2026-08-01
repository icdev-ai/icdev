#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for G-12 — PDP Client (tools/security/pdp_client.py)."""


import tools.security.pdp_client as _pdp


def _clear():
    _pdp.clear_cache()


# ---------------------------------------------------------------------------
# PDPDecision dataclass
# ---------------------------------------------------------------------------

class TestPDPDecision:
    def test_to_dict_fields(self):
        d = _pdp.PDPDecision(permit=True, reason="ok", policy_name="p1", adapter="local")
        result = d.to_dict()
        assert result["permit"] is True
        assert result["reason"] == "ok"
        assert result["policy_name"] == "p1"
        assert result["adapter"] == "local"

    def test_defaults(self):
        d = _pdp.PDPDecision(permit=False, reason="denied")
        assert d.policy_name == ""
        assert d.adapter == "local"


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------

class TestAdapterFactory:
    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("ICDEV_PDP_ADAPTER", raising=False)
        adapter = _pdp._build_pdp_adapter()
        assert isinstance(adapter, _pdp.LocalPDPAdapter)

    def test_disa_icam_selected(self, monkeypatch):
        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "disa_icam")
        adapter = _pdp._build_pdp_adapter()
        assert isinstance(adapter, _pdp.DisaICAMAdapter)

    def test_zscaler_zpa_selected(self, monkeypatch):
        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "zscaler_zpa")
        adapter = _pdp._build_pdp_adapter()
        assert isinstance(adapter, _pdp.ZscalerZPAAdapter)

    def test_unknown_falls_back_to_local(self, monkeypatch):
        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "acme_pdp")
        adapter = _pdp._build_pdp_adapter()
        assert isinstance(adapter, _pdp.LocalPDPAdapter)


# ---------------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------------

class TestStubAdapters:
    # NOTE (shx-safe-03): stub adapters now FAIL CLOSED. Previously these
    # asserted unconditional permit=True (fail-open). They now deny by default
    # and only permit when ICDEV_ZT_ALLOW_STUB is set. Full matrix lives in
    # tests/test_zt_stub_gate.py.
    def test_disa_icam_denies_by_default(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        adapter = _pdp.DisaICAMAdapter()
        decision = adapter.evaluate({}, "res", "read")
        assert decision.permit is False
        assert decision.adapter == "disa_icam"

    def test_disa_icam_permits_with_stub_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        adapter = _pdp.DisaICAMAdapter()
        decision = adapter.evaluate({}, "res", "read")
        assert decision.permit is True
        assert decision.adapter == "disa_icam"

    def test_zscaler_zpa_denies_by_default(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        adapter = _pdp.ZscalerZPAAdapter()
        decision = adapter.evaluate({}, "res", "read")
        assert decision.permit is False
        assert decision.adapter == "zscaler_zpa"

    def test_zscaler_zpa_permits_with_stub_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        adapter = _pdp.ZscalerZPAAdapter()
        decision = adapter.evaluate({}, "res", "read")
        assert decision.permit is True
        assert decision.adapter == "zscaler_zpa"


# ---------------------------------------------------------------------------
# evaluate_access — caching
# ---------------------------------------------------------------------------

class TestEvaluateAccessCache:
    def test_cache_hit_returns_same_decision(self, monkeypatch):
        _clear()
        call_count = [0]

        class CountingAdapter(_pdp.PDPAdapter):
            def evaluate(self, user_ctx, resource, action):
                call_count[0] += 1
                return _pdp.PDPDecision(permit=True, reason="counted", adapter="test")

        monkeypatch.setattr(_pdp, "_build_pdp_adapter", lambda: CountingAdapter())
        monkeypatch.setenv("ICDEV_PDP_CACHE_TTL", "60")

        ctx = {"user_id": "alice", "role": "analyst"}
        _pdp.evaluate_access(ctx, "proj-1", "read")
        _pdp.evaluate_access(ctx, "proj-1", "read")

        assert call_count[0] == 1  # second call served from cache

    def test_different_params_miss_cache(self, monkeypatch):
        _clear()
        call_count = [0]

        class CountingAdapter(_pdp.PDPAdapter):
            def evaluate(self, user_ctx, resource, action):
                call_count[0] += 1
                return _pdp.PDPDecision(permit=True, reason="counted", adapter="test")

        monkeypatch.setattr(_pdp, "_build_pdp_adapter", lambda: CountingAdapter())

        ctx = {"user_id": "alice"}
        _pdp.evaluate_access(ctx, "proj-1", "read")
        _pdp.evaluate_access(ctx, "proj-2", "read")  # different resource

        assert call_count[0] == 2

    def test_clear_cache_forces_refetch(self, monkeypatch):
        _clear()
        call_count = [0]

        class CountingAdapter(_pdp.PDPAdapter):
            def evaluate(self, user_ctx, resource, action):
                call_count[0] += 1
                return _pdp.PDPDecision(permit=True, reason="counted", adapter="test")

        monkeypatch.setattr(_pdp, "_build_pdp_adapter", lambda: CountingAdapter())

        ctx = {"user_id": "bob"}
        _pdp.evaluate_access(ctx, "res", "write")
        _pdp.clear_cache()
        _pdp.evaluate_access(ctx, "res", "write")

        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# evaluate_access — error handling
# ---------------------------------------------------------------------------

class TestEvaluateAccessErrors:
    def test_error_non_strict_permits(self, monkeypatch):
        _clear()

        class BrokenAdapter(_pdp.PDPAdapter):
            def evaluate(self, user_ctx, resource, action):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(_pdp, "_build_pdp_adapter", lambda: BrokenAdapter())
        monkeypatch.setenv("ICDEV_PDP_STRICT", "false")

        decision = _pdp.evaluate_access({"user_id": "u1"}, "x", "read")
        assert decision.permit is True
        assert "non-strict fallback" in decision.reason

    def test_error_strict_denies(self, monkeypatch):
        _clear()

        class BrokenAdapter(_pdp.PDPAdapter):
            def evaluate(self, user_ctx, resource, action):
                raise RuntimeError("upstream unreachable")

        monkeypatch.setattr(_pdp, "_build_pdp_adapter", lambda: BrokenAdapter())
        monkeypatch.setenv("ICDEV_PDP_STRICT", "true")

        decision = _pdp.evaluate_access({"user_id": "u1"}, "x", "read")
        assert decision.permit is False
        assert "strict mode" in decision.reason

    def test_error_strict_cleanup(self, monkeypatch):
        monkeypatch.delenv("ICDEV_PDP_STRICT", raising=False)


# ---------------------------------------------------------------------------
# Cache key determinism
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_same_inputs_same_key(self):
        ctx = {"user_id": "alice", "role": "analyst"}
        k1 = _pdp._cache_key(ctx, "resource", "read")
        k2 = _pdp._cache_key(ctx, "resource", "read")
        assert k1 == k2

    def test_different_action_different_key(self):
        ctx = {"user_id": "alice"}
        k1 = _pdp._cache_key(ctx, "resource", "read")
        k2 = _pdp._cache_key(ctx, "resource", "write")
        assert k1 != k2

    def test_key_is_sha256_hex(self):
        key = _pdp._cache_key({}, "r", "a")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)
