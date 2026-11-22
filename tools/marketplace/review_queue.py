#!/usr/bin/env python3
# CUI // SP-CTI
# DEPRECATED: retired by icx-ent-04 (2026-11-22). A thin shim: it refuses and points to tools/store_publisher.
"""Marketplace Review Queue -- RETIRED (icx-ent-04).

It let a reviewer approve a marketplace asset for the central catalogue. A store package is now reviewed and certified
in ICDEV[OS] (its ``store-review`` workflow: a reviewer who is neither the importer nor one of the publisher's people,
with ICDEV's seven scanner gates re-run there), after a person who did not prepare it published it here
(``tools/store_publisher``, the declared step ``icos-store-publish``). See ``tools/marketplace/superseded.py``.

Every entry point refuses (``Superseded``, code ``superseded``); the CLI prints that as JSON and exits 2.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.marketplace.superseded import Superseded, cli, refuse  # noqa: E402

TOOL = "tools/marketplace/review_queue.py"


def submit_review(*_args, **_kwargs):
    """Retired: refuses (``Superseded``)."""
    refuse(TOOL)


def assign_reviewer(*_args, **_kwargs):
    """Retired: refuses (``Superseded``)."""
    refuse(TOOL)


def complete_review(*_args, **_kwargs):
    """Retired: refuses (``Superseded``). A store package is certified in ICDEV[OS], never here."""
    refuse(TOOL)


def list_pending(*_args, **_kwargs):
    """Retired: refuses (``Superseded``). Waiting packages are on /store-publisher/."""
    refuse(TOOL)


def get_review(*_args, **_kwargs):
    """Retired: refuses (``Superseded``)."""
    refuse(TOOL)


def escalate_review(*_args, **_kwargs):
    """Retired: refuses (``Superseded``)."""
    refuse(TOOL)


def main() -> int:
    return cli(TOOL)


__all__ = ["Superseded", "assign_reviewer", "complete_review", "escalate_review", "get_review", "list_pending",
           "main", "submit_review"]


if __name__ == "__main__":
    sys.exit(main())
