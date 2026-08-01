#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for document-type chunking templates (oss-chunk-01).

Covers: template config loading, structural/row-group segmentation, explicit
dispatch through chunk_content, per-chunk provenance metadata, advisory-only
auto-detection, and the source_registry `chunking` key activation.
"""

from __future__ import annotations

import pytest

from tools.rag.chunker import chunk_content, chunk_fields
from tools.rag.chunking_templates import (
    CONFIG_PATH,
    default_template_name,
    get_template,
    list_templates,
    load_config,
    split_content,
    suggest_template,
)
from tools.rag.source_registry import SOURCE_REGISTRY, get_chunking_template

# --- Fixtures ---------------------------------------------------------------

OSCAL_DOC = """NIST SP 800-53 Rev 5 Control Catalog
This catalog contains selected controls.

AC-2 ACCOUNT MANAGEMENT
The organization manages information system accounts, including establishing
conditions for group membership and identifying authorized users.

AC-2(1) AUTOMATED SYSTEM ACCOUNT MANAGEMENT
The organization employs automated mechanisms to support the management of
information system accounts.

AU-12 AUDIT RECORD GENERATION
Provide audit record generation capability for the event types the system is
capable of auditing.

SC-7 BOUNDARY PROTECTION
Monitor and control communications at the external managed interfaces.
"""

STIG_DOC = """Red Hat Enterprise Linux 9 STIG

V-257777
Severity: CAT I
Check Text: Verify that the operating system is configured correctly.
Fix Text: Configure the operating system.

V-257778
Severity: CAT II
Check Text: Verify the audit daemon is running.
Fix Text: Start the audit daemon.

V-257779
Severity: CAT III
Check Text: Verify banner text.
Fix Text: Set the banner.
"""

RFP_DOC = """Solicitation W91QUZ-26-R-0001

SECTION L INSTRUCTIONS TO OFFERORS
Offerors shall submit proposals in three volumes.

L.1 Volume I - Technical
The technical volume shall not exceed 30 pages.

L.2 Volume II - Past Performance
Provide three references.

SECTION M EVALUATION FACTORS FOR AWARD
Award will be made on a best value basis.

M.1 Technical Approach
Technical approach is significantly more important than cost.
"""

CONTRACT_DOC = """MASTER SERVICES AGREEMENT

ARTICLE I DEFINITIONS
1.1 "Services" means the work described in the Statement of Work.
1.2 "Deliverables" means all items produced under this Agreement.

ARTICLE II PERIOD OF PERFORMANCE
2.1 The period of performance shall be twelve months.

52.204-21 Basic Safeguarding of Covered Contractor Information Systems
The Contractor shall apply the basic safeguarding requirements.
"""

SOP_DOC = """Database Failover Runbook

Prerequisites
Access to the primary and standby hosts is required.

Step 1 Verify replication lag
Run the lag query on the standby and confirm it is under five seconds.

Step 2 Promote the standby
Issue the promotion command and wait for the role change.

Step 3 Redirect application traffic
Update the connection string and restart the application pool.

Rollback
Demote the promoted node and re-point traffic to the original primary.
"""

SLIDE_DOC = """Slide 1
ICDEV Platform Overview
Speaker notes: welcome the audience.

Slide 2
Architecture
Six FORGE layers, deterministic tools.

Slide 3
Compliance Posture
NIST 800-53, FedRAMP, CMMC.
"""

CSV_DOC = "id,name,severity,status\n" + "\n".join(
    f"{i},finding-{i},high,open" for i in range(1, 61)
)


# --- Config -----------------------------------------------------------------


class TestConfig:
    def test_config_file_exists(self):
        assert CONFIG_PATH.exists(), "args/chunking_templates.yaml must ship"

    def test_all_required_templates_present(self):
        names = {t["name"] for t in list_templates()}
        required = {
            "oscal_catalog",
            "stig_checklist",
            "rfp_sow",
            "contract",
            "sop_runbook",
            "slide_deck",
            "spreadsheet",
            "general",
        }
        assert required <= names

    def test_default_is_general(self):
        assert default_template_name() == "general"

    def test_registry_chunking_values_all_resolve(self):
        """Every `chunking` value in source_registry must name a real template.

        This is the point of oss-chunk-01: the key is live, so a value that
        does not resolve is a config bug, not a no-op.
        """
        declared = {
            cfg["chunking"] for cfg in SOURCE_REGISTRY.values() if cfg.get("chunking")
        }
        assert declared, "source_registry should still carry chunking values"
        known = {t["name"] for t in list_templates()}
        assert declared <= known, f"unresolvable: {declared - known}"

    def test_unknown_template_falls_back_with_reason(self):
        name, tmpl, reason = get_template("does_not_exist")
        assert name == default_template_name()
        assert reason == "unknown_template:does_not_exist"

    def test_none_resolves_to_default(self):
        name, tmpl, reason = get_template(None)
        assert name == default_template_name()
        assert reason == ""

    def test_boundary_patterns_compile(self):
        import re

        for name, tmpl in (load_config().get("templates") or {}).items():
            for pat in tmpl.get("boundary_patterns") or []:
                re.compile(pat)  # raises re.error on bad config
            for pat in (tmpl.get("detect") or {}).get("patterns") or []:
                re.compile(pat)


# --- Structural segmentation ------------------------------------------------


def _texts(doc: str, template: str, **kw):
    return [c.content for c in chunk_content(doc, template=template, **kw)]


class TestStructuralChunking:
    def test_oscal_one_chunk_per_control(self):
        texts = _texts(OSCAL_DOC, "oscal_catalog")
        # front matter + AC-2 + AC-2(1) + AU-12 + SC-7
        assert len(texts) == 5
        assert texts[1].startswith("AC-2 ACCOUNT MANAGEMENT")
        assert texts[2].startswith("AC-2(1)")
        assert texts[3].startswith("AU-12")
        assert texts[4].startswith("SC-7")

    def test_oscal_control_body_stays_with_its_control(self):
        texts = _texts(OSCAL_DOC, "oscal_catalog")
        ac2 = next(t for t in texts if t.startswith("AC-2 "))
        assert "conditions for group membership" in ac2
        assert "AU-12" not in ac2

    def test_oscal_never_splits_an_oversize_control(self):
        """oversize_policy: keep — one huge control stays one chunk, flagged."""
        big = "AC-2 ACCOUNT MANAGEMENT\n" + ("Sentence about accounts. " * 4000)
        chunks = chunk_content(big, template="oscal_catalog")
        assert len(chunks) == 1
        assert chunks[0].metadata["chunking_oversize"] is True

    def test_stig_one_chunk_per_rule(self):
        texts = _texts(STIG_DOC, "stig_checklist")
        assert len(texts) == 4  # header + 3 rules
        assert texts[1].startswith("V-257777")
        assert "V-257778" not in texts[1]

    def test_rfp_splits_on_section_l_and_m(self):
        texts = _texts(RFP_DOC, "rfp_sow")
        starts = [t.splitlines()[0] for t in texts]
        assert any(s.startswith("SECTION L") for s in starts)
        assert any(s.startswith("SECTION M") for s in starts)
        assert any(s.startswith("L.1") for s in starts)
        assert any(s.startswith("M.1") for s in starts)

    def test_contract_splits_on_numbered_clauses(self):
        texts = _texts(CONTRACT_DOC, "contract")
        starts = [t.splitlines()[0] for t in texts]
        assert any(s.startswith("ARTICLE I ") for s in starts)
        assert any(s.startswith("1.1 ") for s in starts)
        assert any(s.startswith("52.204-21") for s in starts)

    def test_sop_splits_on_numbered_steps(self):
        texts = _texts(SOP_DOC, "sop_runbook")
        starts = [t.splitlines()[0] for t in texts]
        assert sum(1 for s in starts if s.startswith("Step ")) == 3
        step2 = next(t for t in texts if t.startswith("Step 2"))
        assert "promotion command" in step2
        assert "Step 3" not in step2

    def test_slide_deck_one_chunk_per_slide(self):
        texts = _texts(SLIDE_DOC, "slide_deck")
        assert len(texts) == 3
        assert texts[0].startswith("Slide 1")
        assert "Slide 2" not in texts[0]

    def test_structural_falls_back_when_no_boundaries_match(self):
        plain = "There are no controls here at all. " * 200
        chunks = chunk_content(plain, template="oscal_catalog")
        assert chunks
        meta = chunks[0].metadata
        assert meta["chunking_template"] == "oscal_catalog"
        assert meta["chunking_strategy"] == "sliding_window"
        assert meta["chunking_fallback_reason"] == "no_boundaries_matched"

    def test_oversize_item_is_split_when_policy_is_split(self):
        big = "Step 1 Do the thing\n" + ("Detail sentence here. " * 4000)
        chunks = chunk_content(big, template="sop_runbook")
        assert len(chunks) > 1
        assert chunks[0].metadata["chunking_item_split"] is True
        assert chunks[0].metadata["chunking_item_parts"] == len(chunks)


class TestRowGroups:
    def test_header_repeated_on_every_chunk(self):
        texts = _texts(CSV_DOC, "spreadsheet")
        assert len(texts) == 3  # 60 body rows / 25 per group
        for t in texts:
            assert t.splitlines()[0] == "id,name,severity,status"

    def test_all_rows_present_exactly_once(self):
        texts = _texts(CSV_DOC, "spreadsheet")
        body = [ln for t in texts for ln in t.splitlines()[1:]]
        assert len(body) == 60
        assert len(set(body)) == 60

    def test_header_only_content(self):
        chunks = chunk_content("id,name,severity", template="spreadsheet")
        assert len(chunks) == 1
        assert chunks[0].content == "id,name,severity"


# --- Dispatch + provenance --------------------------------------------------


class TestDispatchAndProvenance:
    def test_default_is_unchanged_sliding_window(self):
        long_text = ". ".join(f"This is sentence number {i}" for i in range(500))
        no_arg = [c.content for c in chunk_content(long_text)]
        explicit = [c.content for c in chunk_content(long_text, template="general")]
        assert no_arg == explicit
        assert len(no_arg) > 1

    def test_structural_template_is_never_chosen_implicitly(self):
        """OSCAL content with no template= argument still uses the default."""
        chunks = chunk_content(OSCAL_DOC)
        assert chunks[0].metadata["chunking_template"] == "general"
        assert chunks[0].metadata["chunking_strategy"] == "sliding_window"

    def test_template_recorded_on_every_chunk(self):
        chunks = chunk_content(STIG_DOC, template="stig_checklist")
        assert len(chunks) > 1
        for c in chunks:
            assert c.metadata["chunking_template"] == "stig_checklist"
            assert c.metadata["chunking_strategy"] == "structural"

    def test_unknown_template_recorded_not_raised(self):
        chunks = chunk_content("some text", template="typo_template")
        assert chunks[0].metadata["chunking_template"] == "general"
        assert chunks[0].metadata["chunking_template_requested"] == "typo_template"
        assert chunks[0].metadata["chunking_fallback_reason"] == (
            "unknown_template:typo_template"
        )

    def test_caller_metadata_preserved(self):
        chunks = chunk_content(
            STIG_DOC, template="stig_checklist", metadata={"scan_id": "s1"}
        )
        for c in chunks:
            assert c.metadata["scan_id"] == "s1"

    def test_total_chunks_and_index_consistent(self):
        chunks = chunk_content(OSCAL_DOC, template="oscal_catalog")
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert all(c.total_chunks == len(chunks) for c in chunks)

    def test_content_hash_computed(self):
        for c in chunk_content(OSCAL_DOC, template="oscal_catalog"):
            assert len(c.content_hash) == 64

    def test_chunk_fields_forwards_template(self):
        chunks = chunk_fields(
            fields={"body": STIG_DOC},
            field_names=["body"],
            template="stig_checklist",
        )
        assert chunks[0].metadata["chunking_template"] == "stig_checklist"

    def test_empty_content_still_returns_nothing(self):
        assert chunk_content("", template="oscal_catalog") == []
        assert chunk_content("   ", template="stig_checklist") == []


# --- Suggestion (advisory only) ---------------------------------------------


class TestSuggestTemplate:
    @pytest.mark.parametrize(
        "doc,expected",
        [
            (OSCAL_DOC, "oscal_catalog"),
            (STIG_DOC, "stig_checklist"),
            (RFP_DOC, "rfp_sow"),
            (SLIDE_DOC, "slide_deck"),
        ],
    )
    def test_suggests_the_right_template(self, doc, expected):
        assert suggest_template(doc)["suggested"] == expected

    def test_always_marked_advisory(self):
        assert suggest_template(OSCAL_DOC)["advisory"] is True

    def test_unrecognized_content_falls_back_to_default(self):
        result = suggest_template("Just some prose with nothing structural in it.")
        assert result["suggested"] == default_template_name()
        assert result["confidence"] == 0.0

    def test_confidence_between_zero_and_one(self):
        for doc in (OSCAL_DOC, STIG_DOC, RFP_DOC, CONTRACT_DOC, SOP_DOC, SLIDE_DOC):
            assert 0.0 <= suggest_template(doc)["confidence"] <= 1.0


# --- Registry wiring --------------------------------------------------------


class TestRegistryWiring:
    def test_get_chunking_template_reads_the_key(self):
        source = next(
            k for k, v in SOURCE_REGISTRY.items() if v.get("chunking") == "canvas_graph"
        )
        assert get_chunking_template(source) == "canvas_graph"

    def test_missing_key_returns_empty_string(self):
        source = next(
            k for k, v in SOURCE_REGISTRY.items() if not v.get("chunking")
        )
        assert get_chunking_template(source) == ""

    def test_unknown_source_returns_empty_string(self):
        assert get_chunking_template("no_such_source") == ""

    def test_ingestion_manager_passes_the_template(self, monkeypatch):
        """ingest_single_record must forward the registry template (was dead)."""
        import importlib

        im = importlib.import_module("tools.rag.ingestion_manager")
        captured = {}

        def fake_chunk_fields(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(im, "chunk_fields", fake_chunk_fields)
        monkeypatch.setattr(im, "_get_embedding_provider", lambda: object())
        monkeypatch.setattr(
            im.VectorStoreFactory, "create", staticmethod(lambda **kw: object())
        )

        source = next(
            k for k, v in SOURCE_REGISTRY.items() if v.get("chunking") == "canvas_graph"
        )
        im.ingest_single_record(source, {"id": "1"})
        assert captured["template"] == "canvas_graph"


# --- split_content unit level -----------------------------------------------


class TestSplitContent:
    def test_sliding_window_template_defers_to_caller(self):
        _, tmpl, _ = get_template("general")
        result = split_content(OSCAL_DOC, tmpl)
        assert result.segments == []
        assert result.fallback_reason == "sliding_window"

    def test_bad_pattern_is_reported_not_raised(self):
        result = split_content("text", {"strategy": "structural", "boundary_patterns": ["("]})
        assert result.pattern_errors
        assert result.fallback_reason == "no_usable_boundary_patterns"

    def test_segment_labels_populated(self):
        _, tmpl, _ = get_template("stig_checklist")
        result = split_content(STIG_DOC, tmpl)
        assert all(s.label for s in result.segments)
