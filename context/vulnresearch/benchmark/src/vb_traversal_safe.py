# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Serves a document by name, inside its directory only. SAFE."""
from pathlib import Path

DOCS = Path(__file__).resolve().parent


def read_doc(name):
    path = (DOCS / name).resolve()
    if DOCS not in path.parents:
        raise ValueError("outside the documents directory")
    return path.read_text(encoding="utf-8")
