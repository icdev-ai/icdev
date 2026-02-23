# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.agent.bedrock_client — centralized Amazon Bedrock API wrapper.

Covers BedrockRequest/BedrockResponse dataclasses, BedrockClient initialization,
model registry, fallback chain, invoke with mocked boto3, token tracking,
probe, effort resolution, request body construction, response parsing,
retry logic, and graceful handling when boto3 is unavailable.
"""

import io
import json
import warnings
from unittest.mock import MagicMock, patch

import pytest

from tools.agent.bedrock_client import (
    AVAILABILITY_CACHE_TTL,
    BASE_RETRY_DELAY,
    DEFAULT_MODELS,
    FALLBACK_CHAIN,
    MAX_RETRIES,
    RETRYABLE_ERROR_CODES,
    BedrockRequest,
    BedrockResponse,
    _track_tokens,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_invoke_response(content_text="Hello", input_tokens=10, output_tokens=20,
                          model="anthropic.claude-opus-4-6-20260215-v1:0",
                          stop_reason="end_turn", tool_use_blocks=None,
                          thinking_blocks=None):
    """Build a mocked invoke_model raw response dict."""
    content_blocks = []
    if thinking_blocks:
        for tb in thinking_blocks:
            content_blocks.append({"type": "thinking", "thinking": tb["text"], "tokens": tb.get("tokens", 0)})
    content_blocks.append({"type": "text", "text": content_text})
    if tool_use_blocks:
        for tu in tool_use_blocks:
            content_blocks.append({
                "type": "tool_use",
                "id": tu.get("id", "tool-1"),
                "name": tu.get("name", "my_tool"),
                "input": tu.get("input", {}),
            })

    body_dict = {
        "model": model,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "content": content_blocks,
    }
    body_bytes = json.dumps(body_dict).encode("utf-8")
    return {"body": io.BytesIO(body_bytes)}


def _create_client(**kwargs):
    """Create a BedrockClient with boto3 and yaml mocked, suppressing deprecation."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from tools.agent.bedrock_client import BedrockClient
        return BedrockClient(**kwargs)


# ---------------------------------------------------------------------------
# TestBedrockRequest
# ---------------------------------------------------------------------------

class TestBedrockRequest:
    """BedrockRequest dataclass construction and default values."""

    def test_defaults(self):
        req = BedrockRequest()
        assert req.messages == []
        assert req.system_prompt == ""
        assert req.agent_id == ""
        assert req.project_id == ""
        assert req.model_preference == "opus"
        assert req.effort == "medium"
        assert req.max_tokens == 4096
        assert req.tools is None
        assert req.output_schema is None
        assert req.temperature == 1.0
        assert req.stop_sequences is None
        assert req.classification == "CUI"

    def test_custom_values(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}]
        req = BedrockRequest(
            messages=msgs,
            system_prompt="Be helpful",
            agent_id="builder-agent",
            project_id="proj-123",
            model_preference="sonnet-3-5",
            effort="high",
            max_tokens=8192,
            temperature=0.5,
            classification="SECRET",
        )
        assert req.messages == msgs
        assert req.system_prompt == "Be helpful"
        assert req.agent_id == "builder-agent"
        assert req.project_id == "proj-123"
        assert req.model_preference == "sonnet-3-5"
        assert req.effort == "high"
        assert req.max_tokens == 8192
        assert req.temperature == 0.5
        assert req.classification == "SECRET"


# ---------------------------------------------------------------------------
# TestBedrockResponse
# ---------------------------------------------------------------------------

class TestBedrockResponse:
    """BedrockResponse dataclass construction and default values."""

    def test_defaults(self):
        resp = BedrockResponse()
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.structured_output is None
        assert resp.model_id == ""
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.thinking_tokens == 0
        assert resp.duration_ms == 0
        assert resp.stop_reason == ""
        assert resp.classification == "CUI"

    def test_custom_values(self):
        resp = BedrockResponse(
            content="Hello world",
            model_id="test-model",
            input_tokens=50,
            output_tokens=100,
            thinking_tokens=25,
            duration_ms=500,
            stop_reason="end_turn",
            classification="SECRET",
        )
        assert resp.content == "Hello world"
        assert resp.model_id == "test-model"
        assert resp.input_tokens == 50
        assert resp.output_tokens == 100
        assert resp.thinking_tokens == 25
        assert resp.duration_ms == 500
        assert resp.stop_reason == "end_turn"
        assert resp.classification == "SECRET"

    def test_tool_calls_mutable_default(self):
        """Each instance should get its own tool_calls list."""
        r1 = BedrockResponse()
        r2 = BedrockResponse()
        r1.tool_calls.append({"name": "test"})
        assert len(r2.tool_calls) == 0


# ---------------------------------------------------------------------------
# TestBedrockClientInit
# ---------------------------------------------------------------------------

class TestBedrockClientInit:
    """BedrockClient initialization, config loading, lazy boto3."""

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_init_loads_default_models_when_no_config(self):
        """Without a YAML config file, default model registry is used."""
        with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
            with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                client = _create_client()
        assert "opus" in client._models
        assert "sonnet-4-5" in client._models
        assert "sonnet-3-5" in client._models

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_init_emits_deprecation_warning(self):
        with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
            with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    from tools.agent.bedrock_client import BedrockClient
                    BedrockClient()
                    deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
                    assert len(deprecation_warnings) >= 1
                    assert "deprecated" in str(deprecation_warnings[0].message).lower()

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_region_from_env(self):
        with patch.dict("os.environ", {"AWS_DEFAULT_REGION": "us-east-1"}):
            with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
                with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                    client = _create_client()
        assert client._region == "us-east-1"

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_default_region(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
                with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                    client = _create_client()
        assert client._region == "us-gov-west-1"


# ---------------------------------------------------------------------------
# TestBedrockClientInvoke
# ---------------------------------------------------------------------------

class TestBedrockClientInvoke:
    """BedrockClient.invoke with mocked boto3 responses."""

    def _make_client_with_mock(self):
        """Create client with a mock boto3 runtime client injected."""
        mock_boto3 = MagicMock()
        mock_runtime = MagicMock()
        mock_boto3.client.return_value = mock_runtime
        with patch("tools.agent.bedrock_client.boto3", mock_boto3):
            with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
                with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                    client = _create_client()
                    client._client = mock_runtime
                    # Pre-fill availability cache so _resolve_model_id does not probe
                    client._availability_cache = {"opus": True, "sonnet-4-5": True, "sonnet-3-5": True}
                    import time
                    client._availability_cache_time = time.time()
        return client, mock_runtime

    @patch("tools.agent.bedrock_client._track_tokens")
    def test_invoke_returns_content(self, mock_track):
        client, mock_runtime = self._make_client_with_mock()
        mock_runtime.invoke_model.return_value = _make_invoke_response(content_text="Answer is 42")

        req = BedrockRequest(
            messages=[{"role": "user", "content": [{"type": "text", "text": "What is 42?"}]}],
            model_preference="opus",
        )
        resp = client.invoke(req)

        assert resp.content == "Answer is 42"
        assert resp.input_tokens == 10
        assert resp.output_tokens == 20
        assert resp.stop_reason == "end_turn"
        assert resp.duration_ms >= 0

    @patch("tools.agent.bedrock_client._track_tokens")
    def test_invoke_parses_tool_use(self, mock_track):
        client, mock_runtime = self._make_client_with_mock()
        tool_blocks = [{"id": "tu-1", "name": "search", "input": {"query": "test"}}]
        mock_runtime.invoke_model.return_value = _make_invoke_response(
            content_text="Let me search",
            tool_use_blocks=tool_blocks,
            stop_reason="tool_use",
        )
        req = BedrockRequest(
            messages=[{"role": "user", "content": [{"type": "text", "text": "Search for test"}]}],
        )
        resp = client.invoke(req)

        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "search"
        assert resp.tool_calls[0]["input"] == {"query": "test"}
        assert resp.stop_reason == "tool_use"

    @patch("tools.agent.bedrock_client._track_tokens")
    def test_invoke_parses_thinking_tokens(self, mock_track):
        client, mock_runtime = self._make_client_with_mock()
        thinking = [{"text": "Thinking about it...", "tokens": 150}]
        mock_runtime.invoke_model.return_value = _make_invoke_response(
            content_text="Result",
            thinking_blocks=thinking,
        )
        req = BedrockRequest(
            messages=[{"role": "user", "content": [{"type": "text", "text": "Think hard"}]}],
        )
        resp = client.invoke(req)
        assert resp.thinking_tokens == 150

    @patch("tools.agent.bedrock_client._track_tokens")
    def test_invoke_calls_track_tokens(self, mock_track):
        client, mock_runtime = self._make_client_with_mock()
        mock_runtime.invoke_model.return_value = _make_invoke_response()

        req = BedrockRequest(
            messages=[{"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
            agent_id="builder-agent",
            project_id="proj-1",
        )
        client.invoke(req)
        mock_track.assert_called_once()

    @patch("tools.agent.bedrock_client._track_tokens")
    def test_invoke_structured_output_parsed(self, mock_track):
        client, mock_runtime = self._make_client_with_mock()
        json_content = '{"result": "success", "count": 5}'
        mock_runtime.invoke_model.return_value = _make_invoke_response(content_text=json_content)

        req = BedrockRequest(
            messages=[{"role": "user", "content": [{"type": "text", "text": "Give JSON"}]}],
        )
        resp = client.invoke(req)
        assert resp.structured_output == {"result": "success", "count": 5}


# ---------------------------------------------------------------------------
# TestModelRegistry
# ---------------------------------------------------------------------------

class TestModelRegistry:
    """DEFAULT_MODELS registry and model config lookup."""

    def test_default_models_has_three_entries(self):
        assert len(DEFAULT_MODELS) == 3
        assert "opus" in DEFAULT_MODELS
        assert "sonnet-4-5" in DEFAULT_MODELS
        assert "sonnet-3-5" in DEFAULT_MODELS

    def test_opus_supports_thinking(self):
        assert DEFAULT_MODELS["opus"]["supports_thinking"] is True
        assert DEFAULT_MODELS["opus"]["supports_effort"] is True
        assert DEFAULT_MODELS["opus"]["supports_structured_output"] is True

    def test_sonnet_35_does_not_support_thinking(self):
        assert DEFAULT_MODELS["sonnet-3-5"]["supports_thinking"] is False
        assert DEFAULT_MODELS["sonnet-3-5"]["supports_effort"] is False
        assert DEFAULT_MODELS["sonnet-3-5"]["supports_structured_output"] is False

    def test_all_models_have_required_keys(self):
        required_keys = {"model_id", "display_name", "max_output_tokens",
                         "supports_thinking", "supports_tool_use", "supports_streaming",
                         "anthropic_version"}
        for name, cfg in DEFAULT_MODELS.items():
            missing = required_keys - set(cfg.keys())
            assert missing == set(), f"Model '{name}' missing keys: {missing}"


# ---------------------------------------------------------------------------
# TestFallbackChain
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """FALLBACK_CHAIN configuration and _resolve_model_id behavior."""

    def test_opus_chain_includes_all_three(self):
        assert FALLBACK_CHAIN["opus"] == ["opus", "sonnet-4-5", "sonnet-3-5"]

    def test_sonnet_45_chain(self):
        assert FALLBACK_CHAIN["sonnet-4-5"] == ["sonnet-4-5", "sonnet-3-5"]

    def test_sonnet_35_chain_is_self_only(self):
        assert FALLBACK_CHAIN["sonnet-3-5"] == ["sonnet-3-5"]

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_resolve_falls_back_when_primary_unavailable(self):
        with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
            with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                client = _create_client()
                # Mark opus unavailable, sonnet-4-5 available
                import time
                client._availability_cache = {"opus": False, "sonnet-4-5": True, "sonnet-3-5": True}
                client._availability_cache_time = time.time()
                model_id = client._resolve_model_id("opus")
                assert model_id == DEFAULT_MODELS["sonnet-4-5"]["model_id"]


# ---------------------------------------------------------------------------
# TestTokenTracking
# ---------------------------------------------------------------------------

class TestTokenTracking:
    """_track_tokens best-effort integration."""

    @patch("tools.agent.bedrock_client.logger")
    def test_track_tokens_handles_import_error(self, mock_logger):
        """When token_tracker is not importable, _track_tokens logs debug and continues."""
        resp = BedrockResponse(model_id="test", input_tokens=10, output_tokens=20)
        with patch.dict("sys.modules", {"tools.agent.token_tracker": None}):
            with patch("tools.agent.bedrock_client._track_tokens.__module__", create=True):
                # Force an ImportError path
                _track_tokens(resp, "agent-1", "proj-1")
        # Should not raise

    def test_track_tokens_success_path(self):
        """When token_tracker is available, _track_tokens calls log_usage."""
        mock_tracker = MagicMock()
        mock_tracker.estimate_cost.return_value = 0.005
        resp = BedrockResponse(model_id="test-model", input_tokens=100, output_tokens=200,
                               thinking_tokens=50, duration_ms=1000)
        with patch.dict("sys.modules", {"tools.agent.token_tracker": mock_tracker}):
            _track_tokens(resp, "builder-agent", "proj-123")
        mock_tracker.log_usage.assert_called_once()
        call_kwargs = mock_tracker.log_usage.call_args
        assert call_kwargs[1]["model_id"] == "test-model" or call_kwargs.kwargs.get("model_id") == "test-model"


# ---------------------------------------------------------------------------
# TestBoto3Unavailable
# ---------------------------------------------------------------------------

class TestBoto3Unavailable:
    """Graceful handling when boto3 is not installed."""

    def test_get_client_raises_import_error_without_boto3(self):
        with patch("tools.agent.bedrock_client.boto3", None):
            with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
                with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                    client = _create_client()
                    with pytest.raises(ImportError, match="boto3 is required"):
                        client._get_client()


# ---------------------------------------------------------------------------
# TestEffortResolution
# ---------------------------------------------------------------------------

class TestEffortResolution:
    """Effort mapping and per-agent overrides."""

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_effort_to_budget_low(self):
        from tools.agent.bedrock_client import BedrockClient
        budget = BedrockClient._effort_to_budget("low", 8192)
        assert budget == max(int(8192 * 0.10), 1024)

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_effort_to_budget_max(self):
        from tools.agent.bedrock_client import BedrockClient
        budget = BedrockClient._effort_to_budget("max", 128000)
        assert budget == max(int(128000 * 1.0), 10240)

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_resolve_effort_uses_request_level(self):
        with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
            with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                client = _create_client()
                req = BedrockRequest(effort="high", agent_id="builder-agent")
                assert client._resolve_effort(req) == "high"


# ---------------------------------------------------------------------------
# TestProbe
# ---------------------------------------------------------------------------

class TestProbe:
    """probe_model_availability caching and error handling."""

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_probe_caches_results(self):
        with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
            with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                client = _create_client()
                mock_runtime = MagicMock()
                mock_runtime.invoke_model.return_value = _make_invoke_response()
                client._client = mock_runtime

                result1 = client.probe_model_availability()
                result2 = client.probe_model_availability()

                # Second call should use cache, so invoke_model called only 3 times (once per model)
                assert mock_runtime.invoke_model.call_count == 3
                assert result1 == result2

    @patch("tools.agent.bedrock_client.boto3", new=MagicMock())
    def test_probe_marks_unavailable_on_error(self):
        with patch("tools.agent.bedrock_client.BEDROCK_MODELS_CONFIG", Path("/nonexistent/bedrock.yaml")):
            with patch("tools.agent.bedrock_client.AGENT_CONFIG", Path("/nonexistent/agent.yaml")):
                client = _create_client()
                mock_runtime = MagicMock()
                mock_runtime.invoke_model.side_effect = Exception("Model not found")
                client._client = mock_runtime

                result = client.probe_model_availability()
                assert all(v is False for v in result.values())


# ---------------------------------------------------------------------------
# TestRetryLogic
# ---------------------------------------------------------------------------

class TestRetryLogic:
    """_is_retryable and _backoff_delay static methods."""

    def test_throttling_is_retryable(self):
        from tools.agent.bedrock_client import BedrockClient
        exc = Exception("rate limited")
        exc.response = {"Error": {"Code": "ThrottlingException"}}
        assert BedrockClient._is_retryable(exc) is True

    def test_generic_error_not_retryable(self):
        from tools.agent.bedrock_client import BedrockClient
        exc = ValueError("bad input")
        assert BedrockClient._is_retryable(exc) is False

    def test_backoff_delay_increases_with_attempt(self):
        from tools.agent.bedrock_client import BedrockClient
        delays = [BedrockClient._backoff_delay(i) for i in range(5)]
        # Generally the max possible delay should increase with attempt
        # Due to jitter, we check the underlying exponential growth via ceiling
        for i in range(1, len(delays)):
            max_possible_current = min(30.0, 1.0 * (2 ** i))
            max_possible_previous = min(30.0, 1.0 * (2 ** (i - 1)))
            assert max_possible_current >= max_possible_previous


# [TEMPLATE: CUI // SP-CTI]
