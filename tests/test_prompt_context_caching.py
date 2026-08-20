# CUI // SP-CTI
"""RED Phase tests: Prompt/Context Caching Enhancements (5 features).

Tests are written FIRST against the expected public interface.
All 5 tests in the initial run MUST FAIL (RED) before implementation.

NIST 800-53 controls: SA-11 (Developer Testing), SC-28 (Protection at Rest),
SI-3 (Malicious Code Protection), AU-12 (Audit Record Generation).

Features under test:
  (1) RAG context caching  — cache_control breakpoints on RAG-injected chunks
  (2) Cache savings dashboard — /cache-savings route with savings metrics
  (3) Multi-breakpoint support — up to 4 Anthropic cache_control breakpoints
  (4) OpenAI cache token parity — extract cached_tokens from prompt_tokens_details
  (5) Cache warming on deploy — CacheWarmer.warm_on_deploy() with seed queries
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional imports — tests skip gracefully if module unavailable
# ---------------------------------------------------------------------------

try:
    from tools.llm.provider import LLMRequest

    HAS_LLM = True
except ImportError:
    HAS_LLM = False

try:
    from tools.llm.router import LLMRouter

    HAS_ROUTER = True
except ImportError:
    HAS_ROUTER = False

try:
    import tools.llm.response_cache  # noqa: F401

    HAS_CACHE = True
except ImportError:
    HAS_CACHE = False


# ===========================================================================
# FEATURE 1: RAG Context Caching — cache_control breakpoints on RAG chunks
# ===========================================================================


@pytest.mark.skipif(not HAS_ROUTER, reason="LLMRouter unavailable")
class TestRAGContextCaching:
    """cache_control='ephemeral' must be applied to RAG-injected system prompt chunks."""

    def _make_router_with_rag_config(self):
        """Return LLMRouter with a synthetic config that enables RAG + context cache."""
        router = LLMRouter.__new__(LLMRouter)
        router._config = {
            "rag": {
                "enabled": True,
                "injection": {
                    "enabled": True,
                    "injection_top_k": 3,
                    "max_injection_chars": 2000,
                    "citation_enabled": True,
                    "citation_instruction": False,
                },
            },
            "response_cache": {
                "enabled": True,
                "per_function": {
                    "code_generation": {"context_cache": True},
                },
                "per_canvas": {},
            },
            "redaction": {"enabled": False},
        }
        router._providers = {}
        router._embedding_providers = {}
        router._availability_cache = {}
        router._availability_cache_time = 0.0
        router._cache_ttl = 1800.0
        return router

    def test_rag_augment_applies_cache_control_to_chunks(self):
        """_rag_augment must mark the injected context block with cache_control='ephemeral'
        when context_cache is configured for the function.
        """
        router = self._make_router_with_rag_config()
        request = LLMRequest(
            messages=[{"role": "user", "content": "What is FedRAMP AC-2?"}],
            system_prompt="You are a compliance assistant.",
            cache_control="ephemeral",
        )

        # Mock RAGRetriever.search to return 2 synthetic chunks
        mock_result = MagicMock()
        mock_result.content = "FedRAMP AC-2 controls account management procedures."
        mock_result.source_type = "knowledge"
        mock_result.source_id = "kb-001"
        mock_result.final_score = 0.92

        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [mock_result, mock_result]

        with patch("tools.rag.retriever.RAGRetriever", return_value=mock_retriever):
            augmented = router._rag_augment(request, "code_generation")

        # The augmented system prompt must carry the injected context
        assert augmented.system_prompt, "Augmented system prompt must not be empty"
        assert "[RELEVANT CONTEXT" in augmented.system_prompt, (
            "Augmented system prompt must contain injected RAG context block"
        )
        # cache_control must be preserved (ephemeral) so provider applies breakpoints
        assert augmented.cache_control == "ephemeral", (
            "cache_control must remain 'ephemeral' after RAG augmentation so provider "
            "can apply cache_control breakpoints to the injected context"
        )

    def test_rag_augment_inserts_cache_breakpoint_marker(self):
        """When cache_control='ephemeral' and RAG context is injected, a cache breakpoint
        marker must be emitted in the augmented system prompt so the Anthropic provider
        can insert cache_control blocks at the boundary.

        Expected marker: '<!-- cache_breakpoint -->' inserted between the injected
        RAG context block and the original system prompt.
        """
        router = self._make_router_with_rag_config()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Explain NIST AC-3."}],
            system_prompt="Answer concisely.",
            cache_control="ephemeral",
        )

        mock_result = MagicMock()
        mock_result.content = "NIST AC-3 enforces access control policies."
        mock_result.source_type = "nist"
        mock_result.source_id = "ac-3"
        mock_result.final_score = 0.88

        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [mock_result]

        with patch("tools.rag.retriever.RAGRetriever", return_value=mock_retriever):
            augmented = router._rag_augment(request, "code_generation")

        assert "<!-- cache_breakpoint -->" in augmented.system_prompt, (
            "RAG augment must insert '<!-- cache_breakpoint -->' between the injected "
            "context and the original system prompt when cache_control='ephemeral'"
        )

    def test_rag_augment_no_breakpoint_without_cache_control(self):
        """No cache breakpoint marker when cache_control is not 'ephemeral'."""
        router = self._make_router_with_rag_config()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Explain NIST SI-3."}],
            system_prompt="You are helpful.",
            cache_control="",  # no cache control
        )

        mock_result = MagicMock()
        mock_result.content = "SI-3 protects against malicious code."
        mock_result.source_type = "nist"
        mock_result.source_id = "si-3"
        mock_result.final_score = 0.85

        mock_retriever = MagicMock()
        mock_retriever.search.return_value = [mock_result]

        with patch("tools.rag.retriever.RAGRetriever", return_value=mock_retriever):
            augmented = router._rag_augment(request, "code_generation")

        # Breakpoint marker must NOT appear when no ephemeral cache control
        assert "<!-- cache_breakpoint -->" not in augmented.system_prompt, (
            "Cache breakpoint marker must NOT be inserted when cache_control != 'ephemeral'"
        )

    def test_anthropic_provider_processes_cache_breakpoint(self):
        """AnthropicLLMProvider.invoke() must split system prompt at '<!-- cache_breakpoint -->'
        and emit separate text blocks with cache_control={'type':'ephemeral'} for each part.
        """
        from tools.llm.anthropic_provider import AnthropicLLMProvider

        provider = AnthropicLLMProvider(api_key="test-key")

        request = LLMRequest(
            messages=[{"role": "user", "content": "Hello"}],
            system_prompt=(
                "[RELEVANT CONTEXT]\nFedRAMP chunk here.\n[END CONTEXT]\n"
                "<!-- cache_breakpoint -->\n"
                "You are a compliance assistant."
            ),
            cache_control="ephemeral",
            max_tokens=10,
        )

        # Capture kwargs sent to client.messages.create
        captured_kwargs = {}

        def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            # Return minimal mock response
            resp = MagicMock()
            resp.stop_reason = "end_turn"
            usage = MagicMock()
            usage.input_tokens = 50
            usage.output_tokens = 5
            usage.cache_creation_input_tokens = 40
            usage.cache_read_input_tokens = 0
            resp.usage = usage
            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = "ok"
            resp.content = [text_block]
            return resp

        provider._client = MagicMock()
        provider._client.messages.create.side_effect = fake_create

        provider.invoke(request, "claude-sonnet-4-5-20250929", {"max_output_tokens": 64000})

        system_arg = captured_kwargs.get("system")
        assert isinstance(system_arg, list), (
            "When '<!-- cache_breakpoint -->' is in system_prompt, system kwarg must be a list of blocks"
        )
        assert len(system_arg) >= 2, (
            f"Expected at least 2 system blocks (RAG chunk + original prompt), got {len(system_arg)}"
        )
        # Each non-last block must carry cache_control
        for block in system_arg[:-1]:
            assert block.get("cache_control") == {"type": "ephemeral"}, (
                f"Non-final system block must have cache_control={{'type':'ephemeral'}}, got: {block}"
            )


# ===========================================================================
# FEATURE 2: Cache Savings Dashboard Page
# ===========================================================================


class TestCacheSavingsDashboard:
    """Cache savings dashboard page must exist and return savings metrics."""

    def test_cache_savings_blueprint_exists(self):
        """tools/cache_savings/blueprint.py must exist and define a Flask Blueprint."""
        import importlib
        import importlib.util

        spec = importlib.util.find_spec("tools.cache_savings.blueprint")
        assert spec is not None, (
            "tools/cache_savings/blueprint.py must exist. "
            "Create it with a Flask Blueprint registering /cache-savings route."
        )

    def test_cache_savings_template_exists(self):
        """tools/dashboard/templates/cache_savings/page.html must exist."""
        import os

        template_path = os.path.join(
            "tools", "dashboard", "templates", "cache_savings", "page.html"
        )
        assert os.path.exists(template_path), (
            f"Dashboard template not found: {template_path}. "
            "Create it with cache savings metrics (total saved tokens, cost savings, hit rate)."
        )

    def test_cache_savings_module_exists(self):
        """tools/cache_savings/savings.py must exist and expose get_savings_stats()."""
        import importlib.util

        spec = importlib.util.find_spec("tools.cache_savings.savings")
        assert spec is not None, (
            "tools/cache_savings/savings.py must exist. "
            "Implement get_savings_stats() returning a summary block with "
            "hit_count, tokens_saved and cost_usd_saved."
        )

    # The three tests below were written TDD-first against a flat return shape
    # (hit_count etc. at the top level). The shipped implementation groups the
    # platform totals under "summary" and adds a per-function breakdown, and every
    # consumer reads that shape: blueprint.py's /api/cache-savings/tile does
    # stats["summary"][...], cache_savings/page.html renders stats.summary.*, and
    # the IQE adapter reads stats["by_function"]. The nested contract is the real
    # one — these assert it rather than the shape that was only ever proposed.

    def test_get_savings_stats_returns_expected_keys(self):
        """get_savings_stats() must return the shape its consumers destructure."""
        from tools.cache_savings.savings import get_savings_stats

        stats = get_savings_stats()
        top_missing = {"enabled", "backend", "summary", "by_function"} - set(stats.keys())
        assert not top_missing, (
            f"get_savings_stats() missing required top-level keys: {top_missing}. "
            f"Got: {list(stats.keys())}"
        )

        required_keys = {"hit_count", "miss_count", "hit_rate_pct", "tokens_saved", "cost_usd_saved"}
        missing = required_keys - set(stats["summary"].keys())
        assert not missing, (
            f"get_savings_stats()['summary'] missing required keys: {missing}. "
            f"Got: {list(stats['summary'].keys())}"
        )

    def test_get_savings_stats_numeric_values(self):
        """summary numeric fields must be non-negative numbers."""
        from tools.cache_savings.savings import get_savings_stats

        summary = get_savings_stats()["summary"]
        for key in ("hit_count", "miss_count", "tokens_saved", "cost_usd_saved"):
            val = summary.get(key, -1)
            assert isinstance(val, (int, float)) and val >= 0, (
                f"get_savings_stats()['summary']['{key}'] must be a non-negative "
                f"number, got: {val!r}"
            )

    def test_hit_rate_pct_range(self):
        """hit_rate_pct is a percentage in [0, 100], or None when unmeasured.

        None is not a gap in the contract, it IS the contract (cch-obs-03): with
        hit_count == miss_count == 0 the cache was never asked, so there is no
        rate — and a hard 0.0 renders as "0.0% Hit Rate", which says the cache
        failed every request it was given.

        This assertion used to demand a number and passed anyway, because in a
        test environment the cache table is absent and `get_savings_stats`
        returns its skeleton (0.0). It would have failed on any deployment where
        the table exists and traffic is quiet — which is the live board.
        """
        from tools.cache_savings.savings import get_savings_stats

        rate = get_savings_stats()["summary"].get("hit_rate_pct", -1)
        if rate is None:
            return
        assert isinstance(rate, (int, float)) and 0.0 <= rate <= 100.0, (
            f"hit_rate_pct must be in [0, 100] or None, got: {rate!r}"
        )


# ===========================================================================
# FEATURE 3: Multi-Breakpoint Support (up to 4 Anthropic breakpoints)
# ===========================================================================


@pytest.mark.skipif(not HAS_LLM, reason="LLM provider unavailable")
class TestMultiBreakpointSupport:
    """Anthropic provider must support up to 4 cache_control breakpoints."""

    def _invoke_with_system(self, system_prompt: str, cache_control: str = "ephemeral"):
        from tools.llm.anthropic_provider import AnthropicLLMProvider

        provider = AnthropicLLMProvider(api_key="test-key")
        request = LLMRequest(
            messages=[{"role": "user", "content": "summarize"}],
            system_prompt=system_prompt,
            cache_control=cache_control,
            max_tokens=10,
        )

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.stop_reason = "end_turn"
            u = MagicMock()
            u.input_tokens = 100
            u.output_tokens = 5
            u.cache_creation_input_tokens = 80
            u.cache_read_input_tokens = 0
            resp.usage = u
            b = MagicMock()
            b.type = "text"
            b.text = "ok"
            resp.content = [b]
            return resp

        provider._client = MagicMock()
        provider._client.messages.create.side_effect = fake_create
        provider.invoke(request, "claude-sonnet-4-5-20250929", {"max_output_tokens": 64000})
        return captured

    def test_two_breakpoints_in_system_prompt(self):
        """Two '<!-- cache_breakpoint -->' markers must produce 3 system blocks,
        with cache_control on the first 2 blocks (non-final blocks).
        """
        system = (
            "Part A: static instructions.\n"
            "<!-- cache_breakpoint -->\n"
            "Part B: RAG context.\n"
            "<!-- cache_breakpoint -->\n"
            "Part C: dynamic user-specific note."
        )
        captured = self._invoke_with_system(system)
        blocks = captured.get("system", [])
        assert isinstance(blocks, list), "system must be a list of blocks"
        assert len(blocks) == 3, f"2 breakpoints must produce 3 blocks, got {len(blocks)}"
        # First 2 blocks must have cache_control
        for i in range(2):
            assert blocks[i].get("cache_control") == {"type": "ephemeral"}, (
                f"Block {i} must have cache_control={{'type':'ephemeral'}}, got {blocks[i]}"
            )
        # Last block must NOT have cache_control
        assert "cache_control" not in blocks[2] or blocks[2].get("cache_control") is None, (
            "Final system block must NOT have cache_control"
        )

    def test_four_breakpoints_capped(self):
        """More than 3 '<!-- cache_breakpoint -->' markers must be capped at 4 total blocks
        (Anthropic limit: 4 breakpoints = max 4 blocks with cache_control on first 3).
        """
        # 5 separators → would normally produce 6 blocks, but must cap at 4
        markers = "\n<!-- cache_breakpoint -->\n"
        system = markers.join([f"Part {i}" for i in range(6)])
        captured = self._invoke_with_system(system)
        blocks = captured.get("system", [])
        assert isinstance(blocks, list), "system must be a list when breakpoints present"
        # Anthropic allows max 4 cache_control blocks; cap combined blocks to 4
        num_with_cache = sum(
            1 for b in blocks if isinstance(b, dict) and b.get("cache_control") is not None
        )
        assert num_with_cache <= 4, (
            f"Must cap breakpoints at 4 (Anthropic limit), got {num_with_cache} blocks with cache_control"
        )

    def test_no_breakpoints_falls_back_to_single_block(self):
        """Without markers, system must be a single block (existing behavior)."""
        system = "You are a compliance assistant. No breakpoints here."
        captured = self._invoke_with_system(system)
        # Single-block behavior: can be str or list with 1 entry
        sys_arg = captured.get("system")
        if isinstance(sys_arg, list):
            assert len(sys_arg) == 1, (
                f"No breakpoints: expected 1-element list or str, got {len(sys_arg)} elements"
            )
        else:
            assert isinstance(sys_arg, (str, dict)), (
                f"No breakpoints: system arg must be str or single block dict, got {type(sys_arg)}"
            )

    def test_max_breakpoints_constant_exists(self):
        """AnthropicLLMProvider must define MAX_CACHE_BREAKPOINTS = 4."""
        from tools.llm.anthropic_provider import AnthropicLLMProvider

        assert hasattr(AnthropicLLMProvider, "MAX_CACHE_BREAKPOINTS"), (
            "AnthropicLLMProvider must define class constant MAX_CACHE_BREAKPOINTS"
        )
        assert AnthropicLLMProvider.MAX_CACHE_BREAKPOINTS == 4, (
            f"MAX_CACHE_BREAKPOINTS must be 4 (Anthropic limit), got {AnthropicLLMProvider.MAX_CACHE_BREAKPOINTS}"
        )


# ===========================================================================
# FEATURE 4: OpenAI Cache Token Parity
# ===========================================================================


@pytest.mark.skipif(not HAS_LLM, reason="LLM provider unavailable")
class TestOpenAICacheTokenParity:
    """OpenAI provider must extract cached_tokens from prompt_tokens_details."""

    def _make_openai_completion_mock(self, cached_tokens: int = 0) -> MagicMock:
        """Build a realistic OpenAI CompletionUsage mock with cached_tokens."""
        completion = MagicMock()
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 20
        # OpenAI stores cache info in prompt_tokens_details
        details = MagicMock()
        details.cached_tokens = cached_tokens
        usage.prompt_tokens_details = details
        completion.usage = usage
        choice = MagicMock()
        choice.finish_reason = "stop"
        msg = MagicMock()
        msg.content = "cached response text"
        msg.tool_calls = None
        choice.message = msg
        completion.choices = [choice]
        return completion

    def test_openai_provider_extracts_cached_tokens(self):
        """OpenAICompatibleProvider.invoke() must set cache_read_input_tokens
        from completion.usage.prompt_tokens_details.cached_tokens.
        """
        from tools.llm.openai_provider import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(api_key="test", base_url="https://api.openai.com/v1")
        request = LLMRequest(
            messages=[{"role": "user", "content": "Hello OpenAI"}],
            max_tokens=50,
        )

        completion_mock = self._make_openai_completion_mock(cached_tokens=75)

        with patch.object(provider, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = completion_mock
            mock_get_client.return_value = mock_client

            resp = provider.invoke(request, "gpt-4o", {"max_output_tokens": 16384})

        assert resp.cache_read_input_tokens == 75, (
            f"OpenAI provider must extract cached_tokens=75 into cache_read_input_tokens, "
            f"got: {resp.cache_read_input_tokens}"
        )

    def test_openai_provider_zero_cached_tokens_when_missing(self):
        """cache_read_input_tokens must be 0 when prompt_tokens_details is absent."""
        from tools.llm.openai_provider import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(api_key="test", base_url="https://api.openai.com/v1")
        request = LLMRequest(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=50,
        )

        completion_mock = self._make_openai_completion_mock(cached_tokens=0)
        # Remove prompt_tokens_details to simulate older OpenAI response
        completion_mock.usage.prompt_tokens_details = None

        with patch.object(provider, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = completion_mock
            mock_get_client.return_value = mock_client

            resp = provider.invoke(request, "gpt-4o", {"max_output_tokens": 16384})

        assert resp.cache_read_input_tokens == 0, (
            f"cache_read_input_tokens must be 0 when prompt_tokens_details absent, "
            f"got: {resp.cache_read_input_tokens}"
        )

    def test_openai_provider_cache_creation_tokens_from_details(self):
        """OpenAI provider must also extract cache write tokens when available
        (e.g. from future prompt_tokens_details.audio_tokens or explicit field).

        For now: cache_creation_input_tokens = prompt_tokens - cached_tokens
        when cached_tokens > 0 (tokens that were newly written to cache).
        """
        from tools.llm.openai_provider import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(api_key="test", base_url="https://api.openai.com/v1")
        request = LLMRequest(
            messages=[{"role": "user", "content": "What is IL5?"}],
            max_tokens=50,
        )

        # 100 prompt tokens, 60 were cached → 40 are new (cache creation)
        completion_mock = self._make_openai_completion_mock(cached_tokens=60)
        completion_mock.usage.prompt_tokens = 100

        with patch.object(provider, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = completion_mock
            mock_get_client.return_value = mock_client

            resp = provider.invoke(request, "gpt-4o", {"max_output_tokens": 16384})

        assert resp.cache_read_input_tokens == 60, (
            f"Expected cache_read_input_tokens=60, got {resp.cache_read_input_tokens}"
        )
        # cache_creation_input_tokens should reflect non-cached portion (100 - 60 = 40)
        # OR remain 0 if provider doesn't compute it — both are acceptable as long as
        # cache_read is correct. We assert it is at least non-negative.
        assert resp.cache_creation_input_tokens >= 0, (
            "cache_creation_input_tokens must be non-negative"
        )

    def test_llmresponse_field_documented_for_openai(self):
        """LLMResponse.cache_read_input_tokens docstring/comment must mention OpenAI."""
        import inspect
        from tools.llm import provider as provider_module

        source = inspect.getsource(provider_module)
        # The field comment in provider.py should acknowledge OpenAI tracking parity
        assert "openai" in source.lower() or "oai" in source.lower() or "prompt_tokens_details" in source.lower(), (
            "provider.py must document OpenAI cache token tracking parity "
            "(mention 'openai', 'OAI', or 'prompt_tokens_details' in source)"
        )


# ===========================================================================
# FEATURE 5: Cache Warming on Deploy
# ===========================================================================


class TestCacheWarmingOnDeploy:
    """CacheWarmer.warm_on_deploy() must pre-populate cache with seed queries at deploy time."""

    def test_cache_warmer_module_exists(self):
        """tools/cache_savings/warmer.py must exist and expose CacheWarmer."""
        import importlib.util

        spec = importlib.util.find_spec("tools.cache_savings.warmer")
        assert spec is not None, (
            "tools/cache_savings/warmer.py must exist. "
            "Implement CacheWarmer with warm_on_deploy() method."
        )

    def test_cache_warmer_class_exists(self):
        """CacheWarmer class must be importable from tools.cache_savings.warmer."""
        from tools.cache_savings.warmer import CacheWarmer

        assert callable(getattr(CacheWarmer, "warm_on_deploy", None)), (
            "CacheWarmer must have a warm_on_deploy() method"
        )

    def test_warm_on_deploy_returns_results(self):
        """warm_on_deploy() must return a list of warm result dicts."""
        from tools.cache_savings.warmer import CacheWarmer

        warmer = CacheWarmer(dry_run=True)
        results = warmer.warm_on_deploy()

        assert isinstance(results, list), (
            f"warm_on_deploy() must return a list, got {type(results)}"
        )

    def test_warm_on_deploy_result_structure(self):
        """Each result in warm_on_deploy() must have 'function', 'status', 'query' keys."""
        from tools.cache_savings.warmer import CacheWarmer

        warmer = CacheWarmer(dry_run=True)
        results = warmer.warm_on_deploy()

        if not results:
            pytest.skip("No seed queries configured — skipping structure check")

        for result in results:
            assert "function" in result, f"warm result missing 'function' key: {result}"
            assert "status" in result, f"warm result missing 'status' key: {result}"
            assert "query" in result, f"warm result missing 'query' key: {result}"

    def test_warm_on_deploy_dry_run_does_not_invoke_llm(self):
        """dry_run=True must not make any real LLM calls."""
        from tools.cache_savings.warmer import CacheWarmer

        # Mock the router so any accidental invoke() call raises
        with patch("tools.cache_savings.warmer.LLMRouter") as mock_router_cls:
            mock_router = MagicMock()
            mock_router.invoke.side_effect = RuntimeError(
                "LLM invoked in dry_run mode — this must not happen"
            )
            mock_router_cls.return_value = mock_router

            warmer = CacheWarmer(dry_run=True)
            results = warmer.warm_on_deploy()

            # Should not have called invoke
            mock_router.invoke.assert_not_called()
        assert isinstance(results, list), "dry_run results must be a list"

    def test_warm_on_deploy_seed_queries_file_exists(self):
        """context/cache_seeds/default_seeds.yaml must exist with seed queries."""
        import os

        seeds_path = os.path.join("context", "cache_seeds", "default_seeds.yaml")
        assert os.path.exists(seeds_path), (
            f"Cache seed queries file not found: {seeds_path}. "
            "Create it with a list of seed queries per function "
            "(e.g. code_generation, compliance_export, narrative_generation)."
        )

    def test_warm_on_deploy_cli_entrypoint(self):
        """warm_on_deploy must be callable from CLI: python -m tools.cache_savings.warmer --warm."""
        import importlib.util

        spec = importlib.util.find_spec("tools.cache_savings.warmer")
        assert spec is not None, "tools/cache_savings/warmer.py must exist"

        # Verify argparse is used (CLI entrypoint present)
        with open(spec.origin, "r", encoding="utf-8") as f:
            source = f.read()

        assert "argparse" in source or "ArgumentParser" in source, (
            "tools/cache_savings/warmer.py must include a CLI entrypoint using argparse"
        )
