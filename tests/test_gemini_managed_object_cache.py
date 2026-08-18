# [TEMPLATE: CUI // SP-CTI]
"""Gemini explicit caching as the ``managed_object`` capability (cch-prov-02).

cch-cap-01 needed a fifth support level because Gemini's caching is neither
"mark the request" (Anthropic) nor "just read the number" (OpenAI): it is a
STORED OBJECT with its own identity, TTL and lifecycle. These tests pin that
lifecycle — create, reuse, expire — and, just as hard, the economics gate in
front of it.

The gate is the part worth testing most. A stored cache is billed per token per
HOUR of storage whether or not anything reads it, so an object created for a
prefix used once is strictly worse than no caching: it pays rent and saves
nothing, silently, with the only trace on the invoice. Hence:

* default OFF everywhere, including in the shipped config;
* nothing stored below ``min_prefix_tokens``;
* nothing stored on a prefix's FIRST sighting.

And the acceptance from the card: an object is created and reused across calls
sharing a prefix, expires as configured, and the tokens land in the shared
``cache_read_input_tokens`` field like every other provider's.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from typing import Any, Dict, List, Optional

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Imported unconditionally: none of this needs the Gemini SDK, a key or a
# network. A try/except degrading to pytest.skip would report "measured" for a
# suite that asserted nothing.
from tools.llm import gemini_provider as gp  # noqa: E402
from tools.llm.managed_cache import (  # noqa: E402
    CACHE_ACTIONS,
    CACHE_AT_CAPACITY,
    CACHE_CREATE,
    CACHE_DISABLED,
    CACHE_FIRST_SIGHTING,
    CACHE_REUSE,
    CACHE_SUPPRESSED,
    CACHE_TOO_SMALL,
    ManagedCacheConfig,
    ManagedPrefixCache,
)
from tools.llm.provider import (  # noqa: E402
    PREFIX_CACHE_MANAGED_OBJECT,
    LLMRequest,
    apply_prefix_cache,
)

# A prefix comfortably over the 4096-token default floor at chars/4.
BIG_PREFIX = "You are a compliance analyst. " * 800
SMALL_PREFIX = "You are helpful."


# ---------------------------------------------------------------------------
# A controllable clock. `managed_cache` does `import time` and calls
# time.monotonic(), so swapping the module's `time` binding moves only this
# module's clock — patching the real time module would move everyone's.
# ---------------------------------------------------------------------------
class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    # Resolved from the CLASS, not by import name. `tools.llm.*` is a shim over
    # `icdev.tools.llm.*` and the two are distinct module objects; patching the
    # wrong one leaves the code under test on the real clock.
    mc = sys.modules[ManagedPrefixCache.__module__]
    c = _Clock()
    monkeypatch.setattr(mc, "time", c)
    return c


def _cache(**overrides) -> ManagedPrefixCache:
    cfg = ManagedCacheConfig(enabled=True, **overrides)
    return ManagedPrefixCache(cfg)


# ===========================================================================
# The economics gate
# ===========================================================================
def test_the_default_is_off_and_the_shipped_config_agrees():
    """Off in code AND off in args/llm_config.yaml.

    A standing per-hour cost that a deployment inherits without asking for it is
    the failure this default exists to prevent, so both halves are asserted —
    a default of False in the dataclass means nothing if the shipped YAML
    switches it on.
    """
    assert ManagedCacheConfig().enabled is False

    shipped = yaml.safe_load((_ROOT / "args" / "llm_config.yaml").read_text(encoding="utf-8"))
    block = shipped.get("managed_object_cache")
    assert block is not None, "managed_object_cache block missing from llm_config.yaml"
    assert block["enabled"] is False, "shipped config must not switch on a standing cost"

    decision = ManagedPrefixCache(ManagedCacheConfig.from_config(shipped)).decide(
        "models/gemini-2.5-flash", BIG_PREFIX
    )
    assert decision.action == CACHE_DISABLED
    assert not decision.uses_object


def test_a_prefix_below_the_floor_is_never_stored():
    """Below the size floor a stored object pays rent to save nothing."""
    cache = _cache()
    for _ in range(5):
        decision = cache.decide("models/gemini-2.5-flash", SMALL_PREFIX)
        assert decision.action == CACHE_TOO_SMALL
        assert not decision.uses_object
    # The measured size is IN the reason — "too small" with no number is not
    # evidence, it is an assertion.
    assert str(decision.estimated_tokens) in decision.reason
    assert cache.stats()["live_objects"] == 0


def test_the_first_sighting_of_a_prefix_creates_nothing(clock):
    """The single most expensive mistake this feature can make.

    A prefix seen once and never again costs strictly more stored than not
    stored. So the first call records the sighting and stores nothing.
    """
    cache = _cache()
    first = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    assert first.action == CACHE_FIRST_SIGHTING
    assert not first.uses_object
    assert first.sightings == 1
    assert cache.stats()["live_objects"] == 0


def test_the_second_sighting_inside_the_window_creates(clock):
    cache = _cache()
    cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    clock.advance(30)
    second = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    assert second.action == CACHE_CREATE
    assert second.uses_object
    assert second.sightings == 2
    assert second.estimated_tokens >= ManagedCacheConfig().min_prefix_tokens


def test_a_sighting_that_ages_out_does_not_count_toward_repetition(clock):
    """Two calls a day apart are not a repeated prefix.

    The window is the TTL: outside it the object would already have expired, so
    the second call would be creating a fresh object with no reader in sight —
    the single-use case again, wearing a longer timescale.
    """
    cache = _cache(ttl_seconds=300)
    assert cache.decide("models/gemini-2.5-flash", BIG_PREFIX).action == CACHE_FIRST_SIGHTING
    clock.advance(301)
    assert cache.decide("models/gemini-2.5-flash", BIG_PREFIX).action == CACHE_FIRST_SIGHTING


def test_capacity_ceiling_refuses_rather_than_renting_without_limit(clock):
    cache = _cache(max_objects=1)
    for prefix in (BIG_PREFIX, BIG_PREFIX + "second"):
        cache.decide("models/gemini-2.5-flash", prefix)  # first sighting
    d1 = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    assert d1.action == CACHE_CREATE
    cache.record_object(d1.fingerprint, object(), token_count=5000)

    d2 = cache.decide("models/gemini-2.5-flash", BIG_PREFIX + "second")
    assert d2.action == CACHE_AT_CAPACITY
    assert not d2.uses_object


def test_a_failed_create_is_suppressed_instead_of_retried_every_call(clock):
    cache = _cache(failure_cooldown_seconds=600)
    cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    decision = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    assert decision.action == CACHE_CREATE

    cache.note_failure(decision.fingerprint)
    assert cache.decide("models/gemini-2.5-flash", BIG_PREFIX).action == CACHE_SUPPRESSED

    clock.advance(601)
    assert cache.decide("models/gemini-2.5-flash", BIG_PREFIX).action != CACHE_SUPPRESSED


def test_every_outcome_is_a_named_action_not_a_bool():
    """Seven causes, seven names.

    "Caching is off", "the prefix is 200 tokens", "we have seen it once" and
    "the vendor refused" all produce zero cached tokens and each sends you
    somewhere different. Collapsing them to a bool is how a zero metric becomes
    unreadable.
    """
    assert len(CACHE_ACTIONS) == 7
    for action in (
        CACHE_DISABLED, CACHE_TOO_SMALL, CACHE_FIRST_SIGHTING,
        CACHE_CREATE, CACHE_REUSE, CACHE_AT_CAPACITY, CACHE_SUPPRESSED,
    ):
        assert action in CACHE_ACTIONS


def test_a_malformed_config_falls_back_to_off_not_to_caching_everything():
    for block in ({"enabled": "yes", "ttl_seconds": "banana"}, {"min_prefix_tokens": -1}, []):
        cfg = ManagedCacheConfig.from_config({"managed_object_cache": block})
        assert cfg.ttl_seconds >= 1
        assert cfg.min_prefix_tokens >= 1
    assert ManagedCacheConfig.from_config({}).enabled is False
    assert ManagedCacheConfig.from_config(None).enabled is False


# ===========================================================================
# Lifecycle: create → reuse → expire
# ===========================================================================
def test_a_live_object_is_reused_and_the_handle_comes_back(clock):
    cache = _cache()
    cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    created = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    handle = object()
    assert cache.record_object(created.fingerprint, handle, token_count=6000) is True

    for expected_read in (1, 2, 3):
        reuse = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
        assert reuse.action == CACHE_REUSE
        assert reuse.handle is handle
        assert f"read #{expected_read}" in reuse.reason


def test_the_object_expires_as_configured(clock):
    cache = _cache(ttl_seconds=300)
    cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    created = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    handle = object()
    cache.record_object(created.fingerprint, handle)

    clock.advance(299)
    assert cache.decide("models/gemini-2.5-flash", BIG_PREFIX).action == CACHE_REUSE

    clock.advance(2)  # past the TTL
    assert cache.stats()["live_objects"] == 0
    after = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    assert after.action != CACHE_REUSE
    assert after.handle is None


def test_expired_handles_are_returned_so_they_can_be_released(clock):
    cache = _cache(ttl_seconds=300)
    cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    created = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    handle = object()
    cache.record_object(created.fingerprint, handle)

    assert cache.expired_handles() == []
    clock.advance(301)
    assert cache.expired_handles() == [handle]
    assert cache.expired_handles() == []


def test_a_lost_create_race_is_told_to_release_its_object(clock):
    """Two threads can both reach CREATE; only one object may be kept.

    The loser must be released, not forgotten — a forgotten object rents for its
    whole TTL with nothing able to reference it.
    """
    cache = _cache()
    cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    created = cache.decide("models/gemini-2.5-flash", BIG_PREFIX)
    assert cache.record_object(created.fingerprint, object()) is True
    assert cache.record_object(created.fingerprint, object()) is False


def test_the_fingerprint_is_model_scoped_and_cannot_be_confused_by_joining():
    """A cache object belongs to one model, and parts are NUL-joined.

    ("ab", "c") and ("a", "bc") are different prefixes; concatenating without a
    separator would give them the same key and serve one surface's cache to
    another.
    """
    cache = _cache()
    assert cache.fingerprint("models/a", "sys") != cache.fingerprint("models/b", "sys")
    assert cache.fingerprint("m", "ab", "c") != cache.fingerprint("m", "a", "bc")
    assert cache.fingerprint("m", "sys") == cache.fingerprint("m", "sys")


# ===========================================================================
# A fake Gemini SDK — enough surface for the lifecycle, no vendor package.
# ===========================================================================
class _FakeUsage:
    def __init__(self, prompt=0, candidates=0, cached=0, total=0):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.cached_content_token_count = cached
        self.total_token_count = total


class _FakePart:
    def __init__(self, text):
        self.text = text


class _FakeContent:
    def __init__(self, text):
        self.parts = [_FakePart(text)]


class _FakeCandidate:
    def __init__(self, text):
        self.content = _FakeContent(text)
        self.finish_reason = None


class _FakeResponse:
    def __init__(self, text, usage):
        self.candidates = [_FakeCandidate(text)]
        self.usage_metadata = usage


class _FakeCachedContent:
    """A stored cachedContents object."""

    created: List["_FakeCachedContent"] = []
    create_error: Optional[Exception] = None

    def __init__(self, kwargs: Dict[str, Any]):
        self.kwargs = kwargs
        self.deleted = False
        self.usage_metadata = _FakeUsage(total=5000)

    @classmethod
    def create(cls, **kwargs):
        if cls.create_error is not None:
            raise cls.create_error
        obj = cls(kwargs)
        cls.created.append(obj)
        return obj

    def delete(self):
        self.deleted = True


class _FakeModel:
    """A GenerativeModel, cache-bound or not."""

    calls: List["_FakeModel"] = []
    fail_when_cached: Optional[Exception] = None

    def __init__(self, model_name=None, generation_config=None, cached_content=None, **kwargs):
        self.model_name = model_name
        self.generation_config = generation_config
        self.cached_content = cached_content
        self.kwargs = kwargs
        self.generated = 0

    @classmethod
    def from_cached_content(cls, cached_content=None, generation_config=None):
        return cls(cached_content=cached_content, generation_config=generation_config)

    def generate_content(self, messages, stream=False):
        self.generated += 1
        _FakeModel.calls.append(self)
        if self.cached_content is not None:
            if _FakeModel.fail_when_cached is not None:
                raise _FakeModel.fail_when_cached
            return _FakeResponse("cached answer", _FakeUsage(prompt=6000, candidates=10, cached=5000))
        return _FakeResponse("plain answer", _FakeUsage(prompt=6000, candidates=10, cached=0))


class _FakeCachingModule:
    CachedContent = _FakeCachedContent


class _FakeGenai:
    caching = _FakeCachingModule
    GenerativeModel = _FakeModel

    @staticmethod
    def configure(api_key=None):
        return None


@pytest.fixture
def fake_gemini(monkeypatch):
    _FakeCachedContent.created = []
    _FakeCachedContent.create_error = None
    _FakeModel.calls = []
    _FakeModel.fail_when_cached = None
    monkeypatch.setattr(gp, "genai", _FakeGenai)
    monkeypatch.setattr(gp, "HAS_GEMINI", True)
    return _FakeGenai


def _provider(clock, **cfg) -> gp.GeminiProvider:
    return gp.GeminiProvider(api_key="k", prefix_cache=_cache(**cfg))


def _request(**kw) -> LLMRequest:
    return LLMRequest(
        messages=[{"role": "user", "content": "what changed?"}],
        system_prompt=BIG_PREFIX,
        cache_prefix=True,
        **kw,
    )


# ===========================================================================
# The adapter: acceptance
# ===========================================================================
def test_one_object_is_created_and_then_reused_across_calls(clock, fake_gemini):
    """The card's acceptance, end to end.

    Three calls sharing a prefix produce exactly ONE stored object: the first
    call is a sighting and goes uncached, the second creates, the third reuses.
    """
    provider = _provider(clock)
    for _ in range(3):
        provider.invoke(_request(), "gemini-2.5-flash", {})

    assert len(_FakeCachedContent.created) == 1, "a prefix must not create more than one object"
    obj = _FakeCachedContent.created[0]
    assert obj.kwargs["model"] == "models/gemini-2.5-flash"
    assert obj.kwargs["system_instruction"] == BIG_PREFIX
    assert obj.kwargs["ttl"].total_seconds() == provider.prefix_cache.config.ttl_seconds

    cached_calls = [m for m in _FakeModel.calls if m.cached_content is not None]
    assert len(cached_calls) == 2, "the create call and the reuse call both go through the object"


def test_the_cached_tokens_land_in_the_shared_field(clock, fake_gemini):
    """Same field every other provider reports into — no Gemini-shaped column."""
    provider = _provider(clock)
    first = provider.invoke(_request(), "gemini-2.5-flash", {})
    assert first.cache_read_input_tokens == 0, "uncached first sighting reports no read"
    assert first.cache_creation_input_tokens == 0

    created = provider.invoke(_request(), "gemini-2.5-flash", {})
    assert created.cache_creation_input_tokens == 5000, "the storage event is recorded"

    reused = provider.invoke(_request(), "gemini-2.5-flash", {})
    assert reused.cache_read_input_tokens == 5000
    assert reused.cache_creation_input_tokens == 0, "a read is never reported as a write"


def test_an_object_expires_and_the_next_call_starts_the_cycle_again(clock, fake_gemini):
    provider = _provider(clock, ttl_seconds=300)
    provider.invoke(_request(), "gemini-2.5-flash", {})
    provider.invoke(_request(), "gemini-2.5-flash", {})
    assert len(_FakeCachedContent.created) == 1

    clock.advance(301)
    resp = provider.invoke(_request(), "gemini-2.5-flash", {})
    assert resp.cache_read_input_tokens == 0, "the expired object cannot serve this call"
    assert len(_FakeCachedContent.created) == 1, "and a fresh sighting does not immediately re-create"


def test_a_different_prefix_gets_its_own_object(clock, fake_gemini):
    provider = _provider(clock)
    other = BIG_PREFIX + " Answer in French."
    for _ in range(2):
        provider.invoke(_request(), "gemini-2.5-flash", {})
        provider.invoke(
            LLMRequest(messages=[{"role": "user", "content": "q"}], system_prompt=other, cache_prefix=True),
            "gemini-2.5-flash",
            {},
        )
    assert len(_FakeCachedContent.created) == 2
    stored = {o.kwargs["system_instruction"] for o in _FakeCachedContent.created}
    assert stored == {BIG_PREFIX, other}


def test_without_the_neutral_intent_nothing_is_stored(clock, fake_gemini):
    """`cache_prefix` unset means the caller never asked. Nothing is inferred."""
    provider = _provider(clock)
    for _ in range(4):
        provider.invoke(
            LLMRequest(messages=[{"role": "user", "content": "q"}], system_prompt=BIG_PREFIX),
            "gemini-2.5-flash",
            {},
        )
    assert _FakeCachedContent.created == []


def test_a_small_prefix_never_reaches_the_vendor_at_all(clock, fake_gemini):
    provider = _provider(clock)
    for _ in range(4):
        provider.invoke(
            LLMRequest(
                messages=[{"role": "user", "content": "q"}],
                system_prompt=SMALL_PREFIX,
                cache_prefix=True,
            ),
            "gemini-2.5-flash",
            {},
        )
    assert _FakeCachedContent.created == []


# ===========================================================================
# The adapter: caching must never be the reason a call fails
# ===========================================================================
def test_a_failed_create_still_answers_and_then_backs_off(clock, fake_gemini):
    provider = _provider(clock)
    _FakeCachedContent.create_error = RuntimeError("cached content too small")

    provider.invoke(_request(), "gemini-2.5-flash", {})
    resp = provider.invoke(_request(), "gemini-2.5-flash", {})
    assert resp.content == "plain answer", "the call succeeds uncached"

    decision = provider.prefix_cache.decide(
        "models/gemini-2.5-flash", BIG_PREFIX, ""
    )
    assert decision.action == CACHE_SUPPRESSED, "and the prefix backs off instead of storming"


def test_a_handle_the_vendor_has_already_dropped_is_retried_uncached(clock, fake_gemini):
    """Our TTL bookkeeping and the vendor's can disagree.

    When it does, the request must still be answered — an optimisation that can
    fail a user's call is not an optimisation.
    """
    provider = _provider(clock)
    provider.invoke(_request(), "gemini-2.5-flash", {})
    provider.invoke(_request(), "gemini-2.5-flash", {})  # creates

    _FakeModel.fail_when_cached = RuntimeError("CachedContent not found")
    resp = provider.invoke(_request(), "gemini-2.5-flash", {})
    assert resp.content == "plain answer"
    assert resp.cache_read_input_tokens == 0
    assert provider.prefix_cache.stats()["live_objects"] == 0, "the dead handle is forgotten"


def test_a_real_api_error_without_caching_still_raises(clock, fake_gemini, monkeypatch):
    """The retry is scoped to the cached path; it must not swallow real errors."""
    provider = _provider(clock)

    def _boom(self, messages, stream=False):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(_FakeModel, "generate_content", _boom)
    with pytest.raises(RuntimeError, match="quota exceeded"):
        provider.invoke(_request(), "gemini-2.5-flash", {})


# ===========================================================================
# The neutral request stays neutral (the cch-cap-01 rule, one layer on)
# ===========================================================================
def test_the_capability_declares_managed_object_and_now_reports_tokens():
    cap = gp.GeminiProvider().prefix_cache_capability
    assert cap.support == PREFIX_CACHE_MANAGED_OBJECT
    assert cap.verified is True
    assert cap.reports_cache_tokens is True, "cch-prov-02 reads cachedContentTokenCount back"


def test_the_handle_never_lands_on_the_neutral_request(clock, fake_gemini):
    """A vendor handle on LLMRequest would be cch-cap-01's defect in new clothes.

    Checked two ways, because a behavioural check only covers the paths it
    walks: the request object is unchanged after a full create-and-reuse cycle,
    AND the adapter's source never assigns to a request attribute at all.
    """
    provider = _provider(clock)
    request = _request()
    before = dict(vars(request))
    for _ in range(3):
        provider.invoke(request, "gemini-2.5-flash", {})
    assert dict(vars(request)) == before

    source = (_ROOT / "tools" / "llm" / "gemini_provider.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "request"
    ]
    assert assigned == [], f"gemini_provider assigns to the neutral request: {assigned}"


def test_the_router_seam_still_hands_managed_object_no_vendor_field():
    """`apply_prefix_cache` has nothing to set for this level — by design."""
    request = LLMRequest(system_prompt=BIG_PREFIX, cache_prefix=True, cache_control="ephemeral")
    out = apply_prefix_cache(gp.GeminiProvider(), request)
    assert out.cache_control == "", "Anthropic's vocabulary must not reach Gemini's wire"
    assert out.cache_prefix is True
