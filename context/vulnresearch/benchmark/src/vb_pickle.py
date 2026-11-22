# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Restores saved preferences. PLANTED: deserialisation of untrusted data (CWE-502)."""
import base64
import pickle


def load_prefs(blob):
    return pickle.loads(base64.b64decode(blob))
