# CUI // SP-CTI
"""Tests: leaked CoT/CoD reasoning scrubber for TRUST-compliant DIC content.

Ensures generated section prose never carries 'Step 1:', '[INSTRUCTION]',
'Critique E1-E3', or meta-narration into published content.
"""

import importlib

dg = importlib.import_module("tools.document_intelligence.doc_generator")
strip = dg._strip_reasoning_artifacts

# The exact class of leak the reviewer caught in the HITL queue.
_LEAKED = (
    "**Step 1: Analyze the Input Data and Constraints**\n"
    "The user has provided a task description that includes \"reasoning steps,\" an "
    "\"[INSTRUCTION]\" to break down those steps, show reasoning, and provide a final "
    "answer. However, the input also contains embedded meta-data indicating previous "
    "failures (Critique E1-E3), missing chunk references for key quotes, and incomplete "
    "reference titles (\"truncated\").\n\n"
    "Final answer:\n"
    "Peering is a settlement-free interconnection between two networks that exchange "
    "traffic directly [source: chunk 12ab]. Transit, by contrast, is a paid service."
)

_CLEAN = (
    "Peering is a settlement-free interconnection between two autonomous systems "
    "[source: chunk 9f]. Internet Exchange Points (IXPs) reduce transit costs."
)


class TestScrubber:
    def test_extracts_after_final_marker(self):
        out = strip(_LEAKED)
        assert "Step 1:" not in out
        assert "[INSTRUCTION]" not in out
        assert "Critique E1" not in out
        assert "The user has provided" not in out
        assert "Peering is a settlement-free interconnection" in out
        assert "[source: chunk 12ab]" in out  # citation preserved

    def test_clean_content_is_noop(self):
        assert strip(_CLEAN) == _CLEAN

    def test_strips_inline_control_tokens(self):
        out = strip("Peering exchanges traffic [SYNTHESIS] directly [JUDGMENT] between peers.")
        assert "[SYNTHESIS]" not in out and "[JUDGMENT]" not in out
        assert "Peering exchanges traffic" in out

    def test_drops_reasoning_paragraphs(self):
        text = (
            "Step 2: I will break down the requirements.\n\n"
            "BGP is the protocol that exchanges routing information between autonomous systems."
        )
        out = strip(text)
        assert "Step 2:" not in out
        assert "BGP is the protocol" in out

    def test_empty_and_whitespace(self):
        assert strip("") == ""
        assert strip("   ") == "   "

    def test_all_reasoning_falls_back_to_token_stripped(self):
        # No real prose, only reasoning + tokens → don't silently empty; strip tokens.
        text = "Let me analyze the task. [INSTRUCTION] I will identify the constraints."
        out = strip(text)
        assert "[INSTRUCTION]" not in out
        assert out  # not empty


class TestResidueDetectorAndGate:
    def test_residue_detected_on_leaked(self):
        assert dg._has_reasoning_residue("Step 1: analyze the input and constraints.") is True
        assert dg._has_reasoning_residue("The prompt asks to compress the following section.") is True

    def test_clean_prose_no_residue(self):
        assert dg._has_reasoning_residue(
            "Peering is a settlement-free interconnection [source: chunk 1]."
        ) is False

    def test_cot_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("ICDEV_DIC_COT_ENABLED", raising=False)
        assert dg._dic_cot_enabled() is False
        monkeypatch.setenv("ICDEV_DIC_COT_ENABLED", "true")
        assert dg._dic_cot_enabled() is True
