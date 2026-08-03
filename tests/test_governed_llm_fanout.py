# CUI // SP-CTI
"""Parallelised governed-LLM loops must be faster and still identical.

Two call sites used to issue independent governed Cortex calls strictly in
sequence, each paying the full 7-gate pipeline: the pairwise O(n^2) contradiction
scan in ``tools/docgen/workflow.py`` (6 sections = 15 serial calls) and the
per-batch classifier in ``tools/bom/taxonomy.py``.

Making them concurrent is only worth anything if the answer does not move, so the
docgen tests here do not assert "looks about right" — they run a REFERENCE COPY
of the loop that was replaced and demand equality, including in the awkward case:
the serial loop abandoned the LLM for the whole REST of the scan the moment one
call raised, and that is a genuine ordering dependency, not an implementation
detail. A fan-out that lets a later pair's successful result survive a earlier
pair's failure would be faster and WRONG.

No network. The model is stubbed throughout.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import importlib
import json
import re
import threading
import time
import types

import pytest

from tools.cortex import pool as cortex_pool


# ─── tools/cortex/pool.py ─────────────────────────────────────────────────────

class TestTheSharedPool:
    """The shape was already hand-copied twice (search_service, analyzers/
    dispatch). These are the two properties every copy needed."""

    def test_results_come_back_in_ITEM_order_not_completion_order(self):
        """The whole point. Completion order here is the exact reverse."""
        p = cortex_pool.get_pool("test-order", max_workers=8)

        def slow(n):
            time.sleep((8 - n) * 0.02)
            return n

        got = cortex_pool.map_ordered(p, slow, list(range(8)))
        assert [r for r, _ in got] == list(range(8))
        assert all(exc is None for _, exc in got)

    def test_one_failure_costs_only_its_own_slot(self):
        p = cortex_pool.get_pool("test-isolation", max_workers=4)

        def maybe_boom(n):
            if n == 2:
                raise RuntimeError("this one only")
            return n * 10

        got = cortex_pool.map_ordered(p, maybe_boom, [0, 1, 2, 3])
        assert [r for r, _ in got] == [0, 10, None, 30]
        assert isinstance(got[2][1], RuntimeError)
        assert [exc is None for _, exc in got] == [True, True, False, True]

    def test_a_failure_is_reported_not_swallowed(self):
        p = cortex_pool.get_pool("test-isolation", max_workers=4)
        (result, exc), = cortex_pool.map_ordered(p, lambda _: 1 / 0, ["x"])
        assert result is None
        assert isinstance(exc, ZeroDivisionError)

    def test_no_items_means_no_work(self):
        p = cortex_pool.get_pool("test-empty")
        assert cortex_pool.map_ordered(p, lambda _: pytest.fail("ran"), []) == []

    def test_a_single_item_never_leaves_the_calling_thread(self):
        """One governed call is not worth a thread hop — and keeping it inline is
        what lets a stub that records `last_prompt` stay correct."""
        p = cortex_pool.get_pool("test-inline", max_workers=4)
        seen = []
        cortex_pool.map_ordered(p, lambda _: seen.append(threading.current_thread()), ["a"])
        assert seen == [threading.current_thread()]

    def test_the_pool_is_process_wide_and_per_name(self):
        assert cortex_pool.get_pool("test-shared") is cortex_pool.get_pool("test-shared")
        assert cortex_pool.get_pool("test-shared") is not cortex_pool.get_pool("test-other")

    def test_an_unparseable_worker_count_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("TEST_FANOUT_WORKERS", "not-a-number")
        p = cortex_pool.get_pool("test-bad-env", env_var="TEST_FANOUT_WORKERS",
                                 default_workers=3)
        assert p._max_workers == 3


# ─── tools/docgen/workflow.py — the pairwise contradiction scan ───────────────

_CONTRADICTS = re.compile(r"Section A \(first 600 chars\):\n(.*?)\n\nSection B",
                          re.DOTALL)


def _fake_cortex(monkeypatch, *, delay=0.0, calls=None):
    """Replace the governed facade with a deterministic, PER-PAIR stub.

    Deterministic per pair rather than per call count on purpose: the reference
    serial loop and the parallel one issue calls in different orders, so a
    call-counting stub would compare two different experiments. Behaviour is
    keyed off the section text — a section containing BOOM raises, one containing
    CLASH contradicts.
    """
    lock = threading.Lock()

    def complete(prompt, **kwargs):
        if delay:
            time.sleep(delay)
        if calls is not None:
            with lock:
                calls.append(prompt)
        body = _CONTRADICTS.search(prompt)
        pair_text = prompt[prompt.index("Section A"):]
        if "BOOM" in pair_text:
            raise RuntimeError("provider unreachable")
        contradicts = "CLASH" in pair_text
        return types.SimpleNamespace(text=json.dumps({
            "contradicts": contradicts,
            "description": f"clash in {(body.group(1)[:20] if body else '?')}",
        }))

    monkeypatch.setattr(importlib.import_module("tools.cortex"), "api",
                        types.SimpleNamespace(complete=complete))


def _serial_detect(doc_text: str) -> list:
    """The loop as it was BEFORE the fan-out. The oracle for every test below.

    Kept verbatim (bar the import block) so "identical to the serial version" is
    an assertion rather than a claim.
    """
    from tools.docgen.workflow import _CONTRADICTION_PAIRS

    if not doc_text or not doc_text.strip():
        return []
    raw_parts = re.split(r"\n#{1,3}\s+", doc_text)
    sections = [p.strip() for p in raw_parts if p.strip()]
    if len(sections) <= 1:
        return []
    sections = sections[:6]
    conflicts: list = []

    _llm_available = True
    from tools.cortex import api as _cortex_api
    from tools.cortex.schemas import CortexContext as _CortexContext

    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            sec_a, sec_b = sections[i], sections[j]
            if _llm_available:
                try:
                    prompt = (
                        "Do the following two document sections contradict each other? "
                        "Return JSON only: {\"contradicts\": true/false, \"description\": \"...\"}.\n\n"
                        f"Section A (first 600 chars):\n{sec_a[:600]}\n\n"
                        f"Section B (first 600 chars):\n{sec_b[:600]}"
                    )
                    cx = _cortex_api.complete(
                        prompt, function="detect_semantic_conflicts",
                        ctx=_CortexContext(domain="document", agent_id="docgen",
                                           trusted_content=True),
                        max_tokens=128, temperature=0.0,
                    )
                    raw = (cx.text or "").strip()
                    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
                    data = json.loads(raw)
                    if data.get("contradicts"):
                        conflicts.append({
                            "section_a": sec_a[:120], "section_b": sec_b[:120],
                            "description": str(data.get("description",
                                                        "Contradiction detected")),
                            "severity": "error",
                        })
                    continue
                except Exception:
                    _llm_available = False
            combined_a, combined_b = sec_a.lower(), sec_b.lower()
            for kw_a, kw_b in _CONTRADICTION_PAIRS:
                if kw_a in combined_a and kw_b in combined_b:
                    conflicts.append({
                        "section_a": sec_a[:120], "section_b": sec_b[:120],
                        "description": f"Potential contradiction: '{kw_a}' vs '{kw_b}'",
                        "severity": "warning",
                    })
                    break
                if kw_b in combined_a and kw_a in combined_b:
                    conflicts.append({
                        "section_a": sec_a[:120], "section_b": sec_b[:120],
                        "description": f"Potential contradiction: '{kw_b}' vs '{kw_a}'",
                        "severity": "warning",
                    })
                    break
    return conflicts


def _doc(*bodies) -> str:
    return "".join(f"\n# Section {n}\n{b}\n" for n, b in enumerate(bodies))


# Six sections => C(6,2) = 15 pairs, the documented worst case.
_SIX = _doc(
    "Encryption enabled across the estate. CLASH",
    "Encryption disabled on the legacy segment.",
    "All traffic is authenticated at the edge.",
    "TLS 1.3 is mandatory for external listeners. CLASH",
    "SSL 3 remains acceptable for the vendor appliance.",
    "Keys are rotated on a ninety day cycle.",
)


class TestTheAnswerDoesNotMove:
    def test_a_full_six_section_scan_matches_the_serial_loop_exactly(
        self, monkeypatch,
    ):
        from tools.docgen.workflow import detect_semantic_conflicts

        _fake_cortex(monkeypatch)
        expected = _serial_detect(_SIX)
        assert detect_semantic_conflicts(_SIX) == expected
        # ...and the oracle is not trivially empty.
        assert expected

    def test_order_survives_a_reversed_completion_order(self, monkeypatch):
        """The pairs finish backwards; the list must still read forwards."""
        from tools.docgen.workflow import detect_semantic_conflicts

        counter = {"n": 0}
        lock = threading.Lock()

        def complete(prompt, **kwargs):
            with lock:
                counter["n"] += 1
                n = counter["n"]
            time.sleep(max(0.0, (16 - n) * 0.01))
            return types.SimpleNamespace(text=json.dumps(
                {"contradicts": True, "description": f"pair-{n}"}))

        monkeypatch.setattr(importlib.import_module("tools.cortex"), "api",
                            types.SimpleNamespace(complete=complete))
        got = detect_semantic_conflicts(_SIX)
        assert len(got) == 15
        # Every pair contradicts, so the list must be the pair enumeration in
        # (i, j) order — section_a walks the sections monotonically.
        order = [(c["section_a"][:11], c["section_b"][:11]) for c in got]
        assert order == sorted(order, key=lambda t: (t[0], t[1]))

    def test_a_failure_mid_scan_still_abandons_the_LLM_for_the_REST(
        self, monkeypatch,
    ):
        """The ordering dependency a naive fan-out would break.

        Pair (0,2) raises. Everything from that pair onward must fall to the
        keyword classifier even though its own governed call had already been
        launched and would have succeeded.
        """
        from tools.docgen.workflow import detect_semantic_conflicts

        doc = _doc(
            "Encryption enabled here. CLASH",
            "Encryption disabled over there.",
            "BOOM — this pairing kills the provider.",
            "Encryption enabled again. CLASH",
        )
        _fake_cortex(monkeypatch)
        expected = _serial_detect(doc)
        got = detect_semantic_conflicts(doc)
        assert got == expected
        # The oracle must actually exercise both paths, or this proves nothing.
        assert {c["severity"] for c in expected} == {"error", "warning"}

    def test_a_provider_that_is_entirely_gone_matches_too(self, monkeypatch):
        from tools.docgen.workflow import detect_semantic_conflicts

        doc = _doc(
            "BOOM. Encryption enabled.",
            "Encryption disabled.",
            "TLS 1.3 only.",
        )
        _fake_cortex(monkeypatch)
        assert detect_semantic_conflicts(doc) == _serial_detect(doc)

    def test_an_air_gapped_run_wastes_exactly_one_call_as_before(self, monkeypatch):
        """Probing the first pair alone is why: firing all 15 into a dead
        provider would make the air-gap case SLOWER than the loop it replaced."""
        from tools.docgen.workflow import detect_semantic_conflicts

        calls: list = []
        _fake_cortex(monkeypatch, calls=calls)
        doc = _doc("BOOM everywhere.", "Second.", "Third.", "Fourth.")
        detect_semantic_conflicts(doc)
        assert len(calls) == 1


class TestItIsActuallyFaster:
    def test_fifteen_pairs_no_longer_cost_fifteen_round_trips(self, monkeypatch):
        from tools.docgen.workflow import detect_semantic_conflicts

        per_call = 0.05
        calls: list = []
        _fake_cortex(monkeypatch, delay=per_call, calls=calls)
        started = time.monotonic()
        detect_semantic_conflicts(_SIX)
        elapsed = time.monotonic() - started

        assert len(calls) == 15                    # every pair still asked
        serial = 15 * per_call                     # 0.75s
        # Probe (1) + ceil(14/4) rounds = 5 round trips ≈ 0.25s. Assert well
        # under half of serial so the check means something without being flaky.
        assert elapsed < serial * 0.6, f"{elapsed:.3f}s vs {serial:.3f}s serial"


# ─── tools/bom/taxonomy.py — the per-batch classifier ─────────────────────────

def _lines(n, prefix="item"):
    from tools.bom.lines import ExtractedLine
    return [
        ExtractedLine(
            line_id=f"l{i}", line_hash="h", source_document="d.xlsx",
            source_sheet="BOM", source_locator=f"A{i}",
            raw_text=f"{prefix} {i}", description=f"{prefix} {i}",
        )
        for i in range(n)
    ]


def _tax():
    from tools.bom import taxonomy as T
    return T.Taxonomy(
        categories=[T.Category("Network Hardware"), T.Category("Compute Hardware")],
        status="approved",
    )


def _fake_extract(monkeypatch, *, delay=0.0, boom_on=None, prompts=None):
    """Answer every item in whatever batch arrives, optionally slowly."""
    lock = threading.Lock()

    def extract(prompt, schema, ctx=None):
        if delay:
            time.sleep(delay)
        if prompts is not None:
            with lock:
                prompts.append(prompt)
        if boom_on and boom_on in prompt:
            raise RuntimeError("provider unreachable")
        keys = re.findall(r"^(t\d+): ", prompt, re.MULTILINE)
        return types.SimpleNamespace(
            text=json.dumps({"assignments": [
                {"line_id": k, "label": "Network Hardware"} for k in keys
            ]}),
            metadata={"schema_valid": True},
        )

    monkeypatch.setattr(importlib.import_module("tools.cortex"), "api",
                        types.SimpleNamespace(extract=extract))


class TestTheBatchesRunTogether:
    def test_every_line_across_many_batches_is_still_classified(self, monkeypatch):
        from tools.bom import taxonomy as T

        monkeypatch.setattr(T, "BATCH", 2)
        prompts: list = []
        _fake_extract(monkeypatch, prompts=prompts)
        got = T.classify_lines(_lines(7), _tax())

        assert len(prompts) == 4                       # ceil(7/2) batches
        assert set(got) == {f"l{i}" for i in range(7)}
        assert set(got.values()) == {"Network Hardware"}

    def test_a_batch_that_fails_costs_only_its_own_lines(self, monkeypatch):
        """Per-item error isolation: the serial loop caught per batch, and so
        must the fan-out — one dead batch must not lose the other six."""
        from tools.bom import taxonomy as T

        monkeypatch.setattr(T, "BATCH", 2)
        # Batch 2 holds items 4 and 5.
        _fake_extract(monkeypatch, boom_on="item 4")
        got = T.classify_lines(_lines(7), _tax())

        assert got["l4"] == T.FALLBACK
        assert got["l5"] == T.FALLBACK
        for i in (0, 1, 2, 3, 6):
            assert got[f"l{i}"] == "Network Hardware"

    def test_the_batches_no_longer_cost_one_round_trip_each(self, monkeypatch):
        from tools.bom import taxonomy as T

        monkeypatch.setattr(T, "BATCH", 1)
        per_call = 0.05
        _fake_extract(monkeypatch, delay=per_call)
        started = time.monotonic()
        got = T.classify_lines(_lines(8), _tax())
        elapsed = time.monotonic() - started

        assert len(got) == 8
        serial = 8 * per_call
        assert elapsed < serial * 0.6, f"{elapsed:.3f}s vs {serial:.3f}s serial"
