# CUI // SP-CTI
"""Thread oracles and self-approval, narrowed (icx-sec-01).

What the card promises, each clause a test here:

  * the shared twin thread resolver (``tools/twin_core/governance.py resolve_thread``) follows a thread only for a
    reader who may see the item it belongs to; for anyone else -- another org, a lower clearance, an agent, no reader
    at all -- the answer is exactly what an id that does not exist gets, byte for byte (no existence oracle):
      - ``wf_step``: a declared step is held to its workflow item's own visibility (a factory build's target, a store
        package, a system's objective evidence); a step whose item has no declared visibility rule resolves for no one;
      - ``factory_builds`` and ``evidence_collectors``: the owning module's own reader rule (``thread_record``);
      - a row that carries its own tenant and marking (cortex audit and findings, scorecard exemptions, studio
        workflows): a reader of that tenant cleared for it; a row that carries a marking (the platform audit trail,
        the ACE audit log): a reader cleared for it;
    and the console routes and the MCP tool pass who is asking;
  * a factory build's export carries the target's org in its publication and its declared step, and re-checks the
    publisher's org and clearance at export, refusing in fixed words;
  * the ClawHub promote route never approves its own review and never takes ``promoted_by`` from the body.

Every person is a labelled test person and every target synthetic.
"""
from __future__ import annotations

import json
import sys
import uuid

import pytest
from flask import Flask, g

from tests import test_icx_ent_03_factory_builds as e3
from tests.test_icx_ent_03_factory_builds import OUTSIDER, PREPARER, PUBLISHER, UNCLEARED, _prepare, _publish, reader

#: icx-ent-03's fixtures (a synthetic factory configuration and a throwaway signer), reused as they are.
fb, signer = e3.fb, e3.signer

ORG = PUBLISHER["tenant_id"]


def _same(a: dict, b: dict) -> bool:
    """Byte-identical apart from the thread id the caller sent."""
    def strip(d):
        return json.dumps({k: v for k, v in d.items() if k != "thread_id"}, sort_keys=True, default=str)
    return strip(a) == strip(b)


def _hidden_like_unknown(thread: str, unknown: str, readers) -> None:
    from tools.twin_core import governance

    want = governance.resolve_thread(unknown, reader=readers[0])
    assert want["resolved"] is False
    for r in readers:
        got = governance.resolve_thread(thread, reader=r)
        assert got["resolved"] is False and _same(got, want), (r, got, want)
        assert _same(governance.resolve_thread(unknown, reader=r), want)


def _agent():
    from tools.store_publisher.service import mcp_reader

    return mcp_reader("sec01-helper")


def _customer(fb, monkeypatch):
    """The fixture's targets as a customer's (not synthetic): an agent, in no org, sees none of them."""
    import copy

    from tools.factory_builds import config

    cfg = copy.deepcopy(fb["cfg"])
    for t in cfg["targets"]:
        t["synthetic"] = False
    monkeypatch.setattr(config, "load", lambda path=None: copy.deepcopy(cfg))
    return cfg


@pytest.fixture()
def people(monkeypatch):
    """Test people's own rows, as tools.dashboard.auth.get_user_by_id returns them (fresh org and clearance)."""
    from tools.dashboard import auth

    rows = {u["id"]: {"id": u["id"], "role": u["role"], "tenant_id": u["tenant_id"],
                      "clearance_level": u["clearance_level"], "status": "active"}
            for u in (PREPARER, PUBLISHER, OUTSIDER, UNCLEARED)}
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid, tenant_id=None: rows.get(str(uid)))
    return rows


# ── 1. wf_step ───────────────────────────────────────────────────────────────


def test_a_step_is_followed_only_by_a_reader_who_may_see_its_item(fb, signer, people, monkeypatch):
    from tools.twin_core import governance

    p = _prepare(fb, signer)
    pub = _publish(fb, p)
    thread = f"wf_step:{pub['step_id']}"
    mine = governance.resolve_thread(thread, reader=reader(PUBLISHER))
    assert mine["resolved"] and mine["record"]["subject"] == "factory_build:syn-ent03-portal"
    unknown = "wf_step:step-" + "0" * 16
    _hidden_like_unknown(thread, unknown, [reader(OUTSIDER), reader(UNCLEARED), None])
    _customer(fb, monkeypatch)
    _hidden_like_unknown(thread, unknown, [_agent()])


def test_a_step_whose_item_declares_no_visibility_resolves_for_no_one(wf_db):
    from tools.workflow_hitl import declared

    tmpl = declared.load_templates()["decl-wft-cortex-award"]
    step = declared.propose("decl-wft-cortex-award", surface=tmpl["decides"][0], subject="bid:syn-sec01",
                            payload={"bid": "syn-sec01"}, actor={"id": "u-sec01-proposer", "role": "pm"},
                            note="SYNTHETIC: a tenant's bid")["step_id"]
    who = {"id": "u-sec01-reader", "role": "admin", "clearance": "SECRET", "org": "syn-sec01-org"}
    _hidden_like_unknown(f"wf_step:{step}", "wf_step:step-" + "f" * 16, [who, _agent(), None])


def test_a_platform_register_s_step_is_as_open_as_its_register(wf_db):
    """The CoE's engineering standards are a platform register: their own threads are open, and so is their step."""
    from tools.twin_core import governance
    from tools.workflow_hitl import declared

    tmpl = declared.load_templates()["decl-wft-coe-standard-review"]
    step = declared.propose("decl-wft-coe-standard-review", surface=tmpl["decides"][0], subject="standard:syn-sec01",
                            payload={}, actor={"id": "u-sec01-proposer", "role": "developer"})["step_id"]
    assert governance.resolve_thread(f"wf_step:{step}", reader=_agent())["resolved"]


# ── 2. the owning module's thread kinds ──────────────────────────────────────


def test_a_factory_build_thread_is_the_target_s_readers_only(fb, signer, people, monkeypatch):
    from tools.twin_core import governance

    p = _prepare(fb, signer)
    pub = _publish(fb, p)
    thread = f"factory_builds:{pub['event_id']}"
    mine = governance.resolve_thread(thread, reader=reader(PUBLISHER))
    assert mine["resolved"] and mine["record"]["event"] == "published" and "actor" not in mine["record"]
    unknown = "factory_builds:fbe-" + "0" * 16
    _hidden_like_unknown(thread, unknown, [reader(OUTSIDER), reader(UNCLEARED), None])
    _customer(fb, monkeypatch)
    _hidden_like_unknown(thread, unknown, [_agent()])


def test_an_objective_evidence_thread_is_the_system_s_readers_only(wf_db, monkeypatch):
    import copy
    from datetime import datetime, timezone

    from tests import test_icx_ent_02_evidence_collectors as e2
    from tools.evidence_collectors import config, credentials, service
    from tools.twin_core import governance

    stub = e2.Stub()
    try:
        cfg = e2.stub_config(stub.base)
        monkeypatch.setattr(config, "load", lambda path=None: copy.deepcopy(cfg))
        monkeypatch.setattr(credentials, "resolve", e2.fake_resolve)
        monkeypatch.setattr(service, "resolve_ref", e2.fake_resolve)
        monkeypatch.setattr(service, "_LAST_LIVE", {}, raising=False)
        ec = {"cfg": cfg, "stub": stub, "db": wf_db}
        p = e2._prepare(ec, now=datetime.now(timezone.utc))
        pub = e2._publish(ec, p)
    finally:
        stub.close()
    thread = f"evidence_collectors:{pub['event_id']}"
    assert governance.resolve_thread(thread, reader=e2.reader(e2.PUBLISHER))["resolved"]
    _hidden_like_unknown(thread, "evidence_collectors:ece-" + "0" * 16,
                         [e2.reader(e2.OUTSIDER), e2.reader(e2.UNCLEARED), None])
    step = f"wf_step:{pub['step']['step_id']}"
    assert governance.resolve_thread(step, reader=e2.reader(e2.PUBLISHER))["resolved"]
    _hidden_like_unknown(step, "wf_step:step-" + "0" * 16, [e2.reader(e2.OUTSIDER), None])


# ── 2b. rows that carry their own tenant and marking ─────────────────────────


def _row(sql: str, params: tuple, conn_kind: str = "rls"):
    from tools.twin_core import governance

    conn = governance._thread_conn(conn_kind)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_a_tenant_row_is_followed_only_by_its_tenant_cleared_for_it(wf_db):
    a, s = f"cgov-sec01-{uuid.uuid4().hex[:8]}", f"cgov-sec01-{uuid.uuid4().hex[:8]}"
    _row("INSERT INTO cortex_audit (id, session_id, tenant_id, classification, function, outcome) "
         "VALUES (%s,%s,%s,%s,%s,%s)", (a, "s-1", "syn-sec01-a", "CUI", "cortex", "pass"))
    _row("INSERT INTO cortex_audit (id, session_id, tenant_id, classification, function, outcome) "
         "VALUES (%s,%s,%s,%s,%s,%s)", (s, "s-2", "syn-sec01-a", "SECRET", "cortex", "pass"))
    w = f"wf-sec01-{uuid.uuid4().hex[:8]}"
    _row("INSERT INTO studio_workflows (workflow_id, name, created_by, tenant_id, classification) "
         "VALUES (%s,%s,%s,%s,%s)", (w, "SYNTHETIC tenant workflow", "u-sec01", "syn-sec01-a", "CUI"), "core")
    from tools.twin_core import governance

    tenant = {"id": "u-sec01-a", "role": "isso", "clearance": "CUI", "org": "syn-sec01-a"}
    other = dict(tenant, id="u-sec01-b", org="syn-sec01-b")
    low = dict(tenant, clearance="PUBLIC")
    for thread, unknown in ((f"cortex_audit:{a}", "cortex_audit:cgov-none"),
                            (f"studio_workflow:{w}", "studio_workflow:wf-none")):
        res = governance.resolve_thread(thread, reader=tenant)
        assert res["resolved"] and "tenant_id" not in res["record"] and "classification" not in res["record"]
        _hidden_like_unknown(thread, unknown, [other, low, _agent(), None])
    assert governance.resolve_thread(f"cortex_audit:{s}", reader=dict(tenant, clearance="SECRET"))["resolved"]
    # A row with no tenant is a platform row: any reader cleared for it, never no reader.
    x = f"cgov-sec01-{uuid.uuid4().hex[:8]}"
    _row("INSERT INTO cortex_audit (id, session_id, tenant_id, classification, function, outcome) "
         "VALUES (%s,%s,%s,%s,%s,%s)", (x, "s-3", "", "CUI // SP-CTI", "cortex", "pass"))
    assert governance.resolve_thread(f"cortex_audit:{x}", reader=other)["resolved"]
    _hidden_like_unknown(f"cortex_audit:{x}", "cortex_audit:cgov-none", [low, None])
    _hidden_like_unknown(f"cortex_audit:{s}", "cortex_audit:cgov-none", [tenant, _agent()])


def test_a_marked_audit_row_is_followed_only_by_a_reader_cleared_for_it(wf_db):
    from tools.twin_core import governance

    conn = governance._thread_conn("core")
    try:
        conn.execute("INSERT INTO audit_trail (event_type, actor, action, details, classification) "
                     "VALUES (%s,%s,%s,%s,%s)", ("approval_denied", "u-sec01", "SYNTHETIC sec01", "{}", "SECRET"))
        conn.commit()
        rid = conn.execute("SELECT MAX(id) FROM audit_trail").fetchone()[0]
    finally:
        conn.close()
    cleared = {"id": "u-sec01-s", "role": "isso", "clearance": "SECRET", "org": "syn-sec01-a"}
    assert governance.resolve_thread(f"audit_trail:{rid}", reader=cleared)["resolved"]
    _hidden_like_unknown(f"audit_trail:{rid}", "audit_trail:999999999",
                         [dict(cleared, clearance="CUI"), _agent(), None])


# ── the surfaces pass who is asking ──────────────────────────────────────────


def test_the_mcp_tool_and_the_observatory_route_hold_a_thread_to_its_item(fb, signer, people, monkeypatch):
    from tools.mcp.gap_handlers import handle_twin_governance
    from tools.twin_observatory.blueprint import twin_observatory_bp

    p = _prepare(fb, signer)
    pub = _publish(fb, p)
    _customer(fb, monkeypatch)
    for thread, unknown in ((f"wf_step:{pub['step_id']}", "wf_step:step-" + "0" * 16),
                            (f"factory_builds:{pub['event_id']}", "factory_builds:fbe-" + "0" * 16)):
        got = handle_twin_governance({"thread_id": thread})["thread"]
        want = handle_twin_governance({"thread_id": unknown})["thread"]
        assert not got["resolved"] and _same(got, want), "an agent (in no org) holding an id learns nothing"

    def client(user):
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.before_request
        def _auth():
            g.current_user = {"id": user["id"], "role": user["role"]}

        app.register_blueprint(twin_observatory_bp)
        return app.test_client()

    url = "/twin-observatory/api/governance/thread"
    thread = f"wf_step:{pub['step_id']}"
    ok = client(PUBLISHER).get(url, query_string={"id": thread})
    assert ok.status_code == 200 and ok.get_json()["record"]["subject"] == "factory_build:syn-ent03-portal"
    o = client(OUTSIDER)
    hidden, none = o.get(url, query_string={"id": thread}), o.get(url, query_string={"id": "wf_step:step-" + "0" * 16})
    assert hidden.status_code == none.status_code == 404
    assert _same(hidden.get_json(), none.get_json())


# ── 3. a factory build's export: the org carried, the publisher re-checked ───


def test_the_export_carries_the_org_and_the_step_signed_it(fb, signer, people):
    from tools.factory_builds import service
    from tools.workflow_hitl import declared

    p = _prepare(fb, signer)
    pub = _publish(fb, p)
    ex = service.export("syn-ent03-portal", reader(PUBLISHER))
    assert ex["publication"]["org"] == ORG
    signed = json.loads(declared._events(pub["step_id"])["decided"]["payload_json"])
    assert signed["org"] == ORG, "the declared step binds the target's owning org"
    assert service.step_ok(ex["publication"] | {"actor": PUBLISHER["id"], "prepared_event": p["event_id"]}, ORG)
    assert not service.step_ok(ex["publication"] | {"actor": PUBLISHER["id"], "prepared_event": p["event_id"]},
                               "another-org")


def test_a_step_signed_for_another_org_publishes_nothing(fb, signer, people, monkeypatch):
    from tools.factory_builds import service
    from tools.workflow_hitl import declared

    p = _prepare(fb, signer)
    _publish(fb, p)
    real = declared._events

    def other_org(sid, conn=None):
        ev = real(sid, conn)
        d = dict(ev["decided"])
        d["payload_json"] = json.dumps({**json.loads(d["payload_json"]), "org": "another-org"})
        return {**ev, "decided": d}

    monkeypatch.setattr(declared, "_events", other_org)
    with pytest.raises(service.Refused) as e:
        service.export("syn-ent03-portal", reader(PUBLISHER))
    assert e.value.code == "step_missing"


@pytest.mark.parametrize("change", [{"tenant_id": "another-org"}, {"clearance_level": "PUBLIC"}, None])
def test_a_publisher_no_longer_of_the_org_or_cleared_publishes_nothing(fb, signer, people, change):
    from tools.factory_builds import service

    p = _prepare(fb, signer)
    _publish(fb, p)
    if change is None:
        people.pop(PUBLISHER["id"])            # their row is gone or unreadable: fail closed
    else:
        people[PUBLISHER["id"]].update(change)
    with pytest.raises(service.Refused) as e:
        service.export("syn-ent03-portal", reader(PREPARER))
    assert (e.value.status, e.value.code, e.value.message) == (409, "publisher", service.PUBLISHER_REFUSED)
    assert "another-org" not in e.value.message and PUBLISHER["id"] not in e.value.message


# ── 4. ClawHub promote ───────────────────────────────────────────────────────


@pytest.fixture()
def clawhub(monkeypatch, tmp_path):
    """The route on a bare app, the bridge's database a private SQLite file, promote_import recorded not run."""
    import sqlite3

    from tools.dashboard.api import clawhub as mod
    from tools.marketplace import openclaw_bridge as bridge

    db = tmp_path / "claw.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE openclaw_imports (id TEXT PRIMARY KEY, review_required INTEGER, review_id TEXT, "
              "status TEXT, tenant_id TEXT)")
    c.executemany("INSERT INTO openclaw_imports VALUES (?,?,?,?,?)",
                  [("oci-plain", 0, None, "quarantined", "syn-sec01"), ("oci-review", 1, None, "review_pending",
                                                                          "syn-sec01"),
                   ("oci-selfrev", 1, "rev-dash-oci-self", "review_pending", "syn-sec01")])
    c.commit()
    c.close()

    def get_db():
        from tools.db.storage import StorageConnection

        return StorageConnection(sqlite3.connect(db), "sqlite")

    calls = []
    monkeypatch.setattr(bridge, "_get_db", get_db)
    monkeypatch.setattr(bridge, "promote_import", lambda iid, by: calls.append((iid, by)) or
                        {"success": False, "import_id": iid, "promoted_by": by})

    def client(user):
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.before_request
        def _auth():
            if user is not None:
                g.current_user = dict(user)

        app.register_blueprint(mod.clawhub_api)
        return app.test_client()

    def review_ids():
        c = sqlite3.connect(db)
        try:
            return dict(c.execute("SELECT id, review_id FROM openclaw_imports").fetchall())
        finally:
            c.close()

    return {"client": client, "calls": calls, "review_ids": review_ids}


def test_promote_is_the_signed_in_person_s_never_the_body_s(clawhub):
    isso = {"id": "u-sec01-isso", "role": "isso"}
    r = clawhub["client"](isso).post("/api/clawhub/promote", json={"import_id": "oci-plain",
                                                                    "promoted_by": "someone-else"})
    assert r.status_code == 200 and clawhub["calls"] == [("oci-plain", "u-sec01-isso")]
    for who, status in ((None, 401), ({"id": "u-svc", "role": "service"}, 403),
                        ({"id": "agent:helper", "role": "admin"}, 403)):
        r = clawhub["client"](who).post("/api/clawhub/promote", json={"import_id": "oci-plain",
                                                                       "promoted_by": "isso@test"})
        assert r.status_code == status
    assert len(clawhub["calls"]) == 1


def test_promote_never_approves_its_own_review(clawhub):
    from tools.dashboard.api import clawhub as mod

    before = clawhub["review_ids"]()
    for iid in ("oci-review", "oci-selfrev"):
        r = clawhub["client"]({"id": "u-sec01-isso", "role": "isso"}).post("/api/clawhub/promote",
                                                                          json={"import_id": iid})
        assert r.status_code == 409 and r.get_json() == {"error": mod.REVIEW_REFUSED, "code": "review_required"}
    assert clawhub["review_ids"]() == before, "the route writes no review"
    assert clawhub["calls"] == [], "nothing that needs a review is promoted here"
    from pathlib import Path

    assert "UPDATE openclaw_imports SET review_id" not in Path(mod.__file__).read_text(encoding="utf-8")


def test_the_resolver_and_the_routes_take_a_reader_in_every_mirror():
    """The packaged mirror under icdev/ is the same file (mirror_parity), so the narrowing ships."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("tools/twin_core/governance.py", "tools/dashboard/api/clawhub.py", "tools/factory_builds/service.py"):
        assert (root / rel).read_bytes() == (root / "icdev" / rel).read_bytes(), rel
    assert "reader" in sys.modules["tools.twin_core.governance"].resolve_thread.__code__.co_varnames
