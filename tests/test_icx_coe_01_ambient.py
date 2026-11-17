# CUI // SP-CTI
"""The engineering standards lifecycle on whatever the platform database is (icx-coe-01).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the ledger ``coe_standard_events``
(migration 20261117231010, PL/pgSQL append-only triggers, ``UNIQUE(standard_id, prev_hash)``) and the declared steps in
``wf_decision_steps``. In the normal suite it is the conftest SQLite database (``wf_db``).

No fixture redirects the backend on PG or opens a raw connection. Both tables are append-only and may be shared, so
everything is keyed by a standard id this file mints.
"""
from __future__ import annotations

import os
import uuid

import pytest


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    """The conftest SQLite platform DB (it carries both tables), unless this is the PG tier."""
    if _pg():
        return None
    return request.getfixturevalue("wf_db")


def test_the_lifecycle_on_the_platform_database():
    from tools.db.storage import get_connection
    from tools.idp import standards as s

    sid = f"ENG-COE01-AMB-{uuid.uuid4().hex[:8].upper()}"
    author = {"id": f"u-{sid.lower()}-author", "role": "developer"}      # labelled test persons (synthetic)
    steward = {"id": f"u-{sid.lower()}-steward", "role": "reviewer"}
    entry = {"id": sid, "title": "Ambient lifecycle (synthetic)", "version": "1.0", "owner": "developer",
             "scope": "Synthetic: the coe01 ambient test only.", "sources": [{"path": "tools/idp/standards.py"}],
             "check": {"scorecard_rule": {"scorecard": "component-readiness", "rule": "completeness-gate"}}}
    s.propose(entry, author)
    v = s.standard_view(sid)
    with pytest.raises(s.StandardRefused) as exc:
        s.review(sid, "1.0", decision="endorse", reason="mine", read_digest=v["entry_digest"],
                 actor={**author, "role": "reviewer"})
    assert exc.value.code == "separation"
    s.review(sid, "1.0", decision="endorse", reason="ambient review (synthetic)", read_digest=v["entry_digest"],
             actor=steward)
    s.adopt(sid, "1.0", decision="sign", reason="ambient adoption (synthetic)", read_digest=v["entry_digest"],
            actor=steward)
    s.deprecate(sid, "1.0", reason="ambient retirement (synthetic)", read_digest=v["entry_digest"], actor=steward)
    v = s.standard_view(sid)
    assert [h["event"] for h in v["history"]] == ["proposed", "reviewed", "adopted", "deprecated"]
    assert v["chain"]["intact"] and v["state"] == "deprecated" and v["in_force"] is None
    conn = get_connection()
    try:
        rows = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s ORDER BY created_at",
                            (s.subject(sid, "1.0"),)).fetchall()
        assert [tuple(r) for r in rows] == [("decl-wft-coe-standard-review", steward["id"]),
                                            ("decl-wft-coe-standard-adopt", steward["id"]),
                                            ("decl-wft-coe-standard-deprecate", steward["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE coe_standard_events SET actor = 'x' WHERE standard_id = %s", (sid,))
    finally:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        conn.close()
    # the chain refuses a second event after the same one
    conn = get_connection()
    try:
        with pytest.raises(s.StandardRefused) as exc:
            s._append(conn, standard_id=sid, version="1.0", event="proposed", actor="x", actor_kind="person",
                      entry=entry, prev_hash=s.GENESIS)
        assert exc.value.code == "concurrent"
    finally:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        conn.close()
