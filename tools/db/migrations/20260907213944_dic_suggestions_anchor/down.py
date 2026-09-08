# CUI // SP-CTI
"""Rollback for 20260907213944 — delegates to up.py's ``down`` so the column
list exists in ONE place."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def down(conn):
    up_py = Path(__file__).with_name("up.py")
    spec = importlib.util.spec_from_file_location("migration_20260907213944_up", str(up_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.down(conn)
