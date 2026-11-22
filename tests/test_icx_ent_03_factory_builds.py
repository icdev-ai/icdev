# CUI // SP-CTI
"""Signed factory builds (icx-ent-03).

What the card promises, each clause a test here:

  * KMS or keyless signing: one artefact key module (tools/crypto/artefact_signer.py) for a KMS key (a fake client
    here, never AWS), the OS keyring (a fake backend) or a throwaway test file; no key is refused, never a MAC; keyless
    only with a CONFIGURED OIDC issuer and Fulcio (a loopback stub with a throwaway CA here); the transparency log off
    by default and recorded so, a configured Rekor's answer recorded as a receipt;
  * per built artefact, SLSA v1.2 provenance and a CycloneDX 1.7 SBOM (an AI-BOM for a model) valid against the
    pinned schemas, an OpenVEX statement per scanned vulnerability, the Sigstore bundles, OCI referrers, ICDEV[OS]'s
    pinned icos-artefact/v1 manifest, and a build that does not verify under its own key never recorded;
  * AI-BOMs linked to the fine-tuning registry: only a promoted version is built, the promotion log row it rests on is
    named (an override is said to be one), a demotion since blocks its publication; weights signed with OpenSSF
    model-signing over files read only under the reviewed root;
  * delivered to ICDEV[OS]: prepared by a person (never an agent: an agent never makes the key sign), published by
    another of the target's org (decl-wft-fb-build-publish), exported only then with its publication beside it, and
    only while its declared step is recorded;
  * the ledger append-only and hash-chained; visibility by org and marking; page, routes, twin, IQE and MCP.

Every person is a labelled test person and every key a throwaway made here; every target is synthetic.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-11-21T09:00:00Z"
ORG = "syn-ent03-org"
COMMIT = "e03" + "0" * 37
PREPARER = {"id": "u-ent03-preparer", "role": "reviewer", "clearance_level": "CUI", "tenant_id": ORG}
PUBLISHER = {"id": "u-ent03-publisher", "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
OUTSIDER = {"id": "u-ent03-outsider", "role": "isso", "clearance_level": "CUI", "tenant_id": "another-org"}
UNCLEARED = {"id": "u-ent03-public", "role": "isso", "clearance_level": "PUBLIC", "tenant_id": ORG}
AGENT = {"id": "agent:ent03-helper", "role": "service"}


def reader(u: dict) -> dict:
    return {"id": u["id"], "role": u["role"], "clearance": u["clearance_level"], "org": u["tenant_id"]}


def actor(u: dict) -> dict:
    return {"id": u["id"], "role": u["role"]}


# ── keys and the database ────────────────────────────────────────────────────


def throwaway_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


@pytest.fixture()
def signer(tmp_path):
    from tools.crypto import artefact_signer as asig

    p = tmp_path / "factory-test.pem"
    p.write_bytes(throwaway_pem())
    return asig.open_signer(f"file:{p}")


def _ft_ddl() -> list[str]:
    from tools.db.init_icdev_db import SCHEMA_SQL

    out = []
    for t in ("ft_datasets", "ft_training_jobs", "ft_model_versions", "ft_promotion_log"):
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {t} \(.*?\n\);", SCHEMA_SQL, re.DOTALL)
        assert m, t
        out.append(m.group(0))
    return out


@pytest.fixture()
def fb(wf_db, monkeypatch, signer):
    """The conftest platform DB with the fine-tuning registry tables, synthetic inputs under a private root inside the
    repo's git-ignored .tmp, and a configuration pointing at it (the shipped file is untouched)."""
    from tools.db.storage import get_canvas_connection
    from tools.factory_builds import config, synthetic

    conn = get_canvas_connection()
    for ddl in _ft_ddl():
        conn.execute(ddl)
    conn.commit()
    rel = f".tmp/icx-ent-03-tests/{uuid.uuid4().hex[:12]}"
    cfg = config.load()
    cfg["artefact_root"], cfg["models_root"] = rel, f"{rel}/models"
    cfg.pop("_digest")
    config.validate(cfg)
    cfg["_digest"] = "test"
    out = synthetic.write_all(cfg, conn)
    conn.close()
    monkeypatch.setattr(config, "load", lambda path=None: copy.deepcopy(cfg))
    from tools.factory_builds import service

    # The factory's configured key, as a site would set it, is the test's throwaway signer.
    monkeypatch.setattr(service, "signer_for", lambda cfg: signer)
    yield {"cfg": cfg, "db": wf_db, "root": config.BASE_DIR / rel, "inputs": out}
    shutil.rmtree(config.BASE_DIR / rel, ignore_errors=True)


def _target(fb, tid="syn-ent03-portal"):
    from tools.factory_builds import config

    return copy.deepcopy(config.target(fb["cfg"], tid))


def _build(fb, signer, tid="syn-ent03-portal", **kw):
    from tools.factory_builds import build

    return build.build(_target(fb, tid), fb["cfg"], signer=signer, version=kw.pop("version", "1.0.0"),
                       at=kw.pop("at", AT), commit=kw.pop("commit", COMMIT), **kw)


def _prepare(fb, signer, who=PREPARER, tid="syn-ent03-portal", **kw):
    from tools.factory_builds import service

    return service.prepare(tid, actor(who), reader(who), version=kw.pop("version", "1.0.0"), cfg=fb["cfg"],
                           signer=signer, now=dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc), commit=COMMIT,
                           **kw)


def _publish(fb, p, who=PUBLISHER, tid="syn-ent03-portal", decision="publish", **kw):
    from tools.factory_builds import service

    return service.publish(tid, decision=decision, reason=kw.pop("reason", "read the build (test)"),
                           read_digest=kw.pop("digest", p["digest"]), actor=actor(who), reader=reader(who),
                           cfg=fb["cfg"], **kw)


# ── the contracts ────────────────────────────────────────────────────────────


def test_icos_contracts_are_vendored_verbatim_and_pinned(monkeypatch):
    import yaml

    from tools.factory_builds import build
    from tools.factory_builds.constants import PUBLICATION_RELPATH, PUBLICATION_SHA256, SCHEMA_RELPATH, SCHEMA_SHA256

    assert hashlib.sha256((ROOT / SCHEMA_RELPATH).read_bytes()).hexdigest() == SCHEMA_SHA256
    assert hashlib.sha256((ROOT / PUBLICATION_RELPATH).read_bytes()).hexdigest() == PUBLICATION_SHA256
    for d, name in (("context/standards/cyclonedx-1.7", "bom-1.7.schema.json"),
                    ("context/standards/openvex/0.2.0", "openvex_json_schema.json")):
        pin = yaml.safe_load((ROOT / d / "SOURCE.yaml").read_text())["files"][name]["sha256"]
        assert hashlib.sha256((ROOT / d / name).read_bytes()).hexdigest() == pin
    monkeypatch.setattr(build, "SCHEMA_SHA256", "0" * 64)
    with pytest.raises(build.BuildRefused) as e:
        build.artefact_errors({})
    assert e.value.code == "schema_unpinned", "a changed contract is refused, never used"


# ── the key ──────────────────────────────────────────────────────────────────


def test_no_key_is_refused_never_a_mac_and_a_test_key_lives_only_in_tmp(tmp_path):
    from tools.crypto import artefact_signer as asig

    for spec in (None, "", "  "):
        with pytest.raises(asig.SigningError) as e:
            asig.open_signer(spec)
        assert e.value.code == "no_key"
    with pytest.raises(asig.SigningError):
        asig.open_signer("hmac:secret")
    outside = ROOT / ".tmp" / f"ent03-{uuid.uuid4().hex[:8]}.pem"
    outside.parent.mkdir(exist_ok=True)
    outside.write_bytes(throwaway_pem())
    try:
        with pytest.raises(asig.SigningError, match="THROWAWAY"):
            asig.open_signer(f"file:{outside}")
    finally:
        outside.unlink()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    rsa_like = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    p = tmp_path / "p384.pem"
    p.write_bytes(rsa_like)
    with pytest.raises(asig.SigningError, match="P-256"):
        asig.open_signer(f"file:{p}")


def test_a_keyring_key_and_a_kms_key_sign_and_every_signature_is_verified_first():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from tools.crypto import artefact_signer as asig

    pem = throwaway_pem()

    class Ring:
        def get_password(self, service, name):
            return pem.decode() if (service, name) == ("icdev-factory", "signing") else None

    s = asig.open_signer("keyring:icdev-factory/signing", keyring_backend=Ring())
    assert asig.verify(s.spki(), b"m", s.sign(b"m")) and s.sign(b"m") == s.sign(b"m"), "RFC 6979: deterministic"
    with pytest.raises(asig.SigningError) as e:
        asig.open_signer("keyring:icdev-factory/other", keyring_backend=Ring())
    assert e.value.code == "no_key"

    key = ec.generate_private_key(ec.SECP256R1())

    class Kms:
        def __init__(self, broken=False):
            self.broken, self.calls = broken, []

        def get_public_key(self, KeyId):
            return {"PublicKey": key.public_key().public_bytes(serialization.Encoding.DER,
                                                                serialization.PublicFormat.SubjectPublicKeyInfo)}

        def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
            self.calls.append((MessageType, SigningAlgorithm, len(Message)))
            if self.broken:
                return {"Signature": b"0" * 70}
            from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

            if MessageType == "DIGEST":
                return {"Signature": key.sign(Message, ec.ECDSA(Prehashed(hashes.SHA256())))}
            return {"Signature": key.sign(Message, ec.ECDSA(hashes.SHA256()))}

    k = Kms()
    s = asig.open_signer("kms:alias/icdev-factory?region=us-gov-west-1", kms_client=k)
    assert asig.verify(s.spki(), b"short", s.sign(b"short"))
    big = b"x" * (asig.KMS_MAX_RAW + 1)
    assert asig.verify(s.spki(), big, s.sign(big))
    assert k.calls == [("RAW", "ECDSA_SHA_256", 5), ("DIGEST", "ECDSA_SHA_256", 32)]
    with pytest.raises(asig.SigningError, match="does not verify"):
        asig.open_signer("kms:alias/x?region=r", kms_client=Kms(broken=True)).sign(b"m")
    with pytest.raises(asig.SigningError):
        asig.open_signer("kms:alias/x")        # no region


# ── one build ────────────────────────────────────────────────────────────────


def test_an_image_build_is_icos_shaped_signed_and_deterministic(fb, signer):
    from tools.crypto import artefact_signer as asig
    from tools.factory_builds import build, sigstore

    out = _build(fb, signer)
    f = out["files"]
    m = json.loads(f["artefact.json"])
    assert build.artefact_errors(m) == [] and m["schema"] == "icos-artefact/v1" and m["kind"] == "image"
    assert m["subject"]["digest"] == fb["inputs"]["image"]["digest"], "the subject is the layout's one manifest"
    assert m["builder"] == "https://icdev.example/factory/v1" and m["key"]["id"] == asig.key_id(signer.spki())
    assert out["digest"] == "sha256:" + hashlib.sha256(f["artefact.json"]).hexdigest()
    assert {x["role"] for x in m["files"]} == {"sbom", "provenance", "vex", "signature", "sbom-attestation",
                                              "provenance-attestation", "vex-attestation"}
    sb = json.loads(f["sbom.cdx.json"])
    assert build.sbom_errors(sb) == [] and sb["specVersion"] == "1.7"
    assert [v["id"] for v in sb["vulnerabilities"]] == ["SYN-ENT03-0001"]
    vex = json.loads(f["vex.openvex.json"])
    assert build.vex_errors(vex) == []
    assert [(s["vulnerability"]["name"], s["status"], s.get("justification")) for s in vex["statements"]] == [
        ("SYN-ENT03-0001", "not_affected", "vulnerable_code_not_in_execute_path")]
    prov = json.loads(f["provenance.slsa.json"])
    assert prov["predicateType"] == "https://slsa.dev/provenance/v1"
    bd, rd = prov["predicate"]["buildDefinition"], prov["predicate"]["runDetails"]
    assert bd["buildType"] and bd["externalParameters"]["commit"] == COMMIT and rd["builder"]["id"] == m["builder"]
    assert rd["metadata"]["startedOn"] == AT == m["produced_at"]
    assert rd["byproducts"][0]["digest"]["sha256"] == hashlib.sha256(f["sbom.cdx.json"]).hexdigest()
    for (path, pt) in build.BUNDLES.values():
        b = json.loads(f[path])
        assert b["verificationMaterial"]["publicKey"]["hint"] == sigstore.hint(signer.spki())
        assert b["verificationMaterial"]["tlogEntries"] == []
        assert sigstore.open_bundle(b, signer.spki())["predicateType"] == pt
    idx = json.loads(f["oci/index.json"])
    assert len(idx["manifests"]) == 4
    assert out["record"]["transparency_log"]["mode"] == "off", "the log is off by default, and it says so"
    assert build.self_check(f, signer.spki()) == []
    again = _build(fb, signer)
    assert again["files"] == f, "the same inputs and key give the same bytes"


def test_a_tampered_file_or_another_key_fails_the_self_check(fb, signer, tmp_path):
    from tools.crypto import artefact_signer as asig
    from tools.factory_builds import build

    f = _build(fb, signer)["files"]
    bad = dict(f)
    sb = json.loads(bad["sbom.cdx.json"])
    sb["components"][0]["version"] = "9.9.9"
    bad["sbom.cdx.json"] = json.dumps(sb).encode()
    assert any("not at its digest" in w for w in build.self_check(bad, signer.spki()))
    p = tmp_path / "other.pem"
    p.write_bytes(throwaway_pem())
    other = asig.open_signer(f"file:{p}")
    assert build.self_check(f, other.spki()), "nothing verifies under a key that did not sign"


def test_nothing_to_state_is_refused_never_an_invented_vex_and_a_scan_must_match_the_sbom(fb, signer):
    from tools.factory_builds import build

    (fb["root"] / "syn-ent03/portal/scan.json").write_text("[]")
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer)
    assert e.value.code == "vex_unexpressible"
    (fb["root"] / "syn-ent03/portal/scan.json").write_text(json.dumps(
        [{"id": "SYN-ENT03-0009", "component": "not-in-the-sbom", "severity": "low"}]))
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer)
    assert e.value.code == "scan_mismatch"
    (fb["root"] / "syn-ent03/portal/scan.json").write_text(json.dumps(
        [{"id": "SYN-ENT03-0003", "component": "requests", "severity": "high"}]))
    vex = json.loads(_build(fb, signer)["files"]["vex.openvex.json"])
    assert [s["status"] for s in vex["statements"]] == ["under_investigation"], "unstated is never not_affected"


def test_inputs_are_read_only_under_the_root_never_through_a_symlink_or_a_hard_link(fb, signer, tmp_path):
    from tools.factory_builds import build, config

    idx = fb["root"] / "syn-ent03/portal/oci/index.json"
    real = tmp_path / "index.json"
    shutil.copyfile(idx, real)
    idx.unlink()
    idx.symlink_to(real)
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer)
    assert e.value.code == "subject" and "symlink" in e.value.message
    idx.unlink()
    shutil.copyfile(real, idx)
    os.link(idx, fb["root"] / "second-link")
    with pytest.raises(build.BuildRefused, match="one link"):
        _build(fb, signer)
    bad = copy.deepcopy(fb["cfg"])
    bad.pop("_digest")
    for k, v in (("layout", "../outside"), ("layout", "/etc"), ("scan", "a/./b")):
        t = next(x for x in bad["targets"] if x["id"] == "syn-ent03-portal")
        t[k] = v
        with pytest.raises(config.ConfigError):
            config.validate(bad)
        t[k] = "syn-ent03/portal/oci" if k == "layout" else "syn-ent03/portal/scan.json"


def test_the_shipped_configuration_loads_with_no_key_and_the_log_off():
    from tools.factory_builds import config

    cfg = config.load()
    assert cfg["signing"] == {"mode": "key", "key": None}, "the factory key is the site's to set, never shipped"
    assert cfg["transparency_log"]["mode"] == "off" and cfg["keyless"]["oidc_issuer"] is None
    assert all(t.get("synthetic") is True for t in cfg["targets"])


# ── the AI-BOM and the fine-tuning registry ─────────────────────────────────


def _conn():
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def test_a_model_build_carries_an_aibom_linked_to_its_registry_promotion_and_signed_weights(fb, signer):
    from tools.factory_builds import build, sigstore

    out = _build(fb, signer, tid="syn-ent03-classifier")
    f = out["files"]
    m = json.loads(f["artefact.json"])
    assert m["kind"] == "model" and build.artefact_errors(m) == []
    sb = json.loads(f["sbom.cdx.json"])
    assert build.sbom_errors(sb) == []
    root = sb["metadata"]["component"]
    assert root["type"] == "machine-learning-model" and "modelCard" in root
    props = {p["name"]: p["value"] for p in root["properties"]}
    assert props["icdev:finetune:model_version_id"] == "mv-syn-ent03-defect-classifier-v3"
    assert props["icdev:finetune:promotion_action"] == "promoted" and props["icdev:finetune:thresholds_bypassed"] == "false"
    assert props["icdev:finetune:promoted_by"] == "syn-ent03-ml-lead"
    assert {c["type"] for c in sb["components"]} == {"machine-learning-model", "data", "library"}
    metrics = {x["type"]: x["value"] for x in root["modelCard"]["quantitativeAnalysis"]["performanceMetrics"]}
    assert metrics == {"bleu": "0.41", "rouge-l": "0.52", "perplexity": "6.3"}
    st = sigstore.open_bundle(json.loads(f["bundles/model.sigstore.json"]), signer.spki(), model_hint=True)
    res = st["predicate"]["resources"]
    assert st["predicateType"] == "https://model_signing/signature/v1.0" and len(res) == 4
    assert st["predicate"]["serialization"] == {"method": "files", "hash_type": "sha256", "allow_symlinks": False}
    assert m["subject"]["digest"] == "sha256:" + sigstore.model_root(res)
    w = fb["root"] / "models/syn-ent03-defect-classifier/v3/weights/part-0.safetensors"
    assert {r["name"]: r["digest"] for r in res}["weights/part-0.safetensors"] == \
        hashlib.sha256(w.read_bytes()).hexdigest()
    assert out["record"]["promotion"]["promotion_log_id"]
    assert json.loads(f["vex.openvex.json"])["statements"][0]["vulnerability"]["name"] == "SYN-ENT03-0002"


def test_only_a_promoted_version_is_built_and_an_override_is_said_to_be_one(fb, signer):
    from tools.factory_builds import build

    c = _conn()
    c.execute("INSERT INTO ft_promotion_log (model_version_id, action, function_name, reason, actor) "
              "VALUES (%s,'demoted',%s,%s,%s)", ("mv-syn-ent03-defect-classifier-v3", "code_generation",
                                                 "SYNTHETIC: replaced", "syn-ent03-ml-lead"))
    c.commit()
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer, tid="syn-ent03-classifier")
    assert e.value.code == "not_promoted", "a demoted adapter is not a release candidate"
    c.execute("INSERT INTO ft_promotion_log (model_version_id, action, function_name, reason, actor) "
              "VALUES (%s,'override_promoted',%s,%s,%s)", ("mv-syn-ent03-defect-classifier-v3", "code_generation",
                                                           "SYNTHETIC: forced", "syn-ent03-ml-lead"))
    c.commit()
    c.close()
    sb = json.loads(_build(fb, signer, tid="syn-ent03-classifier")["files"]["sbom.cdx.json"])
    props = {p["name"]: p["value"] for p in sb["metadata"]["component"]["properties"]}
    assert props["icdev:finetune:promotion_action"] == "override_promoted"
    assert props["icdev:finetune:thresholds_bypassed"] == "true"


def test_a_model_of_another_tenant_or_marked_above_the_target_is_refused(fb, signer):
    from tools.factory_builds import aibom, build

    found = aibom.promoted_version("syn-ent03-defect-classifier")
    t = _target(fb, "syn-ent03-classifier")
    aibom.check_binding(found, t)
    with pytest.raises(aibom.AibomError) as e:
        aibom.check_binding({**found, "version": {**found["version"], "tenant_id": "another-org"}}, t)
    assert e.value.code == "other_org"
    for m in ("SECRET", "", "SOMETHING"):
        with pytest.raises(aibom.AibomError) as e:
            aibom.check_binding({**found, "version": {**found["version"], "classification": m}}, t)
        assert e.value.code == "marking"
    c = _conn()
    c.execute("UPDATE ft_model_versions SET adapter_path = %s WHERE id = %s",
              ("../../outside", "mv-syn-ent03-defect-classifier-v3"))
    c.commit()
    c.close()
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer, tid="syn-ent03-classifier")
    assert e.value.code == "weights_outside_root"


def test_a_symlinked_weights_file_is_never_followed(fb, signer, tmp_path):
    from tools.factory_builds import build

    w = fb["root"] / "models/syn-ent03-defect-classifier/v3/weights/part-1.safetensors"
    real = tmp_path / "elsewhere.bin"
    real.write_bytes(b"not the weights")
    w.unlink()
    w.symlink_to(real)
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer, tid="syn-ent03-classifier")
    assert e.value.code in ("weights_file", "weights_symlink")


# ── the transparency log and keyless ─────────────────────────────────────────


class Stub:
    """One loopback server playing a Rekor log and a Fulcio CA (a throwaway CA made here)."""

    def __init__(self, issuer: str):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self.issuer, self.mode, self.entries = issuer, {"rekor": 201, "fulcio": "ok"}, []
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "syn-ent03 test CA")])
        now = dt.datetime.now(dt.timezone.utc)
        self.ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.ca_key.public_key())
                   .serial_number(1).not_valid_before(now - dt.timedelta(minutes=1))
                   .not_valid_after(now + dt.timedelta(hours=1)).sign(self.ca_key, hashes.SHA256()))
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def _send(self, code, doc):
                body = json.dumps(doc).encode()
                self.send_response(code)
                if code in (301, 302):
                    self.send_header("Location", "http://127.0.0.1:1/x")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if self.path == "/api/v1/log/entries":
                    if stub.mode["rekor"] != 201:
                        return self._send(stub.mode["rekor"], {"code": "no"})
                    stub.entries.append(body)
                    n = len(stub.entries)
                    return self._send(201, {f"uuid-{n}": {"logID": "ab" * 32, "logIndex": 100 + n,
                                                          "integratedTime": 1763715600, "body": "Ym9keQ==",
                                                          "verification": {"signedEntryTimestamp": "c2V0"}}})
                if self.path == "/api/v2/signingCert":
                    return self._send(200, stub.cert_for(body))
                return self._send(404, {})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def cert_for(self, body: dict) -> dict:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization

        pk = serialization.load_pem_public_key(body["publicKeyRequest"]["publicKey"]["content"].encode())
        iss = self.issuer if self.mode["fulcio"] == "ok" else "https://another-issuer.invalid"
        if self.mode["fulcio"] == "other_key":
            from cryptography.hazmat.primitives.asymmetric import ec

            pk = ec.generate_private_key(ec.SECP256R1()).public_key()
        der = b"\x0c" + bytes([len(iss)]) + iss.encode()
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([])).issuer_name(self.ca.subject).public_key(pk)
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=1))
                .not_valid_after(now + dt.timedelta(minutes=10))
                .add_extension(x509.SubjectAlternativeName([x509.RFC822Name("syn-ent03-ci@example.invalid")]),
                               critical=True)
                .add_extension(x509.UnrecognizedExtension(x509.ObjectIdentifier("1.3.6.1.4.1.57264.1.8"), der),
                               critical=False)
                .sign(self.ca_key, hashes.SHA256()))
        return {"signedCertificateEmbeddedSct": {"chain": {"certificates": [
            cert.public_bytes(serialization.Encoding.PEM).decode(),
            self.ca.public_bytes(serialization.Encoding.PEM).decode()]}}}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


ISSUER = "https://ci.syn-ent03.invalid"


@pytest.fixture()
def stub():
    s = Stub(ISSUER)
    yield s
    s.close()


def test_a_configured_rekor_log_receives_each_bundle_and_its_answer_is_recorded_not_trusted(fb, signer, stub):
    from tools.factory_builds import build

    fb["cfg"]["transparency_log"] = {"mode": "rekor", "url": stub.base}
    out = _build(fb, signer)
    rec = out["record"]["transparency_log"]
    assert rec["mode"] == "rekor" and len(rec["entries"]) == 4 and "NOT verified" in rec["note"]
    assert {e["kind"] for e in stub.entries} == {"dsse"} and all(e["apiVersion"] == "0.0.1" for e in stub.entries)
    b = json.loads(out["files"]["bundles/signature.sigstore.json"])
    tle = b["verificationMaterial"]["tlogEntries"][0]
    assert tle["kindVersion"] == {"kind": "dsse", "version": "0.0.1"} and tle["logIndex"].isdigit()
    assert tle["logId"]["keyId"] == base64.b64encode(bytes.fromhex("ab" * 32)).decode()
    assert build.self_check(out["files"], signer.spki()) == []
    for code in (302, 500):
        stub.mode["rekor"] = code
        with pytest.raises(build.BuildRefused) as e:
            _build(fb, signer)
        assert e.value.code == "tlog", "a configured log that does not take the entry fails the build"


def test_outbound_calls_are_https_or_loopback_only():
    from tools.crypto import artefact_signer as asig

    for url in ("http://10.0.0.5/x", "ftp://127.0.0.1/x", "http://localhost.evil.invalid/x"):
        with pytest.raises(asig.SigningError, match="https"):
            asig.post_json(url, {})


def test_keyless_needs_a_configured_issuer_and_a_certificate_for_the_key_that_asked(fb, stub, tmp_path):
    from tools.crypto import artefact_signer as asig
    from tools.factory_builds import build, sigstore

    with pytest.raises(asig.SigningError) as e:
        asig.open_keyless({}, root=ROOT)
    assert e.value.code == "keyless", "no default issuer: nothing reaches a public instance by accident"
    claims = base64.urlsafe_b64encode(json.dumps({"iss": ISSUER, "sub": "syn-ent03-ci"}).encode()).decode().rstrip("=")
    tok = tmp_path / "token"
    tok.write_text(f"e30.{claims}.c2ln")
    kl = {"oidc_issuer": ISSUER, "fulcio_url": stub.base, "token_file": str(tok)}
    s = asig.open_keyless(kl, root=ROOT)
    out = _build(fb, s)
    b = json.loads(out["files"]["bundles/signature.sigstore.json"])
    assert "certificate" in b["verificationMaterial"] and "publicKey" not in b["verificationMaterial"]
    assert sigstore.open_bundle(b, s.spki())
    assert out["record"]["signing"]["mode"] == "keyless" and "refuses this build" in out["record"]["signing"]["note"]
    assert build.self_check(out["files"], s.spki()) == []
    for mode in ("other_issuer", "other_key"):
        stub.mode["fulcio"] = mode
        with pytest.raises(asig.SigningError) as e:
            asig.open_keyless(kl, root=ROOT)
        assert e.value.code == "keyless"
    stub.mode["fulcio"] = "ok"
    with pytest.raises(asig.SigningError, match="configured issuer"):
        asig.open_keyless({**kl, "oidc_issuer": "https://someone-else.invalid"}, root=ROOT)


# ── prepare, publish, export ─────────────────────────────────────────────────


def test_a_person_prepares_another_of_the_org_publishes_and_only_then_it_leaves(fb, signer):
    import jsonschema

    from tools.factory_builds import service
    from tools.factory_builds.constants import PUBLICATION_RELPATH

    p = _prepare(fb, signer)
    with pytest.raises(service.Refused) as e:
        service.export("syn-ent03-portal", reader(PUBLISHER))
    assert e.value.code == "unpublished", "a draft never leaves"
    with pytest.raises(service.Refused) as e:
        _publish(fb, p, who=PREPARER)
    assert e.value.code == "separation", "the preparer never publishes"
    with pytest.raises(service.Refused) as e:
        _publish(fb, p, digest="sha256:" + "0" * 64)
    assert e.value.code == "digest"
    with pytest.raises(service.Refused) as e:
        _publish(fb, p, reason="")
    assert e.value.code == "reason"
    out = _publish(fb, p)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"]
    with pytest.raises(service.Refused) as e:
        _publish(fb, p)
    assert e.value.code == "already_decided"
    ex = service.export("syn-ent03-portal", reader(PUBLISHER))
    pub = ex["publication"]
    assert pub["content_digest"] == p["digest"] and pub["signed_by"] == PUBLISHER["id"]
    assert pub["prepared_by"] == PREPARER["id"] and pub["template"] == "decl-wft-fb-build-publish"
    schema = json.loads((ROOT / PUBLICATION_RELPATH).read_text())
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(pub)) == []
    from tools.factory_builds import sigstore

    files = sigstore.from_transfer(ex["bundle"])
    assert "sha256:" + hashlib.sha256(files["artefact.json"]).hexdigest() == pub["content_digest"]
    assert set(ex) == {"bundle", "publication"}, "the shape ICDEV[OS]'s import reads: the publication beside it"


def test_an_agent_never_makes_the_factory_key_sign(fb, signer):
    from tools.factory_builds import service

    for who in (AGENT, {"id": "bootstrap:seed", "role": "admin"}, {"id": "u-svc", "role": "service"}):
        with pytest.raises(service.Refused) as e:
            service.prepare("syn-ent03-portal", who, reader(PREPARER), version="1.0.0", cfg=fb["cfg"], signer=signer,
                            commit=COMMIT)
        assert e.value.code == "agent_cannot_sign"
    p = _prepare(fb, signer)
    with pytest.raises(service.Refused) as e:
        service.publish("syn-ent03-portal", decision="publish", reason="r", read_digest=p["digest"], actor=AGENT,
                        reader=service.mcp_reader("ent03-helper"), cfg=fb["cfg"])
    assert e.value.code == "agent_cannot_sign"


def test_a_publication_counts_only_with_its_declared_step(fb, signer, monkeypatch):
    from tools.factory_builds import service
    from tools.workflow_hitl import declared

    p = _prepare(fb, signer)
    _publish(fb, p)
    real_events = declared._events

    def other_digest(sid, conn=None):
        ev = real_events(sid, conn)
        d = dict(ev["decided"])
        d["payload_json"] = json.dumps({**json.loads(d["payload_json"]), "content_digest": "sha256:" + "1" * 64})
        return {**ev, "decided": d}

    monkeypatch.setattr(declared, "_events", other_digest)
    with pytest.raises(service.Refused) as e:
        service.export("syn-ent03-portal", reader(PUBLISHER))
    assert e.value.code == "step_missing", "a step that signed another digest publishes nothing"
    monkeypatch.setattr(declared, "_events", real_events)
    monkeypatch.setattr(declared, "step_state", lambda sid: None)
    with pytest.raises(service.Refused) as e:
        service.export("syn-ent03-portal", reader(PUBLISHER))
    assert e.value.code == "step_missing"

    def boom(sid):
        raise RuntimeError("store down")

    monkeypatch.setattr(declared, "step_state", boom)
    with pytest.raises(service.Refused) as e:
        service.export("syn-ent03-portal", reader(PUBLISHER))
    assert e.value.code == "step_missing", "an unreadable step store publishes nothing"


def test_a_model_demoted_since_it_was_built_is_never_published(fb, signer):
    from tools.factory_builds import service

    p = _prepare(fb, signer, tid="syn-ent03-classifier")
    c = _conn()
    c.execute("INSERT INTO ft_promotion_log (model_version_id, action, function_name, reason, actor) "
              "VALUES (%s,'demoted',%s,%s,%s)", ("mv-syn-ent03-defect-classifier-v3", "code_generation",
                                                 "SYNTHETIC: replaced", "syn-ent03-ml-lead"))
    c.commit()
    c.close()
    with pytest.raises(service.Refused) as e:
        _publish(fb, p, tid="syn-ent03-classifier")
    assert e.value.code == "promotion_withdrawn"
    out = _publish(fb, p, tid="syn-ent03-classifier", decision="refuse", reason="demoted since (test)")
    assert out["event"] == "refused", "a person may still refuse it"


def test_what_a_reader_may_not_see_is_no_such_target_byte_for_byte(fb, signer):
    from tools.factory_builds import service

    _prepare(fb, signer)
    outs = []
    for who, tid in ((OUTSIDER, "syn-ent03-portal"), (UNCLEARED, "syn-ent03-portal"), (PUBLISHER, "no-such-target")):
        with pytest.raises(service.Refused) as e:
            service.target_view(tid, reader(who))
        outs.append((e.value.status, e.value.message))
        with pytest.raises(service.Refused):
            service.prepare(tid, actor(who), reader(who), version="1.0.0", cfg=fb["cfg"], signer=signer,
                            commit=COMMIT)
    assert len(set(outs)) == 1 and outs[0] == (404, "no such target")
    v = service.targets_view(reader(OUTSIDER))
    assert v["targets"] == [] and v["held_back"] == 2 and "syn-ent03" not in json.dumps(v["targets"])
    agent = service.targets_view(service.mcp_reader("ent03"))
    assert {t["id"] for t in agent["targets"]} == {"syn-ent03-portal", "syn-ent03-classifier"}
    customer = copy.deepcopy(fb["cfg"])
    for t in customer["targets"]:
        t["synthetic"] = False
    assert service.targets_view(service.mcp_reader("ent03"), cfg=customer)["targets"] == [], \
        "an agent is in no org: it sees no customer's target"


def test_the_ledger_is_append_only_and_hash_chained(fb, signer):
    from tools.factory_builds import ledger, service

    p = _prepare(fb, signer)
    c = _conn()
    for sql in ("UPDATE fb_events SET reason = 'x'", "DELETE FROM fb_events"):
        with pytest.raises(Exception, match="append-only"):
            c.execute(sql)
    c.close()
    rows = ledger.read()
    assert ledger.chain(rows)["_problems"] == []
    forged = [dict(rows[0], reason="rewritten")]
    assert ledger.chain(forged)["_problems"][0]["problem"] == "row hash does not match its content"
    body = json.loads(rows[0]["payload_json"])
    body["transfer"]["files"]["sbom.cdx.json"] = "{}"
    bad = dict(rows[0], payload_json=json.dumps(body))
    bad["row_hash"] = ledger._row_hash(bad)
    assert ledger.chain([bad])["_problems"][0]["problem"] == "the recorded artefact does not match its digest"
    assert service.targets_view(reader(PUBLISHER))["targets"][0]["prepared"]["content_digest"] == p["digest"]


# ── the console, the twin, IQE and MCP ───────────────────────────────────────


def _page_app(user):
    from tools.dashboard.app import app as dash
    from tools.factory_builds.blueprint import fb_bp
    from tools.security.canvas_access import grant_access

    user = dict(user, impact_level="IL5")
    grant_access(user["tenant_id"], "user", user["id"], "factory_builds", "read", granted_by="test-admin")
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

    app.register_blueprint(fb_bp)
    return app.test_client()


def test_the_routes_and_the_page(fb, signer, monkeypatch):
    from tools.factory_builds import service

    monkeypatch.setattr(service, "signer_for", lambda cfg: signer)
    monkeypatch.setattr("tools.factory_builds.build.factory_commit", lambda: COMMIT)
    a, b, o = _page_app(PREPARER), _page_app(PUBLISHER), _page_app(OUTSIDER)
    r = a.post("/factory-builds/api/targets/syn-ent03-portal/prepare", json={"version": "1.0.0"})
    assert r.status_code == 201, r.get_data(as_text=True)
    p = r.get_json()
    html = b.get("/factory-builds/?target=syn-ent03-portal").get_data(as_text=True)
    for want in ("decl-wft-fb-build-publish", "icos-artefact/v1", "factory_builds.fb_api_publish",
                 "factory_builds.fb_api_prepare", 'data-target="syn-ent03-portal"', p["digest"], "SYN-ENT03-0001",
                 "not affected", "OpenSSF model-signing", "CycloneDX 1.7", "SLSA v1.2"):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/factory_builds/index.html").read_text(encoding="utf-8")
    assert o.get("/factory-builds/api/targets/syn-ent03-portal").status_code == 404
    r = a.post("/factory-builds/api/targets/syn-ent03-portal/publish",
               json={"decision": "publish", "reason": "mine", "digest": p["digest"]})
    assert r.status_code == 403
    r = b.post("/factory-builds/api/targets/syn-ent03-portal/publish",
               json={"decision": "publish", "reason": "read it (test)", "digest": p["digest"]})
    assert r.status_code == 200, r.get_data(as_text=True)
    ex = b.get("/factory-builds/api/targets/syn-ent03-portal/export").get_json()
    assert ex["publication"]["signed_by"] == PUBLISHER["id"]
    assert o.get("/factory-builds/api/targets/syn-ent03-portal/export").status_code == 404
    q = {"question": "Show all factory_builds builds"}
    mine = b.post("/factory-builds/api/iqe-query", json=q).get_json()
    assert mine["ok"] and mine["row_count"] == 1 and mine["results"][0]["digest"] == p["digest"]
    assert o.post("/factory-builds/api/iqe-query", json=q).get_json()["row_count"] == 0


def test_the_twin_observes_with_threads_and_replays(fb, signer):
    import tools.twin_core.adapters.factory_builds  # noqa: F401
    from tools.twin_core import governance

    adapter = governance.adapter_for("factory_builds")
    assert adapter.observes == ("factory_builds.signed_builds",)
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["factory_builds.ledger"]["status"] == "verified"
    assert chk["factory_builds.syn-ent03-portal"]["status"] == "unverified", "nothing published is never a pass"
    p = _prepare(fb, signer)
    pub = _publish(fb, p)
    pm = _prepare(fb, signer, tid="syn-ent03-classifier")
    pubm = _publish(fb, pm, tid="syn-ent03-classifier")
    c = _conn()
    c.execute("INSERT INTO ft_promotion_log (model_version_id, action, function_name, reason, actor) "
              "VALUES (%s,'demoted',%s,%s,%s)", ("mv-syn-ent03-defect-classifier-v3", "code_generation",
                                                 "SYNTHETIC: replaced", "syn-ent03-ml-lead"))
    c.commit()
    c.close()
    env2 = governance.observe(adapter)
    chk = {x["id"]: x for x in env2["extra"]["checks"]}
    assert chk["factory_builds.syn-ent03-portal"]["status"] == "verified"
    f = [v for v in env2["violations"] if v["rule_id"] == "factory_builds.promotion_withdrawn"]
    assert f and f[0]["thread_id"] == f"factory_builds:{pubm['event_id']}"
    res = governance.resolve_thread(f"factory_builds:{pub['event_id']}")
    assert res["resolved"] and res["record"]["event"] == "published" and "actor" not in res["record"]
    assert governance.replay(adapter, env2["extra"]["observation_id"])["match"]
    b, o = _page_app(PUBLISHER), _page_app(OUTSIDER)
    th = {"thread": f"factory_builds:{pub['event_id']}"}
    assert b.get("/factory-builds/api/twin", query_string=th).get_json()["record"]["actor"] == PUBLISHER["id"]
    r = o.get("/factory-builds/api/twin", query_string=th)
    assert r.status_code == 404 and r.get_json()["reason"] == "no such thread"


def test_the_mcp_tool_only_reads(fb, signer):
    from tools.mcp.gap_handlers import handle_factory_builds_status
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert "factory_builds_status" in set(list_tools())
    assert READ_ONLY_DECLARATIONS["factory_builds_status"] is True
    assert tool_authorization("factory_builds_status")["tier"] == "read"
    p = _prepare(fb, signer)
    out = handle_factory_builds_status({"target": "syn-ent03-portal"})
    assert out["target"]["build"]["digest"] == p["digest"]
    assert handle_factory_builds_status({"target": "syn-ent03-portal", "export": True})["code"] == "unpublished"
    assert handle_factory_builds_status({"target": "nope"}) == {"error": "no such target", "code": "not_found",
                                                                 "refused": True}


def test_a_tenant_person_s_canvas_grant_is_read_inside_their_own_request(wf_db):
    """The page's POST from a signed-in person's session reached canvas_access.check_access under the row-security
    predicate of that person's context, which names a classification column canvas_access_grants does not have: the
    query raised and the person was refused (403) every write on a granted canvas. icx-ent-02 found and fixed it; this
    card's e2e met it again on main. The grants are read without the label predicate; each query names its tenant."""
    from tools.db import storage
    from tools.security import canvas_access

    canvas_access.grant_access(ORG, "user", "u-ent03-granted", "factory_builds", "read", granted_by="test-admin")
    real = storage.get_connection

    def predicated(*a, **k):
        conn = real(*a, **k)
        conn.set_security_context(type("Ctx", (), {"tenant_id": None, "classification": "CUI",
                                                   "compartments": frozenset()})())
        return conn

    storage.get_connection = predicated
    try:
        assert canvas_access.check_access("u-ent03-granted", ORG, "factory_builds", "read", "reviewer")
        assert not canvas_access.check_access("u-ent03-granted", "another-org", "factory_builds", "read",
                                              "reviewer"), "the tenant still scopes the grant"
    finally:
        storage.get_connection = real


# ── the independent review's findings (each a fail-closed test) ─────────────


def test_m1_the_sbom_is_resolved_from_a_confined_copy_of_the_manifests(fb, signer, tmp_path):
    """A symlinked manifest never pulls an outside package into the signed SBOM, a FIFO never hangs the build, and
    nothing but the named manifests is read (no installed environment)."""
    from tools.factory_builds import build

    proj = fb["root"] / "syn-ent03/portal/src"
    outside = tmp_path / "outside-requirements.txt"
    outside.write_text("syn-outside-package==6.6.6\n")
    (proj / "requirements.txt").unlink()
    (proj / "requirements.txt").symlink_to(outside)
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer)
    assert e.value.code == "sbom" and "symlink" in e.value.message
    (proj / "requirements.txt").unlink()
    os.mkfifo(proj / "requirements.txt")
    with pytest.raises(build.BuildRefused, match="not a regular file"):
        _build(fb, signer)          # O_NONBLOCK: refused at fstat, never a read that blocks
    (proj / "requirements.txt").unlink()
    (proj / "requirements.txt").write_text("requests==2.31.0\n")
    dist = proj / ".venv/lib/python3.11/site-packages/syn_venv_only-1.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: syn-venv-only\nVersion: 1.0\n")
    out = _build(fb, signer)
    sbom = out["files"]["sbom.cdx.json"].decode()
    assert "syn-outside-package" not in sbom and "syn-venv-only" not in sbom and "requests" in sbom
    prov = json.loads(out["files"]["provenance.slsa.json"])
    assert prov["predicate"]["buildDefinition"]["internalParameters"]["inputs"]["manifests"] == {
        "requirements.txt": hashlib.sha256(b"requests==2.31.0\n").hexdigest()}


def test_m2_a_registry_row_with_no_tenant_binds_only_to_a_platform_scoped_target(fb):
    from tools.factory_builds import aibom

    found = aibom.promoted_version("syn-ent03-defect-classifier")
    t = _target(fb, "syn-ent03-classifier")
    unowned = {**found, "version": {**found["version"], "tenant_id": ""}}
    with pytest.raises(aibom.AibomError) as e:
        aibom.check_binding(unowned, t)
    assert e.value.code == "other_org", "an empty tenant never binds to an organisation's target"
    aibom.check_binding(unowned, {**t, "platform_scoped": True})


def test_l7_a_missing_dataset_is_refused_and_an_archived_version_no_longer_stands(fb, signer):
    from tools.factory_builds import aibom

    c = _conn()
    # The foreign key stops this on PostgreSQL; a SQLite database with foreign keys off (their default) can hold it.
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("UPDATE ft_training_jobs SET dataset_id = %s WHERE id = %s",
              ("ds-gone", "job-syn-ent03-defect-classifier-v3"))
    c.commit()
    with pytest.raises(aibom.AibomError) as e:
        aibom.promoted_version("syn-ent03-defect-classifier", c)
    assert e.value.code == "no_provenance"
    c.execute("UPDATE ft_training_jobs SET dataset_id = %s WHERE id = %s",
              ("ds-syn-ent03-defect-classifier-v3", "job-syn-ent03-defect-classifier-v3"))
    c.commit()
    found = aibom.promoted_version("syn-ent03-defect-classifier", c)
    assert aibom.still_promoted(found["version"]["id"], found["promotion"]["id"], c)
    c.execute("UPDATE ft_model_versions SET status = 'archived' WHERE id = %s", (found["version"]["id"],))
    c.commit()
    assert not aibom.still_promoted(found["version"]["id"], found["promotion"]["id"], c)
    c.close()


def test_m4_only_a_builder_role_makes_the_factory_key_sign(fb, signer):
    from tools.factory_builds import config, service

    pm = {"id": "u-ent03-pm", "role": "pm", "clearance_level": "CUI", "tenant_id": ORG}
    with pytest.raises(service.Refused) as e:
        _prepare(fb, signer, who=pm)
    assert e.value.code == "role" and e.value.status == 403
    bad = copy.deepcopy(fb["cfg"])
    bad.pop("_digest")
    for roles in ([], ["service"], ["no-such-role"]):
        bad["builder_roles"] = roles
        with pytest.raises(config.ConfigError):
            config.validate(bad)


def test_l1_a_test_key_is_pinned_to_the_real_temp_root_not_tmpdir(monkeypatch):
    import tempfile

    from tools.crypto import artefact_signer as asig

    elsewhere = ROOT / ".tmp" / f"ent03-tmpdir-{uuid.uuid4().hex[:8]}"
    elsewhere.mkdir(parents=True)
    try:
        (elsewhere / "k.pem").write_bytes(throwaway_pem())
        monkeypatch.setattr(tempfile, "tempdir", str(elsewhere))      # what TMPDIR does
        with pytest.raises(asig.SigningError, match="THROWAWAY"):
            asig.open_signer(f"file:{elsewhere / 'k.pem'}")
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def test_l2_l3_a_fifo_or_a_symlinked_directory_on_the_way_is_refused_promptly(fb, signer, tmp_path):
    from tools.factory_builds import build

    scan = fb["root"] / "syn-ent03/portal/scan.json"
    scan.unlink()
    os.mkfifo(scan)
    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer)
    assert e.value.code == "scan"
    scan.unlink()
    oci = fb["root"] / "syn-ent03/portal/oci"
    moved = tmp_path / "oci"
    shutil.move(str(oci), moved)
    oci.symlink_to(moved, target_is_directory=True)
    with pytest.raises(build.BuildRefused, match="symlink"):
        _build(fb, signer)


def test_l4_the_page_s_self_check_is_re_run_on_read(fb, signer):
    from tools.factory_builds import service

    _prepare(fb, signer)
    v = service.target_view("syn-ent03-portal", reader(PUBLISHER))
    assert v["build"]["self_check"] == "verified now"
    out = _build(fb, signer)
    files = dict(out["files"])
    files["vex.openvex.json"] = b"{}"
    m = json.loads(files["artefact.json"])
    assert service._self_check_now(files, out["record"], m) == "fails now"


def test_l5_a_build_is_published_only_under_the_factory_s_current_key(fb, signer, tmp_path, monkeypatch):
    from tools.crypto import artefact_signer as asig
    from tools.factory_builds import service

    p = _prepare(fb, signer)
    other = tmp_path / "rotated.pem"
    other.write_bytes(throwaway_pem())
    monkeypatch.setattr(service, "signer_for", lambda cfg: asig.open_signer(f"file:{other}"))
    with pytest.raises(service.Refused) as e:
        _publish(fb, p)
    assert e.value.code == "key_mismatch"

    def none(cfg):
        raise service.Refused(409, "no_key", "no factory signing key is configured")

    monkeypatch.setattr(service, "signer_for", none)
    with pytest.raises(service.Refused) as e:
        _publish(fb, p)
    assert e.value.code == "no_key", "a withdrawn key stops its builds leaving"
    assert _publish(fb, p, signer=signer)["event"] == "published"


def test_l6_a_ledger_write_lost_after_the_step_publishes_nothing_and_says_so(fb, signer, monkeypatch):
    from tools.factory_builds import ledger, service

    p = _prepare(fb, signer)
    real = ledger.append

    def lost(conn, **kw):
        if kw.get("event") == "published":
            raise ledger.Refused(503, "store_unavailable", "the build ledger could not be written")
        return real(conn, **kw)

    monkeypatch.setattr(ledger, "append", lost)
    with pytest.raises(service.Refused) as e:
        _publish(fb, p)
    assert e.value.code == "not_recorded" and "step-" in e.value.message
    with pytest.raises(service.Refused) as e:
        service.export("syn-ent03-portal", reader(PUBLISHER))
    assert e.value.code == "unpublished"
    monkeypatch.setattr(ledger, "append", real)
    assert _publish(fb, p)["event"] == "published", "it can be decided again"


def test_l8_one_deadline_covers_every_rekor_submission_of_a_build(fb, signer):
    import time

    from tools.factory_builds import build

    fb["cfg"]["transparency_log"] = {"mode": "rekor", "url": "http://127.0.0.1:9"}
    fb["cfg"]["limits"] = {**fb["cfg"]["limits"], "deadline_seconds": 0.2}
    calls = []

    def slow(env, ver):
        calls.append(1)
        time.sleep(0.15)
        return {"uuid": f"u{len(calls)}", "entry": {"logIndex": str(len(calls)), "logId": {"keyId": "AA=="},
                                                    "kindVersion": {"kind": "dsse", "version": "0.0.1"},
                                                    "integratedTime": "1"}}

    with pytest.raises(build.BuildRefused) as e:
        _build(fb, signer, tlog=slow)
    assert e.value.code == "tlog" and "one deadline" in e.value.message and len(calls) == 2


def test_l9_the_iqe_route_answers_over_its_own_collection_only(fb, signer, monkeypatch):
    import tools.iqe.nl_to_iqe as nl

    monkeypatch.setattr(nl, "nl_to_iqe", lambda q, cols: {"iqe": "foreach f in cortex.findings select f.id"})
    r = _page_app(PUBLISHER).post("/factory-builds/api/iqe-query", json={"question": "anything"})
    assert r.status_code == 400 and "factory_builds.builds only" in r.get_json()["error"]


def test_the_e2e_key_is_honoured_only_under_tests_e2e_fixtures_and_only_with_no_configured_key(monkeypatch, tmp_path):
    from tools.factory_builds import config, service

    cfg = config.load()
    fixtures = ROOT / "tests" / "e2e" / "fixtures"
    inside = fixtures / f".ent03_test_key_{uuid.uuid4().hex[:8]}.pem"
    inside.write_bytes(throwaway_pem())
    outside = tmp_path / "k.pem"
    outside.write_bytes(throwaway_pem())
    try:
        monkeypatch.setenv(service.E2E_KEY_ENV, str(outside))
        assert service._e2e_signer(cfg) is None
        monkeypatch.setenv(service.E2E_KEY_ENV, str(inside))
        assert service._e2e_signer(cfg) is not None
        assert service._e2e_signer({**cfg, "signing": {"mode": "key", "key": "keyring:site/key"}}) is None, \
            "it never replaces a key a site configured"
    finally:
        inside.unlink()
