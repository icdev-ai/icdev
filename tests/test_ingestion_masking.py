# CUI // SP-CTI
"""Tests: ingestion-time PII/CUI masking for RAG (trust-mask-02).

Covers tools/rag/ingestion_manager:
    - _mask_at_ingestion_enabled() config gate (default off)
    - _mask_row_content() in-place anonymization + count + safe no-ops
"""

import importlib
from types import SimpleNamespace

im = importlib.import_module("tools.rag.ingestion_manager")


class _StubAnonymizer:
    """Deterministic stand-in: replaces the literal SSN with a token."""

    def anonymize(self, text, impact_level="IL4", entities=None):
        if "123-45-6789" in text:
            return SimpleNamespace(
                anonymized_text=text.replace("123-45-6789", "[US_SSN]"),
                detections=[object()],  # one detection
            )
        return SimpleNamespace(anonymized_text=text, detections=[])


class TestConfigGate:
    def test_default_off(self):
        # Real args/redaction_config.yaml ships mask_at_ingestion: false
        assert im._mask_at_ingestion_enabled() is False


class TestMaskRowContent:
    def _use_stub(self, monkeypatch):
        monkeypatch.setattr(im, "_get_ingestion_anonymizer", lambda: _StubAnonymizer())

    def test_masks_content_columns_in_place(self, monkeypatch):
        self._use_stub(monkeypatch)
        row = {"body": "SSN is 123-45-6789 here.", "title": "clean title", "id": 1}
        masked = im._mask_row_content(row, ["body", "title"])
        assert masked == 1
        assert "123-45-6789" not in row["body"]
        assert "[US_SSN]" in row["body"]
        assert row["title"] == "clean title"  # untouched

    def test_returns_zero_when_no_pii(self, monkeypatch):
        self._use_stub(monkeypatch)
        row = {"body": "nothing sensitive"}
        assert im._mask_row_content(row, ["body"]) == 0
        assert row["body"] == "nothing sensitive"

    def test_skips_non_string_and_empty(self, monkeypatch):
        self._use_stub(monkeypatch)
        row = {"body": "", "count": 42, "missing": None}
        assert im._mask_row_content(row, ["body", "count", "missing"]) == 0

    def test_no_anonymizer_is_noop(self, monkeypatch):
        monkeypatch.setattr(im, "_get_ingestion_anonymizer", lambda: None)
        row = {"body": "SSN is 123-45-6789"}
        assert im._mask_row_content(row, ["body"]) == 0
        assert row["body"] == "SSN is 123-45-6789"  # unchanged when module absent

    def test_masking_error_never_raises(self, monkeypatch):
        class _Boom:
            def anonymize(self, *a, **k):
                raise RuntimeError("detector down")

        monkeypatch.setattr(im, "_get_ingestion_anonymizer", lambda: _Boom())
        row = {"body": "SSN is 123-45-6789"}
        # must swallow the error and leave content unchanged (never block ingest)
        assert im._mask_row_content(row, ["body"]) == 0
        assert row["body"] == "SSN is 123-45-6789"
