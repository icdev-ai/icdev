# CUI // SP-CTI
"""A learner completes a CoP path and the xAPI batch is published, on whatever the platform database is (icx-cop-01).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: ``cop_academy_events`` with the
UNIQUE(scope, prev_hash) chains and the PL/pgSQL append-only triggers migration 20261118104743 adds (on both tables),
``cop_academy_learners``, and the declared step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite
database (``wf_db``).

No fixture redirects the backend on PG or opens a raw connection. The ledger is append-only and may be shared, so
every person is minted here and only this run's rows are asserted on.
"""
from __future__ import annotations

import os
import uuid

import pytest


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    """The conftest SQLite platform DB (it carries every table), unless this is the PG tier."""
    if _pg():
        return None
    return request.getfixturevalue("wf_db")


def _rollback_close(conn):
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass
    conn.close()


def test_a_learner_completes_a_path_and_a_batch_is_published_on_the_platform_database():
    from tools.db.storage import get_connection
    from tools.idp import cop_tracks as t

    run = uuid.uuid4().hex[:8]
    learner = {"id": f"u-cop01-amb-{run}-learner", "role": "developer"}     # labelled test persons (synthetic)
    preparer = {"id": f"u-cop01-amb-{run}-preparer", "role": "reviewer"}
    publisher = {"id": f"u-cop01-amb-{run}-publisher", "role": "reviewer"}
    cfg = t.load_config()
    track = next(tr for tr in cfg["tracks"] if tr["id"] == "cop-cloud-foundations")
    for m in track["missions"]:
        out = t.attempt(m["id"], {q["id"]: q["answer"] for q in m["questions"]}, learner)
        assert out["passed"]
    assert out["statement"]["verb"]["id"] == "http://adlnet.gov/expapi/verbs/completed"
    name = out["learner"]
    rows = t.read_events()
    mine = [r for r in rows if r["scope"] == f"learner:{name}"]
    assert [r["event"] for r in mine] == ["completed"] * len(track["missions"])
    assert not [p for p in t.chain(rows)["_problems"] if p["scope"] == f"learner:{name}"]
    prep = t.prepare(preparer, note=f"ambient {run}")
    digest = prep["content_digest"]
    with pytest.raises(t.CopRefused) as exc:
        t.publish(decision="publish", reason="mine", read_digest=digest, actor=preparer)
    assert exc.value.code == "separation"
    res = t.publish(decision="publish", reason="Ambient (synthetic).", read_digest=digest, actor=publisher)
    assert res["event"] == "published"
    pub = t.published()
    assert pub["batch"]["content_digest"] == digest and t.validate(pub["batch"]) == []
    assert out["statement"] in pub["batch"]["statements"]
    assert t.verify_published()["ok"]
    conn = get_connection()
    try:
        steps = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                             ("cop_academy:xapi", publisher["id"])).fetchall()
        assert [tuple(r) for r in steps] == [(t.TEMPLATE, publisher["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE cop_academy_events SET reason = 'x' WHERE event_id = %s", (res["event_id"],))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM cop_academy_learners WHERE pseudonym = %s", (name,))
    finally:
        _rollback_close(conn)
