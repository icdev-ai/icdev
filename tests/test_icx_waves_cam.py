# CUI // SP-CTI
"""ICDEV[OS] osx-fth-13 -- waves for an application's own migration plan, and the migrators as CAM jobs.

* app_wave_planner derives the waves from the application's own dependencies, in the strangler order: leaves first,
  a dependency cycle moved together, each wave depending only on earlier ones; each wave's backout is derived and
  names the later waves that must be backed out first; the same inputs give the same waves;
* CAM writes where the run says (``CAM_ARTIFACTS_DIR``), lists a migrator's whole tree, queues one explicit job, and
  a legacy application's database job never falls back to the generic scaffold;
* the exporter binds every file a job wrote by its SHA-256, binds them all in ``artefact_digest`` (what sha256sum
  prints), labels the job unreviewed, never names where it ran, and the bundle meets the vendored contract.

Small synthetic inputs only: no third-party source.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.modernization import apptwin_export as ex
from tools.modernization import app_wave_planner as wp


def _ids(waves):
    return [w["components"] for w in waves]


def test_leaves_first_then_what_depends_on_them():
    comps = {"c1": "web.Cart", "c2": "svc.Pricing", "c3": "dao.Store", "c4": "util.Money"}
    got = wp.plan(comps, [("c1", "c2"), ("c2", "c3"), ("c2", "c4"), ("c3", "c4")], app="shop")
    assert _ids(got["waves"]) == [["util.Money"], ["dao.Store"], ["svc.Pricing"], ["web.Cart"]]
    assert [w["id"] for w in got["waves"]] == ["w1", "w2", "w3", "w4"]
    assert all(w["status"] == "planned" and "shop" in w["backout"] for w in got["waves"])
    w1 = got["waves"][0]
    assert "Back out waves 2, 3 first" in w1["backout"], "dao.Store and svc.Pricing call into util.Money"
    assert "first" not in got["waves"][-1]["backout"], "nothing depends on the last wave"
    assert got["waves"][2]["depends_on"] == [1, 2]
    assert "4 dependencies" in got["basis"]


def test_a_cycle_moves_together():
    comps = {"a": "A", "b": "B", "c": "C", "d": "D"}
    got = wp.plan(comps, [("a", "b"), ("b", "a"), ("c", "a"), ("a", "d")])
    assert _ids(got["waves"]) == [["D"], ["A", "B"], ["C"]]
    assert "cycles of 2 moved together" in got["waves"][1]["name"]


def test_no_dependency_resolved_is_one_wave_and_says_why():
    got = wp.plan({"x": "X", "y": "Y", "z": "Z"}, [("x", "x"), ("x", "outside")])
    assert _ids(got["waves"]) == [["X", "Y", "Z"]]
    assert "no dependency" in got["basis"] and got["waves"][0]["backout"]


def test_the_same_inputs_give_the_same_waves():
    comps = {f"c{i}": f"n{i % 7}.C{i}" for i in range(60)}
    edges = [(f"c{i}", f"c{(i * 7 + 3) % 60}") for i in range(60)] + [(f"c{i}", f"c{i // 2}") for i in range(1, 60)]
    a = wp.plan(comps, edges)
    b = wp.plan(dict(reversed(list(comps.items()))), list(reversed(edges)))
    assert a == b
    assert sorted(c for w in a["waves"] for c in w["components"]) == sorted(set(comps.values()))


def test_a_long_chain_does_not_exhaust_the_stack():
    n = 5000
    got = wp.plan({f"c{i}": f"C{i:05d}" for i in range(n)}, [(f"c{i}", f"c{i + 1}") for i in range(n - 1)])
    assert len(got["waves"]) == n and got["waves"][0]["components"] == [f"C{n - 1:05d}"]


# --- CAM ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def cam(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_CANVAS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_DB_PATH", str(tmp_path / "migration_canvas.db"))
    monkeypatch.setenv("CAM_ARTIFACTS_DIR", str(tmp_path / "cam"))
    from tools.migration_canvas import cam_refactor_engine as eng
    from tools.migration_canvas.db.init_db import get_connection, init_db
    init_db()
    c = get_connection()
    c.execute("INSERT INTO mc_srv_sessions (id, migration_type, tgt_platform) VALUES (%s, %s, %s)", ("s1", "application", ""))
    c.execute("INSERT INTO mc_projects (id, name, mc_session_id) VALUES (%s, %s, %s)", ("p1", "shop", "s1"))
    c.execute("INSERT INTO mc_app_inventory (id, session_id, name, version, language, framework, source_repo) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s)", ("lapp-1", "s1", "shop", "8", "java", "", str(tmp_path / "src")))
    c.commit()
    c.close()
    return eng


def test_cam_writes_where_the_run_says_and_lists_the_whole_tree(cam, tmp_path):
    job = cam.queue("p1", "lapp-1", {"type": "code_scaffold", "scaffold_type": "nothing-known"})
    assert Path(job["output_path"]).is_relative_to(tmp_path / "cam")
    nested = Path(job["output_path"]) / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "X.java").write_text("class X {}")
    arts = cam._scan_artifacts(Path(job["output_path"]), "source")
    assert [a["rel"] for a in arts] == ["a/b/X.java"]


def test_the_java_rule_queues_a_version_job_through_cam(cam):
    jobs = cam.dispatch("p1")
    assert [j["refactor_type"] for j in jobs] == ["version_upgrade"]
    assert json.loads(jobs[0]["params_json"])["target_version"] == "17"


def test_a_legacy_database_job_never_falls_back_to_the_scaffold(cam, monkeypatch):
    import tools.modernization.db_migration_planner as dbp
    monkeypatch.setattr(dbp, "generate_full_migration", lambda *a, **k: {})   # wrote nothing
    job = cam.queue("p1", "lapp-1", {"type": "db_ddl_generation", "scope": "full", "target_db": "postgresql"})
    got = cam.run_job(job["id"])
    assert got["status"] == "failed" and "wrote nothing" in got["error_message"]
    assert not list(Path(job["output_path"]).rglob("aurora_schema_migration.sql"))


# --- the exporter ---------------------------------------------------------------------------------------------------

def _job(tmp_path, files: dict[str, bytes], **kw) -> dict:
    root = tmp_path / "cam" / "p1" / "j1"
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    arts = [{"type": "source", "path": str(root / rel), "rel": rel} for rel in files]
    return {"id": "j1", "component_name": "shop", "refactor_type": "version_upgrade", "status": "completed",
            "output_path": str(root), "artifacts": arts, "result_summary": f"java 8 -> 17 in {root}", **kw}


def test_every_file_is_bound_by_its_sha256_and_the_job_by_their_manifest(tmp_path):
    j = _job(tmp_path, {"b/Y.java": b"class Y {}", "A.java": b"class A {}"})
    [out] = ex.refactor_from_jobs({"jobs": [j]}, at="2026-11-14T00:00:00Z")["jobs"]
    assert [a["path"] for a in out["artefacts"]] == ["A.java", "b/Y.java"]
    assert out["artefacts"][0]["sha256"] == hashlib.sha256(b"class A {}").hexdigest()
    lines = "".join(f"{a['sha256']}  {a['path']}\n" for a in out["artefacts"])
    assert out["artefact_digest"] == hashlib.sha256(lines.encode()).hexdigest()
    assert out["review"] == "unreviewed" and out["status"] == "completed"
    assert str(tmp_path) not in json.dumps(out), "a bundle never says where the job ran"


def test_a_changed_file_changes_the_digest_and_a_missing_one_is_never_guessed(tmp_path):
    j = _job(tmp_path, {"A.java": b"class A {}"})
    d1 = ex.refactor_from_jobs([j], at="2026-11-14T00:00:00Z")["jobs"][0]["artefact_digest"]
    Path(j["artifacts"][0]["path"]).write_bytes(b"class A { int x; }")
    assert ex.refactor_from_jobs([j], at="2026-11-14T00:00:00Z")["jobs"][0]["artefact_digest"] != d1
    Path(j["artifacts"][0]["path"]).unlink()
    [gone] = ex.refactor_from_jobs([j], at="2026-11-14T00:00:00Z")["jobs"]
    assert "artefact_digest" not in gone and "artefacts" not in gone


def test_a_failed_job_carries_its_error_and_the_older_form_still_exports(tmp_path):
    j = _job(tmp_path, {}, status="failed", error_message=f"version_migrator failed: {tmp_path}/x.java")
    [f] = ex.refactor_from_jobs([j], at="2026-11-14T00:00:00Z")["jobs"]
    assert f["status"] == "failed" and f["summary"] == "version_migrator failed: x.java"
    [old] = ex.refactor_from_jobs([{"id": "o", "refactor_type": "code_scaffold", "artifacts_json": "[]"}],
                                  at="2026-11-14T00:00:00Z")["jobs"]
    assert old == {"id": "o", "kind": "code_scaffold", "status": "queued"}


def test_waves_and_jobs_meet_the_vendored_contract(tmp_path):
    sch = ex.schema()
    job = sch["$defs"]["RefactorJob"]["properties"]
    assert {"artefacts", "summary", "review"} <= set(job)
    import jsonschema
    [out] = ex.refactor_from_jobs([_job(tmp_path, {"A.java": b"a"})], at="2026-11-14T00:00:00Z")["jobs"]
    jsonschema.validate(out, {"$defs": sch["$defs"], "$ref": "#/$defs/RefactorJob"})
    waves = wp.plan({"a": "A", "b": "B"}, [("a", "b")])
    for w in waves["waves"]:
        carried = {"id": w["id"], "title": w["name"], "order": w["wave_number"], "status": "planned",
                   "components": w["components"], "backout": w["backout"]}
        jsonschema.validate(carried, {"$defs": sch["$defs"], "$ref": "#/$defs/Wave"})
