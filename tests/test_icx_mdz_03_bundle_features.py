# CUI // SP-CTI
"""Features and processes without a model, in the ICDEV[OS] bundle (icx-mdz-03).

Covers:
  * the reviewed rule set (args/modernisation_signatures.yaml): a step's text becomes verb:object the same way every
    time, a process's step signature is digested with the rules' digest beside it, and a rule YAML would read as a
    boolean is refused;
  * features from the analyser's entry points with file:line evidence and capability hints, merged with the routes
    an REA observed inventory names (static, observed, both); processes from one controller's actions in source
    order, or a dispatcher's branches; the same source gives the same extract;
  * the exporter carries them in the icos-apptwin/v1 contract's shape (still valid), leaves a rejected process out,
    and says for every section and planning part whether it is present or missing, and why;
  * reviewing a process is a person's declared step bound to its signature digest; a changed signature makes the
    review stale;
  * the Modernisation page shows them and calls its routes by endpoint id; the modernisation twin flags a bundle with
    no features, unreviewed processes and missing parts, with threads, and replays; the MCP read tool is read-only;
  * the regenerated bundles of the three real applications validate, carry features and processes, and
    JPetStore 6 and the Cycle Store share a capability hint.

Run: pytest tests/test_icx_mdz_03_bundle_features.py -v
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

from tests._schema_compat import ensure_tables

ROOT = Path(__file__).resolve().parents[1]
PM = {"id": "u-pm", "role": "pm"}
COR = {"id": "u-cor", "role": "cor"}
BUNDLES = ROOT / "tests" / "fixtures" / "icos" / "modsamples"
REAL = ("daytrader7", "cycle-store", "jpetstore6")

# The schema of record's NOT NULL columns and keys (tools/db/schema/pg_consolidated.sql), and the columns read here.
LEGACY_APPLICATIONS_DDL = """CREATE TABLE IF NOT EXISTS legacy_applications (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, source_path TEXT NOT NULL, primary_language TEXT NOT NULL, language_version TEXT, framework TEXT, analysis_status TEXT DEFAULT 'registered')"""
LEGACY_COMPONENTS_DDL = """CREATE TABLE IF NOT EXISTS legacy_components (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, name TEXT NOT NULL, component_type TEXT NOT NULL, file_path TEXT NOT NULL, qualified_name TEXT, properties TEXT)"""
LEGACY_APIS_DDL = """CREATE TABLE IF NOT EXISTS legacy_apis (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, component_id TEXT, method TEXT, path TEXT NOT NULL, handler_function TEXT)"""
LEGACY_DEPENDENCIES_DDL = """CREATE TABLE IF NOT EXISTS legacy_dependencies (id INTEGER PRIMARY KEY AUTOINCREMENT, legacy_app_id TEXT NOT NULL, source_component_id TEXT NOT NULL, target_component_id TEXT, dependency_type TEXT NOT NULL)"""
LEGACY_DB_SCHEMAS_DDL = """CREATE TABLE IF NOT EXISTS legacy_db_schemas (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, db_type TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL, data_type TEXT NOT NULL)"""
DDL = (LEGACY_APPLICATIONS_DDL, LEGACY_COMPONENTS_DDL, LEGACY_APIS_DDL, LEGACY_DEPENDENCIES_DDL, LEGACY_DB_SCHEMAS_DDL)

CART = """package shop.web;
import org.springframework.web.bind.annotation.*;

@RequestMapping("/cart")
public class CartController {

  @GetMapping("")
  public String viewCart() {
    return "cart";
  }

  @GetMapping("/addItem")
  public String addItemToCart(String itemId) {
    CatalogService.getItem(itemId);
    return "cart";
  }

  @PostMapping("/checkout")
  public String checkOut() {
    return "order";
  }
}
"""
CATALOG = """package shop.web;
@RequestMapping("/catalog")
public class CatalogController {
  @GetMapping("/viewCategory")
  public String viewCategory(String categoryId) { return "category"; }
  @GetMapping("/searchProducts")
  public String searchProducts(String keyword) { return "products"; }
}
"""
TRADE = """package shop.web;
@WebServlet(name = "Trade", urlPatterns = { "/app" })
public class TradeServlet extends HttpServlet {
  void performTask(String action) {
    if (action.equals("login")) { login(); }
    else if (action.equals("buy")) { buy(); }
    else if (action.equals("sell")) { sell(); }
  }
}
"""

# (component id, class, file, [(method, path, handler, detected_by, confidence)])
CLASSES = (
    ("c-cart", "CartController", "web/CartController.java",
     [("GET", "/cart", "CartController.viewCart", "annotation", 0.9),
      ("GET", "/cart/addItem", "CartController.addItemToCart", "annotation", 0.9),
      ("POST", "/cart/checkout", "CartController.checkOut", "annotation", 0.9)]),
    ("c-catalog", "CatalogController", "web/CatalogController.java",
     [("GET", "/catalog/viewCategory", "CatalogController.viewCategory", "annotation", 0.9),
      ("GET", "/catalog/searchProducts", "CatalogController.searchProducts", "annotation", 0.9)]),
    ("c-trade", "TradeServlet", "web/TradeServlet.java",
     [("ALL", "/app", "TradeServlet.service", "annotation_any_method", 0.75)]),
)


def _app(*blueprints, user=None):
    app = Flask(__name__, template_folder=str(ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user) if user else None
        g.security_context = {"tenant_id": "default", "user_id": (user or {}).get("id", ""),
                              "classification": "CUI"}

    for bp, kw in blueprints:
        app.register_blueprint(bp, **kw)
    return app.test_client()


def _write_source(src: Path) -> None:
    (src / "web").mkdir(parents=True)
    for name, text in (("CartController.java", CART), ("CatalogController.java", CATALOG),
                       ("TradeServlet.java", TRADE), ("CatalogService.java", "public class CatalogService {}\n")):
        (src / "web" / name).write_text(text, encoding="utf-8")


@pytest.fixture()
def shop(wf_db, tmp_path):
    """A legacy application as legacy_analyzer records one: three controllers and their entry points (synthetic)."""
    src = tmp_path / "src"
    _write_source(src)
    conn = sqlite3.connect(str(wf_db))
    try:
        ensure_tables(conn, DDL)
        conn.execute("INSERT INTO legacy_applications (id, project_id, name, source_path, primary_language, "
                     "framework) VALUES ('app-1', 'proj-1', 'Shop', ?, 'java', 'Spring')", (str(src),))
        conn.execute("INSERT INTO legacy_components (id, legacy_app_id, name, component_type, file_path, "
                     "qualified_name) VALUES ('c-svc', 'app-1', 'CatalogService', 'class', 'web/CatalogService.java', "
                     "'shop.web.CatalogService')")
        n = 0
        for cid, cls, path, eps in CLASSES:
            props = {"entry_points": [{"method": m, "path": p, "handler": h, "detected_by": d, "confidence": c}
                                      for m, p, h, d, c in eps]}
            conn.execute("INSERT INTO legacy_components (id, legacy_app_id, name, component_type, file_path, "
                         "qualified_name, properties) VALUES (?, 'app-1', ?, 'controller', ?, ?, ?)",
                         (cid, cls, path, f"shop.web.{cls}", json.dumps(props)))
            for m, p, h, _d, _c in eps:
                n += 1
                conn.execute("INSERT INTO legacy_apis (id, legacy_app_id, component_id, method, path, "
                             "handler_function) VALUES (?, 'app-1', ?, ?, ?, ?)", (f"api-{n}", cid, m, p, h))
        conn.commit()
    finally:
        conn.close()
    return wf_db


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _report(**kw):
    from tools.modernization import bundle_features
    conn = _conn()
    try:
        return bundle_features.report("app-1", conn, **kw)
    finally:
        conn.close()


# ── the rule set ─────────────────────────────────────────────────────────────


def test_a_step_is_normalised_by_the_reviewed_rules_and_digested():
    from tools.modernization import bundle_features as bf

    r = bf.rules()
    assert bf.normalise("addItemToCart", r)["signature"] == "create:cart+item"
    assert bf.normalise("removeItemFromCart", r)["signature"] == "delete:cart+item"
    assert bf.normalise("checkOut", r, fallback_object=["cart"])["signature"] == "submit:cart"
    assert bf.normalise("signon", r)["signature"] == "authenticate"
    assert bf.normalise("HeaderLayout", r)["signature"] == "view:header+layout", "no verb: the default verb"
    assert bf.normalise("viewProducts", r)["signature"] == "view:product", "plurals fold"
    sig = bf.signature([bf.normalise(t, r) for t in ("viewCart", "addItemToCart")], r)
    assert sig["steps"] == ["view:cart", "create:cart+item"] and sig["rules_digest"] == r["digest"]
    assert sig == bf.signature([bf.normalise(t, r) for t in ("viewCart", "addItemToCart")], bf.rules())
    assert len(sig["digest"]) == 64 and sig["digest"] != bf.signature(
        [bf.normalise(t, r) for t in ("addItemToCart", "viewCart")], r)["digest"], "the order is in the digest"


def test_a_rule_yaml_reads_as_a_boolean_is_refused(tmp_path):
    from tools.modernization import bundle_features as bf

    text = (ROOT / "args" / "modernisation_signatures.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "rules.yaml"
    bad.write_text(text.replace("service, 'no', edge,", "service, no, edge,"), encoding="utf-8")
    with pytest.raises(ValueError, match="not a word"):
        bf.rules(bad)
    same = tmp_path / "same.yaml"
    same.write_text("# a comment changes nothing\n" + text, encoding="utf-8")
    assert bf.rules(same)["digest"] == bf.rules()["digest"]
    other = tmp_path / "other.yaml"
    other.write_text(text.replace("min_steps: 2", "min_steps: 3"), encoding="utf-8")
    assert bf.rules(other)["digest"] != bf.rules()["digest"], "a changed rule is a new rule set"


# ── features and processes ───────────────────────────────────────────────────


def test_features_carry_file_line_evidence_and_capability_hints(shop):
    rep = _report()
    feats = {f["id"]: f for f in rep["features"]}
    assert set(feats) == {"cart", "catalog", "trade"} and {f["source"] for f in feats.values()} == {"static"}
    cart = feats["cart"]
    assert cart["evidence"] == ["web/CartController.java:8", "web/CartController.java:13",
                                "web/CartController.java:19"], "each handler's declaration line, in line order"
    assert cart["apis"] == ["GET /cart", "GET /cart/addItem", "POST /cart/checkout"]
    hints = {h["capability"]: h for h in cart["capability_hints"]}
    assert set(hints) >= {"cart", "catalogue", "order"}
    assert "calls" in hints["catalogue"]["from"], "the application's own CatalogService it calls"
    assert {h["capability"] for h in feats["trade"]["capability_hints"]} >= {"trading", "account"}
    assert rep["observed"]["given"] is False and "never run" in rep["observed"]["basis"]


def test_an_observed_route_makes_a_feature_both_or_observed(shop):
    observed = {"basis": "observed", "features_basis": "observed_by_rea", "features": [
        {"kind": "http_endpoint", "label": "POST /cart/checkout", "evidence_ids": ["ev-1"]},
        {"kind": "http_endpoint", "label": "GET /wishlist/{id}", "evidence_ids": ["ev-2"]},
        {"kind": "ipc_channel", "label": "not-a-route", "evidence_ids": ["ev-3"]}]}
    feats = {f["id"]: f for f in _report(observed=observed)["features"]}
    assert feats["cart"]["source"] == "both" and feats["cart"]["routes"] == ["POST /cart/checkout"]
    assert {"route:POST /cart/checkout", "rea:ev-1"} <= set(feats["cart"]["evidence"])
    wish = feats["wishlist"]
    assert wish["source"] == "observed" and wish["basis"] == "observed" and wish["confidence"] is None
    assert wish["evidence"] == ["rea:ev-2", "route:GET /wishlist/{id}"]
    assert feats["catalog"]["source"] == "static"


def test_a_static_process_is_one_controllers_actions_in_source_order(shop):
    procs = {p["id"]: p for p in _report()["processes"]}
    assert set(procs) == {"cart-actions", "catalog-actions", "trade-actions"}
    cart = procs["cart-actions"]
    assert [s["title"] for s in cart["steps"]] == ["View cart", "Add item to cart", "Check out"]
    assert cart["signature"]["steps"] == ["view:cart", "create:cart+item", "submit:cart"]
    assert [s["evidence"] for s in cart["steps"]] == ["web/CartController.java:8", "web/CartController.java:13",
                                                      "web/CartController.java:19"]
    assert cart["source"] == "static" and "not an observed sequence" in cart["order"]
    trade = procs["trade-actions"]
    assert trade["derived_from"] == "the values its dispatcher branches on"
    assert trade["signature"]["steps"] == ["authenticate", "buy", "sell"]
    assert {p["review"]["state"] for p in procs.values()} == {"unreviewed"}


def test_the_same_source_gives_the_same_extract(shop):
    assert _report() == _report()


def test_an_observed_capture_is_a_process_with_its_signature(shop):
    from tools.workflow_canvas import observed_processify as op

    draft = op.build_draft({"status": "ok", "kind": "process", "scenario": {"executable": "/opt/shop"},
                            "record": {"evidence_id": "ev-9", "normalized_result": {"steps": [
                                {"label": "open the cart"}, {"label": "add an item"}]}}}, name="Observed checkout")
    procs = {p["id"]: p for p in _report(observed_processes=[draft])["processes"]}
    p = procs["observed-checkout"]
    assert p["source"] == "observed" and p["basis"] == "observed"
    assert p["signature"]["steps"] == ["view:cart", "create:item"]
    assert [s["evidence"] for s in p["steps"]] == ["rea:ev-9", "rea:ev-9"]


# ── the bundle ───────────────────────────────────────────────────────────────


def test_the_bundle_carries_features_and_processes_and_still_validates(shop):
    from tools.modernization import apptwin_export as ax

    rep = _report()
    conn = _conn()
    try:
        b = ax.build("app-1", conn, at="2026-11-15T00:00:00Z", extract=rep)
        rejected = json.loads(json.dumps(rep))
        next(p for p in rejected["processes"] if p["id"] == "catalog-actions")["review"] = {"state": "rejected"}
        b2 = ax.build("app-1", conn, at="2026-11-15T00:00:00Z", extract=rejected)
        with pytest.raises(ax.BundleError, match="one features report"):
            ax.build("app-1", conn, extract=rep, static_features={"features": None})
    finally:
        conn.close()
    assert ax.validate(b) == []
    cart = next(f for f in b["features"] if f["id"] == "cart")
    assert cart["provenance"] == "static" and cart["basis"] == "source"
    assert cart["signatures"]["steps"] == ["cart-actions.s01", "cart-actions.s02", "cart-actions.s03"]
    assert "web/CartController.java:13" in cart["evidence"]
    assert sorted(p["id"] for p in b["processes"]) == ["cart-actions", "catalog-actions", "trade-actions"]
    assert sorted(p["id"] for p in b2["processes"]) == ["cart-actions", "trade-actions"], "a rejected process is out"
    assert "signatures" in next(f for f in b2["features"] if f["id"] == "catalog") and not \
        (next(f for f in b2["features"] if f["id"] == "catalog")["signatures"].get("steps"))


def test_every_missing_part_is_said_with_its_reason(shop):
    from tools.modernization import apptwin_export as ax

    conn = _conn()
    try:
        b = ax.build("app-1", conn, at="2026-11-15T00:00:00Z", extract=_report())
    finally:
        conn.close()
    states = ax.section_states(b, waves={"waves": [{"name": "w1"}, {"name": "w2"}]},
                               reasons={"migration": "refused, no ICDEV[OS] may-plan"})
    assert states["features"] == {"state": "present", "count": 3}
    assert states["processes"]["state"] == "present"
    assert states["migration"] == {"state": "missing", "reason": "refused, no ICDEV[OS] may-plan"}
    assert states["migration.waves"]["state"] == "missing" and states["migration.waves"]["derived"] == 2
    assert "migration-plan gate will block" in states["migration.waves"]["reason"]
    for part in ("refactor.jobs", "cloud_native.twelve_factor", "cloud_native.containerisation",
                 "cloud_native.config", "cloud_native.events"):
        assert states[part]["state"] == "missing" and states[part]["reason"], part
    assert all(v["state"] in ("present", "missing") for v in states.values())


# ── the person's review, the page, the twin, MCP ─────────────────────────────


@pytest.fixture()
def canvas(shop, tmp_path, monkeypatch):
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_DB_PATH", str(tmp_path / "mc.db"))
    monkeypatch.setenv("CAM_ARTIFACTS_DIR", str(tmp_path / "cam"))
    monkeypatch.setenv("ICDEV_MIGRATION_CANVAS_ENABLED", "true")
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    from tools.migration_canvas.blueprint import create_migration_blueprint
    return create_migration_blueprint()


def _client(bp, user=PM):
    return _app((bp, {"url_prefix": "/migration-canvas"}), user=user)


def test_reviewing_a_process_is_a_persons_step_bound_to_its_digest(canvas, shop, monkeypatch):
    feats = _client(canvas).get("/migration-canvas/api/modernisation/apps/app-1/features").get_json()
    proc = next(p for p in feats["processes"] if p["id"] == "cart-actions")
    url = "/migration-canvas/api/modernisation/apps/app-1/processes/cart-actions/review"
    body = {"decision": "confirm", "reason": "the cart flow is real", "signature_digest": proc["signature"]["digest"]}
    assert _client(canvas).post(url, json={**body, "reason": ""}).status_code == 400
    assert _client(canvas, COR).post(url, json=body).status_code == 403
    assert _client(canvas).post(url, json={**body, "signature_digest": "0" * 64}).status_code == 409
    assert _client(canvas).post("/migration-canvas/api/modernisation/apps/app-1/processes/nope/review",
                                json=body).status_code == 404
    resp = _client(canvas).post(url, json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    out = resp.get_json()
    assert out["review"] == {"state": "reviewed", "decision": "confirm", "by": "u-pm",
                             "signature_digest": proc["signature"]["digest"]}
    assert out["decision_step"]["template_id"] == "decl-wft-mdz-process-review"
    assert _client(canvas).post(url, json=body).status_code == 409, "reviewed once at a digest"
    after = {p["id"]: p for p in _report()["processes"]}
    assert after["cart-actions"]["review"]["state"] == "reviewed" and after["cart-actions"]["review"]["by"] == "u-pm"
    assert after["catalog-actions"]["review"]["state"] == "unreviewed"
    # the controller changes: the signature does, and the review is stale
    src = Path(_sql_one(shop, "SELECT source_path FROM legacy_applications WHERE id = 'app-1'"))
    text = (src / "web/CartController.java").read_text(encoding="utf-8")
    (src / "web/CartController.java").write_text(text.replace("checkOut", "placeOrder"), encoding="utf-8")
    _sql_exec(shop, "UPDATE legacy_apis SET handler_function = 'CartController.placeOrder' "
                    "WHERE handler_function = 'CartController.checkOut'")
    _sql_exec(shop, "UPDATE legacy_components SET properties = replace(properties, 'checkOut', 'placeOrder') "
                    "WHERE id = 'c-cart'")
    stale = next(p for p in _report()["processes"] if p["id"] == "cart-actions")
    assert stale["signature"]["digest"] != proc["signature"]["digest"] and stale["review"]["state"] == "stale"


def _sql_one(db, sql):
    with sqlite3.connect(str(db)) as c:
        return c.execute(sql).fetchone()[0]


def _sql_exec(db, sql):
    c = sqlite3.connect(str(db))
    try:
        c.execute(sql)
        c.commit()
    finally:
        c.close()


def test_the_bundle_preview_counts_features_processes_and_what_is_missing(canvas):
    d = _client(canvas).get("/migration-canvas/api/modernisation/apps/app-1/bundle").get_json()
    assert d["valid"] is True, d
    assert d["counts"]["features"] == 3 and d["counts"]["processes"] == 3 and d["processes_unreviewed"] == 3
    assert {"migration", "refactor.jobs", "cloud_native.events"} <= set(d["missing"])
    assert _client(canvas).get("/migration-canvas/api/modernisation/apps/nope/features").status_code == 404


def test_the_page_shows_features_processes_and_calls_routes_by_id():
    from tools.conformance.feature_audit import refs_in

    rel = "tools/dashboard/templates/migration_canvas/modernisation.html"
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "/api/" not in text
    assert {"migration_canvas.api_modernisation_features",
            "migration_canvas.api_modernisation_process_review"} <= refs_in(text)
    for needle in ('id="mdz-features"', "data-confirm-process", "data-reject-process", "capability_hints",
                   "signature.digest", "decl-wft-mdz-process-review", "mdz-bundle-missing"):
        assert needle in text, needle
    assert (ROOT / "icdev" / rel).read_text(encoding="utf-8") == text, "not mirrored to icdev/"


def _bundle_evidence(**over):
    base = {"canvas": "mdc", "target_id": "fleet", "observed_at": "2026-11-15T00:00:00+00:00",
            "policy": {"bundle_required": ["processes", "migration.waves", "cloud_native.events"]},
            "assessments": [], "plans": [], "strategy_records": [], "aiify_decisions": [], "aiify_steps": [],
            "drafts": [], "jobs": [], "job_reviews": [], "refusals": [],
            "schema": {"path": "context/icos/apptwin.schema.json", "sha256": "a" * 64, "pinned": "a" * 64},
            "bundles": [
                {"app": "app-1", "name": "Shop", "valid": True, "error": None, "features": 3,
                 "processes": [{"id": "cart-actions", "state": "reviewed", "digest": "d1"},
                               {"id": "trade-actions", "state": "stale", "digest": "d2"}],
                 "missing": {"migration.waves": "no migration plan carries them", "rtm": "not run"}},
                {"app": "app-2", "name": "Empty", "valid": True, "error": None, "features": 0, "processes": [],
                 "missing": {"features": "no entry point", "processes": "no controller offers two actions"}},
                {"app": "app-3", "name": "Broken", "error": "the bundle does not meet the contract"}],
            "errors": {}}
    base.update(over)
    return base


def test_the_twin_flags_no_features_unreviewed_processes_and_missing_parts():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    twin = ModernisationGovernanceTwin()
    found = {(f["rule_id"], f["thread_id"]) for f in twin.judge(_bundle_evidence())["findings"]}
    assert found == {
        ("mdz.exporter.processes_unreviewed", "legacy_app:app-1"),
        ("mdz.exporter.sections_missing", "legacy_app:app-1"),
        ("mdz.exporter.no_features", "legacy_app:app-2"),
        ("mdz.exporter.sections_missing", "legacy_app:app-2"),
        ("mdz.exporter.bundle_invalid", "legacy_app:app-3"),
    }
    out = twin.judge(_bundle_evidence())
    unreviewed = next(f for f in out["findings"] if f["rule_id"] == "mdz.exporter.processes_unreviewed")
    assert "trade-actions (stale)" in unreviewed["detail"] and "cart-actions" not in unreviewed["detail"]
    assert out["summary"]["bundles_without_features"] == 1 and out["summary"]["bundles_previewed"] == 3
    env = twin.envelope(_bundle_evidence(bundles=None, errors={"bundles": "unreadable"}))
    assert env["verdict"] != "pass" and env["coverage_score"] < 1.0


def test_the_twin_observes_a_bundle_resolves_its_thread_and_replays(canvas):
    from tools.twin_core import governance

    adapter = governance.adapter_for("mdc")
    env = governance.observe(adapter)
    assert env["extra"]["recorded"] is True
    threads = {(v["rule_id"], v["thread_id"]) for v in env["violations"]}
    assert ("mdz.exporter.processes_unreviewed", "legacy_app:app-1") in threads
    replayed = governance.replay(adapter, env["extra"]["observation_id"])
    assert replayed["match"] is True, replayed["reason"]
    thread = governance.resolve_thread("legacy_app:app-1")
    assert thread["resolved"] is True and thread["record"]["name"] == "Shop"


def test_the_mcp_features_tool_is_read_only(canvas):
    from tools.mcp import gap_handlers
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert "modernisation_features" in set(list_tools())
    assert READ_ONLY_DECLARATIONS["modernisation_features"] is True
    assert tool_authorization("modernisation_features")["tier"] == "read"
    assert gap_handlers.handle_modernisation_features({}) == {"error": "app_id is required"}
    out = gap_handlers.handle_modernisation_features({"app_id": "app-1"})
    assert [f["id"] for f in out["features"]] == ["cart", "catalog", "trade"]
    assert all("signature" in p and p["review"]["state"] == "unreviewed" for p in out["processes"])
    assert "never a capability mapping" in out["review_means"]


def test_processify_from_source_needs_no_model(shop, monkeypatch, capsys):
    pf = importlib.import_module("tools.workflow_canvas.processify")
    monkeypatch.setattr(pf, "digitize", lambda *a, **k: pytest.fail("a model was asked"))
    assert pf.main(["--from-source", "app-1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and [p["id"] for p in out["processes"]] == [
        "cart-actions", "catalog-actions", "trade-actions"]


# ── the three real applications ──────────────────────────────────────────────


def test_the_regenerated_real_bundles_validate_and_carry_features_and_processes():
    from tools.modernization import apptwin_export as ax

    for name in REAL:
        b = json.loads((BUNDLES / f"{name}.apptwin.json").read_text(encoding="utf-8"))
        gaps = json.loads((BUNDLES / f"{name}.gaps.json").read_text(encoding="utf-8"))
        ext = json.loads((BUNDLES / f"{name}.features.json").read_text(encoding="utf-8"))
        assert ax.validate(b) == [], name
        assert b["features"] and b["processes"], name
        assert {f["provenance"] for f in b["features"]} == {"static"}, f"{name}: the source is never run"
        assert all(":" in e for f in b["features"] for e in f["evidence"]), name
        assert gaps["bundle_sha256"] == __import__("hashlib").sha256(
            (BUNDLES / f"{name}.apptwin.json").read_bytes()).hexdigest(), f"{name}: the report names this bundle"
        sections = gaps["sections"]
        assert all(v["state"] in ("present", "missing") for v in sections.values())
        assert all(v.get("reason") for v in sections.values() if v["state"] == "missing"), name
        assert sections["features"]["count"] == len(b["features"]) == ext["counts"]["features"]
        assert sections["processes"]["count"] == len(b["processes"]) == ext["counts"]["processes"]
        assert ext["rules"]["digest"] == next(s for s in gaps["steps"] if s["step"] == "extract")["rules_digest"]
    hints = {n: {h["capability"] for f in json.loads((BUNDLES / f"{n}.features.json").read_text())["features"]
                 for h in f["capability_hints"]} for n in REAL}
    assert "catalogue" in hints["jpetstore6"] & hints["cycle-store"]
