# CUI // SP-CTI
"""The store publisher on whatever the platform database is (icx-ent-04).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: ``sp_events`` with the
UNIQUE(stream, prev_hash) chain and the PL/pgSQL append-only triggers migration 20261122112221 adds, and the declared
step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite database (``wf_db``).

Two synthetic packages with fresh ids per run are prepared by one test person with a throwaway key and published by
another of their org. The ledger may be shared, so nothing assumes it is empty.
"""
from __future__ import annotations

import contextlib
import copy
import os
import shutil
import uuid

import pytest

from tests.test_icx_ent_04_store_publisher import NOW, ORG, throwaway_pem


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    if _pg():
        return None
    return request.getfixturevalue("wf_db")


def _rollback_close(conn):
    with contextlib.suppress(Exception):
        conn.rollback()
    conn.close()


def test_packages_prepared_and_published_on_the_platform_database():
    from tools.db.storage import get_connection
    from tools.store_publisher import card, config, ledger, package, service, synthetic

    run = uuid.uuid4().hex[:8]
    rel = f".tmp/icx-ent-04-ambient/{run}"
    cfg = config.load()
    cfg.pop("_digest")
    cfg["package_root"] = rel
    for p in cfg["packages"]:
        p["id"] = f"syn-amb-{run}-{p['kind'].replace('_', '-')}"
    config.validate(cfg)
    cfg["_digest"] = "ambient"
    root = config.BASE_DIR / rel
    try:
        synthetic.write_all(cfg, now=NOW)
        s = card.PemSigner(throwaway_pem(), source="test:ambient throwaway")
        a = {"id": f"u-ent04-amb-{run}-preparer", "role": "reviewer"}
        b = {"id": f"u-ent04-amb-{run}-publisher", "role": "isso"}
        ra, rb = {**a, "clearance": "CUI", "org": ORG}, {**b, "clearance": "CUI", "org": ORG}
        for p in cfg["packages"]:
            got = service.prepare(p["id"], a, ra, version="1.0.0", cfg=copy.deepcopy(cfg), signer=s, now=NOW)
            with pytest.raises(service.Refused):
                service.publish(p["id"], decision="publish", reason="mine", read_digest=got["digest"], actor=a,
                                reader=ra, cfg=cfg, signer=s)
            out = service.publish(p["id"], decision="publish", reason="ambient (synthetic)",
                                  read_digest=got["digest"], actor=b, reader=rb, cfg=cfg, signer=s)
            assert out["event"] == "published"
            ex = service.export(p["id"], rb, cfg=cfg)
            assert ex["publication"]["signed_by"] == b["id"] and ex["publication"]["content_digest"] == got["digest"]
            assert package.schema_errors(ex["bundle"]) == []
            rows = ledger.read(stream=f"package:{p['id']}")
            assert [x["event"] for x in rows] == ["prepared", "published"]
        c = get_connection()
        try:
            steps = c.execute("SELECT template_id FROM wf_decision_steps WHERE actor = %s", (b["id"],)).fetchall()
            assert [tuple(x) for x in steps] == [("icos-store-publish",)] * 2
            with pytest.raises(Exception, match="append-only"):
                c.execute("UPDATE sp_events SET reason = 'x' WHERE event_id = %s", (rows[0]["event_id"],))
        finally:
            _rollback_close(c)
        c = get_connection()
        try:
            with pytest.raises(Exception, match="append-only"):
                c.execute("DELETE FROM sp_events WHERE event_id = %s", (rows[0]["event_id"],))
        finally:
            _rollback_close(c)
    finally:
        shutil.rmtree(root, ignore_errors=True)
