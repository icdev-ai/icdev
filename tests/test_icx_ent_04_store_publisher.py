# CUI // SP-CTI
"""Store publisher (icx-ent-04).

What the card promises, each clause a test here:

  * a packager prepares a signed ICDEV[OS] store package (agent, crew, skill, MCP server or plugin) that validates
    against ICDEV[OS]'s store-package and store-publication schemas, vendored and pinned by SHA-256;
  * its A2A Agent Card is JWS-signed (ES384 over RFC 8785 JCS) by the publisher's key from the OS keyring (a fake
    keyring backend here), binds the package's content digest (``icos.store/package_digest``), and its skills are
    exactly the package's declared tools; no key is refused, never unsigned;
  * ICDEV's seven marketplace scanner gates run on the package and ship as the publisher's own report; a gate that
    cannot run is unverified, never pass (and the scanner's SAST gate no longer passes when bandit cannot run);
  * the person who prepares it never publishes it: publishing is another person's declared step (icos-store-publish),
    the export carries the preparer and the publisher, and no agent prepares or publishes;
  * the ledger is append-only and hash-chained; visibility by org and marking; page, routes, twin and MCP.

Every person is a labelled test person and every key a THROWAWAY made here; every package is synthetic.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 11, 22, 9, 0, tzinfo=dt.timezone.utc)
ORG = "syn-ent04-org"
AGENT_PKG, MCP_PKG = "syn-ent04-ledger-agent", "syn-ent04-coverage-mcp"
PREPARER = {"id": "u-ent04-preparer", "role": "reviewer", "clearance_level": "CUI", "tenant_id": ORG}
PUBLISHER = {"id": "u-ent04-publisher", "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
OUTSIDER = {"id": "u-ent04-outsider", "role": "isso", "clearance_level": "CUI", "tenant_id": "another-org"}
UNCLEARED = {"id": "u-ent04-public", "role": "isso", "clearance_level": "PUBLIC", "tenant_id": ORG}
AGENT = {"id": "agent:ent04-helper", "role": "service"}


@pytest.fixture(autouse=True)
def people(monkeypatch):
    """The labelled test people as the dashboard's own user rows: current_reader reads a person's org and clearance
    FRESH from that row and denies when there is none (review L7)."""
    from tools.dashboard import auth

    rows = {u["id"]: dict(u) for u in (PREPARER, PUBLISHER, OUTSIDER, UNCLEARED)}
    real = auth.get_user_by_id
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid, tenant_id=None: rows.get(uid) or real(uid, tenant_id))
    return rows


def reader(u: dict) -> dict:
    return {"id": u["id"], "role": u["role"], "clearance": u["clearance_level"], "org": u["tenant_id"]}


def actor(u: dict) -> dict:
    return {"id": u["id"], "role": u["role"]}


def throwaway_pem(curve=None) -> bytes:
    """A THROWAWAY test key (P-384 unless told otherwise), made here and never stored."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(curve or ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


class FakeKeyring:
    """A stand-in for the OS keyring (the ``keyring`` package's get_password)."""

    def __init__(self, store: dict):
        self.store = store

    def get_password(self, service, name):
        return self.store.get((service, name))


@pytest.fixture()
def signer():
    from tools.store_publisher import card

    return card.open_signer("keyring:icdev-store-test/publisher",
                            keyring_backend=FakeKeyring({("icdev-store-test", "publisher"): throwaway_pem().decode()}))


@pytest.fixture()
def sp(wf_db, monkeypatch, signer):
    """The conftest platform DB, the labelled synthetic inputs under a private root inside the repo's git-ignored
    .tmp, and a configuration pointing at it (the shipped file is untouched). The publisher's configured key, as a site
    would set it in the keyring, is the test's throwaway signer."""
    from tools.store_publisher import config, service, synthetic

    rel = f".tmp/icx-ent-04-tests/{uuid.uuid4().hex[:12]}"
    cfg = config.load()
    cfg.pop("_digest")
    cfg["package_root"] = rel
    config.validate(cfg)
    cfg["_digest"] = "test"
    synthetic.write_all(cfg, now=NOW)
    monkeypatch.setattr(config, "load", lambda path=None: copy.deepcopy(cfg))
    monkeypatch.setattr(service, "signer_for", lambda cfg: signer)
    yield {"cfg": cfg, "db": wf_db, "root": config.BASE_DIR / rel}
    shutil.rmtree(config.BASE_DIR / rel, ignore_errors=True)


def _pkg(sp, pid=AGENT_PKG):
    from tools.store_publisher import config

    return copy.deepcopy(config.package(sp["cfg"], pid))


def _build(sp, signer, pid=AGENT_PKG, **kw):
    from tools.store_publisher import package

    return package.build(_pkg(sp, pid), sp["cfg"], signer=signer, version=kw.pop("version", "1.0.0"),
                         now=kw.pop("now", NOW), **kw)


def _prepare(sp, signer, who=PREPARER, pid=AGENT_PKG, **kw):
    from tools.store_publisher import service

    return service.prepare(pid, actor(who), reader(who), version=kw.pop("version", "1.0.0"), cfg=sp["cfg"],
                           signer=signer, now=kw.pop("now", NOW), **kw)


def _publish(sp, p, who=PUBLISHER, pid=AGENT_PKG, decision="publish", **kw):
    from tools.store_publisher import service

    return service.publish(pid, decision=decision, reason=kw.pop("reason", "read the package (test)"),
                           read_digest=kw.pop("digest", p["digest"]), actor=actor(who), reader=reader(who),
                           cfg=sp["cfg"], **kw)


def _conn():
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


# ── the contracts ────────────────────────────────────────────────────────────


def test_icos_store_contracts_are_vendored_verbatim_and_pinned(monkeypatch):
    from tools.store_publisher import package
    from tools.store_publisher.constants import (
        PUBLICATION_RELPATH,
        PUBLICATION_SHA256,
        SCHEMA_RELPATH,
        SCHEMA_SHA256,
        TEMPLATE,
    )

    assert hashlib.sha256((ROOT / SCHEMA_RELPATH).read_bytes()).hexdigest() == SCHEMA_SHA256
    assert hashlib.sha256((ROOT / PUBLICATION_RELPATH).read_bytes()).hexdigest() == PUBLICATION_SHA256
    pub = json.loads((ROOT / PUBLICATION_RELPATH).read_text())
    assert pub["properties"]["template"]["const"] == TEMPLATE == "icos-store-publish", \
        "the declared template carries the id ICDEV[OS] pins"
    assert json.loads((ROOT / SCHEMA_RELPATH).read_text())["$id"] == "urn:icdev-os:contract:store-package:1"
    monkeypatch.setattr(package, "SCHEMA_SHA256", "0" * 64)
    with pytest.raises(package.PackageRefused) as e:
        package.schema_errors({})
    assert e.value.code == "schema_unpinned", "a changed contract is refused, never used"


def test_the_declared_publish_template_is_icos_s_step_and_separates_the_preparer():
    from tools.workflow_hitl import declared

    t = declared.load_templates()["icos-store-publish"]
    assert declared.validate_template(t) == []
    assert t["decides"] == ["store_publisher.sp_api_publish"]
    stage = declared.manual_stage(t)
    assert stage["separate_from"] == "builders" and "service" not in stage["holders"]
    assert set(stage["reason_required"]) == {"publish", "refuse"}


# ── the key and the card ─────────────────────────────────────────────────────


def test_no_key_is_refused_and_only_a_keyring_p384_key_signs():
    from cryptography.hazmat.primitives.asymmetric import ec

    from tools.store_publisher import card

    for spec in (None, ""):
        with pytest.raises(card.CardError) as e:
            card.open_signer(spec)
        assert e.value.code == "no_key", "no key is refused, never an unsigned card"
    for spec in ("file:/tmp/k.pem", "kms:alias/x", "keyring:a/b/c", "keyring:a b/c"):
        with pytest.raises(card.CardError) as e:
            card.open_signer(spec)
        assert e.value.code == "bad_key", spec
    with pytest.raises(card.CardError) as e:
        card.open_signer("keyring:svc/name", keyring_backend=FakeKeyring({}))
    assert e.value.code == "no_key", "an empty keyring entry is no key"
    p256 = FakeKeyring({("svc", "name"): throwaway_pem(ec.SECP256R1()).decode()})
    with pytest.raises(card.CardError) as e:
        card.open_signer("keyring:svc/name", keyring_backend=p256)
    assert e.value.code == "bad_key", "ICDEV[OS] verifies ES384: only a P-384 key signs"
    pem = throwaway_pem()
    s = card.open_signer("keyring:svc/name", keyring_backend=FakeKeyring({("svc", "name"): pem.decode()}))
    assert "PRIVATE" not in repr(s) and pem.decode() not in repr(s), "the key is never printed"
    assert s.source == "keyring:svc/name" and len(s.sign(b"x")) == 96


def test_the_card_is_jws_es384_over_jcs_and_verifies_only_under_a_pinned_key(signer):
    from tools.store_publisher import card

    c = card.sign_card({"name": "syn", "version": "1.0.0", "skills": [], "metadata": {"k": ""},
                        "url": "", "jku": "https://attacker.invalid/keys"}, signer)
    spki = signer.spki()
    kid = card.kid(spki)
    hdr = json.loads(card.unb64u(c["signatures"][0]["protected"]))
    assert hdr == {"alg": "ES384", "typ": "JOSE", "kid": kid} and len(kid) == 16
    assert card.verify_card(c, {kid: spki}) == kid
    # default-valued fields are pruned from the signed payload (A2A 8.4.1), except inside metadata
    assert json.loads(card.payload(c)) == {"name": "syn", "version": "1.0.0", "metadata": {"k": ""},
                                           "jku": "https://attacker.invalid/keys"}
    assert card.jcs({"b": 1, "a": [1.5, "é", None, True]}) == '{"a":[1.5,"é",null,true],"b":1}'
    with pytest.raises(card.CardError):
        card.verify_card({**c, "version": "9.9.9"}, {kid: spki})
    other = card.PemSigner(throwaway_pem(), source="test:other")
    with pytest.raises(card.CardError):     # a key the card names is never trusted, only a pinned one
        card.verify_card(c, {kid: other.spki()})
    assert card.spki_from_jwk(card.jwk(spki)) == spki


# ── the package ──────────────────────────────────────────────────────────────


def test_a_package_meets_icos_contract_and_its_card_binds_its_content_and_tools(sp, signer):
    from tools.store_publisher import card, package
    from tools.store_publisher.constants import BINDING, SCANNER

    for pid in (AGENT_PKG, MCP_PKG):
        out = _build(sp, signer, pid)
        body = out["package"]
        assert package.schema_errors(body) == [], pid
        assert body["schema"] == "icos-store-package/v1" and body["publisher"] == {"id": "icdev-it",
                                                                                     "name": "ICDEV[IT] factory"}
        c = body["agent_card"]
        assert c["metadata"][BINDING] == package.package_digest(body), "the card binds the package's content"
        assert [s["id"] for s in c["skills"]] == body["tools"] == _pkg(sp, pid)["tools"]
        assert c["name"] == pid and c["version"] == "1.0.0"
        assert card.verify_card(c, {card.kid(signer.spki()): signer.spki()})
        assert body["content_digest"] == package.content_digest(body)
        assert set(body["scan"]) == set(SCANNER)
        assert body["dependencies"]["sbom_sha256"] == hashlib.sha256(
            json.dumps(body["sbom"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert package.self_check(body, signer.spki()) == []
    # a genuine card carried onto other content does not bind it
    moved = copy.deepcopy(body)
    moved["description"] = "SYNTHETIC: other content"
    moved["content_digest"] = package.content_digest(moved)
    assert "the card does not bind this package's content" in package.self_check(moved, signer.spki())
    # an MCP server declares exactly the tools its listing names
    bad = _pkg(sp, MCP_PKG)
    bad["tools"] = ["coverage_lookup", "undeclared_write"]
    with pytest.raises(package.PackageRefused) as e:
        package.build(bad, sp["cfg"], signer=signer, version="1.0.0", now=NOW)
    assert e.value.code == "tools_mismatch"
    with pytest.raises(package.PackageRefused) as e:
        package.build(_pkg(sp), sp["cfg"], signer=None, version="1.0.0", now=NOW)
    assert e.value.code == "no_key"


def test_every_kind_builds_a_package_that_meets_the_contract(sp, signer):
    """A skill, a crew and a plugin as well as an agent and an MCP server: each one's payload is its kind's, and each
    meets ICDEV[OS]'s pinned schema with its card bound."""
    from tools.store_publisher import config, package, synthetic

    base = _pkg(sp)
    extra = {
        "skill": dict(base, id="syn-ent04-summary-skill", kind="skill", tools=[], budget=None,
                      inputs="syn-ent04/summary-skill"),
        "crew": dict(base, id="syn-ent04-ledger-crew", kind="crew", inputs="syn-ent04/ledger-crew"),
        "plugin": dict(base, id="syn-ent04-ledger-plugin", kind="plugin", tools=[], budget=None,
                       inputs="syn-ent04/ledger-plugin", sources=["plugin.py"], distribution_sha256="e" * 64),
    }
    cfg = copy.deepcopy(sp["cfg"])
    cfg["packages"] += [extra[k] for k in ("skill", "crew", "plugin")]
    cfg.pop("_digest")
    config.validate(cfg)
    for k, p in extra.items():
        synthetic.write_package(sp["root"], p, now=NOW)
        out = package.build(p, cfg, signer=signer, version="1.0.0", now=NOW)["package"]
        assert package.schema_errors(out) == [] and package.self_check(out, signer.spki()) == [], k
        assert out["kind"] == k and set(out["payload"]) == {"skill": {"instructions"}, "crew": {"crew"},
                                                             "plugin": {"manifest", "distribution_sha256", "config",
                                                                        "sources"}}[k]
        assert out["scan"]["sast_scan"] == ("pass" if k == "plugin" else "not_applicable"), k
        assert [s["id"] for s in out["agent_card"]["skills"]] == out["tools"]


def test_the_seven_gates_run_on_the_package_and_a_gate_that_cannot_run_is_unverified(sp, signer, monkeypatch):
    from tools.marketplace import asset_scanner
    from tools.store_publisher import package

    agent = _build(sp, signer)["package"]["scan"]
    assert agent["sast_scan"] == "not_applicable", "an agent carries no code"
    assert {k for k, v in agent.items() if v != "pass"} == {"sast_scan"}
    mcp = _build(sp, signer, MCP_PKG)
    assert mcp["package"]["scan"]["sast_scan"] == "pass" and mcp["record"]["gates"]["sast_scan"]["engine"] == "bandit+ast"

    def boom(_path):
        raise RuntimeError("the analysis could not run")

    monkeypatch.setattr(asset_scanner, "sast_findings", boom)
    assert _build(sp, signer, MCP_PKG)["package"]["scan"]["sast_scan"] == "unverified", "never a pass"
    monkeypatch.undo()
    src = sp["root"] / _pkg(sp, MCP_PKG)["inputs"] / "src" / "server.py"
    src.write_text("import os\n\n\ndef handle(cmd):\n    return os.system(cmd)\n")
    assert _build(sp, signer, MCP_PKG)["package"]["scan"]["sast_scan"] == "fail"
    src.write_text("def broken(:\n")
    assert _build(sp, signer, MCP_PKG)["package"]["scan"]["sast_scan"] == "unverified", "unparsed is not analysed"

    inputs = sp["root"] / _pkg(sp)["inputs"]
    scan = json.loads((inputs / "dependency-scan.json").read_text())
    scan["findings"][0]["severity"] = "high"
    (inputs / "dependency-scan.json").write_text(json.dumps(scan))
    assert _build(sp, signer)["package"]["scan"]["dependency_audit"] == "fail"
    scan["findings"][0]["severity"] = None
    (inputs / "dependency-scan.json").write_text(json.dumps(scan))
    assert _build(sp, signer)["package"]["scan"]["dependency_audit"] == "unverified"
    scan["findings"] = []
    (inputs / "dependency-scan.json").write_text(json.dumps(scan))
    stale = _build(sp, signer, now=NOW + dt.timedelta(days=40))["record"]["gates"]["dependency_audit"]
    assert stale["status"] == "unverified" and "older than 30 days" in stale["reason"]

    (inputs / "prompt.md").write_text("SYNTHETIC prompt. password = 'hunter2hunter2'\n")
    assert _build(sp, signer)["package"]["scan"]["secret_detection"] == "fail"
    (inputs / "prompt.md").write_text("# CUI // SP-CTI\nSYNTHETIC prompt marked above the package.\n")
    assert _build(sp, signer)["package"]["scan"]["cui_marking_validation"] == "fail"
    # the report ships as claimed: every gate's status, nothing more
    out = _build(sp, signer)
    assert all(isinstance(v, str) for v in out["package"]["scan"].values())
    assert package.schema_errors(out["package"]) == []


def test_the_scanner_sast_gate_never_passes_when_bandit_cannot_run(tmp_path, monkeypatch):
    """asset_scanner ran ``python -m bandit`` from PATH and read an empty report as zero findings: a pass. Now an
    empty or unparseable report falls back to the AST checks, and a file nothing could parse is not a pass."""
    import subprocess

    from tools.marketplace import asset_scanner

    (tmp_path / "x.py").write_text("def run(s):\n    return eval(s)\n")

    class Empty:
        stdout, stderr, returncode = "", "No module named bandit", 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Empty())
    out = asset_scanner.sast_findings(tmp_path)
    assert out["engine"] == "ast" and out["status"] == "fail" and out["findings"]["critical"] == 1
    (tmp_path / "x.py").write_text("def broken(:\n")
    assert asset_scanner.sast_findings(tmp_path)["status"] == "unverified"


def test_inputs_are_read_only_under_the_root_never_through_a_symlink_or_a_hard_link(sp, signer, tmp_path):
    from tools.store_publisher import package

    inputs = sp["root"] / _pkg(sp)["inputs"]
    outside = tmp_path / "outside-prompt.md"
    outside.write_text("SYNTHETIC: a prompt from outside the root\n")
    (inputs / "prompt.md").unlink()
    (inputs / "prompt.md").symlink_to(outside)
    with pytest.raises(package.PackageRefused) as e:
        _build(sp, signer)
    assert e.value.code == "input" and "symlink" in e.value.message
    (inputs / "prompt.md").unlink()
    elsewhere = sp["root"] / "elsewhere-prompt.md"          # the same filesystem as the root, so a hard link exists
    elsewhere.write_text("SYNTHETIC: a second name for the same file\n")
    os.link(elsewhere, inputs / "prompt.md")
    with pytest.raises(package.PackageRefused) as e:
        _build(sp, signer)
    assert "one link" in e.value.message


def test_the_shipped_configuration_loads_with_no_key_and_fails_closed():
    from tools.store_publisher import config

    cfg = config.load()
    assert cfg["signing"]["key"] is None, "no key until the site sets one"
    assert {p["id"] for p in cfg["packages"]} == {AGENT_PKG, MCP_PKG}
    assert all(p["synthetic"] is True for p in cfg["packages"])
    base = copy.deepcopy(cfg)
    base.pop("_digest")

    def refused(edit):
        c = copy.deepcopy(base)
        edit(c)
        with pytest.raises(config.ConfigError):
            config.validate(c)

    refused(lambda c: c["packages"][0].update(marking_ceiling="cui"))       # needs an accreditation: the owner's
    refused(lambda c: c["packages"][0].update(egress_ceiling="internal", marking_ceiling="public"))
    refused(lambda c: c["packages"][0].update(id="Syn_Bad"))
    refused(lambda c: c["packages"][0].update(inputs="../outside"))
    refused(lambda c: c["packages"][0].update(budget=None))
    refused(lambda c: c["packages"][1].update(sources=[]))
    refused(lambda c: c.update(packager_roles=["admin", "service"]))
    refused(lambda c: c.update(unknown=1))
    refused(lambda c: c["signing"].update(key="file:/tmp/k.pem"))
    refused(lambda c: c["packages"][0]["provenance"].update(source="http://plain.invalid/x"))


def test_the_red_team_report_answers_every_id_and_no_time_is_in_the_future(sp, signer):
    from tools.store_publisher import package

    inputs = sp["root"] / _pkg(sp)["inputs"]
    rt = json.loads((inputs / "redteam.json").read_text())
    rt["results"].pop("ASI07")
    (inputs / "redteam.json").write_text(json.dumps(rt))
    with pytest.raises(package.PackageRefused) as e:
        _build(sp, signer)
    assert e.value.code == "redteam_incomplete"
    rt["results"]["ASI07"] = "failed"
    rt["produced_at"] = "2026-12-31T00:00:00Z"
    (inputs / "redteam.json").write_text(json.dumps(rt))
    with pytest.raises(package.PackageRefused) as e:
        _build(sp, signer)
    assert e.value.code == "future"
    rt["produced_at"] = "2026-11-20T00:00:00"
    (inputs / "redteam.json").write_text(json.dumps(rt))
    with pytest.raises(package.PackageRefused) as e:
        _build(sp, signer)
    assert e.value.code == "time", "a time without a zone is refused, never compared as a string"
    rt["produced_at"] = "2026-11-20T00:00:00Z"
    (inputs / "redteam.json").write_text(json.dumps(rt))
    out = _build(sp, signer)
    assert out["record"]["redteam"]["failed"] == ["ASI07"], "a failed id is the publisher's honest attestation"


# ── prepare, publish, export ─────────────────────────────────────────────────


def test_a_person_prepares_another_of_the_org_publishes_and_only_then_it_leaves(sp, signer):
    import jsonschema

    from tools.store_publisher import package, service
    from tools.store_publisher.constants import PUBLICATION_RELPATH

    p = _prepare(sp, signer)
    assert p["prepared_by"] == PREPARER["id"] and p["template"] == "icos-store-publish"
    with pytest.raises(service.Refused) as e:
        service.export(AGENT_PKG, reader(PUBLISHER), actor=actor(PUBLISHER))
    assert e.value.code == "unpublished", "a draft never leaves"
    with pytest.raises(service.Refused) as e:
        _publish(sp, p, who=PREPARER)
    assert e.value.code == "separation", "the preparer never publishes"
    with pytest.raises(service.Refused) as e:
        _publish(sp, p, digest="0" * 64)
    assert e.value.code == "digest"
    with pytest.raises(service.Refused) as e:
        _publish(sp, p, reason="")
    assert e.value.code == "reason"
    out = _publish(sp, p)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"]
    with pytest.raises(service.Refused) as e:
        _publish(sp, p)
    assert e.value.code == "already_decided"
    ex = service.export(AGENT_PKG, reader(PUBLISHER), actor=actor(PUBLISHER))
    assert set(ex) == {"bundle", "publication"}, "the shape ICDEV[OS]'s import reads: the publication beside it"
    pub, body = ex["publication"], ex["bundle"]
    assert pub["content_digest"] == p["digest"] == body["content_digest"] == package.content_digest(body)
    assert pub["signed_by"] == PUBLISHER["id"] and pub["prepared_by"] == PREPARER["id"]
    assert pub["template"] == "icos-store-publish" and pub["schema"] == "icos-store-publication/v1"
    schema = json.loads((ROOT / PUBLICATION_RELPATH).read_text())
    assert list(jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
                .iter_errors(pub)) == []
    assert package.schema_errors(body) == []
    # a new version is a new package and a new step
    p2 = _prepare(sp, signer, version="1.0.1")
    assert service.export(AGENT_PKG, reader(PUBLISHER), actor=actor(PUBLISHER))["bundle"]["version"] == "1.0.0"
    out = _publish(sp, p2, decision="refuse", reason="not this one (test)")
    assert out["event"] == "refused"
    assert service.export(AGENT_PKG, reader(PUBLISHER), actor=actor(PUBLISHER))["bundle"]["version"] == "1.0.0", "a refusal leaves nothing"


def test_an_agent_never_prepares_or_publishes(sp, signer):
    from tools.store_publisher import service

    for who in (AGENT, {"id": "bootstrap:seed", "role": "admin"}, {"id": "u-svc", "role": "service"}):
        with pytest.raises(service.Refused) as e:
            service.prepare(AGENT_PKG, who, reader(PREPARER), version="1.0.0", cfg=sp["cfg"], signer=signer)
        assert e.value.code == "agent_cannot_sign"
    p = _prepare(sp, signer)
    with pytest.raises(service.Refused) as e:
        service.publish(AGENT_PKG, decision="publish", reason="r", read_digest=p["digest"], actor=AGENT,
                        reader=service.mcp_reader("ent04-helper"), cfg=sp["cfg"])
    assert e.value.code == "agent_cannot_sign"


def test_only_a_packager_role_makes_the_publisher_key_sign(sp, signer):
    from tools.store_publisher import service

    pm = {"id": "u-ent04-pm", "role": "pm", "clearance_level": "CUI", "tenant_id": ORG}
    with pytest.raises(service.Refused) as e:
        _prepare(sp, signer, who=pm)
    assert e.value.code == "role"


def test_a_publication_counts_only_with_its_declared_step(sp, signer, monkeypatch):
    from tools.store_publisher import service
    from tools.workflow_hitl import declared

    p = _prepare(sp, signer)
    _publish(sp, p)
    real_events = declared._events

    def other_digest(sid, conn=None):
        ev = real_events(sid, conn)
        d = dict(ev["decided"])
        d["payload_json"] = json.dumps({**json.loads(d["payload_json"]), "content_digest": "1" * 64})
        return {**ev, "decided": d}

    monkeypatch.setattr(declared, "_events", other_digest)
    with pytest.raises(service.Refused) as e:
        service.export(AGENT_PKG, reader(PUBLISHER), actor=actor(PUBLISHER))
    assert e.value.code == "step_missing", "a step that signed another digest publishes nothing"
    monkeypatch.setattr(declared, "_events", real_events)
    monkeypatch.setattr(declared, "step_state", lambda sid: None)
    with pytest.raises(service.Refused) as e:
        service.export(AGENT_PKG, reader(PUBLISHER), actor=actor(PUBLISHER))
    assert e.value.code == "step_missing"

    def down(sid):
        raise RuntimeError("store down")

    monkeypatch.setattr(declared, "step_state", down)
    with pytest.raises(service.Refused) as e:
        service.export(AGENT_PKG, reader(PUBLISHER), actor=actor(PUBLISHER))
    assert e.value.code == "step_missing", "an unreadable step store publishes nothing"


def test_a_package_is_published_only_under_the_publisher_s_current_key(sp, signer):
    from tools.store_publisher import card, service

    p = _prepare(sp, signer)
    rotated = card.PemSigner(throwaway_pem(), source="test:rotated")
    with pytest.raises(service.Refused) as e:
        _publish(sp, p, signer=rotated)
    assert e.value.code == "key_mismatch"
    cfg = copy.deepcopy(sp["cfg"])
    cfg["signing"]["kid"] = card.kid(rotated.spki())
    with pytest.raises(service.Refused) as e:
        service.prepare(AGENT_PKG, actor(PREPARER), reader(PREPARER), version="1.0.1", cfg=cfg, signer=signer, now=NOW)
    assert e.value.code == "key_mismatch", "a key ICDEV[OS] does not pin never signs a card"


def test_what_a_reader_may_not_see_is_no_such_package_byte_for_byte(sp, signer):
    from tools.store_publisher import service

    _prepare(sp, signer)
    outs = []
    for who, pid in ((OUTSIDER, AGENT_PKG), (UNCLEARED, AGENT_PKG), (PUBLISHER, "no-such-package")):
        with pytest.raises(service.Refused) as e:
            service.package_view(pid, reader(who))
        outs.append((e.value.status, e.value.message, e.value.code))
        with pytest.raises(service.Refused) as e:
            service.prepare(pid, actor(who), reader(who), version="1.0.0", cfg=sp["cfg"], signer=signer)
        outs.append((e.value.status, e.value.message, e.value.code))
        with pytest.raises(service.Refused) as e:
            service.export(pid, reader(who), actor=actor(who))
        outs.append((e.value.status, e.value.message, e.value.code))
    assert len(set(outs)) == 1 and outs[0] == (404, "no such package", "not_found")
    v = service.packages_view(reader(OUTSIDER))
    assert v["packages"] == [] and v["held_back"] == 2 and "syn-ent04" not in json.dumps(v["packages"])
    agent = service.packages_view(service.mcp_reader("ent04"))
    assert {x["id"] for x in agent["packages"]} == {AGENT_PKG, MCP_PKG}
    customer = copy.deepcopy(sp["cfg"])
    for x in customer["packages"]:
        x["synthetic"] = False
    assert service.packages_view(service.mcp_reader("ent04"), cfg=customer)["packages"] == [], \
        "an agent is in no org: it sees no customer's package"


def test_the_ledger_is_append_only_and_hash_chained(sp, signer):
    from tools.store_publisher import ledger, service

    p = _prepare(sp, signer)
    c = _conn()
    for sql in ("UPDATE sp_events SET reason = 'x'", "DELETE FROM sp_events"):
        with pytest.raises(Exception, match="append-only"):
            c.execute(sql)
    c.close()
    rows = ledger.read()
    assert ledger.chain(rows)["_problems"] == []
    forged = [dict(rows[0], reason="rewritten")]
    assert ledger.chain(forged)["_problems"][0]["problem"] == "row hash does not match its content"
    body = json.loads(rows[0]["payload_json"])
    body["package"]["description"] = "swapped after it was prepared"
    bad = dict(rows[0], payload_json=json.dumps(body))
    bad["row_hash"] = ledger._row_hash(bad)
    assert ledger.chain([bad])["_problems"][0]["problem"] == "the recorded package does not match its digest"
    assert service.packages_view(reader(PUBLISHER))["packages"][0]["prepared"]["content_digest"] == p["digest"]


# ── the console, the twin and MCP ────────────────────────────────────────────


def _page_app(user):
    from tools.dashboard.app import app as dash
    from tools.security.canvas_access import grant_access
    from tools.store_publisher.blueprint import sp_bp

    user = dict(user, impact_level="IL5")
    grant_access(user["tenant_id"], "user", user["id"], "store_publisher", "read", granted_by="test-admin")
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
        g.security_context = {"tenant_id": user["tenant_id"], "user_id": user["id"], "classification": "CUI"}

    app.register_blueprint(sp_bp)
    return app.test_client()


def test_the_routes_and_the_page(sp, signer):
    a, b, o = _page_app(PREPARER), _page_app(PUBLISHER), _page_app(OUTSIDER)
    r = a.post(f"/store-publisher/api/packages/{AGENT_PKG}/prepare", json={"version": "1.0.0"})
    assert r.status_code == 201, r.get_data(as_text=True)
    p = r.get_json()
    html = b.get(f"/store-publisher/?package={AGENT_PKG}").get_data(as_text=True)
    for want in ("icos-store-publish", "icos-store-package/v1", "store_publisher.sp_api_publish",
                 "store_publisher.sp_api_prepare", f'data-package="{AGENT_PKG}"', p["digest"], "verified now",
                 'data-gate="sast_scan"', "not applicable", "never passed", "ES384"):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/store_publisher/index.html").read_text(encoding="utf-8")
    assert o.get(f"/store-publisher/api/packages/{AGENT_PKG}").status_code == 404
    r = a.post(f"/store-publisher/api/packages/{AGENT_PKG}/publish",
               json={"decision": "publish", "reason": "mine", "digest": p["digest"]})
    assert r.status_code == 403 and r.get_json()["code"] == "separation"
    r = b.post(f"/store-publisher/api/packages/{AGENT_PKG}/publish",
               json={"decision": "publish", "reason": "read it (test)", "digest": p["digest"]})
    assert r.status_code == 200, r.get_data(as_text=True)
    ex = b.get(f"/store-publisher/api/packages/{AGENT_PKG}/export").get_json()
    assert ex["publication"]["signed_by"] == PUBLISHER["id"] and ex["publication"]["prepared_by"] == PREPARER["id"]
    assert o.get(f"/store-publisher/api/packages/{AGENT_PKG}/export").status_code == 404
    lst = o.get("/store-publisher/api/packages").get_json()
    assert lst["packages"] == [] and lst["held_back"] == 2


def test_the_twin_observes_with_threads_and_replays(sp, signer, monkeypatch):
    import tools.twin_core.adapters.store_publisher  # noqa: F401
    from tools.twin_core import governance

    adapter = governance.adapter_for("store_publisher")
    assert adapter.observes == ("store_publisher.signed_packages",)
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["store_publisher.ledger"]["status"] == "verified"
    assert chk[f"store_publisher.{AGENT_PKG}"]["status"] == "unverified", "nothing published is never a pass"
    inputs = sp["root"] / _pkg(sp)["inputs"]
    rt = json.loads((inputs / "redteam.json").read_text())
    rt["results"]["ASI07"] = "failed"
    (inputs / "redteam.json").write_text(json.dumps(rt))
    p = _prepare(sp, signer)
    pub = _publish(sp, p)
    env2 = governance.observe(adapter)
    chk = {x["id"]: x for x in env2["extra"]["checks"]}
    assert chk[f"store_publisher.{AGENT_PKG}"]["status"] == "verified"
    f = [v for v in env2["violations"] if v["rule_id"] == "store_publisher.redteam_not_passed"]
    assert f and f[0]["thread_id"] == f"store_publisher:{pub['event_id']}"
    # review L3: the generic resolver (any observer) does not resolve a store package's row, and a real event id reads
    # exactly as one that does not exist; only the console's reader-filtered route resolves it
    real = governance.resolve_thread(f"store_publisher:{pub['event_id']}")
    fake = governance.resolve_thread("store_publisher:spe-" + "0" * 16)
    assert not real["resolved"] and {k: v for k, v in real.items() if k != "thread_id"} == \
        {k: v for k, v in fake.items() if k != "thread_id"}
    assert governance.replay(adapter, env2["extra"]["observation_id"])["match"]
    b, o = _page_app(PUBLISHER), _page_app(OUTSIDER)
    th = {"thread": f"store_publisher:{pub['event_id']}"}
    assert b.get("/store-publisher/api/twin", query_string=th).get_json()["record"]["actor"] == PUBLISHER["id"]
    r = o.get("/store-publisher/api/twin", query_string=th)
    assert r.status_code == 404 and r.get_json()["reason"] == "no such thread"
    mine = b.get("/store-publisher/api/twin").get_json()
    assert mine["scope"] == "reader" and mine["summary"]["published"] == 1


def test_the_mcp_tool_only_reads(sp, signer):
    from tools.mcp.gap_handlers import handle_store_publisher_status
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert "store_publisher_status" in set(list_tools())
    assert READ_ONLY_DECLARATIONS["store_publisher_status"] is True
    assert tool_authorization("store_publisher_status")["tier"] == "read"
    p = _prepare(sp, signer)
    out = handle_store_publisher_status({"package": AGENT_PKG})
    assert out["package"]["build"]["digest"] == p["digest"]
    assert "export" not in handle_store_publisher_status({"package": AGENT_PKG, "export": True}), \
        "an agent never takes the full bundle out (review L4)"
    assert handle_store_publisher_status({"package": "nope"}) == {"error": "no such package", "code": "not_found",
                                                                   "refused": True}


def test_no_environment_variable_or_key_file_gives_the_product_a_key(wf_db, monkeypatch):
    """Review L2: the e2e path that let a PEM under tests/e2e/fixtures sign whenever signing.key was null is gone. The
    product signs only with the configured keyring key; the e2e seed signs in its own process, in memory."""
    from tools.store_publisher import config, service

    cfg = config.load()
    key = ROOT / "tests" / "e2e" / "fixtures" / f".ent04-unit-{uuid.uuid4().hex[:8]}.pem"
    key.write_bytes(throwaway_pem())
    try:
        monkeypatch.setenv("ICDEV_STORE_PUBLISHER_E2E_KEY", str(key))
        with pytest.raises(service.Refused) as e:
            service.signer_for(cfg)
        assert e.value.code == "no_key"
        assert service.packages_view(reader(PUBLISHER), cfg=cfg)["signing"]["configured"] is False
        assert "E2E" not in (ROOT / "tools/store_publisher/service.py").read_text()
    finally:
        key.unlink()


def test_a_tenant_person_s_canvas_grant_is_read_inside_their_own_request(wf_db):
    """The page's POST from a signed-in person's session reached canvas_access.check_access under the row-security
    predicate of that person's context, which names a classification column canvas_access_grants does not have: the
    query raised and the person was refused (403) every write on a granted canvas. icx-ent-02 and icx-ent-03 found and
    fixed it on their branches (unmerged); this card's e2e met it on main and carries the same change, byte for byte.
    The grants are read without the label predicate; each query names its tenant."""
    from tools.db import storage
    from tools.security import canvas_access

    canvas_access.grant_access(ORG, "user", "u-ent04-granted", "store_publisher", "read", granted_by="test-admin")
    real = storage.get_connection

    def predicated(*a, **k):
        conn = real(*a, **k)
        conn.set_security_context(type("Ctx", (), {"tenant_id": None, "classification": "CUI",
                                                   "compartments": frozenset()})())
        return conn

    storage.get_connection = predicated
    try:
        assert canvas_access.check_access("u-ent04-granted", ORG, "store_publisher", "read", "reviewer")
        assert not canvas_access.check_access("u-ent04-granted", "another-org", "store_publisher", "read",
                                              "reviewer"), "the tenant still scopes the grant"
    finally:
        storage.get_connection = real


# ── the independent review's findings (each a fail-closed test) ─────────────


def _src(sp, rel: str, text: str) -> None:
    d = sp["root"] / _pkg(sp, MCP_PKG)["inputs"] / "src" / rel
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(text)


def _with_sources(sp, *srcs):
    p = _pkg(sp, MCP_PKG)
    p["sources"] = list(srcs)
    return p


def test_m1_the_analysed_code_cannot_switch_the_sast_gate_off(sp, signer):
    """``# nosec``, a ``.bandit`` ini the sources carry, and a directory bandit skips by default each hid a finding."""
    from tools.store_publisher import config, package

    _src(sp, "server.py", "import os\n\n\ndef handle(cmd):\n    return os.system(cmd)  # nosec\n")
    out = package.build(_pkg(sp, MCP_PKG), sp["cfg"], signer=signer, version="1.0.0", now=NOW)
    assert out["package"]["scan"]["sast_scan"] == "fail", "a # nosec comment is the author's, never the gate's"
    from tools.marketplace.asset_scanner import sast_findings

    d = sp["root"] / "m1"
    (d / "x").mkdir(parents=True)
    (d / ".tox").mkdir()
    (d / "a.py").write_text("print('fine')\n")
    (d / "x" / ".bandit").write_text("[bandit]\nskips=B102,B307,B605\n")
    (d / "x" / "c.py").write_text("exec(input())\n")
    (d / ".tox" / "b.py").write_text("eval(input())\n")
    r = sast_findings(d)
    assert r["status"] == "fail" and r["findings"]["unanalysed"] == []
    ids = {i.get("test_id") for i in r["findings"]["issues"]}
    assert {"B102", "B307"} <= ids, "the .bandit ini in the sources is never read, and .tox is analysed"
    cfg = copy.deepcopy(sp["cfg"])
    cfg.pop("_digest")
    for bad in (".bandit", "x/.bandit", ".tox/b.py"):
        cfg["packages"][1]["sources"] = ["server.py", bad]
        with pytest.raises(config.ConfigError):
            config.validate(cfg)


def test_m1_a_python_file_bandit_did_not_analyse_is_unverified_and_named(sp, monkeypatch):
    from tools.marketplace import asset_scanner

    d = sp["root"] / "m1b"
    d.mkdir(parents=True)
    (d / "a.py").write_text("print('a')\n")
    (d / "b.py").write_text("print('b')\n")
    real = asset_scanner._bandit

    def partial(path):
        out, analysed = real(path)
        return out, {x for x in analysed if not x.endswith("b.py")}

    monkeypatch.setattr(asset_scanner, "_bandit", partial)
    r = asset_scanner.sast_findings(d)
    assert r["status"] == "unverified" and r["findings"]["unanalysed"] == ["b.py"]


def test_m2_a_source_that_is_not_python_is_never_analysed_so_never_passes(sp, signer):
    from tools.store_publisher import package

    _src(sp, "helper.js", "require('child_process').exec(process.argv[2]);\n")
    _src(sp, "run.sh", "#!/bin/sh\nexec \"$@\"\n")
    out = package.build(_with_sources(sp, "server.py", "helper.js", "run.sh"), sp["cfg"], signer=signer,
                        version="1.0.0", now=NOW)
    g = out["record"]["gates"]["sast_scan"]
    assert g["status"] == "unverified" and g["not_analysed"] == ["helper.js", "run.sh"]


def test_m3_the_retired_pipeline_s_effect_is_not_reachable_another_way():
    from tools.marketplace import catalog_manager, federation_sync
    from tools.marketplace.superseded import Superseded
    from tools.mcp import marketplace_server

    with pytest.raises(Superseded):
        catalog_manager.update_status("asset-x", "published")
    with pytest.raises(Superseded):
        catalog_manager.promote_to_central("asset-x")
    with pytest.raises(Superseded):
        federation_sync.promote_approved("tenant-x")
    out = marketplace_server.handle_openclaw_promote({"import_id": "imp-x", "promoted_by": "anyone"})
    assert out["code"] == "superseded", "no caller-named person promotes through MCP"
    import yaml

    stages = yaml.safe_load((ROOT / "args/tool_curation.yaml").read_text())["goals"]["marketplace_publish"]["phases"]
    assert not any(t in ("catalog_manager", "publish_pipeline", "review_queue")
                   for ph in stages.values() for t in ph.get("tools") or [] if ph is not stages["scan"])


def test_m4_one_rfc_8785_copy_serves_the_card_and_the_portfolio_decisions():
    from tools.common import jcs
    from tools.modernization import icos_decisions
    from tools.store_publisher import card

    doc = {"b": [1, 1.5, 1e21, -0.0, "é\u2028\n"], "a": {"z": None, "é": True}, "€": 2 ** 53}
    assert card.jcs(doc) == icos_decisions.jcs(doc) == jcs.jcs(doc)
    assert icos_decisions._jcs is jcs and card._jcs is jcs
    with pytest.raises(icos_decisions.Untrusted):
        icos_decisions.jcs({"x": float("nan")})
    with pytest.raises(card.CardError):
        card.jcs({"x": 2 ** 60})
    for mod in ("tools/store_publisher/card.py", "tools/modernization/icos_decisions.py"):
        text = (ROOT / mod).read_text()
        assert "def _num(" not in text.replace("def _num(x) -> str:\n    return _jcs.num", ""), mod


def test_l1_the_red_team_report_is_read_once(sp, signer, monkeypatch):
    from tools.store_publisher import config

    reads = []
    real = config.read_input
    monkeypatch.setattr(config, "read_input", lambda cfg, p, rel: (reads.append(rel), real(cfg, p, rel))[1])
    _build(sp, signer)
    assert reads.count("redteam.json") == 1, "the bytes hashed are the bytes parsed"


def test_l4_only_a_person_who_packages_or_publishes_takes_the_bundle_out(sp, signer):
    from tools.store_publisher import service

    _publish(sp, _prepare(sp, signer))
    pm = {"id": "u-ent04-pm", "role": "pm", "clearance_level": "CUI", "tenant_id": ORG}
    for who, code in ((None, "no_person"), (AGENT, "agent_cannot_sign"), ({"id": "u-svc", "role": "service"},
                                                                            "agent_cannot_sign"),
                      ({"id": "bootstrap:x", "role": "admin"}, "agent_cannot_sign"), (actor(pm), "role")):
        with pytest.raises(service.Refused) as e:
            service.export(AGENT_PKG, reader(PUBLISHER), actor=who)
        assert e.value.code == code, who
    assert service.export(AGENT_PKG, reader(PREPARER), actor=actor(PREPARER))["publication"]["prepared_by"] == \
        PREPARER["id"]


def test_l5_the_signed_step_names_the_package_s_org(sp, signer):
    from tools.store_publisher import ledger, service
    from tools.workflow_hitl import declared

    out = _publish(sp, _prepare(sp, signer))
    signed = json.loads(declared._events(out["step_id"])["decided"]["payload_json"])
    assert signed["org"] == ORG
    pub = service.replay(ledger.chain(ledger.read())[f"package:{AGENT_PKG}"])["published"]
    assert service.step_ok(pub, ORG) and not service.step_ok(pub, "another-org")


def test_l6_a_lost_ledger_write_voids_the_decided_step(sp, signer, monkeypatch):
    from tools.store_publisher import ledger, service
    from tools.workflow_hitl import declared

    p = _prepare(sp, signer)
    real = ledger.append
    steps = []

    def lost(conn, **kw):
        if kw.get("event") == "published":
            steps.append(kw["step_id"])
            raise ledger.Refused(503, "store_unavailable", "the ledger could not be written")
        return real(conn, **kw)

    monkeypatch.setattr(ledger, "append", lost)
    with pytest.raises(service.Refused) as e:
        _publish(sp, p)
    assert e.value.code == "not_recorded"
    assert declared.step_state(steps[0])["status"] == "withdrawn", "no decision surface shows it as published"
    assert [x["status"] for x in declared.list_steps() if x.get("step_id") == steps[0]] in ([], ["withdrawn"])
    monkeypatch.setattr(ledger, "append", real)
    assert _publish(sp, p)["event"] == "published", "the package may be decided again"


def test_l7_no_fresh_person_row_is_no_reader(monkeypatch):
    from tools.dashboard import auth
    from tools.store_publisher import service

    app = Flask(__name__)
    with app.test_request_context():
        g.current_user = {"id": "u-ent04-svc", "role": "service", "tenant_id": ORG, "clearance_level": "CUI"}
        assert service.current_reader() is None, "a service key is no reader of a tenant's packages"

        def down(uid):
            raise RuntimeError("users table unreadable")

        monkeypatch.setattr(auth, "get_user_by_id", down)
        g.current_user = {"id": "u-ent04-x", "role": "isso", "tenant_id": ORG, "clearance_level": "CUI"}
        assert service.current_reader() is None, "what the request carried is never trusted instead"
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: None)
        assert service.current_reader() is None


def test_l8_a_symlinked_root_or_directory_is_never_written_or_read_through(sp, signer, tmp_path):
    from tools.common.confined_read import ConfinedError, read_confined
    from tools.store_publisher import config, synthetic

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "prompt.md").write_text("SYNTHETIC outside\n")
    link = sp["root"] / "linked-root"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfinedError):
        read_confined(link, "prompt.md", max_bytes=1024)
    cfg = copy.deepcopy(sp["cfg"])
    cfg["package_root"] = f"{sp['cfg']['package_root']}/linked-root"
    with pytest.raises(config.ConfigError):
        config.root(cfg)
    p = _pkg(sp)
    p["inputs"] = "linked-root/agent"
    with pytest.raises(OSError):
        synthetic.write_package(sp["root"], p, now=NOW)
    assert list(outside.iterdir()) == [outside / "prompt.md"], "nothing was written through the symlink"



def test_publishing_checks_the_key_by_public_identity_and_says_when_it_cannot(sp, signer):
    """Publishing signs nothing: the card's key is compared with signing.kid (what ICDEV[OS] pins) or the configured key.
    With neither configured, its currency is unverified -- recorded and shown by the twin, never a pass."""
    import tools.twin_core.adapters.store_publisher  # noqa: F401
    from tools.store_publisher import card, ledger, service
    from tools.twin_core import governance

    _publish(sp, _prepare(sp, signer))
    pub = service.replay(ledger.chain(ledger.read())[f"package:{AGENT_PKG}"])["published"]
    assert pub["key_currency"] == "unverified"
    env = governance.observe(governance.adapter_for("store_publisher"), record=False)
    assert any(v["rule_id"] == "store_publisher.key_unverified" for v in env["violations"])
    pinned = copy.deepcopy(sp["cfg"])
    pinned["signing"]["kid"] = card.kid(signer.spki())
    p2 = service.prepare(AGENT_PKG, actor(PREPARER), reader(PREPARER), version="1.0.1", cfg=pinned, signer=signer,
                         now=NOW)
    service.publish(AGENT_PKG, decision="publish", reason="pinned (test)", read_digest=p2["digest"],
                    actor=actor(PUBLISHER), reader=reader(PUBLISHER), cfg=pinned)
    pub = service.replay(ledger.chain(ledger.read())[f"package:{AGENT_PKG}"])["published"]
    assert pub["key_currency"] == "verified"
    p3 = service.prepare(AGENT_PKG, actor(PREPARER), reader(PREPARER), version="1.0.2", cfg=pinned, signer=signer,
                         now=NOW)
    rotated = copy.deepcopy(pinned)
    rotated["signing"]["kid"] = "0" * 16
    with pytest.raises(service.Refused) as e:
        service.publish(AGENT_PKG, decision="publish", reason="rotated (test)", read_digest=p3["digest"],
                        actor=actor(PUBLISHER), reader=reader(PUBLISHER), cfg=rotated)
    assert e.value.code == "key_mismatch"
