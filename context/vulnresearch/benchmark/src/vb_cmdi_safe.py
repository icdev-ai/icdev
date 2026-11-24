# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Greets a user without a shell. SAFE look-alike: static analysis flags the subprocess call."""
import subprocess


def greet(name):
    out = subprocess.run(["echo", "hello", name], capture_output=True, text=True)
    return out.stdout.strip()
