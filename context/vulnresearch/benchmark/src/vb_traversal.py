# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Serves a document by name. PLANTED: path traversal (CWE-22)."""
import os

DOCS = os.path.dirname(os.path.abspath(__file__))


def read_doc(name):
    with open(os.path.join(DOCS, name), encoding="utf-8") as fh:
        return fh.read()
