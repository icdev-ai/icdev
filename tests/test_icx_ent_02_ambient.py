# CUI // SP-CTI
"""Objective evidence on whatever the platform database is (icx-ent-02).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: ``ec_events`` with the
UNIQUE(stream, prev_hash) chain and the PL/pgSQL append-only triggers migration 20261119040000 adds, and the declared
step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite database (``wf_db``).

The collectors read the loopback stubs of the unit tests; a system with a fresh id per run (synthetic) is prepared by
one test person and published by another of its org. The ledger may be shared, so nothing assumes it is empty.
"""
from __future__ import annotations

import contextlib
import copy
import os
import uuid

import pytest

from tests.test_icx_ent_02_evidence_collectors import NOW, ORG, Stub, fake_resolve, stub_config


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


def test_a_report_prepared_and_published_on_the_platform_database():
    from tools.db.storage import get_connection
    from tools.evidence_collectors import ledger, service

    stub = Stub()
    try:
        run = uuid.uuid4().hex[:8]
        cfg = stub_config(stub.base)
        sid = f"syn-amb-{run}"
        cfg["systems"] = [dict(copy.deepcopy(cfg["systems"][0]), id=sid)]
        a = {"id": f"u-ent02-amb-{run}-preparer", "role": "reviewer"}
        b = {"id": f"u-ent02-amb-{run}-publisher", "role": "isso"}
        ra = {**a, "clearance": "CUI", "org": ORG}
        rb = {**b, "clearance": "CUI", "org": ORG}
        p = service.prepare(sid, a, ra, cfg=cfg, now=NOW, resolve=fake_resolve)
        with pytest.raises(service.Refused):
            service.publish(sid, decision="publish", reason="mine", read_digest=p["content_digest"], actor=a,
                            reader=ra, cfg=cfg)
        out = service.publish(sid, decision="publish", reason="ambient (synthetic)", read_digest=p["content_digest"],
                              actor=b, reader=rb, cfg=cfg)
        assert out["event"] == "published"
        assert out["bundle"]["publication"]["signed_by"] == b["id"]
        rows = ledger.read(stream=f"system:{sid}")
        assert [x["event"] for x in rows] == ["prepared", "published"]
        conn = get_connection()
        try:
            steps = conn.execute("SELECT template_id FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                                 (f"objective_evidence:{sid}", b["id"])).fetchall()
            assert [tuple(s) for s in steps] == [("decl-wft-ec-evidence-publish",)]
            with pytest.raises(Exception, match="append-only"):
                conn.execute("UPDATE ec_events SET reason = 'x' WHERE event_id = %s", (rows[0]["event_id"],))
        finally:
            _rollback_close(conn)
        conn = get_connection()
        try:
            with pytest.raises(Exception, match="append-only"):
                conn.execute("DELETE FROM ec_events WHERE event_id = %s", (rows[0]["event_id"],))
        finally:
            _rollback_close(conn)
    finally:
        stub.close()
