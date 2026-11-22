# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""A calculator. PLANTED: code injection (CWE-95)."""


def calc(expr):
    return eval(expr)
