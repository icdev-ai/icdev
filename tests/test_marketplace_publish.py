# CUI // SP-CTI
"""tools/marketplace/publish_pipeline.py and review_queue.py are RETIRED (icx-ent-04) and DELETED (icx-ent-13).

They published a FORGE asset to the marketplace (a tenant-local asset with no review at all) and let a reviewer approve
it. Their replacement is the store publisher (tools/store_publisher; tests/test_icx_ent_04_store_publisher.py): a person
prepares a signed ICDEV[OS] store package, a different person publishes it, and ICDEV[OS] imports, reviews and
certifies it. What is left of them is the MCP marketplace server's publish and review tools, which refuse with
``superseded`` and point there (args/retired_tools.yaml).
"""
from __future__ import annotations

import importlib.util


def test_the_retired_marketplace_publish_and_review_are_gone_and_their_mcp_tools_refuse():
    from tools.mcp import marketplace_server

    for module in ("tools.marketplace.publish_pipeline", "tools.marketplace.review_queue"):
        assert importlib.util.find_spec(module) is None, f"{module} is deleted"
    for h in (marketplace_server.handle_publish_asset, marketplace_server.handle_review_asset,
              marketplace_server.handle_list_pending):
        out = h({"asset_path": "x", "asset_type": "skill", "tenant_id": "t", "review_id": "r", "reviewer_id": "a",
                 "decision": "approved", "rationale": "r"})
        assert out["code"] == "superseded" and out["use"] == "tools/store_publisher", "no agent publishes or reviews"
    assert marketplace_server.handle_pending_resource("marketplace://pending-reviews")["code"] == "superseded"
