# CUI // SP-CTI
"""Tests for the optional layout-detection probe in the DIC extractors layer.

dic-adapt-02-d1: PaddleOCR / DocLayout-YOLO are optional, heavy, network-on-first-
use deps. ``tools/document_intelligence/extractors.py`` probes for a layout backend
at module import time and pins the extractor to either ``'structured'`` (a backend
is importable) or the always-available ``'flat-ocr'`` fallback. These tests pin:

* the probe is import-only (uses ``importlib.util.find_spec``) so it never imports
  a package body, downloads a model, or touches the network — air-gap safe;
* ``_probe_layout_backend`` returns ``structured`` + backend name when a candidate
  module spec is found, and ``flat-ocr`` + ``None`` when none is;
* a ``find_spec`` that raises (broken install) degrades to flat-ocr, never raises;
* the module-level ``LAYOUT_MODE`` is one of the two known modes and the public
  accessors agree with it.
"""
from __future__ import annotations

import importlib.util

import pytest

from tools.document_intelligence import extractors


def test_layout_mode_is_one_of_two_known_modes():
    assert extractors.LAYOUT_MODE in {
        extractors.LAYOUT_MODE_STRUCTURED,
        extractors.LAYOUT_MODE_FLAT_OCR,
    }
    assert extractors.get_layout_mode() == extractors.LAYOUT_MODE
    assert extractors.layout_detection_available() == (
        extractors.LAYOUT_MODE == extractors.LAYOUT_MODE_STRUCTURED
    )
    # When structured, a backend name is recorded; when flat-ocr, it is None.
    if extractors.LAYOUT_MODE == extractors.LAYOUT_MODE_STRUCTURED:
        assert extractors.LAYOUT_BACKEND is not None
    else:
        assert extractors.LAYOUT_BACKEND is None


def test_probe_returns_flat_ocr_when_no_backend(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    mode, backend = extractors._probe_layout_backend()
    assert mode == extractors.LAYOUT_MODE_FLAT_OCR
    assert backend is None


def test_probe_returns_structured_for_first_available_backend(monkeypatch):
    # Pretend the first candidate (paddleocr) is importable.
    sentinel = object()

    def fake_find_spec(name):
        return sentinel if name == "paddleocr" else None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    mode, backend = extractors._probe_layout_backend()
    assert mode == extractors.LAYOUT_MODE_STRUCTURED
    assert backend == "paddleocr"


def test_probe_falls_through_to_second_backend(monkeypatch):
    sentinel = object()

    def fake_find_spec(name):
        return sentinel if name == "doclayout_yolo" else None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    mode, backend = extractors._probe_layout_backend()
    assert mode == extractors.LAYOUT_MODE_STRUCTURED
    assert backend == "doclayout_yolo"


def test_probe_degrades_to_flat_ocr_when_find_spec_raises(monkeypatch):
    def boom(name):
        raise ImportError("broken native install")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    # Must not raise — a broken install degrades to flat-ocr.
    mode, backend = extractors._probe_layout_backend()
    assert mode == extractors.LAYOUT_MODE_FLAT_OCR
    assert backend is None


def test_backend_priority_order_is_paddle_then_doclayout():
    names = [m for m, _ in extractors._LAYOUT_BACKENDS]
    assert names == ["paddleocr", "doclayout_yolo"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
