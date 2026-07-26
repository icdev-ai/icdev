# CUI // SP-CTI
"""oss2-meas-01 — the memory-tier measurement instrument.

Pins the verdict logic and the honest-measurement guardrail (below MIN_SAMPLE it
refuses to render a keep/drop verdict — it must not launder a claim from too little
data). Hermetic: an in-memory sqlite memory_entries table via a patched get_connection.
"""
from __future__ import annotations

import importlib
import sqlite3

mtm = importlib.import_module("tools.memory.memory_tier_measure")


def _memdb(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE memory_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, "
        "type TEXT DEFAULT 'insight', content_hash TEXT, created_at TEXT DEFAULT (datetime('now')), "
        "embedding BLOB)"
    )
    for content in rows:
        conn.execute("INSERT INTO memory_entries (content, content_hash) VALUES (?, ?)", (content, content[:16]))
    conn.commit()
    return conn


def test_insufficient_data_makes_no_verdict(monkeypatch):
    """Below MIN_SAMPLE entries: no keep/drop claim (the golden-set lesson)."""
    conn = _memdb(["a note", "another note"])  # only 2 < 30
    monkeypatch.setattr(mtm, "get_connection", lambda: conn)
    out = mtm.measure_consolidation()
    assert out["verdict"] == "insufficient_data"
    assert "verdict" in out and "redundancy_rate" not in out  # no laundered number


def test_earns_its_keep_on_high_redundancy(monkeypatch):
    # 40 entries, ~half are near-identical paraphrases sharing almost all keywords.
    dup = "deploy the kubernetes helm chart to the production cluster using argocd"
    dup2 = "deploy the kubernetes helm chart into the production cluster with argocd"
    rows = [dup, dup2] * 15 + [f"unrelated distinct note number {i} about weather today" for i in range(10)]
    conn = _memdb(rows)
    monkeypatch.setattr(mtm, "get_connection", lambda: conn)
    out = mtm.measure_consolidation(threshold=0.75)
    assert out["total_entries"] == 40
    assert out["redundancy_rate"] > 0.15
    assert out["verdict"] == "consolidation_earns_its_keep"


def test_low_value_when_all_distinct(monkeypatch):
    # Each entry's keywords are unique to it (distinct token suffixes), so pairwise
    # Jaccard is ~0 and no consolidation is warranted.
    rows = [f"alpha{i} bravo{i} charlie{i} delta{i} echo{i} foxtrot{i}" for i in range(40)]
    conn = _memdb(rows)
    monkeypatch.setattr(mtm, "get_connection", lambda: conn)
    out = mtm.measure_consolidation(threshold=0.75)
    assert out["redundancy_rate"] < 0.05
    assert out["verdict"] == "low_value"


def test_exact_hash_duplicates_counted_separately(monkeypatch):
    # identical content -> identical content_hash; counted as exact dups, distinct
    # from the semantic-redundancy signal.
    conn = _memdb(["same content here"] * 35)
    monkeypatch.setattr(mtm, "get_connection", lambda: conn)
    out = mtm.measure_consolidation()
    assert out["exact_hash_duplicates"] == 34  # 35 rows, 1 unique hash
