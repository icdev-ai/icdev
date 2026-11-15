# CUI // SP-CTI
"""ICDEV[OS] osx-fth-12 -- an application's features reported STATICALLY, from its entry points.

* the analyser reads each HTTP entry point from source and records how it found it and how sure it is: Spring
  (a class ``@RequestMapping`` is a prefix, never an endpoint; array paths), JAX-RS, ``@WebServlet``, Stripes
  action beans, ASP.NET MVC attributes and conventional routes; a word in a comment is no class;
* static_features groups them by controller, or by resource for one-servlet classes, labelled ``static`` from
  ``source`` (never ``observed``), at the lowest confidence of its entry points; none found is null, not [];
* the exporter carries them in ``features`` and the bundle meets the contract; observed and static together, or a
  feature not labelled static, is refused.

Small synthetic trees only: no third-party source.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.modernization import apptwin_export as ex
from tools.modernization import legacy_analyzer as la
from tools.modernization import static_features as sf

_TABLES = ("projects", "legacy_applications", "legacy_components", "legacy_dependencies", "legacy_apis",
           "legacy_db_schemas")


def _canonical_ddl() -> list[str]:
    text = (Path(ex._BASE) / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    out = []
    for t in _TABLES:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {t} \(.*?\n\);", text, re.DOTALL)
        assert m, f"no canonical DDL for {t}"
        out.append(m.group(0))
    return out


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    monkeypatch.setattr(la, "DB_PATH", path)
    from tools.db.storage import get_connection
    c = get_connection()
    for ddl in _canonical_ddl():
        c.execute(ddl)
    c.execute("INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
              ("proj-1", "shop", "webapp", str(tmp_path)))
    for app, lang in (("app-j", "java"), ("app-c", "csharp")):
        c.execute("INSERT INTO legacy_applications (id, project_id, name, source_path, primary_language) "
                  "VALUES (%s, %s, %s, %s, %s)", (app, "proj-1", app, str(tmp_path / app), lang))
    c.commit()
    yield c
    c.close()


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _java(root: Path) -> Path:
    _write(root, "src/com/acme/web/CartController.java",
           "package com.acme.web;\n/** The class to keep a cart. */\n@Controller\n@RequestMapping(\"/cart\")\n"
           "public class CartController {\n"
           "  @GetMapping({ \"\", \"/\" })\n  public String view(@RequestParam(value = \"q\") String q) { return \"c\"; }\n"
           "  @PostMapping(\"/update\")\n  public String update() { return \"u\"; }\n"
           "  @RequestMapping(value = \"/any\")\n  public String any() { return \"a\"; }\n}\n")
    _write(root, "src/com/acme/rest/OrderResource.java",
           "package com.acme.rest;\n@Path(\"/orders\")\npublic class OrderResource {\n"
           "  @GET\n  public List<Order> all() { return null; }\n"
           "  @POST @Path(\"{id}/cancel\")\n  public Response cancel(@PathParam(\"id\") String id) { return null; }\n}\n")
    _write(root, "src/com/acme/web/actions/CatalogActionBean.java",
           "package com.acme.web.actions;\npublic class CatalogActionBean extends AbstractActionBean {\n"
           "  public Resolution viewCategory() { return null; }\n}\n")
    for name in ("PingOne", "PingTwo"):
        _write(root, f"src/com/acme/ping/{name}.java",
               f"package com.acme.ping;\n// the class to ping\n@WebServlet(name = \"{name}\", urlPatterns = {{ \"/servlet/{name}\" }})\n"
               f"public class {name} extends HttpServlet {{}}\n")
    return root


def _csharp(root: Path) -> Path:
    _write(root, "Controllers/StoreController.cs",
           "using System.Web.Mvc;\nnamespace Shop.Controllers {\n  public class StoreController : Controller {\n"
           "    public ActionResult Browse() { return View(); }\n    public ActionResult Details(int id) { return View(); }\n"
           "  }\n}\n")
    return root


def test_the_analyser_reads_each_entry_point_and_how_it_found_it(db, tmp_path):
    la.analyze_java("app-j", _java(tmp_path / "app-j"))
    eps = {e["ref"]: e for e in sf.entry_points("app-j", db)}
    assert set(eps) == {"GET /cart", "POST /cart/update", "ALL /cart/any", "GET /orders", "POST /orders/{id}/cancel",
                        "ALL /actions/Catalog.action", "ALL /servlet/PingOne", "ALL /servlet/PingTwo"}, \
        "the class @RequestMapping is a prefix, never an endpoint of its own"
    assert eps["GET /cart"]["handler"] == "CartController.view" and eps["GET /cart"]["detected_by"] == "annotation"
    assert eps["ALL /cart/any"]["confidence"] == la.ENTRY_CONFIDENCE["annotation_any_method"]
    assert eps["ALL /actions/Catalog.action"]["detected_by"] == "convention"
    assert eps["ALL /servlet/PingOne"]["handler"] == "PingOne.service", "a word in a comment is no class"


def test_features_are_grouped_by_controller_or_resource_and_labelled_static(db, tmp_path):
    la.analyze_java("app-j", _java(tmp_path / "app-j"))
    feats = {f["id"]: f for f in sf.derive("app-j", db)}
    assert set(feats) == {"cart", "order", "catalog", "servlet"}
    cart = feats["cart"]
    assert cart["title"] == "Cart" and cart["basis"] == "source" and cart["provenance"] == "static"
    assert cart["signatures"]["apis"] == ["ALL /cart/any", "GET /cart", "POST /cart/update"]
    assert cart["confidence"] == la.ENTRY_CONFIDENCE["annotation_any_method"], "the lowest of its entry points"
    assert feats["servlet"]["signatures"]["apis"] == ["ALL /servlet/PingOne", "ALL /servlet/PingTwo"], \
        "one-servlet classes under one path segment are one resource"
    assert feats["catalog"]["confidence"] == la.ENTRY_CONFIDENCE["convention"]
    assert all(e.startswith("source:src/") for f in feats.values() for e in f["evidence"]), "relative, never local"
    assert not any(f.get("provenance") == "observed" or f["basis"] == "observed" for f in feats.values())


def test_aspnet_mvc_conventional_routes_are_a_feature_at_convention_confidence(db, tmp_path):
    la.analyze_csharp("app-c", _csharp(tmp_path / "app-c"))
    [f] = sf.derive("app-c", db)
    assert f["id"] == "store" and f["signatures"]["apis"] == ["ALL /Store/Browse", "ALL /Store/Details"]
    assert f["confidence"] == la.ENTRY_CONFIDENCE["convention"] and f["provenance"] == "static"


def test_no_entry_point_is_no_feature_report_not_an_empty_one(db):
    assert sf.derive("app-j", db) is None
    assert sf.report("app-j", db)["features"] is None


def test_a_descriptor_action_counts_at_the_unrecorded_confidence(db):
    db.execute("INSERT INTO legacy_apis (id, legacy_app_id, method, path, handler_function) VALUES (%s, %s, %s, %s, %s)",
               ("a1", "app-j", "ALL", "/login", "com.acme.LoginAction"))
    [f] = sf.derive("app-j", db)
    assert f["title"] == "Login" and f["confidence"] == sf.UNRECORDED and f["evidence"] == ["api:ALL /login"]


def test_the_exporter_carries_static_features_and_the_bundle_meets_the_contract(db, tmp_path):
    la.analyze_java("app-j", _java(tmp_path / "app-j"))
    rep = json.loads(json.dumps(sf.report("app-j", db)))
    b = ex.build("app-j", db, at="2026-11-14T00:00:00Z", static_features=rep)
    assert ex.validate(b) == []
    assert {f["id"] for f in b["features"]} == {"cart", "order", "catalog", "servlet"}
    assert all(f["provenance"] == "static" and 0 < f["confidence"] <= 1 for f in b["features"])
    refs = {f"{a['method']} {a['path']}" for a in b["apis"]}
    assert all(set(f["signatures"]["apis"]) <= refs for f in b["features"]), "each entry point is a declared api"
    with pytest.raises(ex.BundleError, match="not both"):
        ex.build("app-j", db, at="2026-11-14T00:00:00Z", static_features=rep, features={"features": []})
    bad = {**rep, "features": [{**rep["features"][0], "provenance": "observed"}]}
    with pytest.raises(ex.BundleError, match="not labelled static"):
        ex.build("app-j", db, at="2026-11-14T00:00:00Z", static_features=bad)
