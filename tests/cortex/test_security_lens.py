# CUI // SP-CTI
"""Tests for the security-intelligence domain lens (ctx-canvas-04).

The lens is a configuration profile over existing Cortex machinery. These
tests pin the three observable behaviors from the acceptance criteria:

1. Scope — security mode restricts search results to security source
   collections (row-level ``sources`` prefix filter), observable in metadata.
2. Persona — the SOC-analyst system prompt is injected when the lens is
   active, and NOT injected in general mode (switching back restores default).
3. Triage — a threat query produces an XSIAM-style top-risks / blast-radius /
   recommended-actions summary.

The strategy router is exercised with an explicit ``config=`` so the tests do
not depend on args/cortex_config.yaml edits; backend adapters are
monkeypatched via ``search_service.BACKEND_ADAPTERS``.
"""
from __future__ import annotations

from tools.cortex import domains
from tools.cortex import search_service
from tools.cortex.domains import security
from tools.cortex.schemas import Citation, CortexContext, CortexSearchResult


# A self-contained security lens config (mirrors args/cortex_config.yaml).
SECURITY_CONFIG = {
    "search": {
        "router": {"factual_confidence": 0.75},
        "timeouts": {"default": 5.0, "rag": 5.0, "graph": 5.0, "kb": 5.0},
        "fan_out": {"backends": ["rag", "graph", "dic"], "max_workers": 4},
        "domains": {
            "security": {
                "display_name": "Security Intelligence",
                "backends": ["rag", "graph", "kb"],
                "collections": ["pvm_predictions", "dsoc_threats"],
                "sources": ["pvm_", "dsoc_", "incident_", "cve", "threat", "vuln"],
                "intents": ["threat_hunt", "vuln_triage"],
                "triage": True,
                "persona": security.DEFAULT_SECURITY_PERSONA,
            }
        },
    }
}


def _hit(backend, source_table, score=0.5, title="", content="", **meta):
    return CortexSearchResult(
        content=content or f"{backend} hit from {source_table}",
        score=score,
        backend=backend,
        strategy="native",
        citation=Citation(
            source_id=f"{source_table}-1",
            source_type=f"{backend}_src",
            source_table=source_table,
            title=title,
        ),
        metadata=dict(meta),
    )


# ---------------------------------------------------------------------------
# Profile loading — data-driven, YAML over code defaults
# ---------------------------------------------------------------------------
def test_load_security_profile_from_config():
    profile = domains.load_domain_profile("security", config=SECURITY_CONFIG)

    assert profile is not None
    assert profile.name == "security"
    assert profile.display_name == "Security Intelligence"
    assert profile.backends == ["rag", "graph", "kb"]
    assert "pvm_" in profile.sources
    assert profile.triage is True
    assert "SOC" in profile.persona or "Security Operations Center" in profile.persona


def test_security_profile_falls_back_to_code_defaults():
    # Empty config: no YAML block, so the code-side SECURITY_DEFAULTS fills in.
    profile = domains.load_domain_profile("security", config={})

    assert profile is not None
    assert profile.backends == security.SECURITY_DEFAULTS["backends"]
    assert profile.persona == security.DEFAULT_SECURITY_PERSONA
    assert profile.triage is True


def test_yaml_overrides_code_defaults_field_by_field():
    cfg = {"search": {"domains": {"security": {"backends": ["rag"]}}}}
    profile = domains.load_domain_profile("security", config=cfg)

    # YAML wins for the overridden field...
    assert profile.backends == ["rag"]
    # ...and code defaults fill the rest (persona not in this YAML).
    assert profile.persona == security.DEFAULT_SECURITY_PERSONA


def test_unknown_domain_returns_none():
    assert domains.load_domain_profile("nope", config=SECURITY_CONFIG) is None
    assert domains.load_domain_profile("", config=SECURITY_CONFIG) is None


def test_list_and_intents():
    assert "security" in domains.list_domain_names(config=SECURITY_CONFIG)
    assert domains.domain_intents("security", config=SECURITY_CONFIG) == [
        "threat_hunt",
        "vuln_triage",
    ]
    assert domains.domain_intents("nope", config=SECURITY_CONFIG) == []


# ---------------------------------------------------------------------------
# Row-level source scope
# ---------------------------------------------------------------------------
def test_filter_by_sources_keeps_only_security_sources():
    results = [
        _hit("rag", "pvm_predictions", score=0.9),
        _hit("rag", "dsoc_threats", score=0.8),
        _hit("rag", "proposals", score=0.7),          # out of scope
        _hit("rag", "general_documents", score=0.6),  # out of scope
    ]
    kept, dropped = domains.filter_by_sources(
        results, ["pvm_", "dsoc_", "incident_"]
    )

    assert [r.citation.source_table for r in kept] == ["pvm_predictions", "dsoc_threats"]
    assert len(dropped) == 2


def test_filter_matches_cve_source_id():
    # A CVE hit carries the identifier on source_id, not source_table.
    hit = CortexSearchResult(
        content="CVE-2024-9999 exploited in the wild",
        score=0.95,
        backend="kb",
        citation=Citation(source_id="CVE-2024-9999", source_table="knowledge_patterns"),
    )
    kept, dropped = domains.filter_by_sources([hit], ["cve"])
    assert kept == [hit]
    assert dropped == []


def test_filter_empty_sources_passes_everything():
    results = [_hit("rag", "proposals"), _hit("rag", "anything")]
    kept, dropped = domains.filter_by_sources(results, [])
    assert kept == results
    assert dropped == []


# ---------------------------------------------------------------------------
# End-to-end: search() honors the domain source scope (observable in metadata)
# ---------------------------------------------------------------------------
def test_security_mode_scopes_search_results(monkeypatch):
    def rag(query, top_k=5, ctx=None):
        return [
            _hit("rag", "pvm_predictions", score=0.9),
            _hit("rag", "dsoc_threats", score=0.7),
            _hit("rag", "proposals", score=0.6),  # non-security -> dropped
        ]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)

    results = search_service.search(
        "active exploitation of our exposed services",
        ctx=CortexContext(domain="security"),
        strategy="rag",  # pin backend so the test is deterministic
        config=SECURITY_CONFIG,
    )

    # Only the two security-source hits survive.
    tables = sorted(r.citation.source_table for r in results)
    assert tables == ["dsoc_threats", "pvm_predictions"]

    scope = results[0].metadata["router"]["domain_scope"]
    assert scope["domain"] == "security"
    assert scope["filtered_out"] == 1
    assert "pvm_" in scope["sources"]


def test_general_mode_does_not_scope(monkeypatch):
    def rag(query, top_k=5, ctx=None):
        return [
            _hit("rag", "pvm_predictions", score=0.9),
            _hit("rag", "proposals", score=0.6),
        ]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)

    # No domain -> general behavior: nothing dropped, no domain_scope recorded.
    results = search_service.search(
        "recent activity", strategy="rag", config=SECURITY_CONFIG
    )

    assert len(results) == 2
    assert "domain_scope" not in results[0].metadata["router"]


# ---------------------------------------------------------------------------
# Persona injection — active in security mode, absent in general mode
# ---------------------------------------------------------------------------
def test_persona_injected_in_security_mode():
    prompt = domains.apply_persona(
        CortexContext(domain="security"), config=SECURITY_CONFIG
    )
    assert "SOC" in prompt or "Security Operations Center" in prompt
    assert "severity" in prompt.lower()


def test_persona_prepended_to_base_prompt():
    base = "Answer the question."
    prompt = domains.apply_persona(
        CortexContext(domain="security"), base_system_prompt=base, config=SECURITY_CONFIG
    )
    assert prompt.endswith(base)
    assert prompt != base  # persona was prepended


def test_general_mode_restores_default_no_persona():
    # Switching back to general mode (no domain) leaves the base prompt intact.
    assert domains.apply_persona(CortexContext(), config=SECURITY_CONFIG) == ""
    assert (
        domains.apply_persona(
            CortexContext(), base_system_prompt="base", config=SECURITY_CONFIG
        )
        == "base"
    )


# ---------------------------------------------------------------------------
# XSIAM-style triage summary
# ---------------------------------------------------------------------------
def test_triage_summary_structure_and_severity_ranking():
    results = [
        _hit("rag", "pvm_predictions", score=0.92, title="Critical RCE predicted"),
        _hit("dic", "incident_reports", score=0.65, title="Open incident"),
        _hit("graph", "kg_nodes", score=0.5, title="core-router", entity_type="asset"),
        _hit("kb", "knowledge_patterns", score=0.3, title="Old advisory"),
    ]

    triage = security.triage_summary(results, query="what is exploitable right now")

    assert triage["domain"] == "security"
    assert triage["result_count"] == 4
    # Severity-ranked, highest first.
    assert triage["top_risks"][0]["severity"] == "critical"
    assert triage["top_risks"][0]["score"] == 0.92
    assert [r["severity"] for r in triage["top_risks"]] == [
        "critical",
        "high",
        "medium",
        "low",
    ]
    # Blast radius reflects the graph entity + touched source tables.
    assert triage["blast_radius"]["entity_count"] >= 1
    assert "kg_nodes" in triage["blast_radius"]["affected_sources"]
    # Actions derived from the pvm_/incident_ sources present.
    joined = " ".join(triage["recommended_actions"]).lower()
    assert "patch" in joined
    # Rendered brief leads with severity language.
    assert "Top risks" in triage["text"]


def test_triage_via_domains_summarize_dispatch():
    results = [_hit("rag", "dsoc_threats", score=0.8, title="Active C2 beacon")]
    summary = domains.summarize(
        results, ctx=CortexContext(domain="security"), query="threats", config=SECURITY_CONFIG
    )
    assert summary is not None
    assert summary["top_risks"][0]["title"] == "Active C2 beacon"


def test_summarize_returns_none_in_general_mode():
    results = [_hit("rag", "dsoc_threats", score=0.8)]
    assert domains.summarize(results, ctx=CortexContext(), config=SECURITY_CONFIG) is None


def test_severity_bands():
    assert security.severity_for_score(0.95) == "critical"
    assert security.severity_for_score(0.8) == "critical"
    assert security.severity_for_score(0.79) == "high"
    assert security.severity_for_score(0.6) == "high"
    assert security.severity_for_score(0.4) == "medium"
    assert security.severity_for_score(0.1) == "low"
    assert security.severity_for_score("bogus") == "low"


def test_empty_results_triage_is_safe():
    triage = security.triage_summary([], query="nothing")
    assert triage["result_count"] == 0
    assert triage["top_risks"] == []
    assert "No security-scoped findings" in triage["text"]


# ---------------------------------------------------------------------------
# Namespace parity
# ---------------------------------------------------------------------------
def test_importable_via_canonical_namespace():
    from icdev.tools.cortex.domains import (  # noqa: F401
        SECURITY_DOMAIN,
        apply_persona,
        filter_by_sources,
        load_domain_profile,
        triage_summary,
    )

    assert SECURITY_DOMAIN == "security"
