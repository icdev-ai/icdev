# CUI // SP-CTI
"""ACE role profiles: preview writes nothing, generate writes one held file, delete
removes only generated roles (icx-cnf-04, PM-ACE-10).

Covers tools/ace/profile_generator.py and the /api/ace/profiles routes:
  * preview returns a spec and writes nothing; a model that claims ``green`` or
    Bash is held to the allowlists (red/yellow; Read, Grep, Glob);
  * generate writes ``<roles>/<slug>.yaml`` tagged ``source: generated`` and
    refuses to overwrite a built-in role;
  * a caller-supplied spec (an edited preview) is held to the same allowlists,
    and its role_id is re-slugged so it can never name a path outside the roles
    directory;
  * delete removes a generated role or candidate, protects a built-in one, and
    an unknown role is an error, not a 500;
  * the routes: preview needs a name, generate is a 201, delete of a built-in
    is a 400.

The roles directory is a tmp_path; the model is a fake router, so no call leaves.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml
from flask import Flask

pg = importlib.import_module("icdev.tools.ace.profile_generator")


@pytest.fixture()
def roles(tmp_path, monkeypatch):
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    monkeypatch.setattr(pg, "_ROLES_DIR", roles_dir)
    monkeypatch.setattr(pg, "_CANDIDATES_DIR", roles_dir / "candidates")
    (roles_dir / "ai_developer.yaml").write_text(
        yaml.safe_dump({"role_id": "ai_developer", "display_name": "AI Developer"}),
        encoding="utf-8")
    return roles_dir


@pytest.fixture()
def model(monkeypatch):
    """A router whose reply over-reaches: a green tier and Bash/Write permissions."""
    reply = {"content": yaml.safe_dump({
        "steps": ["triage", "draft", "report"],
        "llm_function": "data_analysis",
        "tool_permissions": ["Read", "Bash", "Write"],
        "trust_tier": "green",
    })}

    class _Router:
        def invoke(self, function, request):
            return type("R", (), reply)()

    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    return reply


def test_preview_writes_nothing_and_holds_the_model_to_the_allowlists(roles, model):
    before = sorted(p.name for p in roles.iterdir())
    spec = pg.preview_profile("Data Steward", "Curates data products")

    assert sorted(p.name for p in roles.iterdir()) == before
    assert spec["role_id"] == "data_steward" and spec["source"] == "generated"
    assert spec["steps"] == ["triage", "draft", "report"]
    assert spec["tool_permissions"] == ["Read"]
    assert spec["trust_tier"] == "yellow"


def test_generate_writes_one_generated_role(roles, model):
    out = pg.generate_profile("Data Steward", "Curates data products")

    path = roles / "data_steward.yaml"
    assert out == {"role_id": "data_steward", "files_written": [str(path)]}
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["source"] == "generated" and written["trust_tier"] == "yellow"
    listed = {p["role_id"]: p["source"] for p in pg.list_profiles()}
    assert listed == {"ai_developer": "builtin", "data_steward": "generated"}


def test_generate_refuses_to_overwrite_a_builtin_role(roles, model):
    with pytest.raises(ValueError, match="built-in"):
        pg.generate_profile("AI Developer")
    assert "source" not in yaml.safe_load((roles / "ai_developer.yaml").read_text())


def test_a_caller_supplied_spec_is_held_to_the_same_allowlists(roles):
    spec = {"role_id": "../../escape", "display_name": "Escape", "source": "builtin",
            "trust_tier": "green", "tool_permissions": ["Bash", "Read"]}
    out = pg.generate_profile("Escape", spec_override=spec)

    assert out["role_id"] == "escape"
    assert Path(out["files_written"][0]).parent == roles
    assert not (roles.parent.parent / "escape.yaml").exists()
    written = yaml.safe_load((roles / "escape.yaml").read_text(encoding="utf-8"))
    assert written["trust_tier"] == "yellow"
    assert written["tool_permissions"] == ["Read"]
    assert written["source"] == "generated"


def test_delete_removes_generated_roles_and_candidates_only(roles, model):
    pg.generate_profile("Data Steward")
    cand = roles / "candidates"
    cand.mkdir()
    (cand / "gap_role.yaml").write_text(
        yaml.safe_dump({"role_id": "gap_role", "source": "candidate"}), encoding="utf-8")

    assert pg.delete_profile("data_steward") == {"role_id": "data_steward", "deleted": True}
    assert not (roles / "data_steward.yaml").exists()
    assert pg.delete_profile("gap_role") == {"role_id": "gap_role", "deleted": True}
    assert "built-in" in pg.delete_profile("ai_developer")["error"]
    assert (roles / "ai_developer.yaml").exists()
    assert "not found" in pg.delete_profile("nobody")["error"]


@pytest.fixture()
def client(roles, model):
    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    bp_mod._state["db_ready"] = True
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp_mod.ace_bp)
    if "ace_api" not in app.blueprints:
        app.register_blueprint(bp_mod.ace_api_bp)
    with app.test_client() as c:
        yield c


def test_the_profile_routes_preview_generate_and_delete(client, roles):
    assert client.post("/api/ace/profiles/preview", json={}).status_code == 400
    preview = client.post("/api/ace/profiles/preview", json={"name": "Data Steward"})
    assert preview.status_code == 200 and preview.get_json()["spec"]["trust_tier"] == "yellow"
    assert not (roles / "data_steward.yaml").exists()

    made = client.post("/api/ace/profiles/generate",
                       json={"name": "Data Steward", "spec": preview.get_json()["spec"]})
    assert made.status_code == 201 and made.get_json()["role_id"] == "data_steward"
    assert (roles / "data_steward.yaml").exists()

    assert client.delete("/api/ace/profiles/ai_developer").status_code == 400
    gone = client.delete("/api/ace/profiles/data_steward")
    assert gone.status_code == 200 and gone.get_json()["deleted"] is True
