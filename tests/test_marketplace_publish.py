# CUI // SP-CTI
"""tools/marketplace/publish_pipeline.py and review_queue.py are RETIRED (icx-ent-04).

They published a FORGE asset to the marketplace (a tenant-local asset with no review at all) and let a reviewer approve
it. Their replacement is the store publisher (tools/store_publisher; tests/test_icx_ent_04_store_publisher.py): a person
prepares a signed ICDEV[OS] store package, a different person publishes it, and ICDEV[OS] imports, reviews and
certifies it. What is left of them is a thin shim that refuses and points there, and that is what this file tests:
every entry point, the CLI and the MCP marketplace server's publish and review tools refuse with ``superseded``.
"""
from __future__ import annotations

import json

import pytest


def test_the_retired_marketplace_publish_and_review_refuse_and_point_to_the_packager(capsys):
    from tools.marketplace import publish_pipeline, review_queue
    from tools.marketplace.superseded import Superseded
    from tools.mcp import marketplace_server

    for fn in (publish_pipeline.publish_asset, review_queue.complete_review, review_queue.submit_review,
               review_queue.list_pending, review_queue.assign_reviewer, review_queue.escalate_review,
               review_queue.get_review):
        with pytest.raises(Superseded) as e:
            fn(asset_path="x", review_id="r")
        assert e.value.code == "superseded" and "store publisher" in str(e.value)
    for h in (marketplace_server.handle_publish_asset, marketplace_server.handle_review_asset,
              marketplace_server.handle_list_pending):
        out = h({"asset_path": "x", "asset_type": "skill", "tenant_id": "t", "review_id": "r", "reviewer_id": "a",
                 "decision": "approved", "rationale": "r"})
        assert out["code"] == "superseded" and out["use"] == "tools/store_publisher", "no agent publishes or reviews"
    assert marketplace_server.handle_pending_resource("marketplace://pending-reviews")["code"] == "superseded"
    assert publish_pipeline.main() == 2
    assert json.loads(capsys.readouterr().out)["page"] == "/store-publisher/"
