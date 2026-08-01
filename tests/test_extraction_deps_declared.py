# CUI // SP-CTI
"""Extraction optional-dependency declarations (spike oss-02 D4 residual).

oss-table-02 declared pdfplumber + beautifulsoup4, but XLSX extraction's openpyxl
stayed undeclared — so on a clean install every XLSX ingestion silently no-ops.
openpyxl is MIT, so it can be a first-class declared dependency. pymupdf is
deliberately NOT declared (dual AGPL-3.0 / Artifex — copyleft ICDEV cannot ship);
this pins both facts so a future edit can't quietly regress either.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQS = (REPO / "requirements.txt").read_text(encoding="utf-8")


def test_openpyxl_is_declared():
    # imported best-effort by tools/document_intelligence/extractors.py for XLSX
    lines = [ln for ln in REQS.splitlines() if ln.strip().startswith("openpyxl")]
    assert lines, "openpyxl (MIT) must be declared — XLSX extraction no-ops without it"


def test_pymupdf_is_not_declared_agpl():
    # AGPL/Artifex dual license — must stay an optional, user-installed accelerator,
    # never a declared/shipped dependency in this Apache-2.0 project.
    active = [
        ln for ln in REQS.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
        and ("pymupdf" in ln.lower() or "fitz" in ln.lower())
    ]
    assert not active, "pymupdf is AGPL — it must not be a declared dependency"
