# CUI // SP-CTI
"""A FOCUS cost bundle prepared and published on whatever the platform database is (osx-cop-05).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the ledger
``finops_focus_events`` with the UNIQUE(period, prev_hash) chain and the PL/pgSQL append-only triggers migration
20261118143737 adds, and the declared step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite
database (``wf_db``).

No fixture redirects the backend on PG or opens a raw connection. The ledger is append-only and may be shared, so the
export is the synthetic sample with a billing month of its own per run, and every person is minted here.
"""
from __future__ import annotations

import csv
import io
import os
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "finops" / "focus_1_4_sample.csv"


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


def test_a_focus_bundle_prepared_and_published_on_the_platform_database(tmp_path, monkeypatch):
    from tools.db.storage import get_connection
    from tools.finops import focus_bundle as f

    run = uuid.uuid4().hex[:8]
    preparer = {"id": f"u-cop05-amb-{run}-preparer", "role": "reviewer"}    # labelled test persons (synthetic)
    publisher = {"id": f"u-cop05-amb-{run}-publisher", "role": "reviewer"}
    # A billing month of this run's own (a shared, append-only ledger): year 2100 + a random month offset.
    n = int(run, 16) % 900
    start = f"{2100 + n // 12}-{n % 12 + 1:02d}-01T00:00:00Z"
    end_y, end_m = (2100 + (n + 1) // 12, (n + 1) % 12 + 1)
    end = f"{end_y}-{end_m:02d}-01T00:00:00Z"
    rows = list(csv.DictReader(io.StringIO(SAMPLE.read_text(encoding="utf-8"))))
    for r in rows:
        r.update(BillingPeriodStart=start, BillingPeriodEnd=end, ChargePeriodStart=start, ChargePeriodEnd=end)
    d = tmp_path / "focus"
    d.mkdir()
    with (d / "amb.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    monkeypatch.setenv("ICDEV_FINOPS_FOCUS_DIR", str(d))
    prep = f.prepare("amb.csv", preparer)
    digest, period = prep["content_digest"], prep["period"]
    assert period == start[:7]
    with pytest.raises(f.FocusRefused) as exc:
        f.publish(prep["event_id"], decision="publish", reason="mine", read_digest=digest, actor=preparer)
    assert exc.value.code == "separation"
    out = f.publish(prep["event_id"], decision="publish", reason="Ambient (synthetic).", read_digest=digest,
                    actor=publisher)
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == publisher["id"]
    b = f.published_bundle(out["event_id"])
    assert b["content_digest"] == digest and f.verify(b) == []
    assert {w["key"]: w["effective_cost"] for w in b["workloads"]}["icos-api"] == "96.457550"
    events = f.read_events(period=period)
    assert [r["event"] for r in events if r["content_digest"] == digest][-2:] == ["prepared", "published"]
    assert f.chain(events)["_problems"] == []
    conn = get_connection()
    try:
        steps = conn.execute("SELECT template_id, actor FROM wf_decision_steps WHERE subject = %s AND actor = %s",
                             (f"finops_focus:{period}", publisher["id"])).fetchall()
        assert [tuple(r) for r in steps] == [("decl-wft-finops-focus-publish", publisher["id"])]
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE finops_focus_events SET reason = 'x' WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
    conn = get_connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM finops_focus_events WHERE event_id = %s", (out["event_id"],))
    finally:
        _rollback_close(conn)
