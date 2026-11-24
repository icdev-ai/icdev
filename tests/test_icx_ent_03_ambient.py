# CUI // SP-CTI
"""Signed factory builds on whatever the platform database is (icx-ent-03).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: ``fb_events`` with the
UNIQUE(stream, prev_hash) chain and the PL/pgSQL append-only triggers migration 20261122003636 adds, the fine-tuning
registry tables, and the declared step in ``wf_decision_steps``. In the normal suite it is the conftest SQLite database
(``wf_db``), with the registry tables added.

Two synthetic targets with fresh ids per run are built by one test person with a throwaway key and published by
another of their org. The ledger and the registry may be shared, so nothing assumes they are empty.
"""
from __future__ import annotations

import contextlib
import copy
import os
import shutil
import uuid

import pytest

from tests.test_icx_ent_03_factory_builds import COMMIT, ORG, _ft_ddl, throwaway_pem


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    if _pg():
        return None
    return request.getfixturevalue("wf_db")


def _rollback_close(conn):
    with contextlib.suppress(Exception):
        conn.rollback()
    conn.close()


def test_builds_prepared_and_published_on_the_platform_database(tmp_path):
    from tools.crypto import artefact_signer as asig
    from tools.db.storage import get_canvas_connection, get_connection
    from tools.factory_builds import config, ledger, service, synthetic

    run = uuid.uuid4().hex[:8]
    conn = get_canvas_connection()
    try:
        conn.execute("SELECT 1 FROM ft_model_versions WHERE 1 = 0").fetchall()
    except Exception:  # noqa: BLE001 — the SQLite test schema has no registry: add it (PG has it from init)
        _rollback_close(conn)
        conn = get_canvas_connection()
        for ddl in _ft_ddl():
            conn.execute(ddl)
        conn.commit()
    rel = f".tmp/icx-ent-03-ambient/{run}"
    cfg = config.load()
    cfg.pop("_digest")
    cfg["artefact_root"], cfg["models_root"] = rel, f"{rel}/models"
    model = f"syn-amb-{run}-classifier"
    for t in cfg["targets"]:
        t["id"] = f"syn-amb-{run}-{t['kind']}"
        if t["kind"] == "model":
            t["model"] = model
    config.validate(cfg)
    cfg["_digest"] = "ambient"
    root = config.BASE_DIR / rel
    try:
        synthetic.image_inputs(root / "syn-ent03" / "portal", run)
        m = synthetic.model_inputs(root / "models", root / "syn-ent03" / "classifier", model_name=model)
        synthetic.registry_rows(conn, adapter_path=m["adapter_path"], model_name=model)
        conn.close()
        key = tmp_path / "k.pem"
        key.write_bytes(throwaway_pem())
        s = asig.open_signer(f"file:{key}")
        a = {"id": f"u-ent03-amb-{run}-preparer", "role": "reviewer"}
        b = {"id": f"u-ent03-amb-{run}-publisher", "role": "isso"}
        ra, rb = {**a, "clearance": "CUI", "org": ORG}, {**b, "clearance": "CUI", "org": ORG}
        for t in cfg["targets"]:
            p = service.prepare(t["id"], a, ra, version="1.0.0", cfg=copy.deepcopy(cfg), signer=s, commit=COMMIT)
            with pytest.raises(service.Refused):
                service.publish(t["id"], decision="publish", reason="mine", read_digest=p["digest"], actor=a,
                                reader=ra, cfg=cfg)
            out = service.publish(t["id"], decision="publish", reason="ambient (synthetic)", read_digest=p["digest"],
                                  actor=b, reader=rb, cfg=cfg, signer=s)
            assert out["event"] == "published"
            ex = service.export(t["id"], rb, cfg=cfg)
            assert ex["publication"]["signed_by"] == b["id"] and ex["publication"]["content_digest"] == p["digest"]
            rows = ledger.read(stream=f"target:{t['id']}")
            assert [x["event"] for x in rows] == ["prepared", "published"]
        c = get_connection()
        try:
            steps = c.execute("SELECT template_id FROM wf_decision_steps WHERE actor = %s", (b["id"],)).fetchall()
            assert [tuple(x) for x in steps] == [("decl-wft-fb-build-publish",)] * 2
            with pytest.raises(Exception, match="append-only"):
                c.execute("UPDATE fb_events SET reason = 'x' WHERE event_id = %s", (rows[0]["event_id"],))
        finally:
            _rollback_close(c)
        c = get_connection()
        try:
            with pytest.raises(Exception, match="append-only"):
                c.execute("DELETE FROM fb_events WHERE event_id = %s", (rows[0]["event_id"],))
        finally:
            _rollback_close(c)
    finally:
        shutil.rmtree(root, ignore_errors=True)
