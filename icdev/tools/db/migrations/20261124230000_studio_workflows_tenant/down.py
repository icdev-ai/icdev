# CUI // SP-CTI
"""Rollback 20261124230000 — nothing: which rows were NULL before is not recorded, and NULL is the less safe value."""
from __future__ import annotations


def down(conn) -> dict:
    return {"status": "irreversible", "reason": "a backfilled tenant is not cleared again"}
