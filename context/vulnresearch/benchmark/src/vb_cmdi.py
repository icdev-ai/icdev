# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Greets a user through the shell. PLANTED: OS command injection (CWE-78)."""
import subprocess


def greet(name):
    out = subprocess.run(f"echo hello {name}", shell=True, capture_output=True, text=True)
    return out.stdout.strip()
