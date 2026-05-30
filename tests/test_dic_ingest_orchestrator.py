"""Tests for the DIC ingest orchestrator (dic-ingest-03).

[TEMPLATE: CUI // SP-CTI]

Embedding and KG bridging are disabled so the suite runs headless without an
LLM router or vector store; the focus is provider routing + DIC row writes
(dic_documents / dic_versions / dic_chunk_links) with tenant/classification
stamps.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.db.storage import get_connection, set_security_context
from tools.document_intelligence import ingest_orchestrator as orch
from tools.document_intelligence.ingest_orchestrator import ingest_file


@pytest.fixture
def sample_doc(tmp_path: Path) -> Path:
    p = tmp_path / "policy.md"
    p.write_text(
        "# Acme Security Policy\n\n"
        + ("All access requires multi-factor authentication. " * 80),
        encoding="utf-8",
    )
    return p


def _rows(table: str, version_id: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE version_id = ?", (version_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def test_provider_routing_picks_builtin_text(sample_doc: Path):
    extraction = orch._select_extractor(sample_doc)
    assert extraction.provider in ("builtin-text", "builtin-fallback")
    assert "multi-factor" in extraction.text


def test_ingest_writes_dic_rows_with_stamps(sample_doc: Path):
    outcome = ingest_file(
        str(sample_doc),
        "test_collection",
        tenant_id="acme",
        classification="CUI",
        created_by="alice",
        embed=False,
        bridge_kg=False,
    )

    assert outcome.chunks >= 1
    assert outcome.tenant_id == "acme"
    assert outcome.classification == "CUI"
    assert outcome.source_id.startswith("src_")

    # dic_documents row.
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT collection_id, tenant_id, classification, provider, source_id "
            "FROM dic_documents WHERE doc_id = ?",
            (outcome.doc_id,),
        )
        doc = cur.fetchone()
    finally:
        conn.close()
    assert doc is not None
    assert doc[0] == "test_collection"
    assert doc[1] == "acme"
    assert doc[2] == "CUI"
    assert doc[4] == outcome.source_id

    # dic_versions: initial version is human_authored / approved.
    versions = _rows("dic_versions", outcome.version_id)
    assert len(versions) == 1
    assert versions[0]["origin"] == "human_authored"
    assert versions[0]["status"] == "approved"
    assert versions[0]["tenant_id"] == "acme"
    assert versions[0]["classification"] == "CUI"

    # dic_chunk_links: one per chunk, mapping rag chunk id back to the doc.
    links = _rows("dic_chunk_links", outcome.version_id)
    assert len(links) == outcome.chunks
    for i, link in enumerate(sorted(links, key=lambda r: r["chunk_index"])):
        assert link["doc_id"] == outcome.doc_id
        assert link["rag_chunk_id"] == f"{outcome.source_id}_chunk_{link['chunk_index']}"
        assert link["tenant_id"] == "acme"
        assert link["classification"] == "CUI"


def test_reingest_is_idempotent(sample_doc: Path):
    first = ingest_file(
        str(sample_doc), "test_collection2", embed=False, bridge_kg=False
    )
    second = ingest_file(
        str(sample_doc), "test_collection2", embed=False, bridge_kg=False
    )
    assert first.doc_id == second.doc_id
    assert first.version_id == second.version_id
    links = _rows("dic_chunk_links", second.version_id)
    assert len(links) == second.chunks  # not doubled


def test_context_falls_back_to_security_context(sample_doc: Path):
    set_security_context(tenant_id="ctx_tenant", classification="SECRET")
    try:
        outcome = ingest_file(
            str(sample_doc), "ctx_collection", embed=False, bridge_kg=False
        )
        assert outcome.tenant_id == "ctx_tenant"
        assert outcome.classification == "SECRET"
    finally:
        set_security_context(tenant_id=None, classification=None)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        ingest_file("does_not_exist_xyz.md", "c", embed=False, bridge_kg=False)


def test_cli_json(sample_doc: Path, capsys):
    from tools.document_intelligence.__main__ import main

    rc = main(
        [
            "--ingest",
            str(sample_doc),
            "--collection",
            "cli_collection",
            "--tenant",
            "cli_tenant",
            "--classification",
            "CUI",
            "--no-embed",
            "--no-kg",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["collection_id"] == "cli_collection"
    assert payload["tenant_id"] == "cli_tenant"
    assert payload["version_id"].endswith("_v1")
