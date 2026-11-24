# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
"""Greets a user through the shell, by an indirect name. PLANTED: OS command injection (CWE-78) that pattern
matching does not see."""
import os


def greet(name):
    runner = getattr(os, "sys" + "tem")
    return runner("echo hello " + name)
