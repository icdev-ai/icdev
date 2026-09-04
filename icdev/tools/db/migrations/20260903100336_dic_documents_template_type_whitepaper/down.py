#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback for 20260903100336_dic_documents_template_type_whitepaper.

Rebuilds the constraint from whatever ``TEMPLATE_TYPES`` currently holds,
which is the only correct rollback available: narrowing the CHECK to a
hardcoded "before" list would respell the vocabulary (the stale copy this
migration exists to avoid) and would start refusing rows the code still
writes. To genuinely un-admit a template type, remove it from
``TEMPLATE_TYPES`` and run this. The constraint follows the constant, in both
directions.
"""
import importlib.util
import pathlib


def _up_module():
    """Load the sibling ``up.py`` by PATH — a migration directory is not an
    importable package name, and the runner loads these files by path too."""
    path = pathlib.Path(__file__).with_name("up.py")
    spec = importlib.util.spec_from_file_location("_rmf_wp_01_up", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def down(conn):
    return {"constraint_rebuilt": _up_module().rebuild_template_type_constraint(conn)}
