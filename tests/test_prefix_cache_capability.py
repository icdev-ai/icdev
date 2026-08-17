# [TEMPLATE: CUI // SP-CTI]
"""Provider-declared prefix-cache capability (cch-cap-01).

`LLMRequest.cache_control = "ephemeral"` is Anthropic's wire format, and the
router used to stamp it on the provider-neutral request for every configured
canvas and function — so three of the eight providers would have had to ignore
a foreign vendor's vocabulary by design.

These tests pin the replacement shape:

- every provider adapter DECLARES a support level with a written reason;
- the router sets only the neutral `cache_prefix` intent and never the vendor
  field (asserted against the router's own source, not just its behaviour);
- the translation happens against the declared capability once the provider is
  known — Anthropic and Bedrock end up in exactly the state they were in
  before, and nobody else is handed `cache_control` at all.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Imported unconditionally on purpose: none of this needs a vendor SDK, a key or
# a network, so an ImportError here is a real failure. A try/except that
# degraded to pytest.skip would report "measured" for a suite that asserted
# nothing.
from tools.llm.provider import (  # noqa: E402
    CACHE_CONTROL_EPHEMERAL,
    PREFIX_CACHE_AUTOMATIC,
    PREFIX_CACHE_EXPLICIT,
    PREFIX_CACHE_LOCAL,
    PREFIX_CACHE_MANAGED_OBJECT,
    PREFIX_CACHE_NONE,
    PREFIX_CACHE_SUPPORT_LEVELS,
    LLMRequest,
    PrefixCacheCapability,
    apply_prefix_cache,
    resolve_prefix_cache_capability,
    wants_prefix_cache,
)
from tools.llm.router import LLMRouter  # noqa: E402


def _providers() -> dict:
    """Instantiate one adapter per provider named in the assessment's table.

    Constructed directly rather than through the router so the declaration is
    tested without credentials, network, or vendor SDKs — every adapter's
    __init__ is inert and the capability is a pure property.
    """
    from tools.llm.anthropic_provider import AnthropicLLMProvider
    from tools.llm.azure_openai_provider import AzureOpenAIProvider
    from tools.llm.bedrock_provider import BedrockLLMProvider
    from tools.llm.gemini_provider import GeminiProvider
    from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider
    from tools.llm.oci_genai_provider import OCIGenAIProvider
    from tools.llm.ollama_provider import OllamaProvider
    from tools.llm.openai_provider import OpenAICompatibleProvider

    return {
        "anthropic": AnthropicLLMProvider(),
        "bedrock": BedrockLLMProvider(region="us-gov-west-1"),
        "openai": OpenAICompatibleProvider(provider_label="openai"),
        "azure_openai": AzureOpenAIProvider(endpoint="https://example.openai.azure.us"),
        "gemini": GeminiProvider(),
        "ollama": OllamaProvider(base_url="http://localhost:11434"),
        "ibm_watsonx": IBMWatsonxProvider(),
        "oci_genai": OCIGenAIProvider(),
    }


#: The level each of the eight providers must declare, per the 2026-08-16
#: assessment (docs/research/prefix-caching-assessment.md section 1).
EXPECTED_SUPPORT = {
    "anthropic": PREFIX_CACHE_EXPLICIT,
    "bedrock": PREFIX_CACHE_EXPLICIT,
    "openai": PREFIX_CACHE_AUTOMATIC,
    "azure_openai": PREFIX_CACHE_AUTOMATIC,
    "gemini": PREFIX_CACHE_MANAGED_OBJECT,
    "ollama": PREFIX_CACHE_LOCAL,
    # cch-prov-04 checked the two the assessment had left unverified. watsonx
    # stayed 'none'; OCI did NOT — its usage object carries a cached-token
    # counter, which is the automatic shape, so the placeholder was wrong.
    "ibm_watsonx": PREFIX_CACHE_NONE,
    "oci_genai": PREFIX_CACHE_AUTOMATIC,
}


# ---------------------------------------------------------------------------
# 1. Every provider declares
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(EXPECTED_SUPPORT))
def test_provider_declares_a_valid_support_level(name):
    """All eight providers answer with a level from the closed vocabulary."""
    cap = _providers()[name].prefix_cache_capability
    assert isinstance(cap, PrefixCacheCapability)
    assert cap.support in PREFIX_CACHE_SUPPORT_LEVELS
    assert cap.support == EXPECTED_SUPPORT[name], (
        f"{name} declares {cap.support!r}, expected {EXPECTED_SUPPORT[name]!r} — "
        "change the assessment table too if the vendor's behaviour really changed"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_SUPPORT))
def test_declaration_carries_a_written_reason(name):
    """'none' and 'local' are first-class answers — but only WITH a reason.

    An undocumented 'none' cannot be told apart from a question nobody asked,
    which is the whole failure mode this declaration exists to prevent.
    """
    cap = _providers()[name].prefix_cache_capability
    assert len(cap.reason.strip()) >= 40, (
        f"{name}'s prefix-cache reason is too thin to be evidence: {cap.reason!r}"
    )


def test_unverified_is_distinct_from_verified_none():
    """No SHIPPING adapter is left saying 'nobody looked' (cch-prov-04).

    The distinction itself still has to hold — an unlisted OpenAI-compatible
    label and a hosted Ollama endpoint are genuinely unchecked, and say so
    below. What cch-prov-04 removes is the two named vendors that were
    carrying `verified=False` as a placeholder.
    """
    unverified = {
        name
        for name, p in _providers().items()
        if p.prefix_cache_capability.verified is False
    }
    assert unverified == set(), (
        f"{sorted(unverified)} still declare 'not checked'. Check the vendor and "
        "record the date and citation, or say so in the assessment — do not "
        "leave a named provider unasked."
    )


def test_reports_cache_tokens_matches_the_adapters_that_read_them():
    """Only the adapters that read a cached-token counter claim to.

    Gemini joined them in cch-prov-02, which implemented the cachedContents
    lifecycle and reads usageMetadata.cachedContentTokenCount back into the
    shared field.
    """
    reporting = {
        name: p.prefix_cache_capability.reports_cache_tokens
        for name, p in _providers().items()
    }
    assert reporting == {
        "anthropic": True,
        "bedrock": True,
        "openai": True,
        "azure_openai": True,
        # cch-prov-04: OCI reports `Usage.prompt_tokens_details.cached_tokens`
        # and the adapter now reads it.
        "oci_genai": True,
        "gemini": True,
        "ollama": False,
        "ibm_watsonx": False,
    }


@pytest.mark.parametrize("name", ["ibm_watsonx", "oci_genai"])
def test_the_two_verified_vendors_cite_a_date(name):
    """A 'none' without a date is a stale assumption dressed as a finding.

    cch-prov-04's whole point: the next reader must be able to tell WHEN the
    vendor was checked, so they know whether to re-check rather than guess.
    """
    reason = _providers()[name].prefix_cache_capability.reason
    assert "2026-08-16" in reason, f"{name}'s declaration carries no check date"
    assert "cch-prov-04" in reason, f"{name}'s declaration cites no verification record"


def test_capability_rejects_an_invented_level_and_an_empty_reason():
    with pytest.raises(ValueError):
        PrefixCacheCapability(support="ephemeral", reason="Anthropic's wire value is not a level")
    with pytest.raises(ValueError):
        PrefixCacheCapability(support=PREFIX_CACHE_NONE, reason="   ")


def test_an_adapter_that_declares_nothing_answers_none_unverified():
    """The default is honest, not inherited from whoever it resembles."""

    class _Undeclared:
        pass

    cap = resolve_prefix_cache_capability(_Undeclared())
    assert cap.support == PREFIX_CACHE_NONE
    assert cap.verified is False


def test_openai_compatible_label_decides_the_answer():
    """One class serves api.openai.com and a local vLLM; they differ."""
    from tools.llm.openai_provider import OpenAICompatibleProvider

    assert (
        OpenAICompatibleProvider(provider_label="vllm").prefix_cache_capability.support
        == PREFIX_CACHE_LOCAL
    )
    unmeasured = OpenAICompatibleProvider(provider_label="some-new-endpoint")
    assert unmeasured.prefix_cache_capability.support == PREFIX_CACHE_NONE
    assert unmeasured.prefix_cache_capability.verified is False


def test_hosted_ollama_does_not_inherit_the_local_claim():
    """'latency only, nothing to bill' is false for a billed endpoint."""
    from tools.llm.ollama_provider import OllamaProvider

    cap = OllamaProvider(base_url="https://ollama.com").prefix_cache_capability
    assert cap.support == PREFIX_CACHE_NONE
    assert cap.verified is False


# ---------------------------------------------------------------------------
# 2. The router no longer sets the vendor field
# ---------------------------------------------------------------------------
def test_router_source_never_assigns_cache_control():
    """The acceptance criterion, asserted structurally.

    A behavioural check only covers the paths the test happens to walk; this
    parses the router and fails on ANY assignment to `.cache_control`, which is
    the thing that must not come back.
    """
    source = Path(inspect.getsourcefile(LLMRouter)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "cache_control":
                offenders.append(target.lineno)
    assert not offenders, (
        "router.py assigns cache_control at line(s) "
        f"{offenders} — set the neutral cache_prefix intent instead and let "
        "apply_prefix_cache translate it for the provider that was chosen"
    )


def test_apply_context_cache_sets_only_the_neutral_intent():
    router = LLMRouter()
    req = LLMRequest(messages=[{"role": "user", "content": "hi"}], system_prompt="s")
    router._apply_context_cache("code_generation", req)
    assert req.cache_prefix is True
    assert req.cache_control == ""


# ---------------------------------------------------------------------------
# 3. The router consults the capability
# ---------------------------------------------------------------------------
def _hinted() -> LLMRequest:
    return LLMRequest(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="stable prefix",
        cache_prefix=True,
    )


@pytest.mark.parametrize("name", ["anthropic", "bedrock"])
def test_explicit_providers_still_receive_the_ephemeral_marker(name):
    """Behaviour for Anthropic and Bedrock is unchanged: same field, same value."""
    out = apply_prefix_cache(_providers()[name], _hinted())
    assert out.cache_control == CACHE_CONTROL_EPHEMERAL


@pytest.mark.parametrize(
    "name", ["openai", "azure_openai", "gemini", "ollama", "ibm_watsonx", "oci_genai"]
)
def test_every_other_provider_is_handed_no_vendor_field(name):
    """automatic / managed_object / local / none: nothing to set, by design."""
    out = apply_prefix_cache(_providers()[name], _hinted())
    assert out.cache_control == ""
    assert out.cache_prefix is True, "the neutral intent survives for the provider to read"


def test_a_foreign_marker_is_stripped_on_fallback_to_another_vendor():
    """An Anthropic-marked request that falls through to Gemini loses the marker."""
    providers = _providers()
    marked = apply_prefix_cache(providers["anthropic"], _hinted())
    assert marked.cache_control == CACHE_CONTROL_EPHEMERAL

    fell_through = apply_prefix_cache(providers["gemini"], marked)
    assert fell_through.cache_control == ""
    assert marked.cache_control == CACHE_CONTROL_EPHEMERAL, (
        "apply_prefix_cache must copy, never mutate — the same request object is "
        "retried down the fallback chain"
    )


def test_no_hint_means_no_change_at_all():
    req = LLMRequest(messages=[{"role": "user", "content": "hi"}])
    assert apply_prefix_cache(_providers()["anthropic"], req) is req
    assert req.cache_control == ""


def test_a_caller_set_marker_still_counts_as_wanting_the_cache():
    """Callers predating cch-cap-01 (and the BDD suite) keep working."""
    legacy = LLMRequest(
        messages=[{"role": "user", "content": "hi"}],
        cache_control=CACHE_CONTROL_EPHEMERAL,
    )
    assert wants_prefix_cache(legacy) is True
    assert apply_prefix_cache(_providers()["anthropic"], legacy) is legacy


def _router_with_rag() -> LLMRouter:
    """A router wired for RAG injection + context cache, with no I/O."""
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
            "per_function": {"code_generation": {"context_cache": True}},
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


def _augment(router: LLMRouter, request: LLMRequest) -> LLMRequest:
    chunk = MagicMock()
    chunk.content = "NIST AC-3 enforces access control policies."
    chunk.source_type = "nist"
    chunk.source_id = "ac-3"
    chunk.final_score = 0.88
    retriever = MagicMock()
    retriever.search.return_value = [chunk]
    with patch("tools.rag.retriever.RAGRetriever", return_value=retriever):
        return router._rag_augment(request, "code_generation")


def test_rag_breakpoint_marker_follows_the_neutral_intent():
    """The RAG splitter runs BEFORE the provider is known, so it reads the intent.

    It cannot read a vendor field the router no longer sets — and the marker is
    what the explicit providers later split their system blocks on, so losing it
    would silently drop Anthropic's multi-breakpoint caching.
    """
    router = _router_with_rag()
    hinted = _augment(router, _hinted())
    assert "[RELEVANT CONTEXT" in hinted.system_prompt
    assert "<!-- cache_breakpoint -->" in hinted.system_prompt
    assert hinted.cache_prefix is True

    plain = _augment(
        router,
        LLMRequest(messages=[{"role": "user", "content": "hi"}], system_prompt="stable prefix"),
    )
    assert "<!-- cache_breakpoint -->" not in plain.system_prompt


def test_anthropic_payload_is_byte_for_byte_what_it_was_before():
    """The end state for Anthropic is unchanged: neutral intent in, same blocks out.

    Builds the request the OLD way (caller stamps cache_control) and the NEW way
    (neutral intent, translated by the capability), and compares what the
    adapter would put on the wire.
    """
    provider = _providers()["anthropic"]
    system = "static preamble\n<!-- cache_breakpoint -->\nvolatile tail"

    old = LLMRequest(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt=system,
        cache_control=CACHE_CONTROL_EPHEMERAL,
    )
    new = apply_prefix_cache(
        provider,
        LLMRequest(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt=system,
            cache_prefix=True,
        ),
    )

    captured = []

    class _Messages:
        def create(self, **kwargs):
            captured.append(kwargs)
            raise _Stop()

    class _Stop(Exception):
        pass

    client = MagicMock()
    client.messages = _Messages()
    with patch.object(provider, "_get_client", return_value=client):
        for request in (old, new):
            with pytest.raises(Exception):
                provider.invoke(request, "claude-sonnet-4", {})

    assert len(captured) == 2
    assert captured[0] == captured[1], "the neutral path must reach the wire identically"

    # The block split itself only happens when the anthropic SDK is importable
    # (the adapter guards on HAS_ANTHROPIC). Assert whichever shape this
    # environment produces rather than skipping — a skipped test asserts nothing.
    from tools.llm import anthropic_provider as ap

    if ap.HAS_ANTHROPIC:
        assert captured[0]["system"] == [
            {"type": "text", "text": "static preamble\n", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "\nvolatile tail"},
        ]
    else:
        assert captured[0]["system"] == system


# ---------------------------------------------------------------------------
# 5. OCI reports cached tokens into the shared field (cch-prov-04)
# ---------------------------------------------------------------------------
class _FakeUsage:
    """The shape of oci.generative_ai_inference.models.Usage."""

    def __init__(self, prompt_tokens=0, completion_tokens=0, cached_tokens=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.prompt_tokens_details = (
            None if cached_tokens is None else _FakePromptTokensDetails(cached_tokens)
        )


class _FakePromptTokensDetails:
    def __init__(self, cached_tokens):
        self.cached_tokens = cached_tokens


class _FakeChatResult:
    """ChatResult: model_id, model_version, chat_response. NO model_usage."""

    def __init__(self, chat_response):
        self.model_id = "cohere.command-r-08-2024"
        self.model_version = "1.0"
        self.chat_response = chat_response


def _oci_response(usage):
    from tools.llm.provider import LLMResponse
    from tools.llm.oci_genai_provider import _read_usage_into

    chat_response = MagicMock(spec=["usage", "text", "finish_reason"])
    chat_response.usage = usage
    result = _FakeChatResult(chat_response)
    resp = LLMResponse(provider="oci_genai")
    _read_usage_into(chat_response, result, resp)
    return resp


def test_oci_reads_cached_tokens_into_the_shared_field():
    """The reporting half: cachedTokens lands in cache_read_input_tokens."""
    resp = _oci_response(_FakeUsage(prompt_tokens=2048, completion_tokens=64, cached_tokens=1536))
    assert resp.input_tokens == 2048
    assert resp.output_tokens == 64
    assert resp.cache_read_input_tokens == 1536
    # OCI reports reads only; there is no cache-creation counter to read.
    assert resp.cache_creation_input_tokens == 0


def test_oci_usage_comes_off_the_chat_response_not_the_chat_result():
    """ChatResult has no model_usage, so the old read reported zero forever.

    Guards the regression directly: a ChatResult whose chat_response carries
    usage must yield non-zero tokens even though `model_usage` is absent.
    """
    resp = _oci_response(_FakeUsage(prompt_tokens=300, completion_tokens=12))
    assert (resp.input_tokens, resp.output_tokens) == (300, 12)


def test_oci_absent_cache_details_is_zero_not_an_error():
    """A model that caches nothing returns no details block; that is 0, not a crash."""
    resp = _oci_response(_FakeUsage(prompt_tokens=300, completion_tokens=12))
    assert resp.cache_read_input_tokens == 0


def test_oci_falls_back_to_the_legacy_attribute_when_the_response_has_no_usage():
    """Kept deliberately: the oci SDK is not importable in this suite."""
    from tools.llm.provider import LLMResponse
    from tools.llm.oci_genai_provider import _read_usage_into

    chat_response = MagicMock(spec=["text"])  # no .usage at all
    result = _FakeChatResult(chat_response)
    result.model_usage = _FakeUsage(prompt_tokens=77, completion_tokens=3, cached_tokens=64)
    resp = LLMResponse(provider="oci_genai")
    _read_usage_into(chat_response, result, resp)
    assert (resp.input_tokens, resp.output_tokens, resp.cache_read_input_tokens) == (77, 3, 64)


def test_oci_no_usage_anywhere_leaves_the_counters_untouched():
    from tools.llm.provider import LLMResponse
    from tools.llm.oci_genai_provider import _read_usage_into

    chat_response = MagicMock(spec=["text"])
    result = MagicMock(spec=["chat_response"])
    resp = LLMResponse(provider="oci_genai")
    _read_usage_into(chat_response, result, resp)
    assert (resp.input_tokens, resp.output_tokens, resp.cache_read_input_tokens) == (0, 0, 0)
