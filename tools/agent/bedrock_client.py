# CUI // SP-CTI
"""Centralized Amazon Bedrock API wrapper for ICDEV multi-agent system.

Bedrock-specific client used by agents (team_orchestrator, collaboration,
agent_executor) that specifically target AWS Bedrock.  Supports Opus 4.6 GA
features (adaptive thinking, effort parameter, 128K output, structured
outputs), tool_use with multi-turn loops, streaming, and automatic model
fallback.

For vendor-agnostic LLM access (multi-provider, local models), use the
``tools.llm`` package instead::

    from tools.llm import get_router
    from tools.llm.provider import LLMRequest
    router = get_router()
    resp = router.invoke("code_generation", LLMRequest(...))

Decision D1: boto3 invoke_model / invoke_model_with_response_stream (air-gap safe).
Decision D3: Model fallback chain Opus 4.6 -> Sonnet 4.5 -> Sonnet 3.5.
Decision D70: BedrockClient preserved for Bedrock-specific callers; tools.llm
    provides the vendor-agnostic alternative.
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # Graceful fallback — config will use defaults

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = Exception

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"
BEDROCK_MODELS_CONFIG = BASE_DIR / "args" / "bedrock_models.yaml"
AGENT_CONFIG = BASE_DIR / "args" / "agent_config.yaml"

logger = logging.getLogger("icdev.bedrock_client")

# ---------------------------------------------------------------------------
# Default model registry — used when args/bedrock_models.yaml is absent
# ---------------------------------------------------------------------------
DEFAULT_MODELS = {
    "opus": {
        "model_id": "anthropic.claude-opus-4-6-20260215-v1:0",
        "display_name": "Claude Opus 4.6",
        "max_output_tokens": 128000,
        "supports_thinking": True,
        "supports_effort": True,
        "supports_structured_output": True,
        "supports_tool_use": True,
        "supports_streaming": True,
        "anthropic_version": "bedrock-2023-05-31",
    },
    "sonnet-4-5": {
        "model_id": "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "display_name": "Claude Sonnet 4.5",
        "max_output_tokens": 16384,
        "supports_thinking": True,
        "supports_effort": True,
        "supports_structured_output": True,
        "supports_tool_use": True,
        "supports_streaming": True,
        "anthropic_version": "bedrock-2023-05-31",
    },
    "sonnet-3-5": {
        "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "display_name": "Claude Sonnet 3.5 v2",
        "max_output_tokens": 8192,
        "supports_thinking": False,
        "supports_effort": False,
        "supports_structured_output": False,
        "supports_tool_use": True,
        "supports_streaming": True,
        "anthropic_version": "bedrock-2023-05-31",
    },
}

# Model preference -> fallback chain
FALLBACK_CHAIN = {
    "opus": ["opus", "sonnet-4-5", "sonnet-3-5"],
    "sonnet-4-5": ["sonnet-4-5", "sonnet-3-5"],
    "sonnet-3-5": ["sonnet-3-5"],
}

# Retry configuration
MAX_RETRIES = 5
BASE_RETRY_DELAY = 1.0    # seconds
MAX_RETRY_DELAY = 30.0    # seconds
RETRYABLE_ERROR_CODES = [
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "InternalServerException",
    "TooManyRequestsException",
]

# Availability cache TTL
AVAILABILITY_CACHE_TTL = 1800  # 30 minutes in seconds


# ---------------------------------------------------------------------------
# Data classes (Bedrock-specific, defined here per spec)
# ---------------------------------------------------------------------------
@dataclass
class BedrockRequest:
    """Encapsulates a single Bedrock invocation request."""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    agent_id: str = ""
    project_id: str = ""
    model_preference: str = "opus"      # opus, sonnet-4-5, sonnet-3-5
    effort: str = "medium"              # low, medium, high, max
    max_tokens: int = 4096
    tools: Optional[List[Dict]] = None
    output_schema: Optional[Dict] = None
    temperature: float = 1.0
    stop_sequences: Optional[List[str]] = None
    classification: str = "CUI"


@dataclass
class BedrockResponse:
    """Encapsulates Bedrock invocation results including token accounting."""
    content: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    structured_output: Optional[Dict] = None
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    duration_ms: int = 0
    stop_reason: str = ""
    classification: str = "CUI"


# ---------------------------------------------------------------------------
# Token tracker integration (optional — graceful if missing)
# ---------------------------------------------------------------------------
def _track_tokens(response: BedrockResponse, agent_id: str, project_id: str):
    """Best-effort token tracking via tools/agent/token_tracker.py."""
    try:
        from tools.agent.token_tracker import log_usage, estimate_cost
        cost = estimate_cost(response.model_id, response.input_tokens, response.output_tokens)
        log_usage(
            model_id=response.model_id,
            agent_id=agent_id,
            project_id=project_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            thinking_tokens=response.thinking_tokens,
            duration_ms=response.duration_ms,
            cost_estimate_usd=cost,
        )
    except ImportError:
        logger.debug("token_tracker not available — skipping token tracking")
    except Exception as exc:
        logger.warning("Token tracking failed: %s", exc)


# ---------------------------------------------------------------------------
# BedrockClient
# ---------------------------------------------------------------------------
class BedrockClient:
    """Centralized Bedrock API wrapper with fallback, retry, and Opus 4.6 support.

    Usage::

        client = BedrockClient()
        req = BedrockRequest(
            messages=[{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
            system_prompt="You are a helpful assistant.",
            model_preference="opus",
            effort="high",
        )
        resp = client.invoke(req)
        print(resp.content)
    """

    def __init__(self, config_path: Optional[str] = None, db_path: Optional[str] = None):
        import warnings
        warnings.warn(
            "BedrockClient is deprecated. Use tools.llm.router.LLMRouter instead. "
            "See D70 for migration guidance.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._region = os.environ.get("AWS_DEFAULT_REGION", "us-gov-west-1")
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._models: Dict[str, Dict[str, Any]] = {}
        self._agent_effort_overrides: Dict[str, str] = {}
        self._client = None  # Lazy-init boto3 client
        self._availability_cache: Dict[str, bool] = {}
        self._availability_cache_time: float = 0.0

        # Load model configuration
        self._load_model_config(config_path)
        # Load per-agent effort overrides from agent_config.yaml
        self._load_agent_config()

    # -----------------------------------------------------------------------
    # Configuration loading
    # -----------------------------------------------------------------------
    def _load_model_config(self, config_path: Optional[str] = None):
        """Load model definitions from args/bedrock_models.yaml or use defaults."""
        path = Path(config_path) if config_path else BEDROCK_MODELS_CONFIG
        if yaml and path.exists():
            try:
                with open(path, "r") as f:
                    raw = yaml.safe_load(f) or {}
                models = raw.get("models", raw)
                if isinstance(models, dict):
                    self._models = models
                    logger.info("Loaded %d model(s) from %s", len(self._models), path)
                    return
            except Exception as exc:
                logger.warning("Failed to load %s: %s — using defaults", path, exc)

        self._models = dict(DEFAULT_MODELS)
        logger.info("Using default model registry (%d models)", len(self._models))

    def _load_agent_config(self):
        """Load per-agent effort overrides from args/agent_config.yaml."""
        if not yaml or not AGENT_CONFIG.exists():
            return
        try:
            with open(AGENT_CONFIG, "r") as f:
                raw = yaml.safe_load(f) or {}
            agents = raw.get("agents", {})
            for agent_key, agent_def in agents.items():
                agent_id = agent_def.get("id", agent_key)
                bedrock_cfg = agent_def.get("bedrock", {})
                effort = bedrock_cfg.get("effort")
                if effort:
                    self._agent_effort_overrides[agent_id] = effort
        except Exception as exc:
            logger.warning("Failed to load agent config: %s", exc)

    # -----------------------------------------------------------------------
    # boto3 client (lazy init)
    # -----------------------------------------------------------------------
    def _get_client(self):
        """Return cached boto3 bedrock-runtime client, creating if needed."""
        if self._client is None:
            if boto3 is None:
                raise ImportError(
                    "boto3 is required for Bedrock invocation. "
                    "Install with: pip install boto3"
                )
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    # -----------------------------------------------------------------------
    # Model availability probing
    # -----------------------------------------------------------------------
    def probe_model_availability(self) -> Dict[str, bool]:
        """Probe each model with a minimal request; cache results for 30 min.

        Returns a dict mapping model preference key -> bool (available).
        """
        now = time.time()
        if (self._availability_cache
                and (now - self._availability_cache_time) < AVAILABILITY_CACHE_TTL):
            return dict(self._availability_cache)

        results: Dict[str, bool] = {}
        client = self._get_client()

        for pref_key, model_cfg in self._models.items():
            model_id = model_cfg.get("model_id", "")
            if not model_id:
                results[pref_key] = False
                continue
            try:
                body = {
                    "anthropic_version": model_cfg.get(
                        "anthropic_version", "bedrock-2023-05-31"
                    ),
                    "max_tokens": 1,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "ping"}
                            ],
                        }
                    ],
                }
                client.invoke_model(
                    modelId=model_id,
                    body=json.dumps(body),
                )
                results[pref_key] = True
                logger.info("Model %s (%s): AVAILABLE", pref_key, model_id)
            except Exception as exc:
                error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                # ThrottlingException means the model exists but we're rate-limited
                if error_code == "ThrottlingException":
                    results[pref_key] = True
                    logger.info("Model %s (%s): AVAILABLE (throttled probe)", pref_key, model_id)
                else:
                    results[pref_key] = False
                    logger.warning("Model %s (%s): UNAVAILABLE (%s)", pref_key, model_id, exc)

        self._availability_cache = results
        self._availability_cache_time = now
        return dict(results)

    # -----------------------------------------------------------------------
    # Model resolution with fallback
    # -----------------------------------------------------------------------
    def _resolve_model_id(self, preference: str) -> str:
        """Resolve preference to an available model_id using fallback chain.

        Probes availability if cache is stale. Falls through the chain:
        opus -> sonnet-4-5 -> sonnet-3-5.

        Raises RuntimeError if no model in the chain is available.
        """
        chain = FALLBACK_CHAIN.get(preference, [preference])
        availability = self.probe_model_availability()

        for pref_key in chain:
            if availability.get(pref_key, False):
                model_cfg = self._models.get(pref_key, {})
                model_id = model_cfg.get("model_id", "")
                if model_id:
                    if pref_key != preference:
                        logger.info(
                            "Fell back from %s to %s (%s)",
                            preference, pref_key, model_id,
                        )
                    return model_id

        # Last resort: return the first model_id in the chain without probing
        # (let Bedrock return the real error)
        for pref_key in chain:
            model_cfg = self._models.get(pref_key, {})
            model_id = model_cfg.get("model_id", "")
            if model_id:
                logger.warning(
                    "No models confirmed available; attempting %s (%s) anyway",
                    pref_key, model_id,
                )
                return model_id

        raise RuntimeError(
            f"No model_id found for preference '{preference}' in chain {chain}"
        )

    # -----------------------------------------------------------------------
    # Model capability lookup
    # -----------------------------------------------------------------------
    def _get_model_config(self, model_id: str) -> Dict[str, Any]:
        """Find model config dict by model_id (reverse lookup)."""
        for _key, cfg in self._models.items():
            if cfg.get("model_id") == model_id:
                return cfg
        return {}

    # -----------------------------------------------------------------------
    # Resolve per-agent effort
    # -----------------------------------------------------------------------
    def _resolve_effort(self, request: BedrockRequest) -> str:
        """Return effort: request-level > agent-config override > default."""
        if request.effort:
            return request.effort
        if request.agent_id and request.agent_id in self._agent_effort_overrides:
            return self._agent_effort_overrides[request.agent_id]
        return "medium"

    # -----------------------------------------------------------------------
    # Request body construction
    # -----------------------------------------------------------------------
    def _build_request_body(self, request: BedrockRequest, model_id: str) -> Dict:
        """Build the JSON body for invoke_model / invoke_model_with_response_stream.

        Rules:
        1. Always include anthropic_version, max_tokens, messages.
        2. Include system if system_prompt provided.
        3. Only add thinking + effort if model supports them.
        4. Only add tools if provided.
        5. Only add output_config if output_schema provided AND model supports it.
        """
        model_cfg = self._get_model_config(model_id)
        anthropic_version = model_cfg.get("anthropic_version", "bedrock-2023-05-31")

        # Respect per-model max output token ceiling
        model_max = model_cfg.get("max_output_tokens", 8192)
        effective_max_tokens = min(request.max_tokens, model_max)

        body: Dict[str, Any] = {
            "anthropic_version": anthropic_version,
            "max_tokens": effective_max_tokens,
            "messages": request.messages,
        }

        # 2. System prompt
        if request.system_prompt:
            body["system"] = [{"type": "text", "text": request.system_prompt}]

        # Temperature
        if request.temperature is not None:
            body["temperature"] = request.temperature

        # Stop sequences
        if request.stop_sequences:
            body["stop_sequences"] = request.stop_sequences

        # 3. Adaptive thinking + effort (Opus 4.6 / Sonnet 4.5 feature)
        if model_cfg.get("supports_thinking", False):
            effort = self._resolve_effort(request)
            body["thinking"] = {
                "type": "adaptive",
                "budget_tokens": self._effort_to_budget(effort, effective_max_tokens),
            }

        # 4. Tools (function calling)
        if request.tools:
            body["tools"] = request.tools

        # 5. Structured output (Opus 4.6 feature)
        if request.output_schema and model_cfg.get("supports_structured_output", False):
            body["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "json_schema": request.output_schema,
                }
            }

        return body

    @staticmethod
    def _effort_to_budget(effort: str, max_tokens: int) -> int:
        """Map effort level to a thinking budget_tokens value.

        Low  = ~10% of max_tokens (floor 1024)
        Medium = ~25% of max_tokens (floor 4096)
        High = ~60% of max_tokens (floor 10240)
        Max  = max_tokens (uncapped thinking)
        """
        ratios = {
            "low": (0.10, 1024),
            "medium": (0.25, 4096),
            "high": (0.60, 10240),
            "max": (1.0, 10240),
        }
        ratio, floor_val = ratios.get(effort, (0.25, 4096))
        budget = int(max_tokens * ratio)
        return max(budget, floor_val)

    # -----------------------------------------------------------------------
    # Response parsing
    # -----------------------------------------------------------------------
    def _parse_response(self, response_body: dict) -> BedrockResponse:
        """Parse the JSON body returned by invoke_model into BedrockResponse."""
        resp = BedrockResponse()

        # Model info
        resp.model_id = response_body.get("model", "")
        resp.stop_reason = response_body.get("stop_reason", "")

        # Token usage
        usage = response_body.get("usage", {})
        resp.input_tokens = usage.get("input_tokens", 0)
        resp.output_tokens = usage.get("output_tokens", 0)

        # Content blocks
        content_blocks = response_body.get("content", [])
        text_parts: List[str] = []
        tool_calls: List[Dict] = []
        thinking_text_parts: List[str] = []

        for block in content_blocks:
            block_type = block.get("type", "")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                })
            elif block_type == "thinking":
                thinking_text_parts.append(block.get("thinking", ""))
                resp.thinking_tokens += block.get("tokens", 0)

        resp.content = "\n".join(text_parts)
        resp.tool_calls = tool_calls

        # If structured output was requested, try to parse content as JSON
        if resp.content.strip().startswith("{") or resp.content.strip().startswith("["):
            try:
                resp.structured_output = json.loads(resp.content)
            except (json.JSONDecodeError, ValueError):
                pass

        return resp

    # -----------------------------------------------------------------------
    # Retry with exponential backoff + jitter
    # -----------------------------------------------------------------------
    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Check if an exception is retryable (rate limit / transient)."""
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if error_code in RETRYABLE_ERROR_CODES:
            return True
        # Also catch generic connection errors
        exc_name = type(exc).__name__
        if exc_name in ("ReadTimeoutError", "ConnectTimeoutError", "ConnectionError"):
            return True
        return False

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff with full jitter (D147 — delegates to resilience.retry)."""
        try:
            from tools.resilience.retry import backoff_delay
            return backoff_delay(attempt, BASE_RETRY_DELAY, MAX_RETRY_DELAY)
        except ImportError:
            delay = min(MAX_RETRY_DELAY, BASE_RETRY_DELAY * (2 ** attempt))
            return delay * random.uniform(0.5, 1.0)

    # -----------------------------------------------------------------------
    # Core invocation
    # -----------------------------------------------------------------------
    def invoke(self, request: BedrockRequest) -> BedrockResponse:
        """Invoke Bedrock synchronously with retry and model fallback.

        Returns BedrockResponse with content, token counts, and timing.
        """
        model_id = self._resolve_model_id(request.model_preference)
        body = self._build_request_body(request, model_id)
        client = self._get_client()

        start_time = time.time()
        last_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                raw_response = client.invoke_model(
                    modelId=model_id,
                    body=json.dumps(body),
                )
                response_body = json.loads(raw_response["body"].read())
                resp = self._parse_response(response_body)
                resp.model_id = model_id
                resp.duration_ms = int((time.time() - start_time) * 1000)
                resp.classification = request.classification

                # Token tracking (best-effort)
                _track_tokens(resp, request.agent_id, request.project_id)

                return resp

            except Exception as exc:
                last_exc = exc
                if self._is_retryable(exc) and attempt < MAX_RETRIES:
                    delay = self._backoff_delay(attempt)
                    error_code = getattr(exc, "response", {}).get(
                        "Error", {}
                    ).get("Code", type(exc).__name__)
                    logger.warning(
                        "Retryable error on attempt %d/%d (%s): %s — retrying in %.1fs",
                        attempt + 1, MAX_RETRIES, error_code, exc, delay,
                    )
                    time.sleep(delay)
                    continue
                else:
                    raise

        # Should not reach here, but just in case
        raise last_exc  # type: ignore[misc]

    # -----------------------------------------------------------------------
    # Streaming invocation
    # -----------------------------------------------------------------------
    def invoke_streaming(self, request: BedrockRequest) -> Iterator[Dict]:
        """Invoke Bedrock with streaming response.

        Yields dicts with keys:
        - {"type": "text", "text": "..."}              — text delta
        - {"type": "thinking", "thinking": "..."}      — thinking delta
        - {"type": "tool_use_start", "id": ..., "name": ...}
        - {"type": "tool_use_input", "partial_json": "..."}
        - {"type": "content_block_stop"}
        - {"type": "message_start", "message": {...}}
        - {"type": "message_delta", "stop_reason": ..., "usage": {...}}
        - {"type": "message_stop"}
        - {"type": "error", "error": "..."}
        """
        model_id = self._resolve_model_id(request.model_preference)
        body = self._build_request_body(request, model_id)
        client = self._get_client()

        start_time = time.time()
        last_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                raw_response = client.invoke_model_with_response_stream(
                    modelId=model_id,
                    body=json.dumps(body),
                )
                stream = raw_response.get("body", [])

                total_input_tokens = 0
                total_output_tokens = 0

                for event in stream:
                    chunk = event.get("chunk")
                    if not chunk:
                        continue
                    chunk_data = json.loads(chunk["bytes"])
                    event_type = chunk_data.get("type", "")

                    if event_type == "message_start":
                        msg = chunk_data.get("message", {})
                        usage = msg.get("usage", {})
                        total_input_tokens += usage.get("input_tokens", 0)
                        yield {"type": "message_start", "message": msg}

                    elif event_type == "content_block_start":
                        block = chunk_data.get("content_block", {})
                        block_type = block.get("type", "")
                        if block_type == "tool_use":
                            yield {
                                "type": "tool_use_start",
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                            }
                        elif block_type == "thinking":
                            yield {"type": "thinking_start"}
                        # text blocks start implicitly

                    elif event_type == "content_block_delta":
                        delta = chunk_data.get("delta", {})
                        delta_type = delta.get("type", "")
                        if delta_type == "text_delta":
                            yield {"type": "text", "text": delta.get("text", "")}
                        elif delta_type == "thinking_delta":
                            yield {"type": "thinking", "thinking": delta.get("thinking", "")}
                        elif delta_type == "input_json_delta":
                            yield {
                                "type": "tool_use_input",
                                "partial_json": delta.get("partial_json", ""),
                            }

                    elif event_type == "content_block_stop":
                        yield {"type": "content_block_stop"}

                    elif event_type == "message_delta":
                        delta = chunk_data.get("delta", {})
                        usage = chunk_data.get("usage", {})
                        total_output_tokens += usage.get("output_tokens", 0)
                        yield {
                            "type": "message_delta",
                            "stop_reason": delta.get("stop_reason", ""),
                            "usage": {
                                "input_tokens": total_input_tokens,
                                "output_tokens": total_output_tokens,
                            },
                        }

                    elif event_type == "message_stop":
                        duration_ms = int((time.time() - start_time) * 1000)
                        yield {
                            "type": "message_stop",
                            "duration_ms": duration_ms,
                            "model_id": model_id,
                        }

                # Stream completed successfully — track tokens
                duration_ms = int((time.time() - start_time) * 1000)
                tracking_resp = BedrockResponse(
                    model_id=model_id,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    duration_ms=duration_ms,
                    classification=request.classification,
                )
                _track_tokens(tracking_resp, request.agent_id, request.project_id)
                return  # Generator done

            except Exception as exc:
                last_exc = exc
                if self._is_retryable(exc) and attempt < MAX_RETRIES:
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        "Stream retry attempt %d/%d: %s — retrying in %.1fs",
                        attempt + 1, MAX_RETRIES, exc, delay,
                    )
                    time.sleep(delay)
                    continue
                else:
                    yield {"type": "error", "error": str(exc)}
                    return

        if last_exc:
            yield {"type": "error", "error": str(last_exc)}

    # -----------------------------------------------------------------------
    # Multi-turn tool loop
    # -----------------------------------------------------------------------
    def invoke_with_tools(
        self,
        request: BedrockRequest,
        tool_handlers: Dict[str, Callable],
        max_iterations: int = 10,
    ) -> BedrockResponse:
        """Invoke with automatic multi-turn tool execution loop.

        1. Call invoke()
        2. If response has tool_calls, execute each via tool_handlers
        3. Build tool_result messages, append to conversation
        4. Call invoke() again
        5. Repeat until no more tool calls or max_iterations reached

        Args:
            request: The initial BedrockRequest (messages will be extended in-place).
            tool_handlers: Dict mapping tool name -> callable(input_dict) -> result.
            max_iterations: Safety cap on tool-call rounds (default 10).

        Returns:
            Final BedrockResponse after all tool calls resolved.
        """
        # Work with a mutable copy of messages
        messages = list(request.messages)
        aggregated_input_tokens = 0
        aggregated_output_tokens = 0
        aggregated_thinking_tokens = 0
        start_time = time.time()

        for iteration in range(max_iterations):
            # Build a per-turn request
            turn_request = BedrockRequest(
                messages=messages,
                system_prompt=request.system_prompt,
                agent_id=request.agent_id,
                project_id=request.project_id,
                model_preference=request.model_preference,
                effort=request.effort,
                max_tokens=request.max_tokens,
                tools=request.tools,
                output_schema=request.output_schema,
                temperature=request.temperature,
                stop_sequences=request.stop_sequences,
                classification=request.classification,
            )

            resp = self.invoke(turn_request)
            aggregated_input_tokens += resp.input_tokens
            aggregated_output_tokens += resp.output_tokens
            aggregated_thinking_tokens += resp.thinking_tokens

            # If no tool calls, we are done
            if not resp.tool_calls:
                resp.input_tokens = aggregated_input_tokens
                resp.output_tokens = aggregated_output_tokens
                resp.thinking_tokens = aggregated_thinking_tokens
                resp.duration_ms = int((time.time() - start_time) * 1000)
                return resp

            # Build the assistant message with content blocks that include tool_use
            assistant_content: List[Dict[str, Any]] = []
            if resp.content:
                assistant_content.append({"type": "text", "text": resp.content})
            for tc in resp.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute each tool call and build tool_result blocks
            tool_result_content: List[Dict[str, Any]] = []
            for tc in resp.tool_calls:
                tool_name = tc["name"]
                tool_input = tc["input"]
                tool_id = tc["id"]

                handler = tool_handlers.get(tool_name)
                if handler is None:
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Unknown tool: {tool_name}",
                            }
                        ],
                    })
                    continue

                try:
                    result = handler(tool_input)
                    # Normalize result to string
                    if isinstance(result, dict) or isinstance(result, list):
                        result_text = json.dumps(result)
                    else:
                        result_text = str(result)
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": [
                            {"type": "text", "text": result_text}
                        ],
                    })
                except Exception as exc:
                    logger.error("Tool '%s' raised: %s", tool_name, exc)
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Tool execution error: {exc}",
                            }
                        ],
                    })

            messages.append({"role": "user", "content": tool_result_content})

            logger.info(
                "Tool loop iteration %d/%d: executed %d tool(s)",
                iteration + 1, max_iterations, len(resp.tool_calls),
            )

        # Max iterations reached — return last response with aggregated tokens
        logger.warning("Tool loop reached max iterations (%d)", max_iterations)
        resp.input_tokens = aggregated_input_tokens
        resp.output_tokens = aggregated_output_tokens
        resp.thinking_tokens = aggregated_thinking_tokens
        resp.duration_ms = int((time.time() - start_time) * 1000)
        return resp


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """CLI for quick Bedrock invocations and model probing."""
    parser = argparse.ArgumentParser(
        description="ICDEV Bedrock Client — centralized model invocation"
    )
    parser.add_argument("--prompt", help="Prompt text to send")
    parser.add_argument(
        "--model", default="opus",
        choices=["opus", "sonnet-4-5", "sonnet-3-5"],
        help="Model preference (default: opus)",
    )
    parser.add_argument(
        "--effort", default="medium",
        choices=["low", "medium", "high", "max"],
        help="Thinking effort level (default: medium)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=4096,
        help="Max output tokens (default: 4096)",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="Probe model availability and exit",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--system", default="",
        help="System prompt (optional)",
    )
    parser.add_argument(
        "--stream", action="store_true",
        help="Use streaming invocation",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    client = BedrockClient()

    # Probe mode
    if args.probe:
        availability = client.probe_model_availability()
        if args.json:
            print(json.dumps({
                "availability": availability,
                "models": {
                    k: {"model_id": v.get("model_id", ""), "display_name": v.get("display_name", "")}
                    for k, v in client._models.items()
                },
                "region": client._region,
                "classification": "CUI",
            }, indent=2))
        else:
            print(f"Region: {client._region}")
            print("Classification: CUI // SP-CTI")
            print(f"{'Model':<20} {'Model ID':<55} {'Available'}")
            print("-" * 90)
            for pref_key, cfg in client._models.items():
                avail = availability.get(pref_key, False)
                status = "YES" if avail else "NO"
                print(f"{pref_key:<20} {cfg.get('model_id', 'N/A'):<55} {status}")
        return

    # Invoke mode
    if not args.prompt:
        parser.error("--prompt is required (unless using --probe)")

    request = BedrockRequest(
        messages=[
            {
                "role": "user",
                "content": [{"type": "text", "text": args.prompt}],
            }
        ],
        system_prompt=args.system,
        model_preference=args.model,
        effort=args.effort,
        max_tokens=args.max_tokens,
        classification="CUI",
    )

    if args.stream:
        # Streaming mode
        text_parts = []
        final_meta = {}
        for event in client.invoke_streaming(request):
            etype = event.get("type", "")
            if etype == "text":
                chunk = event.get("text", "")
                if args.json:
                    text_parts.append(chunk)
                else:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            elif etype == "message_delta":
                final_meta["stop_reason"] = event.get("stop_reason", "")
                final_meta["usage"] = event.get("usage", {})
            elif etype == "message_stop":
                final_meta["duration_ms"] = event.get("duration_ms", 0)
                final_meta["model_id"] = event.get("model_id", "")
            elif etype == "error":
                print(f"\nError: {event.get('error', 'unknown')}", file=sys.stderr)
                sys.exit(1)

        if args.json:
            print(json.dumps({
                "content": "".join(text_parts),
                "model_id": final_meta.get("model_id", ""),
                "stop_reason": final_meta.get("stop_reason", ""),
                "usage": final_meta.get("usage", {}),
                "duration_ms": final_meta.get("duration_ms", 0),
                "classification": "CUI",
            }, indent=2))
        else:
            print()  # Newline after streamed text
            usage = final_meta.get("usage", {})
            print("\n--- Metadata ---")
            print(f"Model: {final_meta.get('model_id', 'unknown')}")
            print(f"Duration: {final_meta.get('duration_ms', 0)}ms")
            print(f"Tokens: {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out")
            print("Classification: CUI // SP-CTI")
    else:
        # Synchronous mode
        try:
            resp = client.invoke(request)
        except Exception as exc:
            if args.json:
                print(json.dumps({
                    "error": str(exc),
                    "classification": "CUI",
                }, indent=2))
            else:
                print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps({
                "content": resp.content,
                "model_id": resp.model_id,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "thinking_tokens": resp.thinking_tokens,
                "duration_ms": resp.duration_ms,
                "stop_reason": resp.stop_reason,
                "tool_calls": resp.tool_calls,
                "structured_output": resp.structured_output,
                "classification": resp.classification,
            }, indent=2))
        else:
            print(resp.content)
            print("\n--- Metadata ---")
            print(f"Model: {resp.model_id}")
            print(f"Duration: {resp.duration_ms}ms")
            print(f"Tokens: {resp.input_tokens} in / {resp.output_tokens} out")
            if resp.thinking_tokens:
                print(f"Thinking tokens: {resp.thinking_tokens}")
            print(f"Stop reason: {resp.stop_reason}")
            print("Classification: CUI // SP-CTI")


if __name__ == "__main__":
    main()
