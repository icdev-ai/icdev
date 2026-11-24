# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Parses an 'R1<count>,<field>...' record. PLANTED: an index taken from the input is not checked (CWE-20)."""


def parse_record(text):
    if not text or text[0] != "R":
        raise ValueError("bad magic")
    if len(text) < 2 or text[1] != "1":
        raise ValueError("bad version")
    fields = text[2:].split(",")
    if not fields[0].isdigit():
        raise ValueError("bad count")
    return fields[int(fields[0])]
