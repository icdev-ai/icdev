"""Tests for structured layout-block ingestion in the DIC ingest orchestrator.

[TEMPLATE: CUI // SP-CTI]

dic-adapt-02-d3: a layout-detection backend yields a document as typed regions
("blocks") — tables (cell/row structure), figures (caption + bbox), and text
regions — instead of one flat text blob. The orchestrator must accept those
blocks (either via an explicit ``blocks=`` argument or on ``Extraction.blocks``)
and map each into a ``dic_sections`` row that RETAINS its structure:

* a table block becomes a section whose ``content`` is a JSON grid of cells/rows
  (a structured representation, never concatenated prose) and whose
  ``block_json`` holds the same grid plus page/geometry;
* a figure block stores its caption + bounding box in ``block_json`` SEPARATELY
  from the main content block;
* embedding / KG / flat-text chunking are unaffected.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.document_intelligence import ingest_orchestrator as orch
from tools.document_intelligence.ingest_orchestrator import ingest_file


# --------------------------------------------------------------------------- #
# Pure helpers — deterministic, no DB, no LLM.
# --------------------------------------------------------------------------- #

def test_table_grid_from_rows():
    grid = orch._table_grid({"rows": [["Name", "Role"], ["Alice", "Admin"]]})
    assert grid == [["Name", "Role"], ["Alice", "Admin"]]


def test_table_grid_from_flat_cells_reassembles_by_index():
    block = {
        "cells": [
            {"row": 0, "col": 0, "text": "Name"},
            {"row": 0, "col": 1, "text": "Role"},
            {"row_idx": 1, "col_idx": 0, "content": "Bob"},
            {"row_idx": 1, "col_idx": 1, "content": "Viewer"},
        ]
    }
    grid = orch._table_grid(block)
    assert grid == [["Name", "Role"], ["Bob", "Viewer"]]


def test_table_grid_returns_none_without_structure():
    assert orch._table_grid({"text": "just prose"}) is None


def test_table_block_section_content_is_structured_json_not_prose():
    block = {"type": "table", "page": 3, "bbox": [10, 20, 110, 80],
             "rows": [["A", "B"], ["1", "2"]]}
    rec = orch._layout_block_to_section(block, 0)
    assert rec["block_type"] == "table"
    # content is a JSON grid of cells/rows — parseable, not concatenated text.
    parsed = json.loads(rec["content"])
    assert parsed["rows"] == [["A", "B"], ["1", "2"]]
    assert parsed["n_rows"] == 2 and parsed["n_cols"] == 2
    # block_json carries the same grid plus geometry.
    bj = json.loads(rec["block_json"])
    assert bj["type"] == "table" and bj["page"] == 3 and bj["bbox"] == [10.0, 20.0, 110.0, 80.0]
    assert "Table 1" in rec["heading"] and "page 3" in rec["heading"]


def test_figure_block_keeps_caption_and_bbox_separate_from_content():
    block = {"type": "figure", "page": 2, "bbox": [0, 0, 50, 50],
             "caption": "Figure 1: System architecture"}
    rec = orch._layout_block_to_section(block, 4)
    assert rec["block_type"] == "figure"
    bj = json.loads(rec["block_json"])
    # caption + bbox live in block_json, away from the body content block.
    assert bj["type"] == "figure"
    assert bj["caption"] == "Figure 1: System architecture"
    assert bj["bbox"] == [0.0, 0.0, 50.0, 50.0]
    assert "Figure 5" in rec["heading"] and "page 2" in rec["heading"]


def test_empty_block_is_skipped():
    assert orch._layout_block_to_section({"type": "text"}, 0) is None
    assert orch._layout_block_to_section({"type": "figure"}, 0)["content"] == ""


# --------------------------------------------------------------------------- #
# End-to-end ingest — blocks mapped into dic_sections rows.
# --------------------------------------------------------------------------- #

@pytest.fixture
def sample_doc(tmp_path: Path) -> Path:
    p = tmp_path / "tabled_report.md"
    p.write_text("# Quarterly Report\n\nSee the table below.\n", encoding="utf-8")
    return p


def _sections(version_id: str) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT heading, content, block_type, block_json, origin, status "
            "FROM dic_sections WHERE version_id = ? ORDER BY section_id",
            (version_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def test_ingest_file_persists_structured_blocks(sample_doc: Path):
    blocks = [
        {"type": "table", "page": 1, "bbox": [5, 5, 95, 45],
         "rows": [["Metric", "Q1", "Q2"], ["Revenue", "100", "120"]]},
        {"type": "figure", "page": 1, "bbox": [5, 50, 95, 90],
         "caption": "Figure 1: Revenue trend"},
        {"type": "text", "page": 1, "text": "Concluding remarks."},
    ]
    outcome = ingest_file(
        str(sample_doc),
        "blocks_collection",
        tenant_id="acme",
        classification="CUI",
        created_by="alice",
        embed=False,
        bridge_kg=False,
        summarize=False,
        extract_metadata=False,
        extract_identifiers=False,
        extract_correspondence=False,
        detect_date_anomalies=False,
        detect_duplicate_blocks=False,
        detect_workload_anomaly=False,
        blocks=blocks,
    )

    assert outcome.structured_sections == 3
    rows = _sections(outcome.version_id)
    by_type = {r["block_type"]: r for r in rows}
    assert set(by_type) == {"table", "figure", "text"}

    # Table section: content is a structured JSON grid, not concatenated text.
    table_content = json.loads(by_type["table"]["content"])
    assert table_content["rows"] == [["Metric", "Q1", "Q2"], ["Revenue", "100", "120"]]

    # Figure section: caption + bbox kept separately in block_json.
    fig_bj = json.loads(by_type["figure"]["block_json"])
    assert fig_bj["caption"] == "Figure 1: Revenue trend"
    assert fig_bj["bbox"] == [5.0, 50.0, 95.0, 90.0]

    # Layout sections are tagged so re-ingest replaces only them.
    assert all(r["origin"] == "layout_extracted" for r in rows)


def test_reingest_is_idempotent_for_layout_sections(sample_doc: Path):
    blocks = [{"type": "table", "rows": [["a", "b"]]}]
    first = ingest_file(
        str(sample_doc), "blocks_idem", embed=False, bridge_kg=False,
        summarize=False, extract_metadata=False, extract_identifiers=False,
        extract_correspondence=False, detect_date_anomalies=False,
        detect_duplicate_blocks=False, detect_workload_anomaly=False, blocks=blocks,
    )
    second = ingest_file(
        str(sample_doc), "blocks_idem", embed=False, bridge_kg=False,
        summarize=False, extract_metadata=False, extract_identifiers=False,
        extract_correspondence=False, detect_date_anomalies=False,
        detect_duplicate_blocks=False, detect_workload_anomaly=False, blocks=blocks,
    )
    # Second call hits the content-hash dedup path (no new version), so the
    # original layout sections are untouched — exactly one table section.
    rows = _sections(first.version_id)
    assert len([r for r in rows if r["block_type"] == "table"]) == 1
    assert second.version_id == first.version_id
