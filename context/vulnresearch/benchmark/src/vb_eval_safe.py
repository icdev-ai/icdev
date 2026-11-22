# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""A literal reader. SAFE: literals only."""
import ast


def calc(expr):
    return ast.literal_eval(expr)
