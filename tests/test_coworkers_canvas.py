# CUI // SP-CTI
"""Co-Workers canvas (/coworkers): roster, chat hand-off and status (icx-cnf-04, PM-CWK-02).

Covers tools/coworkers:
  * the roster is read from config only: chat personas first (the plumbing
    persona hidden), then ACE roles; a missing or malformed source contributes
    nothing instead of raising;
  * a co-worker's chat link is a relative /chat URL carrying its role, goal and id;
  * GET /coworkers lists the roster with a chat link per card, and shows an
    empty roster rather than a 500 when the roster cannot load;
  * GET /coworkers/c/<id> redirects into the shared chat, or 404s an unknown id;
  * GET /coworkers/status counts the roster by kind.

The sources are tmp_path files; the canvas touches no database.
"""
from __future__ import annotations

import importlib
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from flask import Flask

const = importlib.import_module("icdev.tools.coworkers.constants")
registry = importlib.import_module("icdev.tools.coworkers.registry")
factory = importlib.import_module("icdev.tools.coworkers.context_factory")


@pytest.fixture()
def sources(tmp_path, monkeypatch):
    personas = tmp_path / "chat_personas.yaml"
    personas.write_text(yaml.safe_dump({"personas": {
        "default": {"name": "Default"},
        "atlas": {"name": "Atlas", "role": "Architect", "description": "Designs systems",
                  "advisory_types": ["architecture"]},
        "scout": {"name": "Scout", "role": "Researcher"},
    }}), encoding="utf-8")
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "qa_manager.yaml").write_text(yaml.safe_dump({
        "role_id": "qa_manager", "display_name": "QA Manager", "trust_tier": "yellow",
        "steps": ["plan", "test", "report"]}), encoding="utf-8")
    (roles / "broken.yaml").write_text(": not yaml: [", encoding="utf-8")
    monkeypatch.setattr(const, "PERSONAS_YAML", personas)
    monkeypatch.setattr(const, "ACE_ROLES_DIR", roles)
    return tmp_path


def test_the_roster_is_personas_then_ace_roles_from_config(sources):
    roster = registry.list_coworkers()
    assert [c.id for c in roster] == ["persona:atlas", "persona:scout", "ace:qa_manager"]
    atlas = roster[0]
    assert (atlas.kind, atlas.role, atlas.rag_namespace) == ("persona", "Architect", "coworker:atlas")
    assert atlas.tags == ["architecture"]
    qa = roster[2]
    assert (qa.kind, qa.role, qa.tags) == ("ace", "Yellow Tier", ["plan", "test", "report"])
    assert registry.get_coworker("ace:qa_manager").name == "QA Manager"
    assert registry.get_coworker("persona:default") is None


def test_a_missing_source_contributes_nothing(sources, monkeypatch):
    monkeypatch.setattr(const, "PERSONAS_YAML", sources / "absent.yaml")
    monkeypatch.setattr(const, "ACE_ROLES_DIR", sources / "absent")
    assert registry.list_coworkers() == []


def test_the_chat_link_is_a_relative_chat_url_for_that_coworker(sources):
    link = factory.build_chat_link(registry.get_coworker("persona:atlas"))
    parsed = urlparse(link)
    assert parsed.path == "/chat" and not parsed.netloc
    assert parse_qs(parsed.query) == {
        "role": ["Architect"], "goal": ["Collaborate with Atlas"], "coworker": ["persona:atlas"]}


@pytest.fixture()
def client(sources):
    bp_mod = importlib.import_module("icdev.tools.coworkers.blueprint")
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp_mod.create_coworkers_blueprint())
    with app.test_client() as c:
        yield c, bp_mod


def test_the_roster_page_lists_every_coworker_with_its_chat_link(client, monkeypatch):
    c, bp_mod = client
    seen = {}

    def _render(template, **ctx):
        seen.update(template=template, **ctx)
        return "ok"

    monkeypatch.setattr(bp_mod, "render_template", _render)
    assert c.get("/coworkers/").status_code == 200
    assert seen["template"] == "coworkers/index.html"
    assert seen["counts"] == {"total": 3, "persona": 2, "ace": 1}
    links = {cw["id"]: cw["chat_link"] for cw in seen["coworkers"]}
    assert links["ace:qa_manager"].startswith("/chat?")


def test_a_roster_that_cannot_load_is_empty_not_a_500(client, monkeypatch):
    c, bp_mod = client

    def _boom():
        raise RuntimeError("roster unreadable")

    monkeypatch.setattr(bp_mod, "list_coworkers", _boom)
    monkeypatch.setattr(bp_mod, "render_template", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    res = c.get("/coworkers/")
    assert res.status_code == 200
    assert res.get_json() == {"coworkers": [], "counts": {"total": 0, "persona": 0, "ace": 0}}


def test_open_chat_redirects_into_the_shared_chat_or_404s(client):
    c, _ = client
    res = c.get("/coworkers/c/persona:scout")
    assert res.status_code == 302
    assert urlparse(res.headers["Location"]).path == "/chat"
    assert "coworker=persona%3Ascout" in res.headers["Location"]
    assert c.get("/coworkers/c/persona:nobody").status_code == 404


def test_status_counts_the_roster_by_kind(client):
    c, _ = client
    assert c.get("/coworkers/status").get_json() == {
        "ok": True, "canvas": "cwk", "total": 3, "persona": 2, "ace": 1}
