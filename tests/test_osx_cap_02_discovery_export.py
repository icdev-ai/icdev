# CUI // SP-CTI
"""A source's data discovery bundle for ICDEV[OS], published by a person (osx-cap-02).

What the card promises on the ICDEV[IT] side, each clause a test here:

  * the Data Design Canvas's profiler, PII scanner, rule-based anomaly checks, quality rules and catalogue are REUSED
    to build one icos-discovery/v1 bundle per declared source, against ICDEV[OS]'s published schema, vendored and
    pinned (file and body digest); a changed schema is refused;
  * the bundle carries METADATA ONLY: no top value, minimum, maximum or any datum of the source leaves; PII by pattern
    name; anomalies from the rule-based checks with fixed descriptions; a quality rule that never ran is not a pass;
  * deterministic given its inputs; its digest recomputable as ICDEV[OS] recomputes it;
  * publishing is a person's declared step, never the preparer's, bound to the digest; the ledger is append-only and
    hash-chained;
  * the page and routes, the twin (threads that resolve, replay, unverified when nothing is published or declared),
    and the MCP read and draft.

The source is a SYNTHETIC SQLite file written here; people are labelled test persons. All on the conftest SQLite
platform DB and a per-test Data Design Canvas DB.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "tests" / "e2e" / "fixtures" / "discovery_sources.yaml"
PREPARER = {"id": "u-cap02i-preparer", "role": "reviewer"}       # labelled test persons (synthetic)
PUBLISHER = {"id": "u-cap02i-publisher", "role": "reviewer"}
DEVELOPER = {"id": "u-cap02i-dev", "role": "developer"}
KEY = "syn-track-export"
DESIGN = "cap02 synthetic track export"
AT = "2026-11-18T09:00:00Z"
EMAILS = ("ana.lyst@example.org", "bo.watch@example.org")


def synthetic_source(path: Path) -> Path:
    """A SYNTHETIC source: track summaries (one PII column of fake example.org emails) and relay outages (a duplicate
    id, so a uniqueness rule fails)."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE track_summary (id TEXT, ts TEXT, area TEXT, track_count INTEGER, source_detail TEXT,
                                    analyst_email TEXT);
        CREATE TABLE relay_outages (id TEXT, relay TEXT, started_at TEXT, minutes INTEGER);
    """)
    conn.executemany("INSERT INTO track_summary VALUES (?,?,?,?,?,?)", [
        ("t1", "2026-11-17T00:00:00Z", "north", 12, "radar seven", EMAILS[0]),
        ("t2", "2026-11-17T01:00:00Z", "south", 7, "relay feed", EMAILS[1]),
        ("t3", "2026-11-17T02:00:00Z", "north", 40, "radar seven", EMAILS[0]),
    ])
    conn.executemany("INSERT INTO relay_outages VALUES (?,?,?,?)", [
        ("o1", "relay-north", "2026-11-16T08:00:00Z", 10),
        ("o1", "relay-north", "2026-11-16T09:00:00Z", 12),
        ("o2", "relay-south", "2026-11-16T10:00:00Z", 3),
    ])
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def canvas(tmp_path, monkeypatch):
    """A per-test Data Design Canvas DB (SQLite) and the synthetic source config."""
    import importlib

    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    monkeypatch.setattr(init_db, "DB_PATH", str(tmp_path / "ddc.db"))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    init_db.init_db()
    monkeypatch.setenv("ICDEV_DISCOVERY_SOURCES_E2E", str(SOURCES))
    return init_db


def _profile(init_db, src: Path, design_id: str, pid: str, created: str) -> None:
    """Store a profile exactly as POST /data/api/explore/profile does."""
    from tools.data_canvas.data_profiler import profile_database

    prof = profile_database({"db_type": "sqlite", "path": str(src)}, "CUI // SP-CTI")
    conn = init_db.get_connection()
    conn.execute("INSERT INTO dd_explore_profiles (id, design_id, session_id, db_conn_json, profile_json, table_count, "
                 "classification, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (pid, design_id, None, json.dumps({"db_type": "sqlite", "path": str(src)}), json.dumps(prof),
                  prof["table_count"], "CUI // SP-CTI", created))
    conn.commit()
    conn.close()


@pytest.fixture()
def seeded(canvas, wf_db, tmp_path):
    """The design, one stored profile, two quality rules (one run passes, one fails), one never-run rule, and one
    catalogue product whose input port names the source."""
    from tools.data_canvas.quality_engine import run_all_rules

    src = synthetic_source(tmp_path / "synthetic_tracks.db")
    conn = canvas.get_connection()
    conn.execute("INSERT INTO data_designs (id, name, created_at, updated_at) VALUES (?,?,?,?)",
                 ("des-cap02", DESIGN, "2026-11-17T00:00:00Z", "2026-11-17T00:00:00Z"))
    for rid, table, col, kind, thr in (("qr-area", "track_summary", "area", "completeness", 90.0),
                                       ("qr-outage", "relay_outages", "id", "uniqueness", 100.0)):
        conn.execute("INSERT INTO dd_quality_rules (id, design_id, name, table_name, column_name, check_type, threshold, "
                     "enabled) VALUES (?,?,?,?,?,?,?,1)", (rid, "des-cap02", rid, table, col, kind, thr))
    conn.commit()
    run_all_rules("des-cap02", {"db_type": "sqlite", "path": str(src)}, conn)
    conn.execute("INSERT INTO dd_quality_rules (id, design_id, name, table_name, column_name, check_type, threshold, "
                 "enabled) VALUES (?,?,?,?,?,?,?,1)", ("qr-unrun", "des-cap02", "qr-unrun", "track_summary", "ts",
                                                       "completeness", 90.0))
    conn.execute("INSERT INTO dm_domains (id, name) VALUES (?,?)", ("dom-cap02", "synthetic operations"))
    conn.execute("INSERT INTO dm_data_products (id, domain_id, name, status) VALUES (?,?,?,?)",
                 ("dp-cap02", "dom-cap02", "Synthetic track summaries", "active"))
    conn.execute("INSERT INTO dm_input_ports (id, product_id, name, schema_json, source_system) VALUES (?,?,?,?,?)",
                 ("ip-cap02", "dp-cap02", "tracks in", json.dumps({"tables": ["track_summary"]}), KEY))
    conn.commit()
    conn.close()
    _profile(canvas, src, "des-cap02", "prof-cap02", "2026-11-18T08:00:00Z")
    return {"src": src, "db": wf_db}


def _db():
    from tools.data_canvas import discovery_bundle

    return discovery_bundle


def _icos_digest(bundle: dict) -> str:
    """ICDEV[OS]'s rule (icdev_os/data/discoverymodel.content_digest), restated independently."""
    body = {k: v for k, v in bundle.items() if k not in ("content_digest", "publication", "produced_at")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str,
                                     ensure_ascii=False).encode()).hexdigest()


# ── the contract ─────────────────────────────────────────────────────────────


def test_the_schema_is_icos_published_copy_pinned_by_file_and_body(monkeypatch, tmp_path):
    db = _db()
    raw = (ROOT / db.SCHEMA_RELPATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == db.SCHEMA_SHA256
    doc = json.loads(raw)
    assert doc["x-icos-contract"] == db.SCHEMA_CONTRACT == "1.194.0"
    assert doc["$id"] == "urn:icdev-os:contract:discovery:1" and db.body_digest(doc) == db.SCHEMA_BODY_SHA256
    assert doc["properties"]["schema"]["const"] == db.SCHEMA == "icos-discovery/v1"
    altered = tmp_path / "context" / "icos"
    altered.mkdir(parents=True)
    (altered / "discovery.schema.json").write_bytes(raw.replace(b'"Discovery"', b'"Discoveryx"', 1))
    monkeypatch.setattr(db, "BASE_DIR", tmp_path)
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.schema()
    assert exc.value.code == "schema"


def test_the_sources_are_declared_and_another_config_must_live_under_tests(monkeypatch, tmp_path):
    db = _db()
    monkeypatch.delenv(db.E2E_ENV, raising=False)
    shipped = db.load_config()
    assert shipped["sources"] == {}, "no source of this deployment is exported until one is reviewed and listed"
    monkeypatch.setenv(db.E2E_ENV, str(SOURCES))
    cfg = db.load_config()
    assert list(cfg["sources"]) == [KEY] and cfg["sources"][KEY]["design"] == DESIGN
    outside = tmp_path / "sources.yaml"
    outside.write_text(yaml.safe_dump({"sources": []}))
    monkeypatch.setenv(db.E2E_ENV, str(outside))
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.load_config()
    assert exc.value.code == "config", "a path outside tests/ is refused, never followed"
    monkeypatch.setenv(db.E2E_ENV, str(SOURCES))
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.build("not-declared")
    assert exc.value.code == "unknown_source"


# ── the bundle ───────────────────────────────────────────────────────────────


def test_the_bundle_carries_metadata_only_and_meets_the_contract(seeded):
    db = _db()
    b = db.build(KEY, at=AT)
    assert db.validate(b) == [] and b["publication"] is None
    assert b["content_digest"] == _icos_digest(b) == db.content_digest(b), "ICDEV[OS] recomputes the same digest"
    assert b["schema_pin"] == {"contract": "1.194.0", "body_sha256": db.SCHEMA_BODY_SHA256}
    assert b["source"]["key"] == KEY and b["source"]["kind"] == "sqlite" and b["source"]["profile_id"] == "prof-cap02"
    assert b["as_of"] == "2026-11-18" and b["produced_at"] == AT
    text = json.dumps(b)
    for datum in (*EMAILS, "radar seven", "relay-north", "north", "t1", "o1"):
        assert datum not in text, f"a datum of the source left in the bundle: {datum!r}"
    cols = {(t["name"], c["name"]): c for t in b["tables"] for c in t["columns"]}
    assert set(next(iter(cols.values()))) <= {"name", "physical_type", "inferred_type", "null_count", "null_pct",
                                              "distinct_count", "error"}, "no top values, minimum or maximum"
    assert cols[("relay_outages", "id")]["distinct_count"] == 2
    assert cols[("track_summary", "track_count")]["inferred_type"] == "numeric"
    pii = {(p["table"], p["column"]): p for p in b["pii"]}
    assert ("track_summary", "analyst_email") in pii
    assert pii[("track_summary", "analyst_email")]["patterns"] == ["email"] and pii[("track_summary", "analyst_email")]["method"] == "rule"
    assert all(a["method"] == "rule" and a["description"] in db.ANOMALY_TEXT.values() for a in b["anomalies"])
    q = {r["rule_id"]: r for r in b["quality"]}
    assert q["qr-area"]["passed"] is True and q["qr-outage"]["passed"] is False
    assert q["qr-unrun"]["passed"] is None and q["qr-unrun"]["ran_at"] is None, "a rule that never ran is not a pass"
    assert b["summary"]["quality_passed"] == 1 and b["summary"]["quality_failed"] == 1 and b["summary"]["quality_unrun"] == 1
    assert b["catalog"] == [{"product_id": "dp-cap02", "name": "Synthetic track summaries",
                             "domain_name": "synthetic operations", "status": "active", "tables": ["track_summary"]}]


def test_the_outlier_text_and_a_model_never_reach_the_bundle(seeded, monkeypatch):
    """The detector's own outlier description embeds the range; the export carries the fixed text. The model path
    is never called."""
    from tools.data_canvas import anomaly_detector

    monkeypatch.setattr(anomaly_detector, "detect_anomalies",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model path was called")))
    db = _db()
    found = db._anomalies([{"name": "t", "columns": [{"name": "v", "null_pct": 0, "distinct_count": 5, "min": 0,
                                                      "max": 1000, "top_values": [{"value": 1}, {"value": 2}]}]}])
    assert [a["type"] for a in found] == ["outlier_range"]
    assert "1000" not in found[0]["description"] and found[0]["description"] == db.ANOMALY_TEXT[("outlier_range", "medium")]


def test_the_export_is_deterministic_given_its_inputs(seeded, tmp_path, capsys):
    from tools.data_canvas import discovery_export as x

    db = _db()
    assert db.build(KEY, at=AT) == db.build(KEY, at=AT)
    assert x.main(["--source", KEY, "--out", str(tmp_path / "a"), "--at", AT]) == 0
    assert x.main(["--source", KEY, "--out", str(tmp_path / "b"), "--at", AT]) == 0
    a, b = (tmp_path / "a" / f"{KEY}.json").read_bytes(), (tmp_path / "b" / f"{KEY}.json").read_bytes()
    assert a == b and json.loads(a)["publication"] is None, "a draft until a person publishes it"
    assert db.build(KEY, as_of="2026-11-19")["content_digest"] != json.loads(a)["content_digest"]
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.build(KEY, as_of="yesterday")
    assert exc.value.code == "as_of"
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.build(KEY, profile_id="no-such-profile")
    assert exc.value.code == "no_profile"


# ── publishing ───────────────────────────────────────────────────────────────


def test_publishing_is_a_persons_step_never_the_preparers(seeded, tmp_path):
    from tools.data_canvas import discovery_export as x

    db = _db()
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.publish(KEY, decision="publish", reason="r", read_digest="x", actor=PUBLISHER)
    assert exc.value.code == "nothing_prepared"
    prep = db.prepare(KEY, PREPARER)
    assert prep["prepared_by"] == PREPARER["id"] and prep["actor_kind"] == "person"
    d = prep["content_digest"]
    for actor, kw, code in ((PREPARER, {}, "separation"), (PUBLISHER, {"read_digest": "0" * 64}, "digest"),
                            (PUBLISHER, {"reason": ""}, "reason"), (DEVELOPER, {}, "role"),
                            ({"id": "agent:x", "role": "service"}, {}, "not_a_person"), (None, {}, "no_person")):
        with pytest.raises(db.DiscoveryRefused) as exc:
            db.publish(KEY, decision="publish", **{"reason": "ok (synthetic)", "read_digest": d, **kw}, actor=actor)
        assert exc.value.code == code, (actor, kw)
    assert db.published_bundle(KEY) is None, "nothing published yet"
    out = db.publish(KEY, decision="publish", reason="Read it (synthetic).", read_digest=d, actor=PUBLISHER)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"] and out["prepared_by"] == PREPARER["id"]
    pub = out["bundle"]["publication"]
    assert pub["signed_by"] == PUBLISHER["id"] and pub["content_digest"] == d and pub["prepared_by"] == PREPARER["id"]
    assert pub["template"] == "decl-wft-data-discovery-publish" and pub["step_id"] == out["step"]["step_id"]
    assert db.validate(out["bundle"]) == [] and out["bundle"]["content_digest"] == d
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.publish(KEY, decision="publish", reason="again", read_digest=d, actor=PUBLISHER)
    assert exc.value.code == "already_decided"
    assert x.main(["--source", KEY, "--published", "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / f"{KEY}.json").read_text())["publication"]["signed_by"] == PUBLISHER["id"]
    assert x.export(KEY)["publication"]["step_id"] == pub["step_id"], "the same content carries its publication"
    assert x.export(KEY, as_of="2026-11-19")["publication"] is None, "other content is a draft"
    # a refusal: the newer bundle never leaves, and the published one stays the newest published
    p2 = db.prepare(KEY, PUBLISHER, as_of="2026-11-19")
    r = db.publish(KEY, decision="refuse", reason="Not yet (synthetic).", read_digest=p2["content_digest"],
                   actor=PREPARER)
    assert r["event"] == "refused" and "bundle" not in r and db.published_bundle(KEY)["content_digest"] == d
    conn = sqlite3.connect(str(seeded["db"]))
    steps = conn.execute("SELECT template_id, actor, subject FROM wf_decision_steps WHERE subject LIKE 'discovery:%' "
                         "ORDER BY created_at").fetchall()
    conn.close()
    assert steps == [("decl-wft-data-discovery-publish", PUBLISHER["id"], f"discovery:{KEY}"),
                     ("decl-wft-data-discovery-publish", PREPARER["id"], f"discovery:{KEY}")]


def test_the_ledger_is_append_only_and_hash_chained(seeded):
    db = _db()
    p = db.prepare(KEY, PREPARER)
    db.publish(KEY, decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    rows = db.read_events(source=KEY)
    ch = db.chain(rows)
    assert ch["_problems"] == [] and [r["event"] for r in ch[KEY]] == ["prepared", "published"]
    assert ch[KEY][0]["prev_hash"] == db.GENESIS and ch[KEY][1]["prev_hash"] == ch[KEY][0]["row_hash"]
    for sql in ("UPDATE data_discovery_export_events SET actor = 'x'", "DELETE FROM data_discovery_export_events"):
        conn = sqlite3.connect(str(seeded["db"]))
        try:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
        finally:
            conn.close()
    forged = [dict(r) for r in rows]
    forged[0]["bundle_json"] = forged[0]["bundle_json"].replace('"quality_failed":1', '"quality_failed":0', 1)
    forged[0]["row_hash"] = db._row_hash(forged[0])
    probs = db.chain(forged)["_problems"]
    assert any(x["problem"] == "the bundle does not match its digest" for x in probs)
    assert any(x["problem"] == "not on the source's hash chain" for x in probs), "the next row no longer links"
    agent = db.prepare(KEY, {"id": "agent:cap02i-drafter", "role": "service"})
    assert agent["actor_kind"] == "agent"
    with pytest.raises(db.DiscoveryRefused) as exc:
        db.prepare(KEY, None)
    assert exc.value.code == "no_actor"


# ── the console, the routes ──────────────────────────────────────────────────


def _page_app(user):
    from tools.dashboard.app import app as dash
    from tools.data_canvas.discovery_blueprint import discovery_bp

    app = Flask(__name__, template_folder=dash.template_folder, static_folder=dash.static_folder)
    app.config.update(dash.config)
    app.jinja_env.filters.update(dash.jinja_env.filters)
    app.jinja_env.globals.update(dash.jinja_env.globals)
    app.template_context_processors[None].extend(dash.template_context_processors.get(None, []))
    app.secret_key = dash.secret_key or "test-secret"
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user)
        g.security_context = {"tenant_id": "default", "user_id": user["id"], "classification": "CUI"}

    app.register_blueprint(discovery_bp)
    return app.test_client()


def test_the_routes_and_the_page(seeded):
    prep, pub = _page_app(PREPARER), _page_app(PUBLISHER)
    html = prep.get("/data-discovery/").get_data(as_text=True)
    for want in ("Data discovery export to ICDEV[OS]", "metadata only, never a datum", "decl-wft-data-discovery-publish",
                 "data_discovery.dd_api_source", "data_discovery.dd_api_prepare", "data_discovery.dd_api_publish",
                 "data_discovery.dd_api_bundle", "data_discovery.dd_api_twin", f'data-source="{KEY}"', "/data/explore"):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/data_canvas/discovery_export.html").read_text(encoding="utf-8"), \
        "routes are named by endpoint id"
    lst = prep.get("/data-discovery/api/sources").get_json()
    assert [s["key"] for s in lst["sources"]] == [KEY] and lst["schema"] == "icos-discovery/v1"
    v = prep.get(f"/data-discovery/api/sources/{KEY}").get_json()
    assert v["bundle"]["source"]["profile_id"] == "prof-cap02" and v["ledger"]["next"] == ["prepare"]
    assert [p["id"] for p in v["profiles"]] == ["prof-cap02"]
    assert prep.get("/data-discovery/api/sources/zz").status_code == 404
    assert prep.get(f"/data-discovery/api/sources/{KEY}?as_of=yesterday").get_json()["code"] == "as_of"
    assert prep.get(f"/data-discovery/api/sources/{KEY}/bundle").status_code == 404
    r = prep.post(f"/data-discovery/api/sources/{KEY}/prepare", json={})
    assert r.status_code == 201, r.get_data(as_text=True)
    d = r.get_json()["content_digest"]
    led = pub.get(f"/data-discovery/api/sources/{KEY}").get_json()["ledger"]
    assert led["next"] == ["publish"] and PREPARER["id"] in led["builders"]
    body = {"decision": "publish", "reason": "Read it (synthetic).", "digest": d}
    refused = prep.post(f"/data-discovery/api/sources/{KEY}/publish", json=body)
    assert refused.status_code == 403 and refused.get_json()["code"] == "separation"
    out = pub.post(f"/data-discovery/api/sources/{KEY}/publish", json=body).get_json()
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == PUBLISHER["id"]
    got = pub.get(f"/data-discovery/api/sources/{KEY}/bundle?download=1")
    assert got.headers["Content-Disposition"] == f'attachment; filename="{KEY}.json"'
    assert got.get_json()["content_digest"] == d and got.get_json()["publication"]["content_digest"] == d
    # in the nav, and linked from the Explore page
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    comp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "data_discovery")
    assert "/data-discovery/" in [lk["href"] for lk in comp["nav"]["links"]]
    assert 'href="/data-discovery/"' in (ROOT / "tools/dashboard/templates/data_canvas/explore.html").read_text(encoding="utf-8")


# ── the twin ─────────────────────────────────────────────────────────────────


def test_the_twin_observes_with_threads_and_replays(seeded):
    import tools.twin_core.adapters.data_discovery  # noqa: F401
    from tools.twin_core import governance

    db = _db()
    adapter = governance.adapter_for("data_discovery")
    assert adapter.observes == ("data.discovery_export",)
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["data.discovery.ledger"]["status"] == "verified"
    assert chk[f"data.discovery.{KEY}"]["status"] == "unverified", "a source never published is never a pass"
    assert env["verdict"] != "pass"
    p = db.prepare(KEY, PREPARER)
    db.publish(KEY, decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    env = governance.observe(adapter)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk[f"data.discovery.{KEY}"]["status"] == "verified"
    rules = {v["rule_id"]: v for v in env["violations"]}
    assert "qr-outage" in rules["data.discovery.quality_failing"]["title"]
    assert "qr-unrun" in rules["data.discovery.quality_unrun"]["title"]
    t = governance.resolve_thread(rules["data.discovery.quality_failing"]["thread_id"])
    assert t["resolved"] and t["source"] == "data_discovery_export_events" and t["record"]["event"] == "published"
    assert t["record"]["actor"] == PUBLISHER["id"] and t["record"]["source"] == KEY
    assert env["extra"]["summary"]["discovery_published"] == 1
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep.get("reason")


def test_the_twin_says_unverified_when_nothing_is_declared_or_readable(canvas, wf_db, monkeypatch):
    import tools.twin_core.adapters.data_discovery  # noqa: F401
    from tools.twin_core import governance

    db = _db()
    monkeypatch.delenv(db.E2E_ENV, raising=False)
    env = governance.observe(governance.adapter_for("data_discovery"), record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["data.discovery.declared"]["status"] == "unverified" and env["verdict"] != "pass", \
        "nothing declared is nothing observed"

    def boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(db, "read_events", boom)
    env = governance.observe(governance.adapter_for("data_discovery"), record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["data.discovery.ledger"]["status"] == "unverified" and env["verdict"] != "pass"


# ── AI-native: read, and draft only ──────────────────────────────────────────


def test_the_mcp_tools_read_and_only_prepare(seeded):
    from tools.mcp.gap_handlers import handle_data_discovery_export, handle_data_discovery_prepare
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert {"data_discovery_export", "data_discovery_prepare"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["data_discovery_export"] is True
    assert tool_authorization("data_discovery_export")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["data_discovery_prepare"] is False
    listing = handle_data_discovery_export({})
    assert [s["key"] for s in listing["sources"]["sources"]] == [KEY]
    one = handle_data_discovery_export({"source": KEY})
    assert one["source"]["bundle"]["schema"] == "icos-discovery/v1"
    assert handle_data_discovery_export({"source": KEY, "published": True})["code"] == "unpublished"
    draft = handle_data_discovery_prepare({"source": KEY, "agent_id": "cap02i-agent"})
    assert draft["draft"]["prepared_by"] == "agent:cap02i-agent" and draft["draft"]["actor_kind"] == "agent"
    from tools.data_canvas import discovery_bundle as db

    with pytest.raises(db.DiscoveryRefused) as exc:      # an agent can never publish
        db.publish(KEY, decision="publish", reason="r", read_digest=draft["draft"]["content_digest"],
                   actor={"id": "agent:cap02i-agent", "role": "service"})
    assert exc.value.code == "not_a_person"
    assert db.published_bundle(KEY) is None
