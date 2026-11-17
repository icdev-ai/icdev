# CUI // SP-CTI
"""The survivor IV&V and its sign-off on whatever the platform database is (icx-mdz-06).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the result is written to the
append-only, hash-chained ``audit_trail`` (``log_event``) and read back by its action, the sign-off is a person's
``decl-wft-mdz-survivor-ivv-signoff`` step in the append-only ``wf_decision_steps`` (``declared.take_step``), and the
builders are read from both. In the normal suite it is the conftest SQLite database (``wf_db``), with the tables
created from their definitions of record (icx-mdz-04's ambient fixtures, reused).

No fixture redirects the backend on PG or opens a raw connection. The audit trail and the ledger are append-only and may
be shared, so everything is keyed by an application id this file mints; the other rows are removed afterwards.
"""
from __future__ import annotations

from tests.test_icx_mdz_04_ambient import app, platform_db  # noqa: F401 -- fixtures, reused


def test_the_result_and_its_signoff_are_read_back_and_the_bundle_carries_it(app, tmp_path):  # noqa: F811
    from tools.db.storage import get_connection
    from tools.modernization import apptwin_export as ax
    from tools.modernization import survivor_ivv
    from tools.workflow_hitl import declared

    out = tmp_path / "outputs"
    out.mkdir()
    (out / "cart.py").write_text("def add(cart, item):\n    return cart + [item]\n", encoding="utf-8")
    r = survivor_ivv.run(app, target_dir=str(out))
    back = survivor_ivv.latest(app)
    assert back["digest"] == r["digest"] and back["audit_id"] == r["audit_id"] and back["areas"] == r["areas"]
    assert "pass" not in {a["status"] for a in r["areas"] if any(q["automation"] != "auto" for q in a["requirements"])}
    assert survivor_ivv.for_bundle(app)["section"] is None and survivor_ivv.signoff(app, back) == {"state": "unsigned"}
    isso = {"id": f"u-{app}-isso", "role": "isso"}          # a labelled test person (synthetic)
    built = survivor_ivv.builders(app)
    assert isso["id"] not in built
    step = declared.take_step(survivor_ivv.TEMPLATE, surface=survivor_ivv.SURFACE, decision="sign",
                              subject=survivor_ivv.subject(app), reason="mdz06 ambient: independent review (synthetic)",
                              actor=isso, payload=survivor_ivv.payload(back), separated_from=built)
    assert step["affirmative"] is True
    so = survivor_ivv.signoff(app, survivor_ivv.latest(app))
    assert so["state"] == "signed" and so["by"] == isso["id"] and so["result_digest"] == r["digest"]
    fb = survivor_ivv.for_bundle(app)
    assert fb["section"]["kind"] == "independent" and fb["section"]["assessor"] == isso["id"]
    conn = get_connection()
    try:
        b = ax.build(app, conn, at="2026-11-16T00:00:00Z", survivor_ivv=fb)
    finally:
        conn.close()
    assert ax.validate(b) == [] and b["ivv"]["recommendation"] == r["recommendation"]
