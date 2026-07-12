# CUI // SP-CTI
"""Governance-hardening regression tests (Cortex analysis follow-up).

- REST v1 search/ask must call the GOVERNED api facades, not the raw impls.
- Every Cortex LLMRequest must carry a budget/rate agent_id.
"""
from __future__ import annotations

from pathlib import Path

from tools.cortex import api as cortex_api
from tools.cortex import rest_v1
from tools.cortex.schemas import CortexContext


class TestRestSurfaceIsGoverned:
    # Note: assert the __cortex_governed__ STAMP (robust to importlib.reload in
    # sibling tests, which mints new facade objects) rather than `is` identity.
    def test_rest_search_is_governed(self):
        assert getattr(rest_v1.search, "__cortex_governed__", False) is True

    def test_rest_ask_is_governed(self):
        assert getattr(rest_v1.ask, "__cortex_governed__", False) is True

    def test_rest_complete_classify_extract_governed(self):
        for fn in (rest_v1.complete, rest_v1.classify, rest_v1.extract):
            assert getattr(fn, "__cortex_governed__", False) is True

    def test_rest_v1_imports_from_api_not_raw_impls(self):
        # Source-level guard (immune to runtime monkeypatch/reload): ask + search
        # must come from `.api` (governed), never from `.analyst`/`.search_service`.
        src = Path(rest_v1.__file__).read_text(encoding="utf-8")
        assert "from .search_service import search" not in src
        assert "from .analyst import CortexAnalystError, CortexQueryBlocked, ask" not in src
        api_line = next(l for l in src.splitlines() if l.strip().startswith("from .api import"))
        assert "ask" in api_line and "search" in api_line


class TestAgentIdThreaded:
    def test_build_request_sets_agent_id_from_ctx(self):
        ctx = CortexContext(tenant_id="t1", agent_id="team-alpha")
        req = cortex_api._build_request("hello", ctx)
        assert req.agent_id == "team-alpha"

    def test_build_request_derives_per_tenant_key_when_unset(self):
        ctx = CortexContext(tenant_id="acme")
        req = cortex_api._build_request("hello", ctx)
        assert req.agent_id == "cortex:acme"

    def test_build_request_defaults_when_no_tenant(self):
        req = cortex_api._build_request("hello", CortexContext())
        assert req.agent_id == "cortex:default"
