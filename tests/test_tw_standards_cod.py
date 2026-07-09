"""Tech Writer standards-reference validation + CoD gating (ground-tw-04).

Covers:
  - args/tw_standards_whitelist.yaml loads and has all four families
  - validate_standards_references: valid ids pass; unknown/malformed flagged
  - References-section scoping
  - research_and_draft surfaces standards warnings in ResearchResult.warnings
  - ARCH_* drafting routes through Chain of Debate only when
    ICDEV_TW_COD_ENABLED is set, with single-shot fallback on CoD failure
"""
from __future__ import annotations

import sys
import types


import tools.document_intelligence.tech_writing_assist as twmod
from tools.document_intelligence.tech_writing_assist import (
    _load_standards_whitelist,
    validate_standards_references,
)


# ── Whitelist yaml ─────────────────────────────────────────────────────────────

def test_whitelist_yaml_loads_all_families():
    wl = _load_standards_whitelist()
    assert wl, "args/tw_standards_whitelist.yaml must exist and parse"
    assert "53" in [str(n) for n in wl["nist_sp_800"]]
    assert wl["cmmc"]["levels"] == [1, 2, 3]
    assert "AC" in wl["cmmc"]["domains"]
    assert "Moderate" in wl["fedramp_baselines"]
    assert wl["stig"]["vuln_id_min_digits"] >= 1


# ── validate_standards_references ─────────────────────────────────────────────

def test_valid_citations_produce_no_warnings():
    text = (
        "## References\n"
        "- NIST SP 800-53 Rev 5\n"
        "- NIST SP 800-171A\n"
        "- CMMC Level 2 practice AC.L2-3.1.1\n"
        "- FedRAMP Moderate baseline\n"
        "- STIG V-214723 and SRG-OS-000001-GPOS-00001\n"
    )
    assert validate_standards_references(text) == []


def test_unknown_nist_number_flagged():
    warnings = validate_standards_references("References\nNIST SP 800-9999 applies.")
    assert any("800-9999" in w for w in warnings)


def test_malformed_nist_missing_sp_flagged():
    warnings = validate_standards_references("References\nPer NIST 800-53, controls apply.")
    assert any("malformed" in w and "NIST 800-53" in w for w in warnings)
    # Properly-cited SP form must not double-flag
    assert validate_standards_references("References\nPer NIST SP 800-53.") == []


def test_unknown_cmmc_level_and_domain_flagged():
    warnings = validate_standards_references("References\nCMMC Level 7 and ZZ.L2-3.1.1")
    assert any("CMMC level '7'" in w for w in warnings)
    assert any("ZZ.L2-3.1.1" in w for w in warnings)


def test_unknown_fedramp_baseline_flagged():
    warnings = validate_standards_references("References\nFedRAMP Ultra baseline required.")
    assert any("FedRAMP Ultra" in w for w in warnings)


def test_malformed_stig_vuln_id_flagged():
    warnings = validate_standards_references("References\nSee V-12 for details.")
    assert any("V-12" in w for w in warnings)


def test_unknown_srg_component_flagged():
    warnings = validate_standards_references("References\nSRG-XYZQ-000001 applies.")
    assert any("SRG-XYZQ-000001" in w for w in warnings)


def test_scoped_to_references_section_when_present():
    text = (
        "Body cites NIST SP 800-9999 loosely.\n\n"
        "## References\n- NIST SP 800-53\n"
    )
    assert validate_standards_references(text) == []


def test_whole_text_scanned_without_references_heading():
    warnings = validate_standards_references("Body cites NIST SP 800-9999 loosely.")
    assert any("800-9999" in w for w in warnings)


def test_empty_text_no_warnings():
    assert validate_standards_references("") == []


# ── research_and_draft integration ────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeRouter:
    def __init__(self, content="draft"):
        self._content = content
        self.invoked = []

    def invoke(self, function, req):
        self.invoked.append(function)
        return _FakeResponse(self._content)


class _FakeRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _run_draft(monkeypatch, router, template_type=""):
    monkeypatch.setattr(twmod, "is_airgap", lambda **kw: True)
    monkeypatch.setattr(twmod, "RAGRetriever", None)
    monkeypatch.setattr(twmod, "kg_retrieve", None)
    monkeypatch.setattr(twmod, "LLMRouter", lambda: router)
    monkeypatch.setattr(twmod, "LLMRequest", _FakeRequest)
    return twmod.research_and_draft(
        query="zero trust segmentation",
        section_heading="Security Architecture",
        template_type=template_type,
    )


def test_research_and_draft_surfaces_standards_warnings(monkeypatch):
    router = _FakeRouter("## References\nNIST SP 800-9999 and FedRAMP Ultra baseline.")
    result = _run_draft(monkeypatch, router)
    assert any("800-9999" in w for w in result.warnings)
    assert any("FedRAMP Ultra" in w for w in result.warnings)


def test_clean_draft_has_no_standards_warnings(monkeypatch):
    router = _FakeRouter("## References\nNIST SP 800-53 and FedRAMP High baseline.")
    result = _run_draft(monkeypatch, router)
    assert [w for w in result.warnings if "Standards check" in w] == []


# ── CoD gating for ARCH_* templates ───────────────────────────────────────────

def _install_fake_cod(monkeypatch, content="cod draft", raise_exc=None):
    calls = []

    class FakeOrch:
        def __init__(self, router=None):
            pass

        def invoke_chain_of_debate(self, function, req):
            calls.append(function)
            if raise_exc:
                raise raise_exc
            return types.SimpleNamespace(content=content, models_used=["m1", "m2"])

    fake_mod = types.ModuleType("tools.llm.chain_orchestrator")
    fake_mod.ChainOrchestrator = FakeOrch
    monkeypatch.setitem(sys.modules, "tools.llm.chain_orchestrator", fake_mod)
    return calls


def test_cod_enabled_by_default_for_arch(monkeypatch):
    # halluc-02: CoD defaults ON for ARCH_* sections (matches RFI's default).
    monkeypatch.delenv("ICDEV_TW_COD_ENABLED", raising=False)
    calls = _install_fake_cod(monkeypatch, content="cod draft")
    router = _FakeRouter("single-shot draft")
    result = _run_draft(monkeypatch, router, template_type="ARCH_SYSTEM")
    assert calls == ["tech_writing_draft"], "CoD must run by default for ARCH sections"
    assert result.draft_content == "cod draft"
    assert router.invoked == []


def test_cod_off_when_explicitly_disabled(monkeypatch):
    # Operators can still opt out (cost-constrained / air-gapped).
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "false")
    calls = _install_fake_cod(monkeypatch)
    router = _FakeRouter("single-shot draft")
    result = _run_draft(monkeypatch, router, template_type="ARCH_SYSTEM")
    assert calls == []
    assert result.draft_content == "single-shot draft"
    assert router.invoked == ["tech_writing_draft"]


def test_cod_used_for_arch_when_enabled(monkeypatch):
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")
    calls = _install_fake_cod(monkeypatch, content="cod draft")
    router = _FakeRouter("single-shot draft")
    result = _run_draft(monkeypatch, router, template_type="ARCH_NETWORK")
    assert calls == ["tech_writing_draft"]
    assert result.draft_content == "cod draft"
    assert router.invoked == [], "single-shot must not run when CoD succeeds"


def test_cod_not_used_for_non_arch_templates(monkeypatch):
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")
    calls = _install_fake_cod(monkeypatch)
    router = _FakeRouter("single-shot draft")
    result = _run_draft(monkeypatch, router, template_type="SOP")
    assert calls == []
    assert result.draft_content == "single-shot draft"


def test_cod_failure_falls_back_to_single_shot(monkeypatch):
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")
    _install_fake_cod(monkeypatch, raise_exc=RuntimeError("debate exploded"))
    router = _FakeRouter("single-shot draft")
    result = _run_draft(monkeypatch, router, template_type="ARCH_APPLICATION")
    assert result.draft_content == "single-shot draft"
    assert result.error == ""


def test_cod_empty_content_falls_back(monkeypatch):
    monkeypatch.setenv("ICDEV_TW_COD_ENABLED", "true")
    _install_fake_cod(monkeypatch, content="   ")
    router = _FakeRouter("single-shot draft")
    result = _run_draft(monkeypatch, router, template_type="ARCH_SYSTEM")
    assert result.draft_content == "single-shot draft"


def test_tw_cod_flag_parsing(monkeypatch):
    # halluc-02: default ON (unset -> True); explicit falsey values disable.
    monkeypatch.delenv("ICDEV_TW_COD_ENABLED", raising=False)
    assert twmod._tw_cod_enabled() is True
    for val in ("1", "true", "YES"):
        monkeypatch.setenv("ICDEV_TW_COD_ENABLED", val)
        assert twmod._tw_cod_enabled() is True
    for val in ("false", "0", "no"):
        monkeypatch.setenv("ICDEV_TW_COD_ENABLED", val)
        assert twmod._tw_cod_enabled() is False
