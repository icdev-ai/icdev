# CUI // SP-CTI
"""Tests for the DIC layout-detection capability probe + flat-ocr fallback.

dic-adapt-02-d1: layout-aware extraction (PaddleOCR / doclayout-yolo) is an
optional, non-air-gap-safe capability. ``extractors.py`` probes the libraries at
import time and degrades to the always-present 'flat-ocr' baseline when they are
unavailable. These pin the load-bearing guarantees:

* importing the module never raises, regardless of layout-lib availability;
* with the optional libs absent (the default / air-gap case) the active mode is
  'flat-ocr' and the missing libs are recorded;
* the probe is total — a library that raises on import is treated as missing,
  not propagated;
* ``extract_file`` stamps the resolved mode onto the extraction metadata so
  downstream consumers can observe which path produced the text.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

extractors = importlib.import_module("tools.document_intelligence.extractors")


def test_module_imports_without_layout_libs():
    """Import must succeed and expose a concrete mode + accessor."""
    assert extractors.layout_mode() in (
        extractors.LAYOUT_MODE_AWARE,
        extractors.LAYOUT_MODE_FLAT,
    )
    assert extractors.LAYOUT_MODE == extractors.layout_mode()


def test_falls_back_to_flat_ocr_when_libs_missing():
    """With paddleocr/doclayout-yolo not installed, mode is flat-ocr."""
    if any(_lib_importable(lib) for lib in extractors._LAYOUT_LIBS):
        pytest.skip("a layout library is installed in this environment")
    assert extractors.layout_mode() == extractors.LAYOUT_MODE_FLAT
    # Every absent library is recorded so the warning can name them.
    assert set(extractors._LAYOUT_MISSING) == set(extractors._LAYOUT_LIBS)


def test_probe_is_total_on_import_error(monkeypatch):
    """A layout lib that explodes on import is treated as missing, not raised."""
    real_import = importlib.import_module

    def _boom(name, *a, **k):
        if name in extractors._LAYOUT_LIBS:
            raise RuntimeError("simulated broken backend / blocked network fetch")
        return real_import(name, *a, **k)

    # importlib is imported lazily inside _probe_layout_libs, so patching the
    # real importlib.import_module is what the function resolves through.
    monkeypatch.setattr("importlib.import_module", _boom)

    mode, missing = extractors._probe_layout_libs()
    assert mode == extractors.LAYOUT_MODE_FLAT
    assert set(missing) == set(extractors._LAYOUT_LIBS)


def test_extract_file_stamps_layout_mode(tmp_path: Path):
    """extract_file records the active layout mode in Extraction.metadata."""
    doc = tmp_path / "sample.txt"
    doc.write_text("hello world", encoding="utf-8")

    result = extractors.extract_file(doc)

    assert result.text == "hello world"
    assert result.metadata.get("layout_mode") == extractors.LAYOUT_MODE


def _lib_importable(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False
