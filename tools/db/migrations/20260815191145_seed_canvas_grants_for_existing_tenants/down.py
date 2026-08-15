#!/usr/bin/env python3
# CUI // SP-CTI
"""Deliberately does not revoke.

Reversing a grant BACKFILL means removing access that users are now relying on,
and this migration cannot tell a grant it created from one an operator set to the
same value — grant_access upserts, so a pre-existing grant and a seeded one are
the same row. Revoking on rollback would lock people out of canvases to undo a
change that only ever re-asserted defaults.

Rolling the schema back does not un-need the grants. Use
tools/security/canvas_access.py::revoke_access for a deliberate removal.
"""
from __future__ import annotations


def down(conn) -> None:  # noqa: ARG001
    return
