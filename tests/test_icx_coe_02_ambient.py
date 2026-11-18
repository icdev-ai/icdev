# CUI // SP-CTI
"""Exemptions that expire and ADR records on whatever the platform database is (icx-coe-02).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the exemption log
``idp_rule_exemptions`` with the columns, chain index and PL/pgSQL append-only triggers migration 20261118010237
adds, the ADR ledger ``coe_adr_events``, and the declared steps in ``wf_decision_steps``. In the normal suite it
is the conftest SQLite database (``wf_db``).

No fixture redirects the backend on PG or opens a raw connection. Every table here is append-only and may be
shared, so everything is keyed by ids this file mints.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

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


def test_an_exemption_lifecycle_on_the_platform_database():
    from tools.db.storage import get_connection
    from tools.idp import exemptions as e

    run = uuid.uuid4().hex[:8]
    requester = {"id": f"u-coe02-amb-{run}-requester", "role": "reviewer"}    # labelled test persons (synthetic)
    approver = {"id": f"u-coe02-amb-{run}-approver", "role": "reviewer"}
    today = datetime.now(timezone.utc).date()
    then = datetime.now(timezone.utc) - timedelta(days=40)
    row = e.request_exemption("component-readiness", "e2e-spec", f"coe02-amb-{run}", reason="Ambient (synthetic).",
                              requested_by=requester["id"], expires=(today - timedelta(days=5)).isoformat(),
                              clock=lambda: then)
    key = row["exemption_key"]
    cur = e._latest(e._open_connection(), key)
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(key, decision="grant", reason="mine", read_digest=e.exemption_digest(cur),
                           actor=requester, clock=lambda: then)
    assert exc.value.code == "separation"
    e.decide_exemption(key, decision="grant", reason="Ambient grant (synthetic).",
                       read_digest=e.exemption_digest(cur), actor=approver, clock=lambda: then)
    chk = e.check()
    assert key in [x["exemption_key"] for x in chk["expired"]], "expired: it fails the check"
    view = e.exemptions_view()
    assert next(x for x in view["exemptions"] if x["exemption_key"] == key)["state"] == "expired"
    events = [x for x in e.history(conn=None) if x["exemption_key"] == key]
    assert [x["event"] for x in events] == ["requested", "approved"]
    assert e.verify_chain(events)["problems"] == [] and events[0]["prev_hash"] == e.GENESIS
    e.revoke_by_key(key, revoked_by=approver, reason="Ambient clean-up (synthetic).")
    conn = get_connection()
    try:
        rows = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s",
                            (f"exemption:{key}",)).fetchall()
        assert [tuple(r) for r in rows] == [("decl-wft-coe-exemption-grant", approver["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE idp_rule_exemptions SET reason = 'x' WHERE exemption_key = %s", (key,))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM idp_rule_exemptions WHERE exemption_key = %s", (key,))
    finally:
        _rollback_close(conn)


def test_an_adr_lifecycle_on_the_platform_database():
    from tools.db.storage import get_connection
    from tools.idp import adrs as a

    run = uuid.uuid4().hex[:8].upper()
    author = {"id": f"u-coe02-amb-{run.lower()}-architect", "role": "reviewer"}   # labelled test persons
    signer = {"id": f"u-coe02-amb-{run.lower()}-approver", "role": "reviewer"}
    first = {"id": f"D-COE02AMB-{run}-1", "title": "Ambient decision (synthetic)", "version": "1",
             "section": "Ambient", "context": "Synthetic.", "decision": "Synthetic.", "consequences": "None.",
             "links": {"requirements": ["PM-COE-09"], "standards": []}}
    second = {**first, "id": f"D-COE02AMB-{run}-2", "supersedes": [first["id"]]}
    a.propose(first, author)
    v = a.adr_view(first["id"])
    with pytest.raises(a.AdrRefused) as exc:
        a.accept(first["id"], "1", decision="accept", reason="mine", read_digest=v["entry_digest"], actor=author)
    assert exc.value.code == "separation"
    a.accept(first["id"], "1", decision="accept", reason="ok (synthetic)", read_digest=v["entry_digest"], actor=signer)
    a.propose(second, author)
    v2 = a.adr_view(second["id"])
    a.accept(second["id"], "1", decision="accept", reason="ok (synthetic)", read_digest=v2["entry_digest"],
             actor=signer)
    a.supersede(first["id"], "1", decision="supersede", superseded_by=second["id"], reason="replaced (synthetic)",
                read_digest=v["entry_digest"], actor=signer)
    v = a.adr_view(first["id"])
    assert [h["event"] for h in v["history"]] == ["proposed", "accepted", "superseded"]
    assert v["chain"]["intact"] and v["superseded_by"] == second["id"]
    conn = get_connection()
    try:
        rows = conn.execute("SELECT template_id FROM wf_decision_steps WHERE subject = %s ORDER BY created_at",
                            (a.subject(first["id"], "1"),)).fetchall()
        assert [tuple(r)[0] for r in rows] == ["decl-wft-coe-adr-accept", "decl-wft-coe-adr-supersede"]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE coe_adr_events SET actor = 'x' WHERE adr_id = %s", (first["id"],))
    finally:
        _rollback_close(conn)
    conn = a._connection()
    try:
        with pytest.raises(a.AdrRefused) as exc:
            a._append(conn, adr_id=first["id"], version="1", event="proposed", actor="x", actor_kind="person",
                      entry=first, prev_hash=a.GENESIS)
        assert exc.value.code == "concurrent"
    finally:
        _rollback_close(conn)
