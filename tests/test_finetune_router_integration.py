#!/usr/bin/env python3
# CUI // SP-CTI
"""Fine-tuning router integration tests (D-FT-6, Phase 4).

Tests that fine-tuned models properly override the default qwen3 tier1
in the two-tier LLM routing pipeline.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared DB fixture — creates ft_active_models + ft_model_versions tables
# ---------------------------------------------------------------------------

FT_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS ft_active_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    function_name TEXT NOT NULL,
    model_version_id TEXT NOT NULL,
    ollama_model_name TEXT NOT NULL,
    routing_tier TEXT DEFAULT 'worker',
    activated_by TEXT DEFAULT '',
    activation_reason TEXT DEFAULT '',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deactivated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ft_model_versions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    base_model TEXT NOT NULL,
    adapter_path TEXT DEFAULT '',
    gguf_path TEXT DEFAULT '',
    ollama_model_name TEXT DEFAULT '',
    adapter_hash TEXT DEFAULT '',
    file_size_bytes INTEGER DEFAULT 0,
    eval_bleu REAL DEFAULT 0.0,
    eval_rouge_l REAL DEFAULT 0.0,
    eval_perplexity REAL DEFAULT 0.0,
    eval_custom TEXT DEFAULT '{}',
    status TEXT DEFAULT 'created',
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def ft_db(tmp_path):
    """Create an in-memory style DB with ft_active_models table."""
    db_path = tmp_path / "data" / "icdev.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(FT_TABLES_SQL)
    conn.commit()
    conn.close()
    return db_path


def _seed_active_model(
    db_path,
    function_name,
    ollama_model_name,
    model_version_id="mv-test-001",
    tenant_id="",
    project_id="",
    deactivated=False,
):
    """Insert an active model override into ft_active_models."""
    conn = sqlite3.connect(str(db_path))
    deact = "2025-01-01T00:00:00Z" if deactivated else None
    conn.execute(
        """INSERT INTO ft_active_models
           (function_name, model_version_id, ollama_model_name,
            routing_tier, tenant_id, project_id, deactivated_at)
           VALUES (?, ?, ?, 'worker', ?, ?, ?)""",
        (function_name, model_version_id, ollama_model_name, tenant_id, project_id, deact),
    )
    conn.commit()
    conn.close()


# ===========================================================================
# Test _check_finetuned_override
# ===========================================================================


class TestCheckFinetunedOverride:
    """Tests for LLMRouter._check_finetuned_override (D-FT-6)."""

    def test_no_db(self, tmp_path):
        """Returns None when icdev.db doesn't exist."""
        with patch("tools.llm.router.BASE_DIR", tmp_path):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            result = router._check_finetuned_override("code_generation")
            assert result is None

    def test_no_active_model(self, ft_db, tmp_path):
        """Returns None when no model is active for function."""
        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            result = router._check_finetuned_override("code_generation")
            assert result is None

    def test_active_model_found(self, ft_db, tmp_path):
        """Returns ollama_model_name when active model exists."""
        _seed_active_model(ft_db, "code_generation", "govproposal-draft-v3")

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            result = router._check_finetuned_override("code_generation")
            assert result == "govproposal-draft-v3"

    def test_deactivated_model_ignored(self, ft_db):
        """Deactivated models are not returned."""
        _seed_active_model(ft_db, "code_generation", "old-model-v1", deactivated=True)

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            result = router._check_finetuned_override("code_generation")
            assert result is None

    def test_tenant_scoped_override(self, ft_db):
        """Tenant-scoped overrides are returned when tenant matches."""
        _seed_active_model(ft_db, "compliance_export", "acme-compliance-v2", tenant_id="tenant-acme")

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            # Matching tenant
            result = router._check_finetuned_override(
                "compliance_export",
                tenant_id="tenant-acme",
            )
            assert result == "acme-compliance-v2"

    def test_wrong_function_not_returned(self, ft_db):
        """Override for different function is not returned."""
        _seed_active_model(ft_db, "code_generation", "code-model-v1")

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            result = router._check_finetuned_override("compliance_export")
            assert result is None

    def test_most_recent_activation_wins(self, ft_db):
        """When multiple active models exist, most recent wins."""
        _seed_active_model(ft_db, "code_generation", "model-v1", model_version_id="mv-001")
        _seed_active_model(ft_db, "code_generation", "model-v2", model_version_id="mv-002")

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            result = router._check_finetuned_override("code_generation")
            assert result == "model-v2"

    def test_global_override_matches_any_tenant(self, ft_db):
        """A global override (empty tenant_id) matches any tenant query."""
        _seed_active_model(ft_db, "code_generation", "global-model-v1", tenant_id="")

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0
            result = router._check_finetuned_override(
                "code_generation",
                tenant_id="any-tenant",
            )
            assert result == "global-model-v1"


# ===========================================================================
# Test _invoke_finetuned_model
# ===========================================================================


class TestInvokeFinetunedModel:
    """Tests for LLMRouter._invoke_finetuned_model."""

    def _make_router(self):
        """Create a router without __init__ side effects."""
        from tools.llm.router import LLMRouter

        router = LLMRouter.__new__(LLMRouter)
        router._config = {
            "providers": {
                "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
            },
        }
        router._providers = {}
        router._embedding_providers = {}
        router._availability_cache = {}
        router._availability_cache_time = 0.0
        router._cache_ttl = 1800.0
        return router

    def test_invokes_ollama_provider(self):
        """Successfully invokes Ollama provider with fine-tuned model name."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router()
        mock_provider = MagicMock()
        mock_response = LLMResponse(content="fine-tuned output")
        mock_provider.invoke.return_value = mock_response
        router._providers["ollama"] = mock_provider

        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
        )
        result = router._invoke_finetuned_model("my-ft-model:v3", request)

        assert result is not None
        assert result.content == "fine-tuned output"
        mock_provider.invoke.assert_called_once()
        call_args = mock_provider.invoke.call_args
        assert call_args[0][1] == "my-ft-model:v3"

    def test_returns_none_when_no_ollama_provider(self):
        """Returns None when no Ollama provider is available."""
        from tools.llm.provider import LLMRequest

        router = self._make_router()
        router._config = {"providers": {}}  # No providers

        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
        )
        result = router._invoke_finetuned_model("my-ft-model:v3", request)
        assert result is None

    def test_returns_none_on_invoke_error(self):
        """Returns None when Ollama provider raises an error."""
        from tools.llm.provider import LLMRequest

        router = self._make_router()
        mock_provider = MagicMock()
        mock_provider.invoke.side_effect = RuntimeError("connection refused")
        router._providers["ollama"] = mock_provider

        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
        )
        result = router._invoke_finetuned_model("my-ft-model:v3", request)
        assert result is None

    def test_discovers_ollama_provider_by_type(self):
        """Finds Ollama provider even if named differently (e.g. 'local-ollama')."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router()
        router._config = {
            "providers": {
                "local-ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
            },
        }
        mock_provider = MagicMock()
        mock_response = LLMResponse(content="found via type")
        mock_provider.invoke.return_value = mock_response

        # _get_provider will be called — mock it
        with patch.object(router, "_get_provider") as mock_get:
            # First call for "ollama" returns None (not in config)
            # Second call for "local-ollama" returns the provider
            mock_get.side_effect = lambda name: mock_provider if name == "local-ollama" else None

            request = LLMRequest(messages=[{"role": "user", "content": "test"}])
            result = router._invoke_finetuned_model("my-ft-model:v3", request)
            assert result is not None
            assert result.content == "found via type"


# ===========================================================================
# Test two-tier integration with fine-tuned override
# ===========================================================================


class TestTwoTierFineTunedIntegration:
    """Tests that fine-tuned models integrate into two-tier routing."""

    def _make_router_with_two_tier(self):
        """Create a router with two-tier config enabled."""
        from tools.llm.router import LLMRouter

        router = LLMRouter.__new__(LLMRouter)
        router._config = {
            "providers": {
                "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
                "bedrock": {"type": "bedrock", "region": "us-gov-west-1"},
            },
            "models": {
                "qwen3-local": {"provider": "ollama", "model_id": "qwen3:latest"},
                "claude-sonnet": {"provider": "bedrock", "model_id": "claude-sonnet-4-20250514"},
            },
            "two_tier": {
                "enabled": True,
                "tier1_model": "qwen3-local",
                "tier2_model": "claude-sonnet",
                "planner_functions": ["agent_architect"],
                "worker_functions": ["code_generation", "compliance_export"],
                "scanner_functions": ["agent_monitor"],
            },
        }
        router._providers = {}
        router._embedding_providers = {}
        router._availability_cache = {}
        router._availability_cache_time = 0.0
        router._cache_ttl = 1800.0
        return router

    @patch("tools.llm.router.LLMRouter._rag_augment")
    @patch("tools.llm.router.LLMRouter._invoke_finetuned_model")
    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    @patch("tools.llm.router.LLMRouter._check_finetuned_override")
    def test_ft_override_used_for_worker(
        self,
        mock_ft_check,
        mock_direct,
        mock_ft_invoke,
        mock_rag,
    ):
        """When fine-tuned model exists, it replaces qwen3 for drafting."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router_with_two_tier()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Generate code"}],
        )

        # Fine-tuned model found
        mock_ft_check.return_value = "govproposal-draft-v3"
        mock_rag.return_value = request  # RAG pass-through

        # Fine-tuned draft
        ft_draft = LLMResponse(content="fine-tuned draft output")
        mock_ft_invoke.return_value = ft_draft

        # Claude review
        review = LLMResponse(content="reviewed and improved output")
        mock_direct.return_value = review

        result = router._maybe_invoke_two_tier("code_generation", request)

        assert result is not None
        assert result.content == "reviewed and improved output"
        assert getattr(result, "ft_model_used", None) == "govproposal-draft-v3"

        # Fine-tuned model was used, NOT _invoke_model_direct for tier1
        mock_ft_invoke.assert_called_once()
        # Claude review was still called via _invoke_model_direct
        mock_direct.assert_called_once()

    @patch("tools.llm.router.LLMRouter._rag_augment")
    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    @patch("tools.llm.router.LLMRouter._check_finetuned_override")
    def test_no_ft_override_uses_default_qwen3(
        self,
        mock_ft_check,
        mock_direct,
        mock_rag,
    ):
        """Without fine-tuned override, default qwen3 tier1 is used."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router_with_two_tier()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Generate code"}],
        )

        # No fine-tuned model
        mock_ft_check.return_value = None
        mock_rag.return_value = request

        # qwen3 draft + Claude review
        draft = LLMResponse(content="qwen3 draft")
        review = LLMResponse(content="claude review")
        mock_direct.side_effect = [draft, review]

        result = router._maybe_invoke_two_tier("code_generation", request)

        assert result is not None
        assert result.content == "claude review"
        assert getattr(result, "ft_model_used", None) is None
        # Both calls go through _invoke_model_direct (qwen3 + claude)
        assert mock_direct.call_count == 2

    @patch("tools.llm.router.LLMRouter._rag_augment")
    @patch("tools.llm.router.LLMRouter._invoke_finetuned_model")
    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    @patch("tools.llm.router.LLMRouter._check_finetuned_override")
    def test_ft_draft_returned_when_claude_unavailable(
        self,
        mock_ft_check,
        mock_direct,
        mock_ft_invoke,
        mock_rag,
    ):
        """When Claude is unavailable, fine-tuned draft is returned as fallback."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router_with_two_tier()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Generate code"}],
        )

        mock_ft_check.return_value = "ft-model-v1"
        mock_rag.return_value = request

        ft_draft = LLMResponse(content="fine-tuned draft")
        mock_ft_invoke.return_value = ft_draft

        # Claude review fails
        mock_direct.return_value = None

        result = router._maybe_invoke_two_tier("code_generation", request)
        assert result is not None
        assert result.content == "fine-tuned draft"

    @patch("tools.llm.router.LLMRouter._rag_augment")
    @patch("tools.llm.router.LLMRouter._invoke_finetuned_model")
    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    @patch("tools.llm.router.LLMRouter._check_finetuned_override")
    def test_ft_model_unavailable_falls_through(
        self,
        mock_ft_check,
        mock_direct,
        mock_ft_invoke,
        mock_rag,
    ):
        """When fine-tuned model fails, falls through to chain routing."""
        from tools.llm.provider import LLMRequest

        router = self._make_router_with_two_tier()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Generate code"}],
        )

        mock_ft_check.return_value = "ft-model-v1"
        mock_rag.return_value = request

        # Fine-tuned model fails
        mock_ft_invoke.return_value = None

        result = router._maybe_invoke_two_tier("code_generation", request)
        # Should return None (fall through to chain)
        assert result is None

    @patch("tools.llm.router.LLMRouter._rag_augment")
    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    @patch("tools.llm.router.LLMRouter._check_finetuned_override")
    def test_planner_functions_not_affected(
        self,
        mock_ft_check,
        mock_direct,
        mock_rag,
    ):
        """Fine-tuned override does NOT affect planner functions."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router_with_two_tier()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Plan architecture"}],
        )

        # Even if override exists, planner goes directly to Claude
        mock_ft_check.return_value = None  # Not checked for planners
        review = LLMResponse(content="claude direct plan")
        mock_direct.return_value = review

        result = router._maybe_invoke_two_tier("agent_architect", request)
        assert result is not None
        assert result.content == "claude direct plan"
        # Fine-tuned override should NOT be checked for planner functions
        mock_ft_check.assert_not_called()

    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    @patch("tools.llm.router.LLMRouter._check_finetuned_override")
    def test_scanner_functions_not_affected(
        self,
        mock_ft_check,
        mock_direct,
    ):
        """Fine-tuned override does NOT affect scanner functions."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router_with_two_tier()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Scan for issues"}],
        )

        scan_result = LLMResponse(content="scan output")
        mock_direct.return_value = scan_result

        result = router._maybe_invoke_two_tier("agent_monitor", request)
        assert result is not None
        assert result.content == "scan output"
        mock_ft_check.assert_not_called()

    @patch("tools.llm.router.LLMRouter._rag_augment")
    @patch("tools.llm.router.LLMRouter._invoke_finetuned_model")
    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    @patch("tools.llm.router.LLMRouter._check_finetuned_override")
    def test_ft_override_passes_tenant_project(
        self,
        mock_ft_check,
        mock_direct,
        mock_ft_invoke,
        mock_rag,
    ):
        """Fine-tuned check receives tenant_id and project_id from request."""
        from tools.llm.provider import LLMRequest, LLMResponse

        router = self._make_router_with_two_tier()
        request = LLMRequest(
            messages=[{"role": "user", "content": "Generate"}],
        )
        # Simulate tenant/project on request
        request.tenant_id = "tenant-acme"
        request.project_id = "proj-123"

        mock_ft_check.return_value = None
        mock_rag.return_value = request
        mock_direct.side_effect = [
            LLMResponse(content="draft"),
            LLMResponse(content="review"),
        ]

        router._maybe_invoke_two_tier("code_generation", request)

        # Verify tenant/project passed to override check
        mock_ft_check.assert_called_once_with(
            "code_generation",
            tenant_id="tenant-acme",
            project_id="proj-123",
        )

    def test_two_tier_disabled_skips_ft_check(self, monkeypatch):
        """When two-tier is disabled, no fine-tuned check occurs."""
        from tools.llm.provider import LLMRequest

        # LLM_TWO_TIER_ENABLED overrides the config in BOTH directions, and the
        # kanban dispatch environment exports it as "true". Without this delenv
        # the config toggle under test is simply not the thing deciding, and the
        # assertion below fails on a machine that happens to have it set.
        monkeypatch.delenv("LLM_TWO_TIER_ENABLED", raising=False)

        router = self._make_router_with_two_tier()
        router._config["two_tier"]["enabled"] = False

        request = LLMRequest(
            messages=[{"role": "user", "content": "Generate"}],
        )

        with patch.object(router, "_check_finetuned_override") as mock_ft:
            result = router._maybe_invoke_two_tier("code_generation", request)
            assert result is None
            mock_ft.assert_not_called()

    def test_env_var_overrides_disabled_config(self, monkeypatch):
        """LLM_TWO_TIER_ENABLED=true re-enables two-tier over a disabled config.

        The other half of the override contract — asserted so the env var can
        never again be the silent reason a "disabled" test result flips.
        """
        from tools.llm.provider import LLMRequest

        monkeypatch.setenv("LLM_TWO_TIER_ENABLED", "true")

        router = self._make_router_with_two_tier()
        router._config["two_tier"]["enabled"] = False

        request = LLMRequest(messages=[{"role": "user", "content": "Generate"}])

        with patch.object(router, "_check_finetuned_override") as mock_ft:
            mock_ft.return_value = None
            with patch.object(router, "_rag_augment", return_value=request):
                with patch.object(router, "_invoke_model_direct", return_value=None):
                    router._maybe_invoke_two_tier("code_generation", request)
            mock_ft.assert_called_once()

    def test_env_var_false_disables_enabled_config(self, monkeypatch):
        """LLM_TWO_TIER_ENABLED=false wins over an enabled config."""
        from tools.llm.provider import LLMRequest

        monkeypatch.setenv("LLM_TWO_TIER_ENABLED", "false")

        router = self._make_router_with_two_tier()  # config has enabled: True
        request = LLMRequest(messages=[{"role": "user", "content": "Generate"}])

        with patch.object(router, "_check_finetuned_override") as mock_ft:
            assert router._maybe_invoke_two_tier("code_generation", request) is None
            mock_ft.assert_not_called()


# ===========================================================================
# Test end-to-end DB → router integration
# ===========================================================================


class TestEndToEndDBIntegration:
    """Tests the full DB → _check_finetuned_override → routing path."""

    @patch("tools.llm.router.LLMRouter._rag_augment")
    @patch("tools.llm.router.LLMRouter._invoke_model_direct")
    def test_full_db_to_routing(self, mock_direct, mock_rag, ft_db):
        """Active model in DB routes through fine-tuned model in two-tier."""
        from tools.llm.provider import LLMRequest, LLMResponse

        _seed_active_model(ft_db, "code_generation", "custom-coder-v3")

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {
                "providers": {
                    "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
                },
                "models": {
                    "qwen3-local": {"provider": "ollama", "model_id": "qwen3:latest"},
                    "claude-sonnet": {"provider": "bedrock", "model_id": "claude-sonnet-4-20250514"},
                },
                "two_tier": {
                    "enabled": True,
                    "tier1_model": "qwen3-local",
                    "tier2_model": "claude-sonnet",
                    "planner_functions": [],
                    "worker_functions": ["code_generation"],
                    "scanner_functions": [],
                },
            }
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0

            request = LLMRequest(
                messages=[{"role": "user", "content": "Generate code"}],
            )
            mock_rag.return_value = request

            # Mock the Ollama provider that will handle the fine-tuned model
            mock_ollama = MagicMock()
            ft_draft = LLMResponse(content="fine-tuned draft from custom-coder-v3")
            mock_ollama.invoke.return_value = ft_draft
            router._providers["ollama"] = mock_ollama

            # Claude review
            claude_review = LLMResponse(content="reviewed output")
            mock_direct.return_value = claude_review

            result = router._maybe_invoke_two_tier("code_generation", request)

            assert result is not None
            assert result.content == "reviewed output"
            assert getattr(result, "ft_model_used", None) == "custom-coder-v3"

            # Verify Ollama was called with the custom model name
            mock_ollama.invoke.assert_called_once()
            call_args = mock_ollama.invoke.call_args
            assert call_args[0][1] == "custom-coder-v3"

    def test_empty_db_no_override(self, ft_db):
        """Empty ft_active_models table → no override → default routing."""
        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0

            result = router._check_finetuned_override("code_generation")
            assert result is None

    def test_deactivated_model_not_used(self, ft_db):
        """Deactivated model in DB is not used for routing."""
        _seed_active_model(ft_db, "code_generation", "old-model-v1", deactivated=True)

        with patch("tools.llm.router.BASE_DIR", ft_db.parent.parent):
            from tools.llm.router import LLMRouter

            router = LLMRouter.__new__(LLMRouter)
            router._config = {}
            router._providers = {}
            router._embedding_providers = {}
            router._availability_cache = {}
            router._availability_cache_time = 0.0
            router._cache_ttl = 1800.0

            result = router._check_finetuned_override("code_generation")
            assert result is None
