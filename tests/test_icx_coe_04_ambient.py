# CUI // SP-CTI
"""A golden path version proposed, drift-checked and published on whatever the platform database is (icx-coe-04).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the ledger
``coe_golden_path_events`` with the UNIQUE(path_id, prev_hash) chain and the PL/pgSQL append-only triggers migration
20261118070149 adds, and the declared step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite
database (``wf_db``).

No fixture redirects the backend on PG or opens a raw connection. The ledger is append-only and may be shared, so the
path id and every person are minted here. The drift check itself is exercised in test_icx_coe_04_golden_paths.py;
here its result is the one a passing check records.
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


def test_a_path_version_proposed_drift_checked_and_published_on_the_platform_database(monkeypatch):
    from tools.db.storage import get_connection
    from tools.idp import golden_paths as gp

    run = uuid.uuid4().hex[:8]
    author = {"id": f"u-coe04-amb-{run}-author", "role": "reviewer"}       # labelled test persons (synthetic)
    publisher = {"id": f"u-coe04-amb-{run}-publisher", "role": "reviewer"}
    pid = f"amb-{run}"
    base = gp.path_entry("service")
    # Declare exactly what is in force on this database (a shared ledger may hold more than the seeds).
    need = gp.required(base, gp.in_force("ledger"))
    entry = {**base, "id": pid, "version": "1.0",
             "satisfies": [{"id": s["id"], "version": s["version"], "by": "ambient"} for s in need]
             or base["satisfies"]}

    def passing(e, *, forced=None, source="register", cfg=None, keep=None):
        return {"result": "pass", "why": [], "summary": {}, "template_digest": gp.template_digest(e),
                "standards": [], "feature_audit": {}, "tests": {}, "pieces": [], "behind": gp.behind(e, forced or []),
                "seconds": 0.0, "key": "golden_drift"}

    monkeypatch.setattr(gp, "drift", passing)
    p = gp.propose(entry, author, note="ambient (synthetic)")
    gp.record_drift(pid, "1.0", author)
    assert not gp.behind(entry, gp.in_force("ledger"))["behind"]
    with pytest.raises(gp.GoldenPathRefused) as exc:
        gp.publish(pid, "1.0", decision="publish", reason="mine", read_digest=p["entry_digest"], actor=author)
    assert exc.value.code == "separation"
    out = gp.publish(pid, "1.0", decision="publish", reason="Ambient (synthetic).", read_digest=p["entry_digest"],
                     actor=publisher)
    assert out["event"] == "published" and gp.path_view(pid)["in_use"] == "1.0"
    rows = gp.read_events(path_id=pid)
    assert [r["event"] for r in rows] == ["proposed", "drift_checked", "published"]
    assert gp.chain(rows)["_problems"] == []
    conn = get_connection()
    try:
        steps = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                             (f"golden_path:{pid}@1.0", publisher["id"])).fetchall()
        assert [tuple(r) for r in steps] == [("decl-wft-coe-golden-path-publish", publisher["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE coe_golden_path_events SET reason = 'x' WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM coe_golden_path_events WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
