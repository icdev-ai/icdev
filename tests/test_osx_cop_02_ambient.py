# CUI // SP-CTI
"""ICDEV[IT]'s AI-BOM prepared and published on whatever the platform database is (osx-cop-02).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the ledger ``aibom_export_events``
with the UNIQUE(system, prev_hash) chain and the PL/pgSQL append-only triggers migration 20261118140000 adds, and the
declared step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite database (``wf_db``).

No fixture redirects the platform backend on PG or opens a raw connection to it. The ledger is append-only and may be
shared, so the AI-BOM is built from the SYNTHETIC components the tests declare (tests/e2e/fixtures/aibom_sources.yaml),
and every person is minted here.
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


def test_an_ai_bom_prepared_and_published_on_the_platform_database(monkeypatch):
    from pathlib import Path

    from tools.compliance import aibom_bundle as ab
    from tools.db.storage import get_connection

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(ab.E2E_ENV, str(root / "tests" / "e2e" / "fixtures" / "aibom_sources.yaml"))
    run = uuid.uuid4().hex[:8]
    preparer = {"id": f"u-cop02i-amb-{run}-preparer", "role": "reviewer"}    # labelled test persons (synthetic)
    publisher = {"id": f"u-cop02i-amb-{run}-publisher", "role": "reviewer"}
    prep = ab.prepare(preparer, as_of="2026-11-18")
    digest = prep["content_digest"]
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.publish(decision="publish", reason="mine", read_digest=digest, actor=preparer)
    assert exc.value.code == "separation"
    out = ab.publish(decision="publish", reason="Ambient (synthetic).", read_digest=digest, actor=publisher)
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == publisher["id"]
    b = ab.published_bundle()
    assert b["content_digest"] == digest and ab.validate(b) == [] and b["publication"]["prepared_by"] == preparer["id"]
    rows = ab.read_events()
    assert [r["event"] for r in rows if r["actor"] in (preparer["id"], publisher["id"])] == ["prepared", "published"]
    assert ab.chain(rows)["problems"] == []
    conn = get_connection()
    try:
        steps = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                             ("aibom:icdev-it", publisher["id"])).fetchall()
        assert [tuple(r) for r in steps] == [("decl-wft-aibom-publish", publisher["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE aibom_export_events SET reason = 'x' WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM aibom_export_events WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
