#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/rag/entitlement_rag.py.

Covers:
  (1) test_five_step_pipeline_e2e        — mocked pipeline returns decision=PASS
  (2) test_ttl_index_expiry              — register UUID, advance clock, assert is_expired
  (3) test_ttl_purge_removes_expired     — purge_expired returns list of deleted UUIDs
  (4) test_uuid_grounding_enforcer_pass  — fully cited output returns PASS
  (5) test_uuid_grounding_enforcer_fail  — uncited sentences return FAIL
  (6) test_phantom_uuid_detection        — UUID not in authorized set => phantom => FAIL
  (7) test_stateless_no_persistent_index — no global vector index state between calls
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from tools.rag.entitlement_rag import (
    TTLIndex,
    run_entitlement_pipeline,
    tag_cells_with_uuids,
    verify_citation_coverage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _clock_factory(initial: float = 0.0):
    """Returns a mutable clock callable whose value is stored in a list."""
    t = [initial]

    def clock() -> float:
        return t[0]

    def advance(seconds: float) -> None:
        t[0] += seconds

    return clock, advance


# ---------------------------------------------------------------------------
# (1) Five-step pipeline E2E
# ---------------------------------------------------------------------------

class TestFiveStepPipelineE2E:
    def test_five_step_pipeline_e2e(self):
        """Full mocked pipeline returns grounding_report with decision='PASS'."""
        result_set = [{"user": "alice", "level": "SECRET"}]

        fake_translate = MagicMock(return_value="SELECT * FROM entitlements")
        fake_execute = MagicMock(return_value=result_set)

        def fake_synthesize(tagged_data, prompt):
            # Cite every UUID that was generated in tag_cells_with_uuids
            uuids = [v for row in tagged_data for k, v in row.items() if k.startswith("_uuid_")]
            return " ".join(f"Record shows [{u}]." for u in uuids)

        report = run_entitlement_pipeline(
            prompt="Who has SECRET access?",
            user_context={"role": "analyst"},
            user_auth_token="tok_test",
            _translate_fn=fake_translate,
            _execute_fn=fake_execute,
            _synthesize_fn=fake_synthesize,
        )

        assert report["decision"] == "PASS", f"Expected PASS, got: {report}"
        assert report["coverage_pct"] == 1.0
        assert report["phantom_uuids"] == []
        assert report["ungrounded"] == []
        fake_translate.assert_called_once()
        fake_execute.assert_called_once()

    def test_pipeline_passes_prompt_to_translate(self):
        """translate step receives the original prompt and user_context."""
        calls: list = []

        def fake_translate(prompt, ctx):
            calls.append((prompt, ctx))
            return "SELECT 1"

        def fake_execute(q, tok):
            return []

        def fake_synthesize(data, prompt):
            return ""

        run_entitlement_pipeline(
            prompt="test_prompt",
            user_context={"uid": 99},
            user_auth_token="tok",
            _translate_fn=fake_translate,
            _execute_fn=fake_execute,
            _synthesize_fn=fake_synthesize,
        )

        assert calls == [("test_prompt", {"uid": 99})]

    def test_pipeline_empty_result_set_passes(self):
        """Pipeline with no rows in result set still completes and returns PASS."""
        report = run_entitlement_pipeline(
            prompt="Q",
            user_context={},
            user_auth_token="tok",
            _translate_fn=lambda p, c: "SELECT 1",
            _execute_fn=lambda q, t: [],
            _synthesize_fn=lambda d, p: "",
        )
        # Empty output has no sentences → coverage treated as 1.0 → PASS
        assert report["decision"] == "PASS"


# ---------------------------------------------------------------------------
# (2) TTL index expiry
# ---------------------------------------------------------------------------

class TestTTLIndexExpiry:
    def test_ttl_index_expiry(self):
        """Register UUID, advance mock clock past TTL, assert is_expired."""
        clock, advance = _clock_factory(0.0)
        index = TTLIndex(ttl_seconds=60, _clock=clock)
        uid = _uid()

        index.register(uid, embedding_ref="emb://chunk/1")

        # Before TTL: not expired
        advance(30.0)
        assert not index.is_expired(uid)

        # Exactly at boundary: expired
        advance(30.0)  # now t=60.0 == expires_at
        assert index.is_expired(uid)

    def test_ttl_not_expired_before_threshold(self):
        """is_expired returns False while TTL has not elapsed."""
        clock, advance = _clock_factory(0.0)
        index = TTLIndex(ttl_seconds=100, _clock=clock)
        uid = _uid()
        index.register(uid, "emb://x")
        advance(50.0)
        assert not index.is_expired(uid)

    def test_ttl_unregistered_uuid_not_expired(self):
        """is_expired returns False for UUIDs never registered."""
        index = TTLIndex()
        assert not index.is_expired(_uid())

    def test_ttl_custom_seconds(self):
        """TTL respects the configured ttl_seconds value."""
        clock, advance = _clock_factory(0.0)
        short_index = TTLIndex(ttl_seconds=5, _clock=clock)
        uid = _uid()
        short_index.register(uid, "emb://short")

        advance(4.9)
        assert not short_index.is_expired(uid)
        advance(0.2)  # total 5.1
        assert short_index.is_expired(uid)


# ---------------------------------------------------------------------------
# (3) TTL purge removes expired
# ---------------------------------------------------------------------------

class TestTTLPurgeRemovesExpired:
    def test_ttl_purge_removes_expired(self):
        """purge_expired() returns list of deleted UUIDs."""
        clock, advance = _clock_factory(0.0)
        index = TTLIndex(ttl_seconds=100, _clock=clock)

        uid_a = _uid()
        index.register(uid_a, "emb://a")  # expires at t=100

        advance(50.0)
        uid_b = _uid()
        index.register(uid_b, "emb://b")  # expires at t=150
        uid_c = _uid()
        index.register(uid_c, "emb://c")  # expires at t=150

        # Advance past uid_a expiry but not uid_b / uid_c
        advance(60.0)  # t=110
        purged = index.purge_expired()

        assert uid_a in purged
        assert uid_b not in purged
        assert uid_c not in purged
        # uid_b and uid_c are still valid
        assert not index.is_expired(uid_b)
        assert not index.is_expired(uid_c)

    def test_purge_empty_index_returns_empty_list(self):
        """purge_expired on empty index returns []."""
        index = TTLIndex()
        assert index.purge_expired() == []

    def test_purge_removes_entry_from_index(self):
        """After purge, the deleted UUID is no longer tracked."""
        clock, advance = _clock_factory(0.0)
        index = TTLIndex(ttl_seconds=10, _clock=clock)
        uid = _uid()
        index.register(uid, "emb://z")
        advance(11.0)
        index.purge_expired()
        # is_expired returns False for untracked UUIDs
        assert not index.is_expired(uid)

    def test_purge_all_when_all_expired(self):
        """purge_expired returns all UUIDs when every entry has expired."""
        clock, advance = _clock_factory(0.0)
        index = TTLIndex(ttl_seconds=1, _clock=clock)
        uids = [_uid() for _ in range(5)]
        for u in uids:
            index.register(u, f"emb://{u}")
        advance(2.0)
        purged = index.purge_expired()
        assert set(purged) == set(uids)

    def test_register_purge_handler_called_on_fire(self):
        """Registered handler is invoked when fire_event is called."""
        index = TTLIndex()
        fired: list = []
        index.register_purge_handler("record.deleted", lambda **kw: fired.append(kw))
        index.fire_event("record.deleted", record_id="r1")
        assert fired == [{"record_id": "r1"}]

    def test_unknown_event_fires_no_handler(self):
        """fire_event with an unknown name does not raise."""
        index = TTLIndex()
        index.fire_event("no.such.event")  # must not raise


# ---------------------------------------------------------------------------
# (4) UUID grounding enforcer — PASS
# ---------------------------------------------------------------------------

class TestUUIDGroundingEnforcerPass:
    def test_uuid_grounding_enforcer_pass(self):
        """Output with all claims cited returns decision=PASS."""
        uid1, uid2 = _uid(), _uid()
        uuid_set = {uid1, uid2}
        output = f"Record A was processed [{uid1}]. Record B was accessed [{uid2}]."

        report = verify_citation_coverage(output, uuid_set)

        assert report["decision"] == "PASS"
        assert report["coverage_pct"] == 1.0
        assert report["phantom_uuids"] == []
        assert report["ungrounded"] == []

    def test_100_pct_coverage_single_sentence(self):
        """Single cited sentence achieves 100% coverage → PASS."""
        uid = _uid()
        report = verify_citation_coverage(f"Data confirmed [{uid}].", {uid})
        assert report["decision"] == "PASS"
        assert report["coverage_pct"] == 1.0

    def test_empty_output_is_pass(self):
        """Empty output has no sentences → treated as PASS (nothing ungrounded)."""
        report = verify_citation_coverage("", set())
        assert report["decision"] == "PASS"


# ---------------------------------------------------------------------------
# (5) UUID grounding enforcer — FAIL
# ---------------------------------------------------------------------------

class TestUUIDGroundingEnforcerFail:
    def test_uuid_grounding_enforcer_fail(self):
        """Output with uncited sentences returns decision=FAIL."""
        uid1 = _uid()
        uuid_set = {uid1}
        # 1 cited + 2 uncited → coverage 1/3 ≈ 0.33 < 0.80
        output = (
            f"Record A was processed [{uid1}]. "
            "This claim has no citation. "
            "Neither does this one."
        )

        report = verify_citation_coverage(output, uuid_set)

        assert report["decision"] == "FAIL"
        assert report["coverage_pct"] < 0.80
        assert len(report["ungrounded"]) >= 2

    def test_exactly_at_threshold_fails(self):
        """coverage_pct exactly at 0.80 passes; just below fails."""
        # 4 sentences: 3 cited → coverage = 0.75 → FAIL
        uids = [_uid() for _ in range(3)]
        uuid_set = set(uids)
        output = (
            f"Fact one [{uids[0]}]. "
            f"Fact two [{uids[1]}]. "
            f"Fact three [{uids[2]}]. "
            "Uncited claim here."
        )
        report = verify_citation_coverage(output, uuid_set)
        assert report["decision"] == "FAIL"
        assert abs(report["coverage_pct"] - 0.75) < 0.01

    def test_no_citations_at_all_fails(self):
        """Output with zero citations returns FAIL with 0.0 coverage."""
        output = "No citations here. None here either. Nor here."
        report = verify_citation_coverage(output, {_uid()})
        assert report["decision"] == "FAIL"
        assert report["coverage_pct"] == 0.0


# ---------------------------------------------------------------------------
# (6) Phantom UUID detection
# ---------------------------------------------------------------------------

class TestPhantomUUIDDetection:
    def test_phantom_uuid_detection(self):
        """UUID in output but not in authorized set => phantom => FAIL."""
        authorized = _uid()
        phantom = _uid()
        uuid_set = {authorized}
        output = (
            f"Record A exists [{authorized}]. "
            f"Unauthorized reference [{phantom}]."
        )

        report = verify_citation_coverage(output, uuid_set)

        assert phantom in report["phantom_uuids"]
        assert report["decision"] == "FAIL"

    def test_no_phantom_when_all_authorized(self):
        """No phantom UUIDs when every cited UUID is in the authorized set."""
        uid1, uid2 = _uid(), _uid()
        uuid_set = {uid1, uid2}
        output = f"Item [{uid1}] and item [{uid2}] retrieved."
        report = verify_citation_coverage(output, uuid_set)
        assert report["phantom_uuids"] == []

    def test_phantom_fails_even_with_high_coverage(self):
        """Phantom UUID triggers FAIL even when coverage_pct >= 0.80."""
        # 5 sentences all cited, but one UUID is phantom → FAIL
        good_uids = [_uid() for _ in range(4)]
        phantom = _uid()
        uuid_set = set(good_uids)  # phantom NOT in set

        sentences = [f"Fact [{u}]." for u in good_uids] + [f"Phantom fact [{phantom}]."]
        output = " ".join(sentences)

        report = verify_citation_coverage(output, uuid_set)
        assert report["coverage_pct"] == 1.0  # all sentences have a citation
        assert phantom in report["phantom_uuids"]
        assert report["decision"] == "FAIL"

    def test_multiple_phantoms_all_listed(self):
        """All phantom UUIDs are listed in the report."""
        phantoms = [_uid(), _uid(), _uid()]
        uuid_set: set = set()
        output = " ".join(f"Claim [{p}]." for p in phantoms)

        report = verify_citation_coverage(output, uuid_set)
        assert set(report["phantom_uuids"]) == set(phantoms)


# ---------------------------------------------------------------------------
# (7) Stateless — no persistent index between calls
# ---------------------------------------------------------------------------

class TestStatelessNoPersistentIndex:
    def test_stateless_no_persistent_index(self):
        """entitlement_rag module exposes no global vector-index state."""
        import tools.rag.entitlement_rag as era

        forbidden = {
            "_INDEX", "_VECTOR_STORE", "_EMBEDDING_CACHE",
            "_DB_CONN", "_CONNECTION", "_GLOBAL_INDEX",
            "_GLOBAL_STORE", "_PERSISTENT_INDEX",
        }
        module_globals = {k for k in vars(era) if not k.startswith("__")}
        overlap = forbidden & module_globals
        assert not overlap, f"Forbidden global state found: {overlap}"

    def test_stateless_two_calls_independent(self):
        """Sequential pipeline calls do not share state across invocations."""
        call_log: list = []

        def fake_translate(prompt, ctx):
            call_log.append(prompt)
            return "SELECT 1"

        def fake_execute(q, tok):
            return [{"field": tok}]  # token encoded in result to verify isolation

        def fake_synthesize(data, prompt):
            uuids = [v for row in data for k, v in row.items() if k.startswith("_uuid_")]
            return " ".join(f"Item [{u}]." for u in uuids)

        r1 = run_entitlement_pipeline(
            "prompt_alice", {"user": "alice"}, "tok_alice",
            _translate_fn=fake_translate,
            _execute_fn=fake_execute,
            _synthesize_fn=fake_synthesize,
        )
        r2 = run_entitlement_pipeline(
            "prompt_bob", {"user": "bob"}, "tok_bob",
            _translate_fn=fake_translate,
            _execute_fn=fake_execute,
            _synthesize_fn=fake_synthesize,
        )

        assert call_log == ["prompt_alice", "prompt_bob"]
        assert r1["decision"] == "PASS"
        assert r2["decision"] == "PASS"

    def test_tag_cells_generates_unique_uuids_per_call(self):
        """Each call to tag_cells_with_uuids produces distinct UUIDs."""
        rows = [{"x": 1}]
        result_a = tag_cells_with_uuids(rows)
        result_b = tag_cells_with_uuids(rows)

        uuid_a = result_a[0]["_uuid_x"]
        uuid_b = result_b[0]["_uuid_x"]
        assert uuid_a != uuid_b, "UUIDs must be unique per call (stateless UUID generation)"

    def test_ttl_index_instances_are_independent(self):
        """Two TTLIndex instances do not share state."""
        clock, advance = _clock_factory(0.0)
        idx_a = TTLIndex(ttl_seconds=10, _clock=clock)
        idx_b = TTLIndex(ttl_seconds=10, _clock=clock)

        uid = _uid()
        idx_a.register(uid, "emb://a")
        # uid not registered in idx_b
        assert not idx_b.is_expired(uid)
        advance(15.0)
        assert idx_a.is_expired(uid)
        assert not idx_b.is_expired(uid)  # still not registered
