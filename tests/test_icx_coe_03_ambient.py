# CUI // SP-CTI
"""A conformance bundle prepared and published on whatever the platform database is (icx-coe-03).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the ledger
``coe_conformance_events`` with the UNIQUE(repo, prev_hash) chain and the PL/pgSQL append-only triggers migration
20261118024513 adds, and the declared step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite
database (``wf_db``).

No fixture redirects the backend on PG or opens a raw connection. The ledger is append-only and may be shared, so
the bundle is built from synthetic sources this file writes, and every person is minted here.
"""
from __future__ import annotations

import json
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


def test_a_bundle_prepared_and_published_on_the_platform_database(tmp_path, monkeypatch):
    from tools.db.storage import get_connection
    from tools.idp import conformance as c

    run = uuid.uuid4().hex[:8]
    preparer = {"id": f"u-coe03-amb-{run}-preparer", "role": "reviewer"}    # labelled test persons (synthetic)
    publisher = {"id": f"u-coe03-amb-{run}-publisher", "role": "reviewer"}
    git = {"schema": "icdev-it/git-history/v1", "repo": "ft", "ref": "origin/main", "head": "a" * 40,
           "since": "2026-10-19T00:00:00Z", "until": "2026-11-18T00:00:00Z", "reverts": [],
           "merges": [{"sha": f"{run}{'b' * 32}", "merged_at": "2026-11-10T00:00:00Z", "ai": True, "card": None,
                       "commits": [{"sha": "c" * 40, "authored_at": "2026-11-09T22:00:00Z", "ai": True}]}]}
    (tmp_path / "ft.git.json").write_text(json.dumps(git))
    monkeypatch.setenv("ICDEV_COE_SOURCES_DIR", str(tmp_path))
    monkeypatch.setenv("ICDEV_COE_LIVE_GIT", "0")
    prep = c.prepare("ft", preparer, as_of="2026-11-17")
    digest = prep["content_digest"]
    with pytest.raises(c.ConformanceRefused) as exc:
        c.publish("ft", decision="publish", reason="mine", read_digest=digest, actor=preparer)
    assert exc.value.code == "separation"
    out = c.publish("ft", decision="publish", reason="Ambient (synthetic).", read_digest=digest, actor=publisher)
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == publisher["id"]
    b = c.published_bundle("ft")
    assert b["content_digest"] == digest and c.validate(b) == []
    m = b["delivery"]["metrics"]
    assert m["lead_time"]["value"] == 2.0 and m["change_failure_rate"]["status"] == "unverified"
    rows = [r for r in c.read_events(repo="ft") if r["content_digest"] == digest]
    assert [r["event"] for r in rows][-2:] == ["prepared", "published"]
    assert c.chain(c.read_events(repo="ft"))["_problems"] == []
    conn = get_connection()
    try:
        steps = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                             ("conformance:ft", publisher["id"])).fetchall()
        assert [tuple(r) for r in steps] == [("decl-wft-coe-conformance-publish", publisher["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE coe_conformance_events SET reason = 'x' WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM coe_conformance_events WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
