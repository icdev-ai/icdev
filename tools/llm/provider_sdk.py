# [TEMPLATE: CUI // SP-CTI]
"""ICDEV LLM Provider SDK — Public API for custom provider development.

Re-exports the core provider interfaces, request/response types, format
helpers, and a test harness for validating custom implementations.

Usage for building a custom provider::

    from tools.llm.provider_sdk import (
        BaseProvider,
        LLMRequest,
        LLMResponse,
        EmbeddingProvider,
        ProviderTestHarness,
    )

    class MyProvider(BaseProvider):
        @property
        def provider_name(self): return "my_provider"
        def invoke(self, request, model_id, model_config): ...
        def check_availability(self, model_id): ...

    # Validate your implementation
    harness = ProviderTestHarness(MyProvider())
    harness.run_all()

Decision D66: ABC + implementations provider pattern.
Decision D67: OpenAI-compatible covers Ollama, vLLM, Azure.
Decision D68: Function-level LLM routing (not agent-level).
"""

import logging
from typing import List

# Re-export core types
from tools.llm.provider import (
    LLMProvider as BaseProvider,
    LLMRequest,
    LLMResponse,
    EmbeddingProvider,
    messages_to_anthropic,
    messages_to_openai,
    tools_to_anthropic,
    tools_to_openai,
)

logger = logging.getLogger("icdev.llm.provider_sdk")

__all__ = [
    "BaseProvider",
    "LLMRequest",
    "LLMResponse",
    "EmbeddingProvider",
    "messages_to_anthropic",
    "messages_to_openai",
    "tools_to_anthropic",
    "tools_to_openai",
    "ProviderTestHarness",
]


class ProviderTestHarness:
    """Test harness for validating custom LLM provider implementations.

    Usage::

        from tools.llm.provider_sdk import BaseProvider, ProviderTestHarness

        class MyProvider(BaseProvider):
            ...

        harness = ProviderTestHarness(MyProvider())
        results = harness.run_all()
        for name, passed, msg in results:
            print(f"{'PASS' if passed else 'FAIL'}: {name} — {msg}")
    """

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    def test_implements_abc(self) -> tuple:
        """Verify the provider implements all required abstract methods."""
        name = "implements_abc"
        try:
            assert hasattr(self.provider, "provider_name"), "Missing provider_name property"
            assert hasattr(self.provider, "invoke"), "Missing invoke() method"
            assert hasattr(self.provider, "check_availability"), "Missing check_availability()"
            assert callable(self.provider.invoke), "invoke must be callable"
            assert callable(self.provider.check_availability), "check_availability must be callable"
            pname = self.provider.provider_name
            assert isinstance(pname, str) and len(pname) > 0, "provider_name must be non-empty string"
            return (name, True, "All ABC methods implemented")
        except AssertionError as exc:
            return (name, False, str(exc))
        except Exception as exc:
            return (name, False, f"Unexpected error: {exc}")

    def test_invoke_returns_response(self) -> tuple:
        """Test that invoke returns an LLMResponse (may fail if provider is offline)."""
        name = "invoke_returns_response"
        try:
            req = LLMRequest(
                messages=[{"role": "user", "content": "Say hello in exactly one word."}],
                max_tokens=50,
                temperature=0.0,
            )
            resp = self.provider.invoke(req, "test-model", {})
            assert isinstance(resp, LLMResponse), f"Expected LLMResponse, got {type(resp)}"
            assert isinstance(resp.content, str), "content must be a string"
            return (name, True, f"Got response: {resp.content[:50]}")
        except NotImplementedError:
            return (name, False, "invoke() not implemented")
        except Exception as exc:
            return (name, False, f"Invocation failed (provider may be offline): {exc}")

    def test_check_availability(self) -> tuple:
        """Test that check_availability returns a boolean."""
        name = "check_availability"
        try:
            result = self.provider.check_availability("test-model")
            assert isinstance(result, bool), f"Expected bool, got {type(result)}"
            return (name, True, f"Available: {result}")
        except Exception as exc:
            return (name, False, f"Error: {exc}")

    def test_streaming_fallback(self) -> tuple:
        """Test that invoke_streaming works (at least via fallback)."""
        name = "streaming_fallback"
        try:
            req = LLMRequest(
                messages=[{"role": "user", "content": "Say hi."}],
                max_tokens=20,
            )
            chunks = list(self.provider.invoke_streaming(req, "test-model", {}))
            assert len(chunks) > 0, "No chunks returned"
            assert any(c.get("type") == "text" for c in chunks), "No text chunk found"
            return (name, True, f"Got {len(chunks)} chunks")
        except Exception as exc:
            return (name, False, f"Streaming failed: {exc}")

    def test_error_handling(self) -> tuple:
        """Test that the provider handles invalid input gracefully."""
        name = "error_handling"
        try:
            req = LLMRequest(messages=[], max_tokens=0)
            try:
                self.provider.invoke(req, "", {})
                return (name, True, "No error on empty input (provider is lenient)")
            except (ValueError, RuntimeError, TypeError):
                return (name, True, "Provider correctly raises on invalid input")
            except Exception as exc:
                return (name, True, f"Provider raised {type(exc).__name__} on invalid input")
        except Exception as exc:
            return (name, False, f"Unexpected error: {exc}")

    def run_all(self) -> List[tuple]:
        """Run all test methods and return results.

        Returns:
            List of (test_name, passed, message) tuples.
        """
        results = [
            self.test_implements_abc(),
            self.test_check_availability(),
            self.test_invoke_returns_response(),
            self.test_streaming_fallback(),
            self.test_error_handling(),
        ]
        passed = sum(1 for _, p, _ in results if p)
        total = len(results)
        logger.info(
            "ProviderTestHarness: %d/%d tests passed for %s",
            passed, total, self.provider.provider_name,
        )
        return results
