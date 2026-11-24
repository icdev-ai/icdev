# CUI // SP-CTI
"""A data discovery bundle prepared and published on whatever the platform database is (osx-cap-02).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the ledger
``data_discovery_export_events`` with the UNIQUE(source, prev_hash) chain and the PL/pgSQL append-only triggers
migration 20261118113000 adds, and the declared step in ``wf_decision_steps``. In the normal suite it is the conftest
SQLite database (``wf_db``).

No fixture redirects the platform backend on PG or opens a raw connection to it. The ledger is append-only and may be
shared, so the bundle is built from a SYNTHETIC source and profile this file writes into a per-test Data Design Canvas
database, and every person is minted here. The source key is the synthetic one the tests declare
(tests/e2e/fixtures/discovery_sources.yaml).
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import uuid

import pytest

KEY = "syn-track-export"


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


def test_a_discovery_bundle_prepared_and_published_on_the_platform_database(tmp_path, monkeypatch):
    from pathlib import Path

    from tools.data_canvas import discovery_bundle as db
    from tools.data_canvas.data_profiler import profile_database
    from tools.db.storage import get_connection

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(db.E2E_ENV, str(root / "tests" / "e2e" / "fixtures" / "discovery_sources.yaml"))
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    monkeypatch.setattr(init_db, "DB_PATH", str(tmp_path / "ddc.db"))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    init_db.init_db()
    run = uuid.uuid4().hex[:8]
    src = tmp_path / "synthetic.db"                                     # SYNTHETIC source
    s = sqlite3.connect(str(src))
    s.execute("CREATE TABLE relay_outages (id TEXT, relay TEXT, minutes INTEGER)")
    s.executemany("INSERT INTO relay_outages VALUES (?,?,?)", [(f"o{run}", "relay-x", 3), (f"p{run}", "relay-y", 4)])
    s.commit()
    s.close()
    prof = profile_database({"db_type": "sqlite", "path": str(src)}, "CUI // SP-CTI")
    c = init_db.get_connection()
    c.execute("INSERT INTO data_designs (id, name) VALUES (?,?)", (f"des-{run}", "cap02 synthetic track export"))
    c.execute("INSERT INTO dd_explore_profiles (id, design_id, profile_json, table_count, created_at) VALUES (?,?,?,?,?)",
              (f"prof-{run}", f"des-{run}", json.dumps(prof), 1, "2026-11-18T08:00:00Z"))
    c.commit()
    c.close()
    preparer = {"id": f"u-cap02i-amb-{run}-preparer", "role": "reviewer"}    # labelled test persons (synthetic)
    publisher = {"id": f"u-cap02i-amb-{run}-publisher", "role": "reviewer"}
    prep = db.prepare(KEY, preparer)
    digest = prep["content_digest"]
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.publish(KEY, decision="publish", reason="mine", read_digest=digest, actor=preparer)
    assert exc.value.code == "separation"
    out = db.publish(KEY, decision="publish", reason="Ambient (synthetic).", read_digest=digest, actor=publisher)
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == publisher["id"]
    b = db.published_bundle(KEY)
    assert b["content_digest"] == digest and db.validate(b) == [] and b["source"]["profile_id"] == f"prof-{run}"
    rows = [r for r in db.read_events(source=KEY) if r["content_digest"] == digest]
    assert [r["event"] for r in rows][-2:] == ["prepared", "published"]
    assert db.chain(db.read_events(source=KEY))["_problems"] == []
    conn = get_connection()
    try:
        steps = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                             (f"discovery:{KEY}", publisher["id"])).fetchall()
        assert [tuple(r) for r in steps] == [("decl-wft-data-discovery-publish", publisher["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE data_discovery_export_events SET reason = 'x' WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM data_discovery_export_events WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
