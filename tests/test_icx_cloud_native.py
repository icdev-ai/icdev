# CUI // SP-CTI
"""ICDEV[OS] osx-fth-14 -- the cloud-native target, generated from static analysis as CAM jobs.

* the analysis reads the stack (build tool, packaging, ports, config files) and finds hard-coded settings by WHERE they
  are -- a credential's value is never copied into a finding, a mapping or any generated file;
* the twelve-factor report scores each factor from evidence, unverified where static analysis cannot tell, and never
  met on nothing found;
* a message-driven bean's destination becomes a proposed event channel, its commands messages, their payload what the
  producers set;
* CAM's cloud_native rules fire only when that set is dispatched (a refactor run's jobs are unchanged); the event job
  of an application with no message-driven bean fails with why, never writes a stub;
* the exporter binds every generated file by SHA-256 and the section by the digest of that list, labels each
  unreviewed, drafts AI use cases from AI-Ify (never registers one), maps the plan's targets to what proposes them
  (never "reached"), and the section meets the vendored contract.

Small synthetic inputs only: no third-party source.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.modernization import apptwin_export as ex
from tools.modernization import cloud_native as cn

SECRET = "s3cr3t-never-copied"


def _ee_app(root: Path) -> Path:
    """A small Java EE application: an ear build, a server config with a literal credential, an MDB and its producer."""
    j = root / "ejb/src/main/java/com/acme"
    j.mkdir(parents=True)
    (root / "pom.xml").write_text("<project><packaging>ear</packaging></project>")
    (root / "ejb/pom.xml").write_text("<project><packaging>ejb</packaging></project>")
    cfg = root / "app/src/main/liberty/config"
    cfg.mkdir(parents=True)
    (cfg / "server.xml").write_text(
        "<server>\n<featureManager><feature>javaee-7.0</feature></featureManager>\n"
        '<httpEndpoint host="*" httpPort="9082" httpsPort="9443" id="defaultHttpEndpoint"/>\n'
        f'<authData id="TradeAuth" password="{SECRET}" user="trader"/>\n'
        '<dataSource jndiName="jdbc/Trade"><properties.derby.embedded databaseName="${shared.resource.dir}/db"/></dataSource>\n'
        "</server>\n")
    (root / "app/src/main/resources").mkdir(parents=True)
    (root / "app/src/main/resources/app.properties").write_text(
        f"db.url=jdbc:postgresql://db.example.internal:5432/trade\ndb.password={SECRET}\nreport.dir=/var/acme/reports\n")
    (j / "BrokerMDB.java").write_text(
        "package com.acme;\n"
        '@MessageDriven(activationConfig = { @ActivationConfigProperty(propertyName = "destinationType", '
        'propertyValue = "javax.jms.Queue"),\n'
        '  @ActivationConfigProperty(propertyName = "destination", propertyValue = "BrokerQueue") })\n'
        "public class BrokerMDB implements MessageListener {\n"
        "  public void onMessage(Message message) {\n"
        '    String command = message.getStringProperty("command");\n'
        '    if (command.equalsIgnoreCase("neworder")) { int id = message.getIntProperty("orderID"); }\n'
        '    else if (command.equalsIgnoreCase("ping")) { long t = message.getLongProperty("publishTime"); }\n'
        "  }\n}\n")
    (j / "Trade.java").write_text(
        "package com.acme;\npublic class Trade {\n  void buy(HttpSession s) {\n"
        "    TextMessage message = context.createTextMessage();\n"
        '    message.setStringProperty("command", "neworder");\n'
        '    message.setIntProperty("orderID", 7);\n'
        '    message.setLongProperty("publishTime", 1L);\n'
        "    context.createProducer().send(queue, message);\n  }\n}\n")
    (root / "src/test/java").mkdir(parents=True)
    (root / "src/test/java/TradeTest.java").write_text('class TradeTest { String password = "only-in-a-test"; }\n')
    return root


def _clean_app(root: Path) -> Path:
    (root / "src/main/java").mkdir(parents=True)
    (root / "pom.xml").write_text("<project><packaging>jar</packaging><dependency>spring-boot-starter-web"
                                  "</dependency></project>")
    (root / "src/main/java/App.java").write_text("class App { public static void main(String[] a) {} }\n")
    return root


@pytest.fixture
def ee(tmp_path):
    cn._analyse.cache_clear()
    return cn.analyse(_ee_app(tmp_path / "ee"), language="java", version="8", framework="ejb")


def test_the_stack_is_read_from_the_source(ee):
    st = ee["stack"]
    assert st["build_tool"] == "maven" and st["packaging"] == "ear"
    assert st["runtime"] == "Java 17 on a Jakarta EE application server"
    assert [(p["port"], p["found"]) for p in st["ports"]] == [(9082, True), (9443, True)]
    assert st["ports"][0]["location"] == "app/src/main/liberty/config/server.xml:3"
    assert "app/src/main/resources/app.properties" in st["config_files"]


def test_hard_coded_config_is_found_by_location_never_by_value(ee):
    kinds = {(f["kind"], f["location"]) for f in ee["findings"]}
    assert ("credential", "app/src/main/liberty/config/server.xml:4") in kinds
    assert ("credential", "app/src/main/resources/app.properties:2") in kinds
    assert ("jdbc_url", "app/src/main/resources/app.properties:1") in kinds
    assert ("file_path", "app/src/main/resources/app.properties:3") in kinds
    assert SECRET not in json.dumps(ee["findings"]), "a credential's value is never copied"
    assert not any("src/test" in f["location"] for f in ee["findings"]), "test code does not run with the application"
    env = {e["name"]: e for e in cn.env_mapping(ee["findings"])}
    assert env["TRADE_AUTH_PASSWORD"]["secret"] and env["DB_PASSWORD"]["secret"]
    assert not env["DB_URL"]["secret"]


def test_each_factor_rests_on_evidence_and_unverified_is_never_met(ee, tmp_path):
    fs = {f["id"]: f for f in cn.twelve_factor(ee)}
    assert list(fs) == list(cn.FACTORS)
    assert fs["config"]["status"] == "not_met" and fs["config"]["evidence"]
    assert fs["backing_services"]["status"] == "not_met", "an embedded database is not an attached resource"
    assert fs["processes"]["status"] == "not_met" and fs["port_binding"]["status"] == "not_met"
    assert fs["dependencies"]["status"] == "met" and "pom.xml" in fs["dependencies"]["evidence"]
    for k in ("codebase", "concurrency", "disposability", "build_release_run"):
        assert fs[k]["status"] == "unverified" and fs[k]["why"]
    assert all(f["evidence"] for f in fs.values() if f["status"] != "unverified")
    cn._analyse.cache_clear()
    clean = {f["id"]: f for f in cn.twelve_factor(cn.analyse(_clean_app(tmp_path / "c"), language="java", version="17"))}
    assert clean["config"]["status"] == "unverified", "no setting found is not the same as configured from the environment"
    assert clean["processes"]["status"] == "unverified" and clean["port_binding"]["status"] == "met"


def test_a_message_driven_bean_becomes_a_proposed_channel(ee):
    [ch] = cn.events(ee)
    assert (ch["id"], ch["destination"], ch["kind"], ch["consumer"]) == ("brokerqueue", "BrokerQueue", "queue", "BrokerMDB")
    msgs = {m["name"]: m for m in ch["messages"]}
    assert set(msgs) == {"neworder", "ping"}
    assert msgs["neworder"]["payload"] == [{"name": "command", "type": "string"}, {"name": "orderID", "type": "integer"},
                                           {"name": "publishTime", "type": "integer"}]
    assert msgs["neworder"]["producers"] == ["ejb/src/main/java/com/acme/Trade.java:5"]


def test_generated_files_are_labelled_and_never_carry_a_secret(tmp_path):
    src = _ee_app(tmp_path / "ee")
    cn._analyse.cache_clear()
    app = {"name": "acme", "language": "java", "version": "8", "framework": "ejb"}
    written = []
    for s in cn.SCAFFOLDS:
        files, summary = cn.generate(s, source=str(src), app=app, out=tmp_path / "out" / s)
        assert files and "unreviewed" in summary or s == "twelve_factor_report"
        written += files
    names = sorted(p.name for p in written)
    assert names == sorted(["Containerfile", "deployment.yaml", "config-findings.json", "env.mapping.json",
                            "config.env.example", "twelve-factor.json", "asyncapi.yaml", "event-contract.json"])
    for p in written:
        text = p.read_text()
        assert SECRET not in text, f"{p.name} carries a credential's value"
        assert "GENERATED" in text and "UNREVIEWED" in text
    cf = (tmp_path / "out/container_definition/Containerfile").read_text()
    assert "EXPOSE 9082" in cf and "USER 1001" in cf and "TRADE_AUTH_PASSWORD  (secret" in cf


def test_no_message_driven_bean_fails_with_why(tmp_path):
    cn._analyse.cache_clear()
    with pytest.raises(RuntimeError, match="no message-driven bean"):
        cn.generate("event_contract", source=str(_clean_app(tmp_path / "c")), app={"name": "c", "language": "java"},
                    out=tmp_path / "o")


def test_targets_are_proposed_or_not_addressed_never_reached():
    got = {t["target"]: t for t in cn.targets(
        {"cloud": ["containerised", "managed postgres", "stateless services", "Java 17"], "ai": ["demand forecast"]},
        {"container_definition", "deployment_manifest", "version_upgrade"}, ["Dockerfile"])}
    assert got["containerised"]["status"] == "generated" and "Dockerfile" in got["containerised"]["why"]
    assert got["Java 17"]["by"] == ["version_upgrade"]
    assert got["managed postgres"]["status"] == "not_addressed", "no database job ran"
    assert got["stateless services"]["factors"] == ["processes"]
    assert got["demand forecast"]["kind"] == "ai" and got["demand forecast"]["status"] == "not_addressed"
    assert not any("reached" == t["status"] for t in got.values())


# --- CAM ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def cam(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_CANVAS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_DB_PATH", str(tmp_path / "migration_canvas.db"))
    monkeypatch.setenv("CAM_ARTIFACTS_DIR", str(tmp_path / "cam"))
    cn._analyse.cache_clear()
    src = _ee_app(tmp_path / "src")
    from tools.migration_canvas import cam_refactor_engine as eng
    from tools.migration_canvas.db.init_db import get_connection, init_db
    init_db()
    c = get_connection()
    c.execute("INSERT INTO mc_srv_sessions (id, migration_type, tgt_platform) VALUES (%s, %s, %s)", ("s1", "application", ""))
    c.execute("INSERT INTO mc_projects (id, name, mc_session_id) VALUES (%s, %s, %s)", ("p1", "acme", "s1"))
    c.execute("INSERT INTO mc_app_inventory (id, session_id, name, version, language, framework, source_repo) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s)", ("lapp-1", "s1", "acme", "8", "java", "ejb", str(src)))
    c.commit()
    c.close()
    return eng


def test_the_cloud_native_rules_fire_only_as_their_set(cam):
    assert {j["refactor_type"] for j in cam.dispatch("p1", dry_run=True)} == {"version_upgrade", "framework_migration"}
    jobs = cam.dispatch("p1", rule_set="cloud_native")
    assert sorted(json.loads(j["params_json"])["scaffold_type"] for j in jobs) == sorted(cn.SCAFFOLDS)
    done = [cam.run_job(j["id"]) for j in jobs]
    assert all(d["status"] == "completed" for d in done), [d.get("error_message") for d in done]
    tf = next(d for d in done if "twelve factors" in d["result_summary"])
    assert "unverified" in tf["result_summary"]


def test_the_exporter_binds_every_generated_file_and_drafts_ai_use_cases(cam, tmp_path):
    for j in cam.dispatch("p1", rule_set="cloud_native"):
        cam.run_job(j["id"])
    rep = {"targets": {"cloud": ["containerised", "events instead of message-driven beans"], "ai": ["forecast"]},
           "jobs": [{"scaffold": json.loads(j["params_json"])["scaffold_type"], **j} for j in cam.get_jobs("p1")]}
    aiify = {"opportunities": [
        {"id": "o1", "pattern_type": "nested_conditionals", "module_path": "a/B.java", "ai_readiness": "partial", "composite_score": 0.5},
        {"id": "o2", "pattern_type": "nested_conditionals", "module_path": "a/C.java", "ai_readiness": "partial", "composite_score": 0.7},
        {"id": "o3", "pattern_type": "large_rule_table", "module_path": "a/D.java", "ai_readiness": "not_suitable", "composite_score": 0.9},
        {"id": "o4", "pattern_type": "regex_user_input", "module_path": "a/E.java", "ai_readiness": "partial", "composite_score": 0.6,
         "disqualifiers": ["handles credentials"]}]}
    sec = ex.cloud_native_from_jobs(rep, at="2026-11-14T00:00:00Z", app="acme", aiify=aiify)
    assert {a["kind"] for a in sec["artefacts"]} == set(ex.CLOUD_FILES.values())
    for a in sec["artefacts"]:
        assert a["review"] == "unreviewed" and not a["path"].startswith("/")
    lines = "".join(f"{a['sha256']}  {a['path']}\n" for a in sorted(sec["artefacts"], key=lambda a: a["path"]))
    assert sec["artefact_digest"] == hashlib.sha256(lines.encode()).hexdigest()
    cf = next(a for a in sec["artefacts"] if a["kind"] == "container_definition")
    job = next(j for j in rep["jobs"] if j["id"] == cf["job"])
    assert hashlib.sha256((Path(job["output_path"]) / "Containerfile").read_bytes()).hexdigest() == cf["sha256"]
    assert str(tmp_path) not in json.dumps(sec) and SECRET not in json.dumps(sec)
    assert [f["id"] for f in sec["factors"]] == list(cn.FACTORS)
    assert [e["id"] for e in sec["events"]] == ["brokerqueue"]
    [d] = sec["ai_use_cases"]
    assert (d["id"], d["status"], d["opportunities"]) == ("acme-nested-conditionals", "draft", ["o2", "o1"]), \
        "not suitable and disqualified opportunities are never drafted"
    assert {t["target"]: t["status"] for t in sec["targets"]} == {
        "containerised": "generated", "events instead of message-driven beans": "generated", "forecast": "not_addressed"}
    import jsonschema
    sch = ex.schema()
    jsonschema.validate(sec, {"$defs": sch["$defs"], "$ref": "#/$defs/CloudNative"})


def test_no_twelve_factor_report_no_section():
    assert ex.cloud_native_from_jobs({"jobs": [{"scaffold": "twelve_factor_report", "status": "failed"}]},
                                     at="2026-11-14T00:00:00Z", app="x") is None
