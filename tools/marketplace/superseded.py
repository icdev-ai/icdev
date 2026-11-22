# CUI // SP-CTI
"""What the retired marketplace publish and review tools say now (icx-ent-04).

``tools/marketplace/publish_pipeline.py`` and ``review_queue.py`` were deprecated on 2026-05-09 (no audit-trail use).
They published an asset (tenant-local assets with no review at all) and let a reviewer approve it, with one platform
key. Their replacement is the store publisher (``tools/store_publisher``, /store-publisher/): a person prepares a
SIGNED ``icos-store-package/v1`` for ICDEV[OS]'s agent store, a DIFFERENT person publishes it (the declared step
``icos-store-publish``), and ICDEV[OS] imports it, re-runs its gates and has a reviewer who is none of them certify it.
There is no auto-publish path, and no agent publishes.

The two modules are kept as thin shims (their names are imported by ``tools/mcp/marketplace_server.py``) that refuse
and point here; they are removed with the other deprecated duplicates once nothing names them (osx-ent-13).
"""
from __future__ import annotations

import json

USE = ("the store publisher: a person prepares a signed store package on /store-publisher/ "
       "(python -m tools.store_publisher.cli), another person publishes it (icos-store-publish), and ICDEV[OS] "
       "imports, reviews and certifies it")


class Superseded(RuntimeError):
    """The retired tool was called. ``code`` is always ``superseded``."""

    code = "superseded"

    def __init__(self, tool: str):
        self.tool = tool
        super().__init__(f"{tool} is retired (icx-ent-04): use {USE}")

    def to_dict(self) -> dict:
        return {"error": str(self), "code": self.code, "refused": True, "tool": self.tool,
                "use": "tools/store_publisher", "page": "/store-publisher/"}


def refuse(tool: str):
    raise Superseded(tool)


def cli(tool: str) -> int:
    """A retired CLI: says so on stdout as JSON and exits 2 (it did nothing)."""
    print(json.dumps(Superseded(tool).to_dict()))
    return 2


__all__ = ["Superseded", "USE", "cli", "refuse"]

