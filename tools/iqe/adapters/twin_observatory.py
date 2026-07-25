# CUI // SP-CTI
"""IQE Twin Observatory collection adapters (twx-obs-01).

Importing this module registers two collections on the module-level Executor:
  twin_observatory.twins  — one row per registered twin (health snapshot from
                            the twin_core observer).
  twin_observatory.events — recent twin_* cross-canvas events (canvas_events).

These collections are computed live from the twin_core observer / event feed —
there is no dedicated table (the Observatory owns no state).
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection
from tools.twin_observatory.observatory import events_collection, twins_collection


def twins_adapter(conn: Any) -> list[dict]:  # noqa: ARG001 — computed, no table
    return twins_collection()


def events_adapter(conn: Any) -> list[dict]:  # noqa: ARG001 — computed, no table
    return events_collection()


register_collection("twin_observatory.twins", twins_adapter)
register_collection("twin_observatory.events", events_adapter)
