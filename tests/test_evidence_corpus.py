# CUI // SP-CTI
"""Tests for the evidence flywheel: approve -> index -> retrieve -> cite.

The interesting cases are the refusals. Indexing approved prose creates a closed
loop, so what must NOT enter the corpus matters more than what does.
"""
import json
from types import SimpleNamespace

import pytest

from tools.govcon import evidence_corpus as ec
from tools.govcon import rfi_engine_runner as runner
from tools.rag.source_registry import SOURCE_REGISTRY, get_source_config


class TestSourceRegistry:
    @pytest.mark.parametrize(
        "source_type",
        ["rfi_approved_sections", "proposal_approved_drafts", "prior_submissions"],
    )
    def test_evidence_sources_are_registered(self, source_type):
        """ingest_source rejects any source_type absent from the registry."""
        assert get_source_config(source_type) is not None

    def test_filters_only_admit_approved_content(self):
        assert "hitl_approved" in SOURCE_REGISTRY["rfi_approved_sections"]["filter"]
        assert "accepted" in SOURCE_REGISTRY["rfi_approved_sections"]["filter"]
        assert "approved" in SOURCE_REGISTRY["proposal_approved_drafts"]["filter"]
        assert "ingested" in SOURCE_REGISTRY["prior_submissions"]["filter"]

    def test_rfi_filter_enforces_the_depth_cap(self):
        """A section written from the corpus must not re-enter it."""
        assert "prior_submissions" in SOURCE_REGISTRY["rfi_approved_sections"]["filter"]

    def test_filters_pass_the_ingestion_safety_regex(self):
        """ingestion_manager rejects filters that do not start with `col op`."""
        import re

        pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*(=|!=|<|>|<=|>=|IN|LIKE|IS)\s*", re.I)
        for source_type in ("rfi_approved_sections", "proposal_approved_drafts", "prior_submissions"):
            filt = SOURCE_REGISTRY[source_type]["filter"]
            assert pattern.match(filt), f"{source_type} filter would be rejected as unsafe: {filt}"

    def test_metadata_cols_exist_on_the_source_table(self):
        """Batch ingest SELECTs metadata_cols from the table; a phantom column breaks it."""
        cfg = SOURCE_REGISTRY["proposal_approved_drafts"]
        assert "shall_statement_id" in cfg["metadata_cols"]
        assert "shall_id" not in cfg["metadata_cols"], "no such column on proposal_section_drafts"


class TestEvidenceTier:
    def test_uploaded_submissions_are_primary(self):
        assert runner.evidence_tier("prior_submissions") == "primary"

    def test_our_own_approved_prose_is_derived(self):
        assert runner.evidence_tier("rfi_approved_sections") == "derived"
        assert runner.evidence_tier("proposal_approved_drafts") == "derived"


class TestDepthCap:
    def test_section_built_from_corpus_is_flagged(self):
        assert ec._is_corpus_derived(json.dumps(["uploads", "prior_submissions"])) is True
        assert ec._is_corpus_derived(json.dumps(["rfi_approved_sections"])) is True

    def test_section_built_from_ground_truth_is_not(self):
        assert ec._is_corpus_derived(json.dumps(["uploads", "rag_general"])) is False

    def test_missing_or_malformed_sources_json_is_not_derived(self):
        assert ec._is_corpus_derived(None) is False
        assert ec._is_corpus_derived("") is False
        assert ec._is_corpus_derived("not json") is False


class TestForceOverrideExclusion:
    def test_placeholder_override_blocks_promotion(self):
        meta = json.dumps({"placeholder_guard_override": ["[VERIFY]"]})
        assert ec._was_force_overridden(meta) is True

    def test_citation_override_blocks_promotion(self):
        meta = json.dumps({"citation_guard_override": [{"type": "missing_citations"}]})
        assert ec._was_force_overridden(meta) is True

    def test_clean_approval_is_promotable(self):
        assert ec._was_force_overridden(json.dumps({"reviewer": "human"})) is False
        assert ec._was_force_overridden(None) is False

    def test_override_keys_match_what_approve_draft_actually_writes(self):
        """approve_draft records 'placeholder_guard_override', not 'force_placeholders'."""
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "tools/govcon/response_drafter.py").read_text(
            encoding="utf-8"
        )
        for key in ec._OVERRIDE_KEYS:
            assert f'"{key}"' in src, f"{key} is not the key approve_draft writes"


class TestPromotionRefusals:
    """promote_rfi_section must decline; it must never raise into the Accept click."""

    def _patch_row(self, monkeypatch, row):
        monkeypatch.setattr(ec, "_canvas_db", lambda: SimpleNamespace(
            execute=lambda *a, **k: SimpleNamespace(fetchone=lambda: row)
        ))

    def test_unapproved_section_is_not_indexed(self, monkeypatch):
        self._patch_row(monkeypatch, {"id": "s1", "content": "text", "status": "ai_draft_ready"})
        assert ec.promote_rfi_section("s1")["reason"] == "not_approved"

    def test_section_holding_verify_token_is_not_indexed(self, monkeypatch):
        self._patch_row(monkeypatch, {
            "id": "s1", "title": "t", "content": "Proven by [VERIFY].",
            "status": "accepted", "sources_json": None,
        })
        result = ec.promote_rfi_section("s1")
        assert result["status"] == "skipped"
        assert result["reason"] == "unresolved_placeholders"

    def test_corpus_derived_section_is_not_reindexed(self, monkeypatch):
        self._patch_row(monkeypatch, {
            "id": "s1", "title": "t", "content": "Clean prose.",
            "status": "accepted", "sources_json": json.dumps(["prior_submissions"]),
        })
        assert ec.promote_rfi_section("s1")["reason"] == "derivation_depth"

    def test_empty_section_is_not_indexed(self, monkeypatch):
        self._patch_row(monkeypatch, {"id": "s1", "content": "  ", "status": "accepted"})
        assert ec.promote_rfi_section("s1")["reason"] == "empty"

    def test_missing_section_never_raises(self, monkeypatch):
        self._patch_row(monkeypatch, None)
        assert ec.promote_rfi_section("nope")["status"] == "skipped"

    def test_db_failure_never_raises(self, monkeypatch):
        def _boom():
            raise RuntimeError("db offline")

        monkeypatch.setattr(ec, "_canvas_db", _boom)
        result = ec.promote_rfi_section("s1")
        assert result["status"] == "skipped" and "db offline" in result["reason"]


class TestLostProposalsAreNotEvidence:
    def test_lost_outcome_is_non_citable(self):
        assert "lost" in ec.NON_CITABLE_OUTCOMES
        assert "lost" in runner._NON_CITABLE_OUTCOMES

    def test_gatherer_drops_lost_prose_and_ranks_primary_first(self, monkeypatch):
        def _fake_search(topic, top_k, source_types=None):
            return [
                SimpleNamespace(content="derived prose", source_type="rfi_approved_sections",
                                source_id="d1", final_score=0.9, metadata={}),
                SimpleNamespace(content="lost prose", source_type="prior_submissions",
                                source_id="l1", final_score=0.95, metadata={"outcome": "lost"}),
                SimpleNamespace(content="won prose", source_type="prior_submissions",
                                source_id="p1", final_score=0.5, metadata={"outcome": "won"}),
            ]

        monkeypatch.setattr(runner, "_rag_search", _fake_search)
        out = runner._gather_prior_submissions("topic", 4000)
        assert "lost prose" not in out, "a lost proposal must not be reused as evidence"
        assert out.index("p1") < out.index("d1"), "primary evidence must outrank derived"
        assert "(primary)" in out and "(derived)" in out
        assert "do not treat it as proof" in out, "model must be told derived text is not proof"

    def test_no_results_yields_no_header(self, monkeypatch):
        monkeypatch.setattr(runner, "_rag_search", lambda *a, **k: [])
        assert runner._gather_prior_submissions("topic", 4000) == ""
