#!/usr/bin/env python3
# CUI // SP-CTI
# DEPRECATED: retired by icx-ent-04 (2026-11-22). A thin shim: it refuses and points to tools/store_publisher.
"""Marketplace Publish Pipeline -- RETIRED (icx-ent-04).

It published a FORGE asset to the marketplace (tenant-local assets with no review at all). Its replacement is the store
publisher (``tools/store_publisher``, /store-publisher/): a person prepares a signed ``icos-store-package/v1``, a
different person publishes it, and ICDEV[OS] imports, reviews and certifies it. See ``tools/marketplace/superseded.py``.

Every entry point refuses (``Superseded``, code ``superseded``); the CLI prints that as JSON and exits 2.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.marketplace.superseded import Superseded, cli, refuse  # noqa: E402

TOOL = "tools/marketplace/publish_pipeline.py"


def publish_asset(*_args, **_kwargs):
    """Retired: refuses (``Superseded``). Use the store publisher."""
    refuse(TOOL)


def main() -> int:
    return cli(TOOL)


__all__ = ["Superseded", "main", "publish_asset"]


if __name__ == "__main__":
    sys.exit(main())
