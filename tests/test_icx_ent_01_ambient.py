# CUI // SP-CTI
"""Vulnerability discovery on whatever the platform database is (icx-ent-01).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: ``vr_events`` with the
UNIQUE(stream, prev_hash) chain and the PL/pgSQL append-only triggers migration 20261118231119 adds, and the
declared steps in ``wf_decision_steps``. In the normal suite it is the conftest SQLite database (``wf_db``).

A target is registered and authorised by two people minted here (labelled test persons, synthetic), discovery runs
on the synthetic benchmark through the sandbox double with canned SAST/SCA output (the same as the unit tests), and
a findings bundle is prepared and published. The ledger may be shared, so nothing assumes it is empty.
"""
from __future__ import annotations

import os
import uuid

import pytest

from tests.test_icx_ent_01_vulnresearch import BENCH, LocalDouble, canned_audit, canned_bandit


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    if _pg():
        return None
    return request.getfixturevalue("wf_db")


def _rollback_close(conn):
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass
    conn.close()


def test_discovery_and_a_bundle_on_the_platform_database(tmp_path, monkeypatch):
    from tools.db.storage import get_connection
    from tools.security import dependency_auditor, sast_runner
    from tools.vulnresearch import bundle, discovery, harness, ledger, pov, targets

    monkeypatch.setenv("ICDEV_VR_DATA_DIR", str(tmp_path / "vr"))
    monkeypatch.setenv("ICDEV_NO_LLM", "true")
    monkeypatch.setattr(pov, "EXECUTOR", lambda: LocalDouble())
    monkeypatch.setattr(harness, "image_present", lambda image: False)
    monkeypatch.setattr(sast_runner, "run_bandit", canned_bandit)
    monkeypatch.setattr(dependency_auditor, "audit_python", canned_audit)
    run = uuid.uuid4().hex[:8]
    a = {"id": f"u-ent01-amb-{run}-registrant", "role": "reviewer"}
    b = {"id": f"u-ent01-amb-{run}-authoriser", "role": "isso"}
    r = targets.register(a, name=f"bench {run} (synthetic)", root=BENCH, reason="ambient (synthetic)",
                         src_root="src", test_dir="tests", fuzz_targets=["vb_parser:parse_record"])
    tid = r["target_id"]
    with pytest.raises(targets.TargetRefused) as exc:
        targets.authorise(tid, decision="authorise", reason="mine", read_digest=r["content_digest"], actor=a)
    assert exc.value.code == "separation"
    targets.authorise(tid, decision="authorise", reason="ambient (synthetic)", read_digest=r["content_digest"], actor=b)
    out = discovery.run(tid, a, use_model=False)
    assert out["run"]["counts"]["findings"] == 5
    p = bundle.prepare(tid, a)
    pub = bundle.publish(tid, decision="publish", reason="ambient (synthetic)", read_digest=p["content_digest"],
                         actor=b)
    assert pub["event"] == "published"
    got = bundle.published(tid)
    assert bundle.verify(got["bundle"], got["publication"], ledger.read())["ok"]
    rows = ledger.read(stream=f"target:{tid}")
    assert [x["event"] for x in rows] == ["registered", "authorised"]
    conn = get_connection()
    try:
        steps = conn.execute("SELECT template_id FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                             (f"vr_target:{tid}", b["id"])).fetchall()
        assert [tuple(s) for s in steps] == [("decl-wft-vr-target-authorise",)]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE vr_events SET reason = 'x' WHERE event_id = %s", (rows[0]["event_id"],))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM vr_events WHERE event_id = %s", (rows[0]["event_id"],))
    finally:
        _rollback_close(conn)
