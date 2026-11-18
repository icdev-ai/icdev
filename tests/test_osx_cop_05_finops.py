# CUI // SP-CTI
"""FOCUS cost bundles from FinOps, for ICDEV[OS]'s Cloud CoP (osx-cop-05).

What the card promises, each clause a test here:

  * ICDEV[OS]'s schema is vendored and pinned twice: by the file's SHA-256 and by its schema digest (what a bundle
    carries as its pin); a changed file is refused;
  * a reviewed config (FOCUS 1.4, the tag key, ICDEV[OS]'s four workloads, the currency, where exports are read);
    one that breaks a rule is refused;
  * a FOCUS 1.4 export (CSV, JSON array, JSON lines) is read with decimals as numbers, JSON columns as JSON and an
    empty cell as null; ONE row that fails the pinned $defs/FocusRow refuses the WHOLE export, named;
  * the bundle is built EXACTLY as ICDEV[OS] checks it: totals per declared workload and unattributed recomputed by
    ICDEV[OS]'s algorithm (Decimal, six places, half-even), the content digest likewise, deterministic;
  * a draft has no publication; publishing is a person's declared step, never the preparer's, bound to the digest;
    a self-publication is refused; the ledger is append-only and hash-chained per billing month;
  * the page and routes, the twin (unverified when nothing is published, threads that resolve, replay, a
    self-published or off-contract bundle a finding), MCP read and draft only;
  * no cloud-vendor training or certification content in the config, the fixtures or the page.

People here are labelled test persons (synthetic); the FOCUS export is synthetic ("Example Cloud (synthetic)"). All on
the conftest SQLite platform DB.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "finops" / "focus_1_4_sample.csv"
SAMPLE_NAME = SAMPLE.name
PREPARER = {"id": "u-cop05-preparer", "role": "reviewer"}
PUBLISHER = {"id": "u-cop05-publisher", "role": "reviewer"}
DEVELOPER = {"id": "u-cop05-dev", "role": "developer"}
AT = "2026-11-18T00:00:00Z"
ICOS_WORKLOADS = ["icos-api", "icos-mesh", "icos-data-plane", "icos-twin"]   # ICDEV[OS] args/cloud.yaml
OBSERVES = ("coe.standards", "coe.exemptions", "coe.adrs", "coe.conformance", "coe.golden_paths", "finops.focus")


def _f():
    from tools.finops import focus_bundle

    return focus_bundle


def _canonical(v) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


@pytest.fixture()
def exports(tmp_path, monkeypatch):
    """The declared exports directory, holding the synthetic sample (and whatever a test plants)."""
    d = tmp_path / "focus"
    d.mkdir()
    shutil.copy(SAMPLE, d / SAMPLE_NAME)
    monkeypatch.setenv("ICDEV_FINOPS_FOCUS_DIR", str(d))
    return d


@pytest.fixture()
def db(wf_db, exports):
    """The platform DB (conftest SQLite) with finops_focus_events and the wf_ tables, and the exports."""
    return wf_db


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _sample_rows() -> list[dict]:
    return list(csv.DictReader(io.StringIO(SAMPLE.read_text(encoding="utf-8"))))


def _write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> Path:
    fields = fields or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


# ── ICDEV[OS]'s algorithm, re-stated here from icdev_os/cloud/focus.py (the authority), not imported ─────────────


def _icos_amount(v) -> str:
    return str(Decimal(str(v)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN))


def _icos_totals(rows, tag, keys):
    acc = {k: {"rows": 0, "EffectiveCost": Decimal(0), "BilledCost": Decimal(0)} for k in keys}
    rest = {"rows": 0, "EffectiveCost": Decimal(0), "BilledCost": Decimal(0)}
    for r in rows:
        t = r.get("Tags")
        w = t.get(tag) if isinstance(t, dict) else None
        slot = acc.get(w if isinstance(w, str) and w else "", rest)
        slot["rows"] += 1
        for c in ("EffectiveCost", "BilledCost"):
            slot[c] += Decimal(str(r.get(c) or 0))

    def out(a):
        return {"rows": a["rows"], "effective_cost": _icos_amount(a["EffectiveCost"]),
                "billed_cost": _icos_amount(a["BilledCost"])}
    return {k: out(a) for k, a in acc.items()}, out(rest)


def _icos_digest(bundle) -> str:
    body = {k: v for k, v in bundle.items() if k not in ("content_digest", "publication", "produced_at")}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


# ── the pin and the config ───────────────────────────────────────────────────


def test_the_schema_is_pinned_by_file_and_by_schema_digest(monkeypatch):
    f = _f()
    raw = (ROOT / f.SCHEMA_RELPATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == f.SCHEMA_SHA256
    s = json.loads(raw)
    assert s["$id"] == "urn:icdev-os:contract:focus-cost:1" and s["x-icos-contract"] == f.SCHEMA_CONTRACT
    stripped = {k: v for k, v in s.items() if k != "x-icos-contract"}
    assert hashlib.sha256(_canonical(stripped).encode("utf-8")).hexdigest() == f.SCHEMA_DIGEST == f.schema_digest()
    readme = (ROOT / "context/icos/README.md").read_text(encoding="utf-8")
    assert f.SCHEMA_SHA256 in readme and f.SCHEMA_DIGEST in readme and "DRAFT" in readme
    monkeypatch.setattr(f, "SCHEMA_SHA256", "0" * 64)
    with pytest.raises(f.FocusRefused) as exc:
        f.schema()
    assert exc.value.code == "schema", "a changed file is refused, never used"


def test_the_config_is_declared_and_one_that_breaks_a_rule_is_refused(tmp_path):
    f = _f()
    cfg = f.load_config()
    assert cfg["focus_version"] == "1.4" and cfg["tag_key"] == "icdev-workload" and cfg["currency"] == "USD"
    assert f.workload_keys(cfg) == ICOS_WORKLOADS, "exactly ICDEV[OS]'s declared workloads"
    assert cfg["exports"]["dir"] == "data/finops/focus"
    good = yaml.safe_load((ROOT / f.CONFIG_RELPATH).read_text(encoding="utf-8"))
    assert f.config_errors(good) == []
    bad = [({"focus_version": "1.3"}, "FOCUS 1.4"), ({"tag_key": "x"}, "tag_key"), ({"currency": "usd"}, "ISO 4217"),
           ({"workloads": []}, "at least one"),
           ({"workloads": [{"key": "icos-api", "name": "a"}, {"key": "icos-api", "name": "b"}]}, "twice"),
           ({"workloads": [{"key": "ICOS_API", "name": "a"}]}, "does not match"),
           ({"workloads": [{"key": "icos-api"}]}, "no name"),
           ({"exports": {**good["exports"], "dir": "/etc"}}, "inside the repo"),
           ({"exports": {**good["exports"], "dir": "../elsewhere"}}, "inside the repo"),
           ({"exports": {**good["exports"], "max_rows": 0}}, "max_rows")]
    for change, want in bad:
        errs = f.config_errors({**good, **change})
        assert any(want in e for e in errs), (change, errs)
    p = tmp_path / "finops_focus.yaml"
    p.write_text(yaml.safe_dump({**good, "focus_version": "1.2"}), encoding="utf-8")
    with pytest.raises(f.FocusRefused) as exc:
        f.load_config(p)
    assert exc.value.code == "config"


# ── reading an export ────────────────────────────────────────────────────────


def test_the_synthetic_export_reads_csv_json_and_json_lines_alike(tmp_path):
    f = _f()
    ex = f.read_export(SAMPLE)
    rows = ex["rows"]
    assert len(rows) == 22 and ex["billing_period"] == {"start": "2026-10-01T00:00:00Z", "end": "2026-11-01T00:00:00Z"}
    assert ex["sha256"] == hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    r0 = rows[0]
    assert r0["EffectiveCost"] == 61.9008 and isinstance(r0["PricingQuantity"], int), "decimals are numbers"
    assert r0["Tags"] == {"icdev-workload": "icos-api", "env": "synthetic"}, "a JSON column is JSON"
    assert r0["ChargeClass"] is None, "an empty cell is null"
    assert {r["ServiceProviderName"] for r in rows} == {"Example Cloud (synthetic)"}, "a made-up provider"
    assert sum(1 for r in rows if not isinstance(r["Tags"], dict) or "icdev-workload" not in r["Tags"]) == 2
    req = json.loads((ROOT / f.SCHEMA_RELPATH).read_text())["$defs"]["FocusRow"]["required"]
    assert set(req) <= set(r0), "every mandatory FOCUS column is present"
    (tmp_path / "a.json").write_text(json.dumps(rows), encoding="utf-8")
    (tmp_path / "a.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    for p in (tmp_path / "a.json", tmp_path / "a.jsonl"):
        assert f.read_export(p)["rows"] == rows, p.suffix


def test_a_row_that_fails_the_pinned_schema_refuses_the_whole_export(tmp_path):
    f = _f()
    base = _sample_rows()
    plants = [
        ("category", {2: {"ServiceCategory": "Teleportation"}}, "row 3: ServiceCategory"),
        ("mandatory", {4: {"BilledCost": ""}}, "row 5: BilledCost"),
        ("number", {0: {"EffectiveCost": "twelve"}}, "row 1: EffectiveCost"),
        ("datetime", {6: {"ChargePeriodStart": "October 2026"}}, "row 7: ChargePeriodStart"),
        ("tags", {1: {"Tags": "{not json"}}, "row 2: Tags"),
    ]
    for name, edits, want in plants:
        rows = [dict(r) for r in base]
        for i, ch in edits.items():
            rows[i].update(ch)
        p = _write_csv(tmp_path / f"{name}.csv", rows)
        with pytest.raises(f.FocusRefused) as exc:
            f.read_export(p)
        assert exc.value.code == "export" and want in exc.value.message, (name, exc.value.message)
        assert "not half read" in exc.value.message
    rows = [dict(r, Unknown="x") for r in base]
    with pytest.raises(f.FocusRefused) as exc:
        f.read_export(_write_csv(tmp_path / "extra.csv", rows))
    assert "Unknown" in exc.value.message, "a column FOCUS does not define (and not x_) is refused"
    rows = [dict(r, x_CostCentre="cc-1") for r in base]
    assert len(f.read_export(_write_csv(tmp_path / "custom.csv", rows))["rows"]) == 22, "x_ columns are FOCUS's own"
    rows = [dict(r) for r in base]
    rows[3]["BillingCurrency"] = "EUR"
    with pytest.raises(f.FocusRefused) as exc:
        f.read_export(_write_csv(tmp_path / "eur.csv", rows))
    assert "EUR" in exc.value.message
    rows = [dict(r) for r in base]
    rows[0].update(BillingPeriodStart="2026-09-01T00:00:00Z", BillingPeriodEnd="2026-10-01T00:00:00Z")
    with pytest.raises(f.FocusRefused) as exc:
        f.read_export(_write_csv(tmp_path / "two.csv", rows))
    assert "one billing period" in exc.value.message
    (tmp_path / "nan.json").write_text('[{"BilledCost": NaN}]', encoding="utf-8")
    with pytest.raises(f.FocusRefused):
        f.read_export(tmp_path / "nan.json")


# ── the bundle, as ICDEV[OS] checks it ───────────────────────────────────────


def test_the_bundle_totals_and_digest_recompute_by_icos_algorithm():
    f = _f()
    cfg = f.load_config()
    b = f.build(f.read_export(SAMPLE, cfg), cfg=cfg, at=AT)
    assert list(b) == ["schema", "producer", "produced_at", "classification", "focus", "pin", "billing_period",
                       "currency", "service_tag", "rows", "workloads", "unattributed", "publication",
                       "content_digest"]
    assert b["schema"] == "icos-focus-cost/v1" and b["producer"]["system"] == "ICDEV[IT]"
    assert b["focus"] == {"version": "1.4", "dataset": "CostAndUsage"}
    assert b["pin"] == {"schema": "icos-focus-cost/v1", "sha256": f.SCHEMA_DIGEST}
    assert b["produced_at"] == AT and b["currency"] == "USD" and b["service_tag"] == "icdev-workload"
    want, rest = _icos_totals(b["rows"], "icdev-workload", ICOS_WORKLOADS)
    assert b["workloads"] == [{"key": k, **want[k]} for k in ICOS_WORKLOADS] and b["unattributed"] == rest
    assert b["content_digest"] == _icos_digest(b)
    # Worked by hand from the fixture: 96.4575505 rounds half-even to .457550, not .457551.
    api = next(w for w in b["workloads"] if w["key"] == "icos-api")
    assert api == {"key": "icos-api", "rows": 5, "effective_cost": "96.457550", "billed_cost": "96.457550"}
    dp = next(w for w in b["workloads"] if w["key"] == "icos-data-plane")
    assert dp["effective_cost"] == "453.424000" and dp["billed_cost"] == "728.424000"
    mesh = next(w for w in b["workloads"] if w["key"] == "icos-mesh")
    assert mesh["effective_cost"] == "149.751200", "a credit row counts, negative"
    assert b["unattributed"] == {"rows": 2, "effective_cost": "18.700000", "billed_cost": "18.700000"}
    assert f.verify(b, draft_ok=True) == []
    # deterministic given its inputs and the time; produced_at is outside the digest
    again = f.build(f.read_export(SAMPLE, cfg), cfg=cfg, at=AT)
    assert _canonical(again) == _canonical(b)
    later = f.build(f.read_export(SAMPLE, cfg), cfg=cfg, at="2026-12-01T00:00:00Z")
    assert later["content_digest"] == b["content_digest"] and later["produced_at"] != b["produced_at"]
    # a declared workload with no rows reports zero; it is never left out
    rows = [r for r in b["rows"] if (r.get("Tags") or {}).get("icdev-workload") != "icos-twin"]
    b2 = f.build({"rows": rows, "billing_period": b["billing_period"]}, cfg=cfg, at=AT)
    assert {"key": "icos-twin", "rows": 0, "effective_cost": "0.000000", "billed_cost": "0.000000"} in b2["workloads"]


def test_a_tampered_or_off_contract_bundle_does_not_verify():
    f = _f()
    cfg = f.load_config()
    b = f.build(f.read_export(SAMPLE, cfg), cfg=cfg, at=AT)
    t = json.loads(json.dumps(b))
    t["rows"][0]["EffectiveCost"] = 1.0
    assert any(e.startswith("tampered") for e in f.verify(t, draft_ok=True))
    t["content_digest"] = _icos_digest(t)
    assert any("icos-api's total" in e for e in f.verify(t, draft_ok=True)), "a total is recomputed from its rows"
    w = json.loads(json.dumps(b))
    w["pin"]["sha256"] = "1" * 64
    w["content_digest"] = _icos_digest(w)
    assert any(e.startswith("wrong_pin") for e in f.verify(w, draft_ok=True))
    u = json.loads(json.dumps(b))
    u["workloads"] = u["workloads"][:3]
    u["content_digest"] = _icos_digest(u)
    assert any(e.startswith("undeclared") for e in f.verify(u, draft_ok=True)), "every declared workload is listed"
    x = json.loads(json.dumps(b))
    x["extra"] = 1
    assert f.verify(x, draft_ok=True), "the schema's additionalProperties: false"


def test_a_draft_has_no_publication(db):
    f = _f()
    v = f.preview(SAMPLE_NAME, at=AT)
    assert v["export"]["name"] == SAMPLE_NAME and v["export"]["rows"] == 22
    b = v["bundle"]
    assert b["publication"] is None
    assert f.verify(b, draft_ok=True) == []
    assert f.verify(b) == ["unpublished: nobody published it; a bundle with no publication is a draft"]
    assert f.published_bundle() is None and f.published_bundle("latest") is None
    for bad in ("../focus_1_4_sample.csv", "/etc/passwd", "focus.txt", "missing.csv"):
        with pytest.raises(f.FocusRefused) as exc:
            f.preview(bad)
        assert exc.value.code in ("export_name", "no_export"), bad


# ── publishing is a person's step ────────────────────────────────────────────


def test_publishing_binds_the_digest_and_is_never_the_preparers(db, tmp_path):
    f = _f()
    prep = f.prepare(SAMPLE_NAME, PREPARER, at=AT)
    assert prep["prepared_by"] == PREPARER["id"] and prep["actor_kind"] == "person" and prep["period"] == "2026-10"
    d, ev = prep["content_digest"], prep["event_id"]
    assert prep["export"]["sha256"] == hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    for actor, kw, code in ((PREPARER, {}, "separation"), (PUBLISHER, {"read_digest": "0" * 64}, "digest"),
                            (PUBLISHER, {"reason": ""}, "reason"), (DEVELOPER, {}, "role"),
                            ({"id": "agent:x", "role": "service"}, {}, "not_a_person"), (None, {}, "no_person")):
        with pytest.raises(f.FocusRefused) as exc:
            f.publish(ev, decision="publish", **{"reason": "ok (synthetic)", "read_digest": d, **kw}, actor=actor)
        assert exc.value.code == code, (actor, kw)
    assert f.published_bundle() is None, "nothing published yet"
    out = f.publish(ev, decision="publish", reason="Read it (synthetic).", read_digest=d, actor=PUBLISHER)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"] and out["prepared_by"] == PREPARER["id"]
    b = out["bundle"]
    pub = b["publication"]
    assert pub == {"signed_by": PUBLISHER["id"], "role": "reviewer", "step_id": out["step"]["step_id"],
                   "decided_at": pub["decided_at"], "template": "decl-wft-finops-focus-publish",
                   "content_digest": d, "prepared_by": PREPARER["id"], "event_id": out["event_id"]}
    assert b["content_digest"] == d == _icos_digest(b) and f.verify(b) == []
    assert f.published_bundle(out["event_id"]) == b == f.published_bundle(ev) == f.published_bundle()
    with pytest.raises(f.FocusRefused) as exc:
        f.publish(ev, decision="publish", reason="again", read_digest=d, actor=PUBLISHER)
    assert exc.value.code == "already_decided"
    with pytest.raises(f.FocusRefused) as exc:
        f.publish(out["event_id"], decision="publish", reason="r", read_digest=d, actor=PUBLISHER)
    assert exc.value.code == "not_prepared"
    # the export command writes the PUBLISHED bundle, and verifies one
    path = tmp_path / "out" / "bundle.json"
    assert f.main(["--published", out["event_id"], "--out", str(path)]) == 0
    assert json.loads(path.read_text())["publication"]["signed_by"] == PUBLISHER["id"]
    assert f.main(["--verify", str(path)]) == 0
    t = json.loads(path.read_text())
    t["workloads"][0]["effective_cost"] = "0.000000"
    (tmp_path / "t.json").write_text(json.dumps(t))
    assert f.main(["--verify", str(tmp_path / "t.json")]) == 1
    assert f.main(["--published", "fofe-nope", "--out", str(tmp_path / "x.json")]) == 1
    # a refusal: the bundle never leaves
    p2 = f.prepare(SAMPLE_NAME, PUBLISHER, at=AT)
    r = f.publish(p2["event_id"], decision="refuse", reason="Not yet (synthetic).", read_digest=p2["content_digest"],
                  actor=PREPARER)
    assert r["event"] == "refused" and "bundle" not in r
    assert f.published_bundle()["publication"]["event_id"] == out["event_id"], "the refused one never leaves"
    steps = _rows(db, "SELECT template_id, actor, subject FROM wf_decision_steps WHERE subject LIKE 'finops_focus:%' "
                      "ORDER BY created_at")
    assert steps == [("decl-wft-finops-focus-publish", PUBLISHER["id"], "finops_focus:2026-10"),
                     ("decl-wft-finops-focus-publish", PREPARER["id"], "finops_focus:2026-10")]


def test_a_self_publication_is_refused_and_never_counts(db):
    f = _f()
    p = f.prepare(SAMPLE_NAME, PUBLISHER, at=AT)
    with pytest.raises(f.FocusRefused) as exc:
        f.publish(p["event_id"], decision="publish", reason="mine (synthetic)", read_digest=p["content_digest"],
                  actor=PUBLISHER)
    assert exc.value.code == "separation"
    b = f.preview(SAMPLE_NAME, at=AT)["bundle"]
    b["publication"] = {"signed_by": PUBLISHER["id"], "role": "reviewer", "step_id": "step-x",
                        "decided_at": AT, "template": f.TEMPLATE, "content_digest": b["content_digest"],
                        "prepared_by": PUBLISHER["id"], "event_id": "fofe-x"}
    assert any("prepared it and published it" in e for e in f.verify(b)), "what ICDEV[OS] refuses, refused here"
    b["publication"]["prepared_by"] = PREPARER["id"]
    b["publication"]["content_digest"] = "2" * 64
    assert any("published at" in e for e in f.verify(b)), "a publication at another digest"


def test_an_off_contract_prepared_bundle_is_never_published(db):
    f = _f()
    b = f.preview(SAMPLE_NAME, at=AT)["bundle"]
    b["workloads"][0]["effective_cost"] = "1.000000"
    b["content_digest"] = _icos_digest(b)
    p = f.prepare("planted", PREPARER, bundle=b)
    with pytest.raises(f.FocusRefused) as exc:
        f.publish(p["event_id"], decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"],
                  actor=PUBLISHER)
    assert exc.value.code == "off_contract"


def test_the_ledger_is_append_only_and_hash_chained(db):
    f = _f()
    p = f.prepare(SAMPLE_NAME, PREPARER, at=AT)
    f.publish(p["event_id"], decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"],
              actor=PUBLISHER)
    rows = f.read_events(period="2026-10")
    ch = f.chain(rows)
    assert ch["_problems"] == [] and [r["event"] for r in ch["2026-10"]] == ["prepared", "published"]
    assert ch["2026-10"][0]["prev_hash"] == f.GENESIS and ch["2026-10"][1]["prev_hash"] == ch["2026-10"][0]["row_hash"]
    for sql in ("UPDATE finops_focus_events SET actor = 'x'", "DELETE FROM finops_focus_events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn = sqlite3.connect(str(db))
            try:
                conn.execute(sql)
            finally:
                conn.close()
    forged = [dict(r) for r in rows]
    forged[0]["bundle_json"] = forged[0]["bundle_json"].replace('"icos-api"', '"icos-twin"', 1)
    forged[0]["row_hash"] = f._row_hash(forged[0])
    probs = f.chain(forged)["_problems"]
    assert any(x["problem"] == "the bundle does not match its digest" for x in probs)
    assert any(x["problem"] == "not on the billing month's hash chain" for x in probs), "the next row no longer links"
    # only the month's newest prepared bundle can be decided
    a = f.prepare(SAMPLE_NAME, PREPARER, at=AT)
    newer = f.prepare(SAMPLE_NAME, {"id": "agent:cop05-drafter", "role": "service"}, at=AT)
    assert newer["actor_kind"] == "agent"
    with pytest.raises(f.FocusRefused) as exc:
        f.publish(a["event_id"], decision="publish", reason="r", read_digest=a["content_digest"], actor=PUBLISHER)
    assert exc.value.code == "superseded"
    with pytest.raises(f.FocusRefused) as exc:
        f.prepare(SAMPLE_NAME, None)
    assert exc.value.code == "no_actor"


def test_the_template_is_declared():
    from tools.workflow_hitl import declared

    t = declared.load_templates()["decl-wft-finops-focus-publish"]
    assert declared.validate_template(t) == []
    assert t["decides"] == ["idp.idp_api_finops_focus_publish"]
    stage = declared.manual_stage(t)
    assert stage["separate_from"] == "builders" and set(stage["reason_required"]) == {"publish", "refuse"}
    assert "service" not in stage["holders"]


# ── the console, the routes ──────────────────────────────────────────────────


def _page_app(user):
    from tools.dashboard.app import app as dash
    from tools.idp.blueprint import bp

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

    app.register_blueprint(bp, url_prefix="/idp")
    return app.test_client()


def test_the_routes_and_the_page(db):
    prep, pub = _page_app(PREPARER), _page_app(PUBLISHER)
    html = prep.get("/idp/finops/focus").get_data(as_text=True)
    for want in ("FOCUS cost bundles", "refuses the whole export", "unattributed", "decl-wft-finops-focus-publish",
                 f'data-export="{SAMPLE_NAME}"', "idp.idp_api_finops_focus_preview", "idp.idp_api_finops_focus_prepare",
                 "idp.idp_api_finops_focus_publish", "idp.idp_api_finops_focus_bundle", "idp.idp_api_standards_twin",
                 _f().SCHEMA_DIGEST):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/idp/finops_focus.html").read_text(encoding="utf-8"), \
        "routes are named by endpoint id"
    lst = prep.get("/idp/api/finops/focus").get_json()
    assert lst["months"] == [] and [x["name"] for x in lst["exports"]] == [SAMPLE_NAME]
    assert lst["schema"]["digest"] == _f().SCHEMA_DIGEST
    v = prep.get(f"/idp/api/finops/focus/preview?export={SAMPLE_NAME}").get_json()
    assert v["bundle"]["publication"] is None and len(v["bundle"]["workloads"]) == 4
    assert prep.get("/idp/api/finops/focus/preview?export=../x.csv").status_code == 400
    assert prep.get("/idp/api/finops/focus/latest/bundle").get_json()["code"] == "unpublished"
    r = prep.post("/idp/api/finops/focus/prepare", json={"export": SAMPLE_NAME})
    assert r.status_code == 201, r.get_data(as_text=True)
    ev, d = r.get_json()["event_id"], r.get_json()["content_digest"]
    month = pub.get("/idp/api/finops/focus").get_json()["months"][0]
    assert month["next"] == ["publish"] and PREPARER["id"] in month["builders"]
    body = {"decision": "publish", "reason": "Read it (synthetic).", "digest": d}
    refused = prep.post(f"/idp/api/finops/focus/{ev}/publish", json=body)
    assert refused.status_code == 403 and refused.get_json()["code"] == "separation"
    out = pub.post(f"/idp/api/finops/focus/{ev}/publish", json=body).get_json()
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == PUBLISHER["id"]
    got = pub.get(f"/idp/api/finops/focus/{out['event_id']}/bundle").get_json()
    assert got["content_digest"] == d and got["publication"]["content_digest"] == d
    assert pub.get("/idp/api/finops/focus/latest/bundle").get_json() == got
    assert pub.post("/idp/api/finops/focus/fofe-none/publish", json=body).status_code == 404
    # linked from the portal and in the nav
    text = (ROOT / "tools/dashboard/templates/idp/page.html").read_text(encoding="utf-8")
    assert 'href="/idp/finops/focus"' in text
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    idp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "idp")
    assert "/idp/finops/focus" in [lk["href"] for lk in idp["nav"]["links"]]


# ── the twin ─────────────────────────────────────────────────────────────────


def test_the_twin_observes_bundles_with_threads_and_replays(db):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    f = _f()
    adapter = governance.adapter_for("idp")
    assert adapter.observes == OBSERVES
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["finops.focus.ledger"]["status"] == "verified"
    for k in ["published", *ICOS_WORKLOADS]:
        assert chk[f"finops.focus.{k}"]["status"] == "unverified", "nothing published is unverified, never a pass"
    p = f.prepare(SAMPLE_NAME, PREPARER, at=AT)
    out = f.publish(p["event_id"], decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"],
                    actor=PUBLISHER)
    env = governance.observe(adapter)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    for k in ["published", *ICOS_WORKLOADS]:
        assert chk[f"finops.focus.{k}"]["status"] == "verified", k
    found = [v for v in env["violations"] if v["rule_id"].startswith("finops.focus")]
    un = next(v for v in found if v["rule_id"] == "finops.focus.unattributed")
    assert un["thread_id"] == f"finops_focus:{out['event_id']}" and "2 row(s)" in un["title"]
    assert not any(v["rule_id"] in ("finops.focus.self_published", "finops.focus.off_contract") for v in found)
    t = governance.resolve_thread(un["thread_id"])
    assert t["resolved"] and t["source"] == "finops_focus_events" and t["record"]["event"] == "published"
    assert t["record"]["actor"] == PUBLISHER["id"] and t["record"]["content_digest"] == p["content_digest"]
    assert env["extra"]["summary"]["finops_focus_published"] == 1
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep.get("reason")


def test_a_self_published_or_off_contract_bundle_is_a_finding_and_counts_for_nothing(db):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    f = _f()
    adapter = governance.adapter_for("idp")
    # planted, as a forger with write access would: the ledger refuses neither row (it is append-only, not
    # authorising), so the twin has to see it.
    p = f.prepare(SAMPLE_NAME, PREPARER, at=AT)
    conn = f._connection()
    try:
        ordered = f.chain(f.read_events(conn, "2026-10"))["2026-10"]
        row = f._append(conn, period="2026-10", event="published", actor=PREPARER["id"], actor_kind="person",
                        step_id="step-forged", reason="planted", content=p["content_digest"],
                        detail={"prepared_event": p["event_id"], "decision": "publish"}, prev_hash=f._head(ordered))
    finally:
        conn.close()
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["finops.focus.published"]["status"] == "unverified" and chk["finops.focus.icos-api"]["status"] == "unverified"
    v = next(v for v in env["violations"] if v["rule_id"] == "finops.focus.self_published")
    assert v["severity"] == "critical" and v["thread_id"] == f"finops_focus:{row['event_id']}"
    # off contract: a prepared bundle whose total was altered (its digest recomputed), then published
    b = f.preview(SAMPLE_NAME, at=AT)["bundle"]
    b["workloads"][1]["effective_cost"] = "9.000000"
    b["content_digest"] = _icos_digest(b)
    bad = f.prepare("planted", PREPARER, bundle=b)
    conn = f._connection()
    try:
        ordered = f.chain(f.read_events(conn, "2026-10"))["2026-10"]
        row2 = f._append(conn, period="2026-10", event="published", actor=PUBLISHER["id"], actor_kind="person",
                         step_id="step-forged-2", reason="planted", content=bad["content_digest"],
                         detail={"prepared_event": bad["event_id"], "decision": "publish"}, prev_hash=f._head(ordered))
    finally:
        conn.close()
    env = governance.observe(adapter, record=False)
    v = next(v for v in env["violations"] if v["rule_id"] == "finops.focus.off_contract")
    assert v["thread_id"] == f"finops_focus:{row2['event_id']}" and "icos-mesh's total" in v.get("detail", "")
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["finops.focus.published"]["status"] == "unverified"


def test_the_twin_says_unverified_when_the_ledger_cannot_be_read(monkeypatch):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    f = _f()

    def boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(f, "read_events", boom)
    env = governance.observe(governance.adapter_for("idp"), record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["finops.focus.ledger"]["status"] == "unverified" and env["verdict"] != "pass"
    assert chk["finops.focus.published"]["status"] == "unverified"


# ── AI-native: read, and draft only ──────────────────────────────────────────


def test_the_mcp_tools_read_and_only_draft(db):
    from tools.conformance.feature_audit import approval_policy
    from tools.mcp.gap_handlers import handle_finops_focus, handle_finops_focus_prepare
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    f = _f()
    assert {"finops_focus", "finops_focus_prepare"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["finops_focus"] is True and tool_authorization("finops_focus")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["finops_focus_prepare"] is False
    policy = approval_policy(ROOT, "args/agent_approval_policy.yaml")
    assert policy["tiers"].get("finops_focus_prepare", policy["default"]) in policy["halts"], "a draft halts at the gate"
    led = handle_finops_focus({})
    assert led["ledger"]["months"] == [] and [x["name"] for x in led["ledger"]["exports"]] == [SAMPLE_NAME]
    draft = handle_finops_focus({"export": SAMPLE_NAME})
    assert draft["draft"]["bundle"]["publication"] is None
    out = handle_finops_focus_prepare({"export": SAMPLE_NAME, "agent_id": "cop05-drafter"})
    assert out["draft"]["prepared_by"] == "agent:cop05-drafter" and out["draft"]["actor_kind"] == "agent"
    assert "a person who did not prepare it" in out["next"]
    assert f.published_bundle() is None, "an agent's draft publishes nothing"
    assert handle_finops_focus({"event": "latest"})["code"] == "unpublished"
    assert not any(n.startswith("finops_focus") and "publish" in n for n in list_tools()), "no MCP tool publishes"


# ── owner: no cloud-vendor training or certification content ─────────────────


def test_no_cloud_vendor_training_or_certification_content():
    files = [ROOT / "args/finops_focus.yaml", SAMPLE, ROOT / "tools/dashboard/templates/idp/finops_focus.html",
             ROOT / "args/workflow_hitl_templates/finops-focus-publish.yaml",
             ROOT / "tests/e2e/fixtures/cop05_seed.py", ROOT / "tests/e2e/finops_focus.spec.ts"]
    for p in files:
        body = p.read_text(encoding="utf-8")
        for banned in (r"\bAWS\b", r"\bAmazon\b", r"\bAzure\b", r"\bGCP\b", r"\bGoogle Cloud\b",
                       r"[Cc]ertification exam", r"Solutions Architect", r"Cloud Practitioner"):
            assert not re.search(banned, body), (p.name, banned)
