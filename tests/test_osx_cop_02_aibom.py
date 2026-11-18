# CUI // SP-CTI
"""ICDEV[IT]'s own AI-BOM for ICDEV[OS], in SPDX 3.0.1 and CycloneDX 1.7, published by a person (osx-cop-02).

What the card promises on the ICDEV[IT] side, each clause a test here:

  * ICDEV[IT]'s AI components (router configs, the models they route to and the registered ones, datasets, gold sets,
    prompts by digest) are described as ONE AI-BOM in two standards: an SPDX 3.0.1 AI/Dataset document and a
    CycloneDX 1.7 ML-BOM, each validated against the OFFICIAL schema, vendored unmodified and pinned by digest;
    an AI-BOM that fails either official schema is refused;
  * it travels in an icos-aibom/v1 bundle against ICDEV[OS]'s published contract, vendored and pinned (file and body);
  * deterministic given its inputs; its digest recomputable as ICDEV[OS] recomputes it; a prompt's text never leaves;
  * publishing is a person's declared step, never the preparer's (a self-published AI-BOM is refused), bound to the
    digest; an unpublished one never carries a publication; the ledger is append-only and hash-chained;
  * the page and routes, the twin (threads that resolve, replay, unverified when nothing is published or declared),
    and the MCP read and draft.

The components are SYNTHETIC fixtures (tests/e2e/fixtures/aibom/); people are labelled test persons. All on the
conftest SQLite platform DB.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "tests" / "e2e" / "fixtures" / "aibom_sources.yaml"
PREPARER = {"id": "u-cop02i-preparer", "role": "reviewer"}       # labelled test persons (synthetic)
PUBLISHER = {"id": "u-cop02i-publisher", "role": "reviewer"}
DEVELOPER = {"id": "u-cop02i-dev", "role": "developer"}
AT = "2026-11-18T09:00:00Z"
AS_OF = "2026-11-18"
FX = "tests/e2e/fixtures/aibom"
EXPECTED = [f"dataset:{FX}/dataset.jsonl", f"gold-set:{FX}/gold.yaml", "model:syn-drafter", "model:syn-reviewer",
            f"prompt:{FX}/prompts/draft.md", f"prompt:{FX}/prompts/review.md", f"router-config:{FX}/router.yaml"]


@pytest.fixture()
def fixture_cfg(monkeypatch):
    monkeypatch.setenv("ICDEV_AIBOM_SOURCES_E2E", str(SOURCES))


@pytest.fixture()
def seeded(fixture_cfg, wf_db):
    return {"db": wf_db}


def _ab():
    from tools.compliance import aibom_bundle

    return aibom_bundle


def _icos_digest(bundle: dict) -> str:
    """ICDEV[OS]'s rule (icdev_os/models/aibommodel.content_digest), restated independently."""
    body = {k: v for k, v in bundle.items() if k not in ("content_digest", "publication", "produced_at")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str,
                                     ensure_ascii=False).encode()).hexdigest()


# ── the contract and the official schemas ────────────────────────────────────


def test_the_schemas_are_pinned_the_icos_contract_by_file_and_body(monkeypatch, tmp_path):
    ab = _ab()
    raw = (ROOT / ab.SCHEMA_RELPATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ab.SCHEMA_SHA256
    doc = json.loads(raw)
    assert doc["x-icos-contract"] == ab.SCHEMA_CONTRACT and ab.body_digest(doc) == ab.SCHEMA_BODY_SHA256
    assert doc["properties"]["schema"]["const"] == ab.SCHEMA == "icos-aibom/v1"
    # the official schemas, vendored unmodified with their licences, each pinned
    assert hashlib.sha256((ROOT / ab.SPDX_SCHEMA_RELPATH).read_bytes()).hexdigest() == ab.SPDX_SCHEMA_SHA256
    for name, sha in ab.CDX_SCHEMAS.items():
        assert hashlib.sha256((ROOT / ab.CDX_DIR_RELPATH / name).read_bytes()).hexdigest() == sha, name
    assert (ROOT / "context/standards/cyclonedx/1.7/LICENSE").read_text().lstrip().startswith("Apache License")
    assert "Community Specification License 1.0" in (ROOT / "context/standards/spdx/3.0.1/License.md").read_text()
    assert "1.7.2" in (ROOT / "context/standards/cyclonedx/1.7/NOTICE.md").read_text()
    altered = tmp_path / "context" / "icos"
    altered.mkdir(parents=True)
    (altered / "aibom.schema.json").write_bytes(raw.replace(b'"AIBOM"', b'"AIBOMx"', 1))
    monkeypatch.setattr(ab, "BASE_DIR", tmp_path)
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.schema()
    assert exc.value.code == "schema"


def test_the_components_are_declared_and_another_config_must_live_under_tests(monkeypatch, tmp_path):
    ab = _ab()
    monkeypatch.delenv(ab.E2E_ENV, raising=False)
    shipped = ab.load_config()
    assert shipped["router_configs"] == ["args/llm_config.yaml"] and shipped["registry"] is True
    assert shipped["prompts"] == ["hardprompts"]
    monkeypatch.setenv(ab.E2E_ENV, str(SOURCES))
    cfg = ab.load_config()
    assert [c["id"] for c in ab.inventory(cfg)["components"]] == EXPECTED
    outside = tmp_path / "sources.yaml"
    outside.write_text(yaml.safe_dump({"prompts": []}))
    monkeypatch.setenv(ab.E2E_ENV, str(outside))
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.load_config()
    assert exc.value.code == "config", "a path outside tests/ is refused, never followed"


# ── the AI-BOM ───────────────────────────────────────────────────────────────


def test_the_ai_bom_is_spdx_and_cyclonedx_valid_against_the_official_schemas(seeded):
    ab = _ab()
    b = ab.build(as_of=AS_OF, at=AT)
    assert ab.validate(b) == [] and b["publication"] is None
    assert ab.spdx_errors(b["spdx"]) == [] and ab.cyclonedx_errors(b["cyclonedx"]) == []
    assert b["content_digest"] == _icos_digest(b) == ab.content_digest(b), "ICDEV[OS] recomputes the same digest"
    assert b["standards"] == {"spdx": "3.0.1", "cyclonedx": "1.7"} and b["cyclonedx"]["specVersion"] == "1.7"
    assert b["spdx"]["@context"] == "https://spdx.org/rdf/3.0.1/spdx-context.jsonld"
    assert [c["id"] for c in b["components"]] == EXPECTED
    assert b["summary"] == {"router_configs": 1, "models": 2, "datasets": 1, "gold_sets": 1, "prompts": 2}
    graph = {e.get("spdxId"): e for e in b["spdx"]["@graph"]}
    cdx = {c["bom-ref"]: c for c in b["cyclonedx"]["components"]}
    for c in b["components"]:
        e = graph[ab.ID_PREFIX + c["id"]]
        assert e["type"] == ab.SPDX_TYPE[c["kind"]] and e["verifiedUsing"][0]["hashValue"] == c["digest"]
        assert cdx[c["id"]]["type"] == ab.CDX_TYPE[c["kind"]] and cdx[c["id"]]["hashes"][0]["content"] == c["digest"]
    assert graph[ab.ID_PREFIX + "model:syn-drafter"]["type"] == "ai_AIPackage"
    assert cdx["model:syn-drafter"]["type"] == "machine-learning-model" and cdx["model:syn-drafter"]["supplier"] == {
        "name": "synthetic-provider"}
    prompt = (ROOT / FX / "prompts" / "draft.md").read_text()
    assert hashlib.sha256(prompt.encode()).hexdigest() == cdx[f"prompt:{FX}/prompts/draft.md"]["hashes"][0]["content"]
    assert "draft a practice from its sources" not in json.dumps(b), "a prompt is carried by its digest, never its text"


def test_an_ai_bom_failing_the_official_schema_is_refused(seeded, monkeypatch):
    """Planted: a document that only looks like the standard is not one."""
    ab = _ab()
    b = ab.build(as_of=AS_OF, at=AT)
    bad = copy.deepcopy(b)
    bad["cyclonedx"]["components"][0]["hashes"][0]["alg"] = "SHA-999"
    bad["content_digest"] = ab.content_digest(bad)
    assert any(e.startswith("cyclonedx/") for e in ab.validate(bad))
    old = copy.deepcopy(b)
    old["cyclonedx"]["specVersion"] = "1.6"
    old["content_digest"] = ab.content_digest(old)
    assert any("specVersion" in e for e in ab.validate(old)), "a 1.6 BOM is not a 1.7 BOM"
    spdx = copy.deepcopy(b)
    spdx["spdx"]["@graph"][-1]["profileConformance"] = ["core", "not-a-profile"]
    spdx["content_digest"] = ab.content_digest(spdx)
    assert any(e.startswith("spdx/") for e in ab.validate(spdx))
    nohash = copy.deepcopy(b)
    el = next(e for e in nohash["spdx"]["@graph"] if e.get("verifiedUsing"))
    el["verifiedUsing"][0]["algorithm"] = "sha999"
    nohash["content_digest"] = ab.content_digest(nohash)
    assert any(e.startswith("spdx/") for e in ab.validate(nohash))
    # and build itself refuses to produce one (the generator's documents are always checked)
    real = ab.cyclonedx_bom
    monkeypatch.setattr(ab, "cyclonedx_bom", lambda *a, **k: {**real(*a, **k), "bomFormat": "NotCycloneDX"})
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.build(as_of=AS_OF, at=AT)
    assert exc.value.code == "invalid"


def test_the_export_is_deterministic_given_its_inputs(seeded, tmp_path):
    from tools.compliance import aibom_export as x

    ab = _ab()
    assert ab.build(as_of=AS_OF, at=AT) == ab.build(as_of=AS_OF, at=AT)
    assert x.main(["--out", str(tmp_path / "a"), "--as-of", AS_OF, "--at", AT]) == 0
    assert x.main(["--out", str(tmp_path / "b"), "--as-of", AS_OF, "--at", AT]) == 0
    a, b = (tmp_path / "a" / "icdev-it.json").read_bytes(), (tmp_path / "b" / "icdev-it.json").read_bytes()
    assert a == b and json.loads(a)["publication"] is None, "a draft until a person publishes it"
    assert ab.build(as_of="2026-11-19")["content_digest"] != json.loads(a)["content_digest"]
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.build(as_of="yesterday")
    assert exc.value.code == "as_of"


def test_the_registry_models_and_datasets_by_their_digest_or_said_left_out(fixture_cfg, wf_db, monkeypatch, tmp_path):
    ab = _ab()
    conn = sqlite3.connect(str(wf_db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ft_model_versions (id TEXT, job_id TEXT, model_name TEXT, version INTEGER,
            base_model TEXT, adapter_hash TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS ft_datasets (id TEXT, name TEXT, purpose TEXT, content_hash TEXT, status TEXT);
    """)
    conn.execute("INSERT INTO ft_model_versions VALUES ('mv1','j1','syn-ft',1,'syn-base',?,'promoted')", ("a" * 64,))
    conn.execute("INSERT INTO ft_model_versions VALUES ('mv2','j2','syn-ft',2,'syn-base','','created')")
    conn.execute("INSERT INTO ft_model_versions VALUES ('mv3','j3','syn-old',1,'syn-base',?,'archived')", ("b" * 64,))
    conn.execute("INSERT INTO ft_datasets VALUES ('ds-syn','synthetic','general',?,'ready')", ("c" * 64,))
    conn.commit()
    conn.close()
    cfg = {**ab.load_config(), "registry": True}
    inv = ab.inventory(cfg)
    ids = [c["id"] for c in inv["components"]]
    assert "model:ft/syn-ft@v1" in ids and "dataset:ds-syn" in ids and "model:ft/syn-old@v1" not in ids
    assert inv["omitted"] == [{"kind": "model", "name": "ft/syn-ft@v2", "why": "no SHA-256 adapter hash is registered"}]
    b = ab.build(as_of=AS_OF, cfg=cfg)
    assert ab.validate(b) == []
    ft = next(c for c in b["cyclonedx"]["components"] if c["bom-ref"] == "model:ft/syn-ft@v1")
    assert ft["modelCard"]["modelParameters"]["approach"]["type"] == "supervised"


# ── publishing ───────────────────────────────────────────────────────────────


def test_publishing_is_a_persons_step_never_the_preparers(seeded, tmp_path):
    """Planted: a self-published AI-BOM is refused; an unpublished one never carries a publication."""
    from tools.compliance import aibom_export as x

    ab = _ab()
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.publish(decision="publish", reason="r", read_digest="x", actor=PUBLISHER)
    assert exc.value.code == "nothing_prepared"
    assert x.main(["--published", "--out", str(tmp_path)]) == 1, "nothing published: --published refuses"
    prep = ab.prepare(PREPARER, as_of=AS_OF)
    assert prep["prepared_by"] == PREPARER["id"] and prep["actor_kind"] == "person"
    d = prep["content_digest"]
    for actor, kw, code in ((PREPARER, {}, "separation"), (PUBLISHER, {"read_digest": "0" * 64}, "digest"),
                            (PUBLISHER, {"reason": ""}, "reason"), (DEVELOPER, {}, "role"),
                            ({"id": "agent:x", "role": "service"}, {}, "not_a_person"), (None, {}, "no_person")):
        with pytest.raises(ab.AIBOMRefused) as exc:
            ab.publish(decision="publish", **{"reason": "ok (synthetic)", "read_digest": d, **kw}, actor=actor)
        assert exc.value.code == code, (actor, kw)
    assert ab.published_bundle() is None, "nothing published yet"
    assert x.export(as_of=AS_OF)["publication"] is None, "an unpublished AI-BOM carries no publication"
    out = ab.publish(decision="publish", reason="Read it (synthetic).", read_digest=d, actor=PUBLISHER)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"] and out["prepared_by"] == PREPARER["id"]
    pub = out["bundle"]["publication"]
    assert pub["signed_by"] == PUBLISHER["id"] and pub["content_digest"] == d and pub["prepared_by"] == PREPARER["id"]
    assert pub["template"] == "decl-wft-aibom-publish" and pub["step_id"] == out["step"]["step_id"]
    assert ab.validate(out["bundle"]) == [] and out["bundle"]["content_digest"] == d
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.publish(decision="publish", reason="again", read_digest=d, actor=PUBLISHER)
    assert exc.value.code == "already_decided"
    assert x.main(["--published", "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "icdev-it.json").read_text())["publication"]["signed_by"] == PUBLISHER["id"]
    assert x.export(as_of=AS_OF)["publication"]["step_id"] == pub["step_id"], "the same content carries it"
    assert x.export(as_of="2026-11-19")["publication"] is None, "other content is a draft"
    p2 = ab.prepare(PUBLISHER, as_of="2026-11-19")
    r = ab.publish(decision="refuse", reason="Not yet (synthetic).", read_digest=p2["content_digest"], actor=PREPARER)
    assert r["event"] == "refused" and "bundle" not in r and ab.published_bundle()["content_digest"] == d
    conn = sqlite3.connect(str(seeded["db"]))
    steps = conn.execute("SELECT template_id, actor, subject FROM wf_decision_steps WHERE subject LIKE 'aibom:%' "
                         "ORDER BY created_at").fetchall()
    conn.close()
    assert steps == [("decl-wft-aibom-publish", PUBLISHER["id"], "aibom:icdev-it"),
                     ("decl-wft-aibom-publish", PREPARER["id"], "aibom:icdev-it")]


def test_the_ledger_is_append_only_and_hash_chained(seeded):
    ab = _ab()
    p = ab.prepare(PREPARER, as_of=AS_OF)
    ab.publish(decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    rows = ab.read_events()
    ch = ab.chain(rows)
    assert ch["problems"] == [] and [r["event"] for r in ch["ordered"]] == ["prepared", "published"]
    assert ch["ordered"][0]["prev_hash"] == ab.GENESIS and ch["ordered"][1]["prev_hash"] == ch["ordered"][0]["row_hash"]
    for sql in ("UPDATE aibom_export_events SET actor = 'x'", "DELETE FROM aibom_export_events"):
        conn = sqlite3.connect(str(seeded["db"]))
        try:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
        finally:
            conn.close()
    forged = [dict(r) for r in rows]
    forged[0]["bundle_json"] = forged[0]["bundle_json"].replace('"prompts":2', '"prompts":3', 1)
    forged[0]["row_hash"] = ab._row_hash(forged[0])
    probs = ab.chain(forged)["problems"]
    assert any(x["problem"] == "the bundle does not match its digest" for x in probs)
    assert any(x["problem"] == "not on the AI-BOM's hash chain" for x in probs), "the next row no longer links"
    agent = ab.prepare({"id": "agent:cop02i-drafter", "role": "service"}, as_of=AS_OF)
    assert agent["actor_kind"] == "agent"
    with pytest.raises(ab.AIBOMRefused) as exc:
        ab.prepare(None)
    assert exc.value.code == "no_actor"


# ── the console, the routes ──────────────────────────────────────────────────


def _page_app(user):
    from tools.compliance.aibom_blueprint import aibom_bp
    from tools.dashboard.app import app as dash

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

    app.register_blueprint(aibom_bp)
    return app.test_client()


def test_the_routes_and_the_page(seeded):
    prep, pub = _page_app(PREPARER), _page_app(PUBLISHER)
    html = prep.get("/ai-bom/").get_data(as_text=True)
    for want in ("AI-BOM export to ICDEV[OS]", "SPDX 3.0.1", "CycloneDX 1.7", "by digest only", "decl-wft-aibom-publish",
                 "aibom.aibom_api_view", "aibom.aibom_api_prepare", "aibom.aibom_api_publish",
                 "aibom.aibom_api_bundle", "aibom.aibom_api_document", "aibom.aibom_api_twin",
                 "you may not publish it", PREPARER["id"]):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/compliance/aibom_export.html").read_text(encoding="utf-8"), \
        "routes are named by endpoint id"
    v = prep.get("/ai-bom/api/view").get_json()
    assert [c["id"] for c in v["components"]] == EXPECTED and v["ledger"]["next"] == ["prepare"]
    assert v["standards"]["spdx"]["version"] == "3.0.1" and v["standards"]["cyclonedx"]["version"] == "1.7"
    assert v["bundle"]["schema"] == "icos-aibom/v1" and v["me"] == PREPARER["id"]
    assert prep.get("/ai-bom/api/view?as_of=yesterday").get_json()["unbuilt"]["code"] == "as_of"
    assert prep.get("/ai-bom/api/bundle").status_code == 404
    assert prep.get("/ai-bom/api/document/spdx").status_code == 404
    r = prep.post("/ai-bom/api/prepare", json={"as_of": AS_OF})
    assert r.status_code == 201, r.get_data(as_text=True)
    d = r.get_json()["content_digest"]
    led = pub.get("/ai-bom/api/view").get_json()["ledger"]
    assert led["next"] == ["publish"] and PREPARER["id"] in led["builders"]
    body = {"decision": "publish", "reason": "Read it (synthetic).", "digest": d}
    refused = prep.post("/ai-bom/api/publish", json=body)
    assert refused.status_code == 403 and refused.get_json()["code"] == "separation"
    out = pub.post("/ai-bom/api/publish", json=body).get_json()
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == PUBLISHER["id"]
    got = pub.get("/ai-bom/api/bundle?download=1")
    assert got.headers["Content-Disposition"] == 'attachment; filename="icdev-it.json"'
    assert got.get_json()["content_digest"] == d and got.get_json()["publication"]["content_digest"] == d
    spdx = pub.get("/ai-bom/api/document/spdx?download=1")
    assert spdx.headers["Content-Disposition"] == 'attachment; filename="icdev-it.spdx3.json"'
    assert spdx.get_json()["@context"].endswith("3.0.1/spdx-context.jsonld")
    assert pub.get("/ai-bom/api/document/cyclonedx").get_json()["specVersion"] == "1.7"
    assert pub.get("/ai-bom/api/document/xml").status_code == 404
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    comp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "aibom")
    assert "/ai-bom/" in [lk["href"] for lk in comp["nav"]["links"]]


# ── the twin ─────────────────────────────────────────────────────────────────


def test_the_twin_observes_with_threads_and_replays(seeded, monkeypatch):
    import tools.twin_core.adapters.aibom  # noqa: F401
    from tools.twin_core import governance

    ab = _ab()
    adapter = governance.adapter_for("aibom")
    assert adapter.observes == ("compliance.aibom_export",)
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["compliance.aibom.ledger"]["status"] == "verified"
    assert chk["compliance.aibom.published"]["status"] == "unverified", "never published is never a pass"
    assert env["verdict"] != "pass"
    p = ab.prepare(PREPARER, as_of=AS_OF)
    ab.publish(decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    # a policy under which every published AI-BOM is already stale: a finding threaded to the publication
    monkeypatch.setattr(tools.twin_core.adapters.aibom, "load_policy", lambda section: {"stale_days": -1})
    env = governance.observe(adapter)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["compliance.aibom.published"]["status"] == "verified"
    rules = {v["rule_id"]: v for v in env["violations"]}
    assert "aibom.stale" in rules, rules
    t = governance.resolve_thread(rules["aibom.stale"]["thread_id"])
    assert t["resolved"] and t["source"] == "aibom_export_events" and t["record"]["event"] == "published"
    assert t["record"]["actor"] == PUBLISHER["id"] and t["record"]["system"] == "icdev-it"
    assert env["extra"]["summary"]["aibom_published"] == 1
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep.get("reason")


def test_the_twin_says_unverified_when_nothing_is_declared_or_readable(wf_db, monkeypatch, tmp_path):
    import tools.twin_core.adapters.aibom  # noqa: F401
    from tools.twin_core import governance

    ab = _ab()
    empty = ROOT / "tests" / "e2e" / "fixtures" / "aibom_empty.yaml"
    monkeypatch.setenv(ab.E2E_ENV, str(empty))
    env = governance.observe(governance.adapter_for("aibom"), record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["compliance.aibom.declared"]["status"] == "unverified" and env["verdict"] != "pass", \
        "nothing declared is nothing observed"

    def boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(ab, "read_events", boom)
    env = governance.observe(governance.adapter_for("aibom"), record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["compliance.aibom.ledger"]["status"] == "unverified" and env["verdict"] != "pass"


# ── AI-native: read, and draft only ──────────────────────────────────────────


def test_the_mcp_tools_read_and_only_prepare(seeded):
    from tools.mcp.gap_handlers import handle_aibom_export, handle_aibom_prepare
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert {"aibom_export", "aibom_prepare"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["aibom_export"] is True
    assert tool_authorization("aibom_export")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["aibom_prepare"] is False
    one = handle_aibom_export({"as_of": AS_OF})
    assert one["aibom"]["bundle"]["schema"] == "icos-aibom/v1"
    assert handle_aibom_export({"published": True})["code"] == "unpublished"
    draft = handle_aibom_prepare({"agent_id": "cop02i-agent", "as_of": AS_OF})
    assert draft["draft"]["prepared_by"] == "agent:cop02i-agent" and draft["draft"]["actor_kind"] == "agent"
    ab = _ab()
    with pytest.raises(ab.AIBOMRefused) as exc:      # an agent can never publish
        ab.publish(decision="publish", reason="r", read_digest=draft["draft"]["content_digest"],
                   actor={"id": "agent:cop02i-agent", "role": "service"})
    assert exc.value.code == "not_a_person"
    assert ab.published_bundle() is None
