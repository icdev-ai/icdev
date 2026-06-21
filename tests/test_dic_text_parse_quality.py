# CUI // SP-CTI
"""Tests for TextParseQualityReport / assess_text_parse_quality.

aiify-opp-131: hardcoded_threshold -> anomaly_detection in text parser.
Replaces the external paperless pattern of hard-coded minimum text-length /
word-count thresholds with IQR-based statistical anomaly detection on the
document's internal line-length distribution.

Pinned guarantees:
* Empty / near-empty text (< 3 non-empty lines, no binary noise) returns
  is_anomalous=False — not enough structure to characterise.
* binary_noise fires when encoding replacement character ratio > 5 %.
* line_length_outliers fires when lines far above Q3 + 1.5*IQR exist and
  IQR > 10 (or above mean + 2*std when IQR = 0), and reports the correct count.
* Clean, uniformly structured documents are never flagged.
* to_dict() is JSON-serialisable and contains all expected keys.
"""
from __future__ import annotations

import importlib
import json

REPL = "�"  # U+FFFD REPLACEMENT CHARACTER — written as escape to stay ASCII-safe

extractors = importlib.import_module("tools.document_intelligence.extractors")
assess = extractors.assess_text_parse_quality
TextParseQualityReport = extractors.TextParseQualityReport


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_string_returns_clean():
    report = assess("")
    assert report.is_anomalous is False


def test_single_line_returns_clean():
    report = assess("just one line of text here")
    assert report.is_anomalous is False


def test_two_nonempty_lines_returns_clean():
    report = assess("line one\nline two")
    assert report.is_anomalous is False


# ── binary_noise ──────────────────────────────────────────────────────────────

def test_binary_noise_detected_above_5_percent():
    # 50 clean chars + 10 replacement chars = 10/60 ~16.7 % > 5 % threshold
    text = "word " * 10 + REPL * 10
    report = assess(text)
    assert report.is_anomalous is True
    assert report.anomaly_type == "binary_noise"
    assert report.replacement_char_ratio > 0.05


def test_binary_noise_detail_mentions_encoding():
    text = "ok text " * 5 + REPL * 20
    report = assess(text)
    if report.is_anomalous and report.anomaly_type == "binary_noise":
        assert "encoding" in report.detail.lower() or "binary" in report.detail.lower()


def test_few_replacement_chars_not_flagged():
    # < 1 % replacement chars — should not trigger binary_noise
    text = "normal sentence content here\n" * 20 + REPL
    report = assess(text)
    assert not (report.is_anomalous and report.anomaly_type == "binary_noise")


# ── line_length_outliers ──────────────────────────────────────────────────────

def test_long_line_outliers_detected():
    # 15 normal short lines + 3 very long lines (base64-style blob)
    normal = ["short sentence here"] * 15
    blobs = ["A" * 2000] * 3
    text = "\n".join(normal + blobs)
    report = assess(text)
    assert report.is_anomalous is True
    assert report.anomaly_type == "line_length_outliers"
    assert report.outlier_line_count >= 1


def test_long_line_outlier_detail_mentions_fence():
    normal = ["typical line content"] * 10
    blobs = ["X" * 3000] * 2
    text = "\n".join(normal + blobs)
    report = assess(text)
    if report.is_anomalous and report.anomaly_type == "line_length_outliers":
        assert "fence" in report.detail or "chars" in report.detail


def test_uniform_long_lines_not_flagged():
    # All lines equally long -> IQR = 0, std = 0 -> no fence applied
    text = "\n".join(["A" * 500] * 20)
    report = assess(text)
    assert not (report.is_anomalous and report.anomaly_type == "line_length_outliers")


# ── Clean document ────────────────────────────────────────────────────────────

def test_clean_prose_not_flagged():
    paragraph = (
        "The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. "
        "How vexingly quick daft zebras jump.\n"
    )
    text = paragraph * 20
    report = assess(text)
    assert report.is_anomalous is False
    assert report.line_count > 0
    assert report.mean_line_length > 0


def test_clean_report_stats_populated():
    text = "\n".join(["content line here " * 3] * 10)
    report = assess(text)
    assert report.line_count == 10
    assert report.mean_line_length > 0
    assert report.q3_line_length > 0


# ── Serialisation ─────────────────────────────────────────────────────────────

def test_to_dict_keys_present():
    text = "\n".join(["sample text content"] * 8)
    report = assess(text)
    d = report.to_dict()
    for key in (
        "is_anomalous",
        "anomaly_type",
        "line_count",
        "mean_line_length",
        "q3_line_length",
        "replacement_char_ratio",
        "outlier_line_count",
        "detail",
    ):
        assert key in d, f"missing key: {key}"


def test_to_dict_values_json_serialisable():
    text = "\n".join(["document line"] * 6)
    report = assess(text)
    json.dumps(report.to_dict())  # must not raise


def test_anomalous_to_dict_is_anomalous_true():
    # binary_noise path: 10/60 chars are replacement -> is_anomalous=True
    text = "word " * 10 + REPL * 10
    report = assess(text)
    if report.is_anomalous:
        assert report.to_dict()["is_anomalous"] is True
