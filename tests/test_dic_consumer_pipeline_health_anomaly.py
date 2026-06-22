"""Tests for collection-level consumption pipeline health anomaly detection.

aiify-opp-38: hardcoded_threshold -> anomaly_detection in
src/documents/management/commands/document_consumer.py (paperless-ngx).
The DIC analog is detect_collection_anomalies() in ingest_orchestrator —
IQR-based outlier detection on page_count and byte_size of recently-ingested
documents, replacing hardcoded OCR char-count floors, retry limits, and
queue-depth ceilings.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch


orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
detect_collection_anomalies = orch.detect_collection_anomalies
ConsumerHealthReport = orch.ConsumerHealthReport


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_row(doc_id: str, page_count: int = 1, byte_size: int = 1000) -> dict:
    return {"doc_id": doc_id, "page_count": page_count, "byte_size": byte_size}


def _mock_conn(rows: list[dict]) -> MagicMock:
    """Return a mock get_connection() context-manager that yields rows."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


# ── too-few-samples guard ─────────────────────────────────────────────────────

def test_returns_none_when_fewer_than_four_docs():
    rows = [_make_row(f"d{i}") for i in range(3)]
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        result = detect_collection_anomalies("col-1")
    assert result is None


def test_returns_none_when_zero_docs():
    with patch.object(orch, "get_connection", return_value=_mock_conn([])):
        result = detect_collection_anomalies("col-1")
    assert result is None


# ── healthy collection ────────────────────────────────────────────────────────

def test_healthy_uniform_collection():
    rows = [_make_row(f"d{i}", page_count=5, byte_size=10_000) for i in range(10)]
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        report = detect_collection_anomalies("col-uniform")
    assert report is not None
    assert report.verdict == "healthy"
    assert report.outlier_count == 0
    assert report.doc_count == 10
    assert not report.backlog_warning


# ── page_count outlier ───────────────────────────────────────────────────────

def test_flags_high_page_count_outlier():
    # 9 docs with 2 pages each, 1 doc with 1000 pages — obvious outlier.
    rows = [_make_row(f"d{i}", page_count=2, byte_size=5_000) for i in range(9)]
    rows.append(_make_row("outlier", page_count=1000, byte_size=5_000))
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        report = detect_collection_anomalies("col-pc")
    assert report is not None
    assert "outlier" in report.outlier_doc_ids
    assert any("page_count_high" in s for s in report.signals)


# ── byte_size outlier ────────────────────────────────────────────────────────

def test_flags_high_byte_size_outlier():
    rows = [_make_row(f"d{i}", page_count=1, byte_size=1_000) for i in range(9)]
    rows.append(_make_row("bigfile", page_count=1, byte_size=100_000_000))
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        report = detect_collection_anomalies("col-bs")
    assert report is not None
    assert "bigfile" in report.outlier_doc_ids
    assert any("byte_size_high" in s for s in report.signals)


# ── verdict thresholds ────────────────────────────────────────────────────────

def test_degraded_verdict_at_warn_ratio(monkeypatch):
    monkeypatch.setattr(orch, "_CONSUMER_WARN_RATIO", 0.10)
    monkeypatch.setattr(orch, "_CONSUMER_CRITICAL_RATIO", 0.25)
    # 10 docs, 1 outlier (10% = warn threshold exactly → degraded).
    rows = [_make_row(f"d{i}", page_count=2, byte_size=1_000) for i in range(9)]
    rows.append(_make_row("outlier", page_count=9999, byte_size=1_000))
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        report = detect_collection_anomalies("col-deg")
    assert report is not None
    assert report.verdict in {"degraded", "critical"}


def test_critical_verdict_at_critical_ratio(monkeypatch):
    # Lower the critical threshold so a 20% outlier rate (2/10 docs) triggers it.
    # IQR flags outliers when they are < 25% of the sample; using 8 normal + 2
    # extreme docs keeps them below the upper-quartile boundary so IQR fires.
    monkeypatch.setattr(orch, "_CONSUMER_WARN_RATIO", 0.10)
    monkeypatch.setattr(orch, "_CONSUMER_CRITICAL_RATIO", 0.15)
    rows = [_make_row(f"d{i}", page_count=2, byte_size=1_000) for i in range(8)]
    rows.append(_make_row("huge1", page_count=9999, byte_size=1_000))
    rows.append(_make_row("huge2", page_count=9999, byte_size=1_000))
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        report = detect_collection_anomalies("col-crit")
    assert report is not None
    # 2 outliers / 10 docs = 20% ≥ critical_ratio (0.15) → "critical"
    assert report.verdict == "critical"
    assert report.outlier_count == 2


# ── backlog warning ───────────────────────────────────────────────────────────

def test_backlog_warning_triggers_critical(monkeypatch):
    monkeypatch.setattr(orch, "_CONSUMER_MAX_QUEUE_DOCS", 5)
    rows = [_make_row(f"d{i}", page_count=1, byte_size=100) for i in range(5)]
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        report = detect_collection_anomalies("col-backlog")
    assert report is not None
    assert report.backlog_warning is True
    assert report.verdict == "critical"
    assert any("backlog_warning" in s for s in report.signals)


def test_no_backlog_warning_below_ceiling(monkeypatch):
    monkeypatch.setattr(orch, "_CONSUMER_MAX_QUEUE_DOCS", 500)
    rows = [_make_row(f"d{i}", page_count=1, byte_size=100) for i in range(10)]
    with patch.object(orch, "get_connection", return_value=_mock_conn(rows)):
        report = detect_collection_anomalies("col-ok")
    assert report is not None
    assert report.backlog_warning is False


# ── external conn passthrough ─────────────────────────────────────────────────

def test_accepts_external_conn():
    rows = [_make_row(f"d{i}", page_count=3, byte_size=2_000) for i in range(6)]
    ext_conn = _mock_conn(rows)
    result = detect_collection_anomalies("col-ext", conn=ext_conn)
    assert result is not None
    # External conn must not be closed by the function.
    ext_conn.close.assert_not_called()


# ── limit parameter ───────────────────────────────────────────────────────────

def test_limit_parameter_passed_to_query():
    rows = [_make_row(f"d{i}") for i in range(4)]
    conn = _mock_conn(rows)
    with patch.object(orch, "get_connection", return_value=conn):
        detect_collection_anomalies("col-lim", limit=50)
    cur = conn.cursor.return_value
    call_args = cur.execute.call_args
    # Second positional argument should be ("col-lim", 50).
    assert call_args[0][1][1] == 50


# ── error resilience ──────────────────────────────────────────────────────────

def test_returns_none_on_db_error():
    bad_conn = MagicMock()
    bad_conn.cursor.side_effect = RuntimeError("DB down")
    bad_conn.__enter__ = lambda s: s
    bad_conn.__exit__ = MagicMock(return_value=False)
    with patch.object(orch, "get_connection", return_value=bad_conn):
        result = detect_collection_anomalies("col-err")
    assert result is None


# ── to_dict ───────────────────────────────────────────────────────────────────

def test_to_dict_structure():
    report = ConsumerHealthReport(
        collection_id="c1",
        doc_count=10,
        outlier_count=1,
        outlier_fraction=0.1,
        verdict="degraded",
        backlog_warning=False,
        outlier_doc_ids=["d-outlier"],
        signals=["page_count_high:d-outlier:100>10"],
    )
    d = report.to_dict()
    assert d["collection_id"] == "c1"
    assert d["verdict"] == "degraded"
    assert d["outlier_count"] == 1
    assert d["outlier_fraction"] == 0.1
    assert not d["backlog_warning"]
    assert "d-outlier" in d["outlier_doc_ids"]
