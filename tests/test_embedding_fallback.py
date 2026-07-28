#!/usr/bin/env python3
"""Falling back to a local embedding provider must be FAST, not merely correct.

The chain `[openai-embed, gemini-embed, nomic-embed-local]` already fell through
to the local provider when the cloud ones failed — that part worked. What it did
not do was remember. Every cold process re-probed both cloud providers first.

Measured on a machine with an invalid OpenAI key: ~12s of failing round-trips
ahead of a 0.06s local embed, paid again by every dashboard restart, CLI
invocation and worker process. Air-gapped it is worse than slow — the OpenAI SDK
defaults to a 600s timeout with retries, so an unreachable host stalls the
caller for minutes before the local provider is ever reached.

Two properties are pinned here:

  * a provider that failed a probe is SKIPPED while the record is fresh, and
  * the record EXPIRES, so a repaired credential recovers without intervention.
"""
from __future__ import annotations

import time

import pytest

from tools.llm.router import LLMRouter


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Keep the persisted circuit-breaker file out of the real data/ dir."""
    monkeypatch.setattr(
        LLMRouter, "_embedding_state_path",
        classmethod(lambda cls: tmp_path / "llm_embedding_unavailable.json"),
    )
    monkeypatch.setattr(LLMRouter, "_embedding_unavailable_at", {})
    yield
    LLMRouter._embedding_unavailable_at = {}


# --------------------------------------------------------------------------- #
# Marking and skipping
# --------------------------------------------------------------------------- #


def test_a_fresh_provider_is_not_skipped():
    assert LLMRouter._embedding_recently_failed("openai-embed") is False


def test_a_failed_provider_is_skipped():
    LLMRouter._mark_embedding_unavailable("openai-embed")
    assert LLMRouter._embedding_recently_failed("openai-embed") is True


def test_marking_one_provider_does_not_skip_the_others():
    """The local provider must stay reachable — that is the whole point."""
    LLMRouter._mark_embedding_unavailable("openai-embed")
    assert LLMRouter._embedding_recently_failed("nomic-embed-local") is False
    assert LLMRouter._embedding_recently_failed("gemini-embed") is False


# --------------------------------------------------------------------------- #
# Expiry — a permanent mark would make a fixed key look like it changed nothing
# --------------------------------------------------------------------------- #


def test_the_mark_expires():
    stale = time.time() - (LLMRouter._EMBEDDING_UNAVAILABLE_TTL_SECONDS + 60)
    LLMRouter._embedding_unavailable_at["openai-embed"] = stale
    assert LLMRouter._embedding_recently_failed("openai-embed") is False


def test_expiry_forgets_the_entry_rather_than_re_checking_forever():
    stale = time.time() - (LLMRouter._EMBEDDING_UNAVAILABLE_TTL_SECONDS + 60)
    LLMRouter._embedding_unavailable_at["openai-embed"] = stale
    LLMRouter._embedding_recently_failed("openai-embed")
    assert "openai-embed" not in LLMRouter._embedding_unavailable_at


def test_reset_clears_every_mark():
    """For a settings page that just saved a new key and should not wait out TTL."""
    LLMRouter._mark_embedding_unavailable("openai-embed")
    LLMRouter._mark_embedding_unavailable("gemini-embed")
    LLMRouter.reset_embedding_availability()
    assert LLMRouter._embedding_unavailable_at == {}


# --------------------------------------------------------------------------- #
# Persistence — the per-process cost is what actually hurt
# --------------------------------------------------------------------------- #


def test_marks_survive_a_process_restart():
    """In-process caching alone would not help: the cost is paid at COLD start."""
    LLMRouter._mark_embedding_unavailable("openai-embed")
    LLMRouter._embedding_unavailable_at = {}          # simulate a new process
    LLMRouter._load_embedding_unavailable()
    assert LLMRouter._embedding_recently_failed("openai-embed") is True


def test_a_missing_state_file_is_not_an_error():
    LLMRouter._embedding_unavailable_at = {}
    LLMRouter._load_embedding_unavailable()
    assert LLMRouter._embedding_unavailable_at == {}


def test_a_corrupt_state_file_degrades_to_empty():
    """State is an optimisation. It must never become a gate on embedding at all."""
    path = LLMRouter._embedding_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    LLMRouter._embedding_unavailable_at = {}
    LLMRouter._load_embedding_unavailable()
    assert LLMRouter._embedding_unavailable_at == {}


# --------------------------------------------------------------------------- #
# The probe timeout
# --------------------------------------------------------------------------- #


def test_probe_timeout_is_bounded_and_short():
    """An unbounded probe turns a working local fallback into an apparent hang."""
    from tools.llm.embedding_provider import _PROBE_TIMEOUT_SECONDS

    assert 0 < _PROBE_TIMEOUT_SECONDS <= 30


def test_probe_uses_its_own_client_not_the_embedding_client():
    """Real embed calls keep the SDK defaults — a big batch legitimately runs long."""
    import inspect

    from tools.llm.embedding_provider import OpenAIEmbeddingProvider

    probe = inspect.getsource(OpenAIEmbeddingProvider.check_availability)
    assert "max_retries" in probe and "timeout" in probe
    embed = inspect.getsource(OpenAIEmbeddingProvider.embed)
    assert "timeout" not in embed, "probe timeout must not leak onto real calls"
