# CUI // SP-CTI
"""oss2-fix-02 (D2) — DIC ingest now reaches the template chunking capability.

Template chunking (oscal_catalog / stig_checklist / rfp_sow / …) shipped in
oss-chunk-01, but ingest_orchestrator.ingest_file called chunk_content WITHOUT
template=, so every DIC document got general sliding-window chunking and structured
controls were split mid-control. The fix resolves a template (explicit wins, else
advisory auto-detect that safely defaults to general) and passes it through.
"""
from __future__ import annotations

import importlib

io = importlib.import_module("tools.document_intelligence.ingest_orchestrator")


def test_explicit_template_always_wins():
    tmpl, reason = io._resolve_chunk_template("any content", "stig_checklist")
    assert tmpl == "stig_checklist"
    assert reason == "explicit"


def test_auto_detect_used_when_no_explicit(monkeypatch):
    monkeypatch.setattr(
        io, "suggest_template",
        lambda text, **k: {"suggested": "oscal_catalog", "reason": "matched AC-1 markers"},
    )
    tmpl, reason = io._resolve_chunk_template("AC-1 POLICY AND PROCEDURES ...", None)
    assert tmpl == "oscal_catalog"
    assert "auto" in reason


def test_auto_detect_defaults_to_general_when_unsure(monkeypatch):
    # suggest_template returns the default when nothing scores above min_score.
    monkeypatch.setattr(io, "suggest_template", lambda text, **k: {"suggested": "general", "reason": "no template matched"})
    tmpl, _ = io._resolve_chunk_template("just some prose", None)
    assert tmpl == "general"


def test_missing_suggestion_falls_back_to_general(monkeypatch):
    monkeypatch.setattr(io, "suggest_template", lambda text, **k: {})  # defensive
    tmpl, _ = io._resolve_chunk_template("x", None)
    assert tmpl == "general"


def test_chunk_content_accepts_the_template_kwarg():
    """Regression guard that the plumbing the fix depends on exists: chunk_content
    has a template= parameter (the capability the DIC pipeline now reaches)."""
    import inspect

    from tools.rag.chunker import chunk_content

    assert "template" in inspect.signature(chunk_content).parameters


def test_ingest_file_exposes_chunk_template_param():
    """ingest_file must accept chunk_template so a caller/operator can override."""
    import inspect

    assert "chunk_template" in inspect.signature(io.ingest_file).parameters
