# CUI // SP-CTI
"""Rollback for 20260908003311 — delegates to up.py's ``down`` so the column
list exists in ONE place. Retained files on disk are never touched."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def down(conn):
    up_py = Path(__file__).with_name("up.py")
    spec = importlib.util.spec_from_file_location("migration_20260908003311_up", str(up_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.down(conn)
