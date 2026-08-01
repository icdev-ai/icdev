"""Tests for ground-tw-04: standards-reference validation + CoD gating in
tools/document_intelligence/tech_writing_assist.py."""
import importlib

import pytest

twa = importlib.import_module("tools.document_intelligence.tech_writing_assist")


@pytest.fixture(autouse=True)
def _fresh_whitelist_cache():
    twa._whitelist_cache = None
    yield
    twa._whitelist_cache = None


# ── validate_standards_references ─────────────────────────────────────────────

def test_known_identifiers_produce_no_warnings():
    text = (
        "## References\n"
        "- NIST SP 800-53 Rev 5\n"
        "- NIST SP 800-171\n"
        "- CMMC Level 2 (AC.L2-3.1.1)\n"
        "- FedRAMP Moderate baseline\n"
        "- SRG-APP-000001, V-12345\n"
    )
    assert twa.validate_standards_references(text) == []


def test_unknown_nist_publication_flagged():
    warnings = twa.validate_standards_references("## References\n- NIST SP 800-999\n")
    assert any("SP 800-999" in w for w in warnings)


def test_malformed_nist_citation_flagged():
    warnings = twa.validate_standards_references("## References\nSee NIST SP 800- for details.\n")
    assert any("malformed NIST" in w for w in warnings)


def test_unknown_cmmc_level_flagged():
    warnings = twa.validate_standards_references("## References\n- CMMC Level 7\n")
    assert any("CMMC level '7'" in w for w in warnings)


def test_unknown_cmmc_practice_domain_flagged():
    warnings = twa.validate_standards_references("## References\n- ZZ.L2-3.1.1\n")
    assert any("practice domain 'ZZ'" in w for w in warnings)


def test_unknown_fedramp_baseline_flagged():
    warnings = twa.validate_standards_references("## References\n- FedRAMP Extreme baseline\n")
    assert any("FedRAMP baseline 'Extreme'" in w for w in warnings)


def test_unknown_srg_family_flagged():
    warnings = twa.validate_standards_references("## References\n- SRG-XYZ-000001\n")
    assert any("SRG family 'XYZ'" in w for w in warnings)


def test_malformed_stig_v_id_flagged():
    warnings = twa.validate_standards_references("## References\n- V-12 applies here\n")
    assert any("V-12" in w for w in warnings)


def test_scopes_to_references_section_when_present():
    # Bogus id in the body, clean References section → no warnings.
    text = (
        "## Design\nWe considered NIST SP 800-999 informally.\n\n"
        "## References\n- NIST SP 800-53\n"
    )
    assert twa.validate_standards_references(text) == []


def test_scans_full_text_when_no_references_section():
    warnings = twa.validate_standards_references("Complies with NIST SP 800-999.")
    assert any("SP 800-999" in w for w in warnings)


def test_empty_text_and_missing_whitelist_are_safe(tmp_path, monkeypatch):
    assert twa.validate_standards_references("") == []
    monkeypatch.setattr(twa, "_WHITELIST_PATH", tmp_path / "nope.yaml")
    assert twa.validate_standards_references("NIST SP 800-999") == []


def test_duplicate_citations_warn_once():
    warnings = twa.validate_standards_references(
        "## References\n- NIST SP 800-999\n- NIST SP 800-999\n"
    )
    assert len([w for w in warnings if "800-999" in w]) == 1


# ── CoD gating ────────────────────────────────────────────────────────────────

def test_tw_cod_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ICDEV_TW_COD_ENABLED", raising=False)
    assert twa._tw_cod_enabled() is False


def test_tw_cod_enabled_via_env(monkeypatch):
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")
    assert twa._tw_cod_enabled() is True
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "0")
    assert twa._tw_cod_enabled() is False


def test_cod_failure_falls_back_to_single_shot(monkeypatch):
    """ARCH_* + flag on + CoD raising → single-shot draft with a warning."""
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")

    class _Resp:
        content = "single-shot draft"

    class _Router:
        def invoke(self, fn, req):
            assert fn == "tech_writing_draft"
            return _Resp()

    class _Req:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(twa, "LLMRouter", _Router)
    monkeypatch.setattr(twa, "LLMRequest", _Req)
    monkeypatch.setattr(twa, "RAGRetriever", None)
    monkeypatch.setattr(twa, "kg_retrieve", None)
    monkeypatch.setattr(twa, "is_airgap", lambda **kw: True)
    monkeypatch.setattr(
        twa, "_cod_draft",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("cod down")),
    )
    # The single-shot fallback now routes through cortex.complete (adoption pilot);
    # bridge it to the fake router so no real LLM is called.
    import tools.cortex.api as _cx
    from tools.cortex.schemas import CortexResult as _CR
    monkeypatch.setattr(
        _cx, "complete",
        lambda prompt, function="", ctx=None, system_prompt="", **kw:
            _CR(text=_Router().invoke(function, _Req()).content),
    )

    result = twa.research_and_draft("segmentation strategy", "Design", template_type="ARCH_NETWORK")
    assert result.draft_content == "single-shot draft"
    assert any("CoD draft failed" in w for w in result.warnings)


def test_cod_used_for_arch_templates_when_enabled(monkeypatch):
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")

    class _Router:
        def invoke(self, fn, req):
            raise AssertionError("single-shot should not run when CoD succeeds")

    class _Req:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(twa, "LLMRouter", _Router)
    monkeypatch.setattr(twa, "LLMRequest", _Req)
    monkeypatch.setattr(twa, "RAGRetriever", None)
    monkeypatch.setattr(twa, "kg_retrieve", None)
    monkeypatch.setattr(twa, "is_airgap", lambda **kw: True)
    monkeypatch.setattr(twa, "_cod_draft", lambda *a, **kw: "debated draft")

    result = twa.research_and_draft("api contracts", "Design", template_type="ARCH_APPLICATION")
    assert result.draft_content == "debated draft"


def test_non_arch_template_skips_cod(monkeypatch):
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")

    class _Resp:
        content = "sop draft"

    class _Router:
        def invoke(self, fn, req):
            return _Resp()

    class _Req:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(twa, "LLMRouter", _Router)
    monkeypatch.setattr(twa, "LLMRequest", _Req)
    monkeypatch.setattr(twa, "RAGRetriever", None)
    monkeypatch.setattr(twa, "kg_retrieve", None)
    monkeypatch.setattr(twa, "is_airgap", lambda **kw: True)
    monkeypatch.setattr(
        twa, "_cod_draft",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("CoD must not run for SOP")),
    )
    # Single-shot now routes through cortex.complete; bridge to the fake router.
    import tools.cortex.api as _cx
    from tools.cortex.schemas import CortexResult as _CR
    monkeypatch.setattr(
        _cx, "complete",
        lambda prompt, function="", ctx=None, system_prompt="", **kw:
            _CR(text=_Router().invoke(function, _Req()).content),
    )

    result = twa.research_and_draft("backup procedure", "Steps", template_type="SOP")
    assert result.draft_content == "sop draft"
