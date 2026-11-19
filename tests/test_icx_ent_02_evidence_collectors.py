# CUI // SP-CTI
"""Evidence collectors for customer environments (icx-ent-02).

What the card promises, each clause a test here:

  * four read-only collectors, each a real, named integration: cloud configuration (AWS Config's Describe calls,
    SigV4-signed), the identity provider (OIDC Discovery 1.0 and SCIM 2.0 /Users), endpoints (Intune through Microsoft
    Graph) and the SIEM (Splunk's REST API) -- tested against LOCAL STUBS on loopback only, never the network;
  * credentials only by reference (OS keyring or vault), refused as literals when the configuration loads, never in
    an observation, a log line, the ledger, a view or a bundle;
  * every observation carries observed_at (RFC 3339 with its zone), its collector, its system and a freshness
    budget; a stale, future, unparseable or missing time is unverified; nothing is ever met from absence;
  * a reviewed mapping (args/) to SP 800-171 Rev. 2 and SP 800-53 Rev. 5 objectives that never claims more than was
    observed;
  * one icos-objective-evidence/v1 report per system per run, valid against ICDEV[OS]'s pinned contract, prepared
    by one person and published by another of the system's org (decl-wft-ec-evidence-publish), its publication
    embedded with exactly the contract's fields; the ledger is append-only and hash-chained (the shared stream_chain);
  * clearance and org: a system the reader may not see is "no such system", and lists count what they hold back;
  * the page, routes, twin (threads, replay) and MCP read and draft.

People here are labelled test persons (synthetic); every source is a synthetic stub.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 19, 9, 0, 0, tzinfo=timezone.utc)
ORG = "syn-dib"
PREPARER = {"id": "u-ent02-preparer", "role": "reviewer", "clearance_level": "CUI", "tenant_id": ORG}
PUBLISHER = {"id": "u-ent02-publisher", "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
OUTSIDER = {"id": "u-ent02-outsider", "role": "isso", "clearance_level": "CUI", "tenant_id": "another-org"}
UNCLEARED = {"id": "u-ent02-public", "role": "isso", "clearance_level": "PUBLIC", "tenant_id": ORG}
SECRETS = {"keyring:ec-test/aws-id": "AKIDSTUBEXAMPLE", "keyring:ec-test/aws-secret": "stub-aws-secret-0f9e8d",
           "keyring:ec-test/scim": "stub-scim-token-7c6b5a", "keyring:ec-test/graph": "stub-graph-secret-4d3c2b",
           "keyring:ec-test/splunk": "stub-splunk-token-1a2b3c"}


def reader(u: dict) -> dict:
    return {"id": u["id"], "role": u["role"], "clearance": u["clearance_level"], "org": u["tenant_id"]}


def actor(u: dict) -> dict:
    return {"id": u["id"], "role": u["role"]}


# ── local stubs (loopback only) ──────────────────────────────────────────────


class Stub:
    """One loopback server playing all four sources. ``state`` changes what each answers."""

    def __init__(self):
        self.state = {
            "aws": {"cloudtrail-enabled": ("COMPLIANT", NOW - timedelta(hours=2)),
                    "cloud-trail-log-file-validation-enabled": ("COMPLIANT", NOW - timedelta(hours=3)),
                    "encrypted-volumes": ("COMPLIANT", NOW - timedelta(hours=1)),
                    "s3-bucket-server-side-encryption-enabled": ("COMPLIANT", NOW - timedelta(hours=1))},
            "users": [{"userName": f"user{i}", "active": True, "groups": [{"value": "g1"}]} for i in range(5)],
            "devices": [{"id": f"d{i}", "deviceName": f"laptop-{i}", "operatingSystem": "Windows",
                         "complianceState": "compliant", "isEncrypted": True,
                         "lastSyncDateTime": (NOW - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")}
                        for i in range(3)],
            "indexes": {"audit": {"frozenTimePeriodInSecs": "188697600", "disabled": False},
                        "cloudtrail": {"frozenTimePeriodInSecs": "31536000", "disabled": "0"}},
            "searches": {"Audit review - daily": {"is_scheduled": "1", "disabled": "0"}},
            "issuer_override": None, "redirect": False,
        }
        self.seen: list[dict] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                return

            def _send(self, code, doc, ctype="application/json"):
                body = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                stub.seen.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if stub.state["redirect"]:
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                    self.end_headers()
                    return
                if u.path == "/idp/.well-known/openid-configuration":
                    iss = stub.state["issuer_override"] or stub.base + "/idp"
                    return self._send(200, {"issuer": iss, "authorization_endpoint": stub.base + "/idp/authorize",
                                            "token_endpoint": stub.base + "/idp/token", "jwks_uri": stub.base + "/idp/jwks",
                                            "code_challenge_methods_supported": ["S256"],
                                            "id_token_signing_alg_values_supported": ["RS256"]})
                if u.path == "/scim/v2/Users":
                    if self.headers.get("Authorization") != "Bearer " + SECRETS["keyring:ec-test/scim"]:
                        return self._send(401, {"detail": "no"})
                    start, count = int(q.get("startIndex", 1)), int(q.get("count", 2))
                    users = stub.state["users"]
                    page = users[start - 1:start - 1 + count]
                    return self._send(200, {"schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                                            "totalResults": len(users), "startIndex": start,
                                            "itemsPerPage": len(page), "Resources": page}, "application/scim+json")
                if u.path == "/graph/v1.0/deviceManagement/managedDevices":
                    if self.headers.get("Authorization") != "Bearer graph-access-token":
                        return self._send(401, {"error": "no"})
                    devs = stub.state["devices"]
                    skip = int(q.get("$skip", 0))
                    doc = {"value": devs[skip:skip + 2]}
                    if skip + 2 < len(devs):
                        doc["@odata.nextLink"] = (stub.base + "/graph/v1.0/deviceManagement/managedDevices?"
                                                  + urllib.parse.urlencode({"$skip": skip + 2}))
                    return self._send(200, doc)
                if u.path in ("/splunk/services/data/indexes", "/splunk/services/saved/searches"):
                    if self.headers.get("Authorization") != "Bearer " + SECRETS["keyring:ec-test/splunk"]:
                        return self._send(401, {"messages": []})
                    src = stub.state["indexes" if "indexes" in u.path else "searches"]
                    return self._send(200, {"entry": [{"name": k, "content": v} for k, v in src.items()]})
                return self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                stub.seen.append({"method": "POST", "path": self.path, "headers": dict(self.headers), "body": body})
                if self.path.endswith("/oauth2/v2.0/token"):
                    form = dict(urllib.parse.parse_qsl(body.decode()))
                    if form.get("client_secret") != SECRETS["keyring:ec-test/graph"]:
                        return self._send(401, {"error": "invalid_client"})
                    return self._send(200, {"access_token": "graph-access-token", "token_type": "Bearer"})
                if self.path.startswith("/aws"):
                    target = self.headers.get("X-Amz-Target", "")
                    if not self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 Credential=AKIDSTUBEXAMPLE/"):
                        return self._send(403, {"message": "unsigned"})
                    names = json.loads(body).get("ConfigRuleNames") or []
                    rules = stub.state["aws"]
                    if target == "StarlingDoveService.DescribeComplianceByConfigRule":
                        return self._send(200, {"ComplianceByConfigRules": [
                            {"ConfigRuleName": r, "Compliance": {"ComplianceType": rules[r][0]}} for r in names
                            if r in rules]})
                    if target == "StarlingDoveService.DescribeConfigRuleEvaluationStatus":
                        return self._send(200, {"ConfigRulesEvaluationStatus": [
                            {"ConfigRuleName": r, **({"LastSuccessfulEvaluationTime": rules[r][1].timestamp()}
                                                     if rules[r][1] else {})} for r in names if r in rules]})
                return self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def stub_config(base: str) -> dict:
    """The shipped mapping and collectors with one synthetic system pointed at the stub."""
    from tools.evidence_collectors import config

    cfg = config.load()
    sys_ = {"id": "syn-dib-01", "synthetic": True, "org": ORG, "marking": "CUI", "collectors": {
        "aws-config": {"endpoint": base + "/aws", "region": "us-gov-west-1",
                       "rules": {"CLOUD_TRAIL_ENABLED": "cloudtrail-enabled",
                                 "CLOUD_TRAIL_LOG_FILE_VALIDATION_ENABLED": "cloud-trail-log-file-validation-enabled",
                                 "ENCRYPTED_VOLUMES": "encrypted-volumes",
                                 "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED": "s3-bucket-server-side-encryption-enabled"},
                       "credentials": {"access_key_id": "keyring:ec-test/aws-id",
                                       "secret_access_key": "keyring:ec-test/aws-secret"}},
        "oidc-scim": {"issuer": base + "/idp", "scim_base": base + "/scim/v2", "page_size": 2,
                      "credentials": {"scim_token": "keyring:ec-test/scim"}},
        "intune": {"tenant_id": "tenant-0", "client_id": "client-0", "graph_base": base + "/graph",
                   "login_base": base + "/login", "credentials": {"client_secret": "keyring:ec-test/graph"}},
        "splunk": {"base": base + "/splunk", "audit_indexes": ["audit", "cloudtrail"], "required_retention_days": 365,
                   "review_searches": ["Audit review - daily"], "credentials": {"token": "keyring:ec-test/splunk"}}}}
    secret = {"id": "syn-secret-01", "synthetic": True, "org": ORG, "marking": "SECRET",
              "collectors": copy.deepcopy(sys_["collectors"])}
    other = {"id": "syn-other-01", "synthetic": True, "org": "another-org", "marking": "CUI",
             "collectors": copy.deepcopy(sys_["collectors"])}
    raw = {k: v for k, v in cfg.items() if not k.startswith("_")}
    raw["systems"] = [sys_, secret, other]
    out = config.validate(copy.deepcopy(raw))
    out["_sha256"], out["_path"] = "f" * 64, "args/evidence_collectors.yaml"
    return out


def fake_resolve(ref: str):
    from tools.evidence_collectors.credentials import CredentialError, Secret, check_ref

    check_ref(ref)
    if ref not in SECRETS:
        raise CredentialError(f"{ref}: no such entry in the OS keyring")
    return Secret(SECRETS[ref], ref)


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.close()


@pytest.fixture()
def ec(wf_db, stub, monkeypatch):
    """The conftest SQLite platform DB, the stub, and a configuration pointed at it (the shipped file is untouched)."""
    from tools.evidence_collectors import config, credentials

    cfg = stub_config(stub.base)
    monkeypatch.setattr(config, "load", lambda path=None: copy.deepcopy(cfg))
    monkeypatch.setattr(credentials, "resolve", fake_resolve)
    from tools.evidence_collectors import service

    monkeypatch.setattr(service, "resolve_ref", fake_resolve)
    return {"cfg": cfg, "stub": stub, "db": wf_db}


def _collect(ec, now=NOW):
    from tools.evidence_collectors import config, service

    return service.collect(config.system(ec["cfg"], "syn-dib-01"), ec["cfg"], now=now, resolve=fake_resolve)


def _prepare(ec, who=PREPARER, **kw):
    from tools.evidence_collectors import service

    return service.prepare("syn-dib-01", actor(who), reader(who), cfg=ec["cfg"], now=kw.pop("now", NOW),
                           resolve=fake_resolve, **kw)


def _publish(ec, p, who=PUBLISHER, decision="publish", **kw):
    from tools.evidence_collectors import service

    return service.publish("syn-dib-01", decision=decision, reason=kw.pop("reason", "read it (test)"),
                           read_digest=kw.pop("digest", p["content_digest"]), actor=actor(who), reader=reader(who),
                           cfg=kw.pop("cfg", ec["cfg"]), **kw)


# ── the contract and the machinery ───────────────────────────────────────────


def test_the_contract_is_icos_verbatim_and_pinned(monkeypatch, tmp_path):
    from tools.evidence_collectors import bundle
    from tools.evidence_collectors.constants import SCHEMA, SCHEMA_RELPATH, SCHEMA_SHA256

    raw = (ROOT / SCHEMA_RELPATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SCHEMA_SHA256
    doc = json.loads(raw)
    assert doc["$id"] == "urn:icdev-os:contract:objective-evidence:1"
    assert doc["properties"]["schema"]["const"] == SCHEMA == "icos-objective-evidence/v1"
    assert set(doc["$defs"]["Publication"]["required"]) == {"prepared_by", "signed_by", "step_id", "decided_at",
                                                             "content_digest"}
    alt = tmp_path / "context" / "icos"
    alt.mkdir(parents=True)
    (alt / "objective-evidence.schema.json").write_bytes(raw.replace(b"satisfied", b"met"))
    monkeypatch.setattr(bundle, "BASE_DIR", tmp_path)
    with pytest.raises(bundle.BundleError) as e:
        bundle.schema()
    assert e.value.code == "schema_unpinned"


def test_sigv4_matches_aws_published_get_vanilla_vector():
    from tools.evidence_collectors import sigv4

    h = sigv4.sign("GET", "https://example.amazonaws.com/", {}, b"", access_key="AKIDEXAMPLE",
                   secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", region="us-east-1", service="service",
                   when=datetime(2015, 8, 30, 12, 36, tzinfo=timezone.utc))
    assert h["Authorization"].endswith("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_credentials_are_references_only_and_never_quoted(monkeypatch):
    import sys
    import types

    from tools.evidence_collectors import config, credentials

    cfg = stub_config("http://127.0.0.1:9")
    raw = {k: v for k, v in cfg.items() if not k.startswith("_")}
    for bad in ("AKIDLITERALVALUE123", "env:AWS_SECRET", "file:/etc/secret"):
        r = copy.deepcopy(raw)
        r["systems"][0]["collectors"]["aws-config"]["credentials"]["secret_access_key"] = bad
        with pytest.raises(config.ConfigError) as e:
            config.validate(r)
        assert bad not in str(e.value), "a refused literal is never repeated"
    # A secret-looking key outside credentials, at any depth and in any case, is refused.
    r = copy.deepcopy(raw)
    r["systems"][0]["collectors"]["splunk"]["Extra"] = {"nested": {"Splunk_TOKEN": "x"}}
    with pytest.raises(config.ConfigError, match="Splunk_TOKEN"):
        config.validate(r)
    # An unknown field is refused (fail closed), and so is an http base off loopback.
    r = copy.deepcopy(raw)
    r["systems"][0]["collectors"]["oidc-scim"]["issuer"] = "http://idp.example.com"
    with pytest.raises(config.ConfigError, match="loopback"):
        config.validate(r)
    fake = types.SimpleNamespace(get_password=lambda svc, user: {"svc": {"u": "v4lue"}}.get(svc, {}).get(user))
    monkeypatch.setitem(sys.modules, "keyring", fake)
    s = credentials.resolve("keyring:svc/u")
    assert s.reveal() == "v4lue" and "v4lue" not in repr(s) and "v4lue" not in str(s)
    with pytest.raises(credentials.CredentialError, match="no such entry"):
        credentials.resolve("keyring:svc/missing")


def test_the_shipped_configuration_loads_and_every_mapping_row_is_bounded():
    from tools.evidence_collectors import config
    from tools.evidence_collectors.collectors import FACT_OWNER, REGISTRY

    cfg = config.load()
    assert set(cfg["collectors"]) == set(REGISTRY) == {"aws-config", "oidc-scim", "intune", "splunk"}
    assert {REGISTRY[n].KIND for n in REGISTRY} == {"cloud_config", "identity_provider", "endpoint_mdm", "siem"}
    objs = [m["objective"] for m in cfg["mapping"]]
    assert len(objs) == len(set(objs))
    assert {o.split("/")[0] for o in objs} == {"sp800-171r2", "sp800-53r5"}
    assert all(f in FACT_OWNER for m in cfg["mapping"] for f in m["all_of"])
    assert not any(f.startswith("oidc:") for m in cfg["mapping"] for f in m["all_of"]), \
        "discovery metadata says what is offered, not enforced: it maps to no objective"
    s = config.system(cfg, "syn-dib-01")
    assert s["synthetic"] is True and s["marking"] == "CUI"
    assert all(v.startswith("keyring:") for c in s["collectors"].values() for v in c["credentials"].values())


def test_the_transport_only_reads_and_stays_on_its_host(stub):
    from tools.evidence_collectors import transport

    with pytest.raises(transport.CollectorError, match="not a read"):
        transport.request_json("POST", stub.base + "/aws/", body=b"{}", op="aws-config:PutConfigRule")
    with pytest.raises(transport.CollectorError, match="not a read"):
        transport.request_json("DELETE", stub.base + "/x")
    with pytest.raises(transport.CollectorError, match="loopback"):
        transport.check_base("http://siem.example.com")
    with pytest.raises(transport.CollectorError, match="off the configured host"):
        transport.url_for(stub.base, "http://127.0.0.2:1/next")
    stub.state["redirect"] = True
    with pytest.raises(transport.CollectorError, match="redirect"):
        transport.request_json("GET", stub.base + "/scim/v2/Users")


# ── the collectors, against the stubs ────────────────────────────────────────


def test_every_collector_reads_its_source_and_every_observation_says_when_who_and_how_fresh(ec):
    obs = _collect(ec)
    by = {o["fact"]: o for o in obs}
    want = {"aws-config:CLOUD_TRAIL_ENABLED", "aws-config:ENCRYPTED_VOLUMES", "oidc:discovery_valid", "oidc:pkce_s256",
            "oidc:signed_id_tokens", "scim:users_identified", "scim:group_membership_specified",
            "intune:devices_identified", "intune:all_encrypted", "intune:all_compliant", "splunk:audit_retention_met",
            "splunk:review_searches_scheduled"}
    assert want <= set(by)
    for o in obs:
        assert o["value"] is True, (o["fact"], o["why"])
        assert o["system"] == "syn-dib-01" and o["collector"] and o["max_age_h"] > 0
        assert o["observed_at"].endswith("Z") and o["observed_at"][10] == "T"
    # observed_at is the SOURCE's time where it has one.
    assert by["aws-config:CLOUD_TRAIL_ENABLED"]["observed_at"] == "2026-11-19T07:00:00Z"
    assert by["intune:all_encrypted"]["observed_at"] == "2026-11-19T04:00:00Z"
    # The reads were reads: GETs, and only the named read POSTs, every AWS call SigV4-signed.
    posts = [s for s in ec["stub"].seen if s["method"] == "POST"]
    assert {s["headers"].get("X-Amz-Target") for s in posts if s["path"].startswith("/aws")} == {
        "StarlingDoveService.DescribeComplianceByConfigRule", "StarlingDoveService.DescribeConfigRuleEvaluationStatus"}
    assert all(s["path"].startswith("/aws") or s["path"].endswith("/oauth2/v2.0/token") for s in posts)
    # Paging was followed (SCIM: 5 users, 2 a page; Graph: 3 devices, 2 a page).
    assert by["scim:users_identified"]["detail"]["active"] == 5
    assert by["intune:devices_identified"]["detail"]["devices"] == 3
    # No secret anywhere in what a collector returns, and no user or device named.
    text = json.dumps(obs)
    for v in SECRETS.values():
        assert v not in text
    assert "user3" not in text and "laptop-1" not in text


def test_a_source_that_says_no_is_false_and_one_that_cannot_say_is_unverified(ec):
    st = ec["stub"].state
    st["aws"]["encrypted-volumes"] = ("NON_COMPLIANT", NOW - timedelta(hours=1))
    st["aws"]["cloudtrail-enabled"] = ("INSUFFICIENT_DATA", NOW - timedelta(hours=1))
    del st["aws"]["s3-bucket-server-side-encryption-enabled"]
    st["aws"]["cloud-trail-log-file-validation-enabled"] = ("COMPLIANT", None)
    st["users"][1]["userName"] = "USER0"                       # duplicate without case (RFC 7643)
    st["users"][2]["groups"] = []
    st["devices"][0]["complianceState"] = "noncompliant"
    st["indexes"]["cloudtrail"]["frozenTimePeriodInSecs"] = "86400"
    del st["searches"]["Audit review - daily"]
    st["issuer_override"] = "https://evil.example"
    by = {o["fact"]: o for o in _collect(ec)}
    assert by["aws-config:ENCRYPTED_VOLUMES"]["value"] is False
    assert by["aws-config:CLOUD_TRAIL_ENABLED"]["value"] is None
    assert by["aws-config:S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED"]["value"] is None
    assert "not in the account" in by["aws-config:S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED"]["why"]
    assert by["aws-config:CLOUD_TRAIL_LOG_FILE_VALIDATION_ENABLED"]["value"] is None, "never evaluated: unverified"
    assert by["scim:users_identified"]["value"] is False
    assert by["scim:group_membership_specified"]["value"] is False
    assert by["intune:all_compliant"]["value"] is False
    assert by["splunk:audit_retention_met"]["value"] is False
    assert by["splunk:review_searches_scheduled"]["value"] is None, "a search not there may be named otherwise"
    assert by["oidc:discovery_valid"]["value"] is False


def test_a_device_that_has_not_reported_leaves_its_fleet_unverified(ec):
    ec["stub"].state["devices"][2]["lastSyncDateTime"] = (NOW - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ec["stub"].state["devices"][1]["lastSyncDateTime"] = "not a time"
    by = {o["fact"]: o for o in _collect(ec)}
    assert by["intune:devices_identified"]["value"] is True
    for f in ("intune:all_encrypted", "intune:all_compliant"):
        assert by[f]["value"] is None and "have not reported" in by[f]["why"]


def test_an_unreachable_source_or_an_unreadable_credential_is_unverified_with_why(ec):
    from tools.evidence_collectors import config, service

    cfg = copy.deepcopy(ec["cfg"])
    s = config.system(cfg, "syn-dib-01")
    s["collectors"]["splunk"]["base"] = "http://127.0.0.1:1"
    s["collectors"]["intune"]["credentials"]["client_secret"] = "keyring:ec-test/missing"
    obs = service.collect(s, cfg, now=NOW, resolve=fake_resolve)
    by = {o["fact"]: o for o in obs}
    assert by["splunk:audit_retention_met"]["value"] is None and "unreachable" in by["splunk:audit_retention_met"]["why"]
    assert by["intune:all_encrypted"]["value"] is None and "credential" in by["intune:all_encrypted"]["why"]
    assert by["aws-config:CLOUD_TRAIL_ENABLED"]["value"] is True, "one source down does not stop the others"


# ── judging objectives ───────────────────────────────────────────────────────


def test_freshness_parses_instants_and_never_passes_stale_future_or_timeless_evidence(ec):
    from tools.evidence_collectors import bundle

    obs = _collect(ec)
    mapping = ec["cfg"]["mapping"]
    j = bundle.evaluate(obs, mapping, NOW)
    assert {e["result"] for e in j["entries"]} == {"satisfied"} and not j["unverified"]
    # Stale: 30 hours on, the 24h budgets (SCIM, Splunk) have lapsed; the 72h ones have not.
    later = bundle.evaluate(obs, mapping, NOW + timedelta(hours=30))
    gone = {u["objective"] for u in later["unverified"]}
    assert {"sp800-171r2/3.1.1[a]", "sp800-171r2/3.3.1[f]", "sp800-53r5/au-6_obj.a"} <= gone
    assert "stale" in next(u["why"] for u in later["unverified"] if u["objective"] == "sp800-171r2/3.1.1[a]")
    assert "sp800-171r2/3.3.1[c]" in {e["objective"] for e in later["entries"]}
    # Future, junk, zoneless and empty times are unverified; a later string that is an earlier instant is fresh.
    for bad in ("2026-11-19T10:00:00Z", "yesterday", "2026-11-19T08:00:00", ""):
        o2 = [dict(o, observed_at=bad) if o["fact"] == "scim:users_identified" else o for o in obs]
        j2 = bundle.evaluate(o2, mapping, NOW)
        assert "sp800-171r2/3.1.1[a]" in {u["objective"] for u in j2["unverified"]}, bad
    o3 = [dict(o, observed_at="2026-11-19T09:30:00+01:00") if o["fact"] == "scim:users_identified" else o for o in obs]
    assert "sp800-171r2/3.1.1[a]" in {e["objective"] for e in bundle.evaluate(o3, mapping, NOW)["entries"]}


def test_satisfied_needs_every_fact_and_a_fresh_false_is_a_finding_even_beside_an_unverified_one(ec):
    from tools.evidence_collectors import bundle

    obs = _collect(ec)
    mapping = ec["cfg"]["mapping"]
    o = [dict(x, value=None, why="down") if x["fact"] == "intune:all_encrypted" else x for x in obs]
    j = bundle.evaluate(o, mapping, NOW)
    assert "sp800-171r2/3.13.16" in {u["objective"] for u in j["unverified"]}, "two of three facts is not satisfied"
    o = [dict(x, value=False) if x["fact"] == "aws-config:ENCRYPTED_VOLUMES" else x for x in o]
    e = {x["objective"]: x for x in bundle.evaluate(o, mapping, NOW)["entries"]}
    assert e["sp800-171r2/3.13.16"]["result"] == "other_than_satisfied"
    assert "aws-config:ENCRYPTED_VOLUMES=false" in e["sp800-171r2/3.13.16"]["note"]
    # Nothing observed: nothing to report (never met from absence).
    with pytest.raises(bundle.BundleError) as err:
        bundle.build({"id": "syn-dib-01", "marking": "CUI"}, [], mapping, made=NOW)
    assert err.value.code == "nothing_determinable"


def test_the_report_meets_icos_contract_and_icos_digest_rule(ec):
    from tools.evidence_collectors import bundle, config
    from tools.idp.dora import digest

    rep, _ = bundle.build(config.system(ec["cfg"], "syn-dib-01"), _collect(ec), ec["cfg"]["mapping"], made=NOW,
                          mapping_sha256="a" * 64)
    assert bundle.validate(rep) == []
    assert set(rep) == {"schema", "system", "as_of", "produced_at", "producer", "collector", "classification",
                        "entries", "content_digest"}
    assert rep["content_digest"] == digest({k: v for k, v in rep.items() if k != "content_digest"})
    assert rep["produced_at"] == "2026-11-19T09:00:00Z" and rep["as_of"] == "2026-11-19"
    assert all(e["evidence"] == [] and e["method"] == "automated" and len(e["note"]) <= 2000 for e in rep["entries"])
    bad = dict(rep, entries=[dict(rep["entries"][0], result="met")])
    assert bundle.validate(bad), "off the contract"
    pub = bundle.published(rep, prepared_by="a", signed_by="b", step_id="s", decided_at="2026-11-19T09:05:00Z")
    assert bundle.validate(pub) == [] and set(pub["publication"]) == {"prepared_by", "signed_by", "step_id",
                                                                       "decided_at", "content_digest"}
    for who in ("A ", "agent:x"):
        with pytest.raises(bundle.BundleError):
            bundle.published(rep, prepared_by="a", signed_by=who, step_id="s", decided_at="t")


# ── prepare and publish ──────────────────────────────────────────────────────


def test_one_person_prepares_and_another_of_the_org_publishes_the_embedded_report(ec):
    from tools.evidence_collectors import service

    p = _prepare(ec)
    assert p["entries"] > 0 and p["template"] == "decl-wft-ec-evidence-publish"
    with pytest.raises(service.Refused) as e:
        _publish(ec, p, who=PREPARER)
    assert e.value.status in (403, 409), "the preparer never publishes"
    for who in (OUTSIDER, UNCLEARED):
        with pytest.raises(service.Refused) as e:
            _publish(ec, p, who=who)
        assert (e.value.status, e.value.message) == (404, "no such system")
    with pytest.raises(service.Refused) as e:
        _publish(ec, p, digest="0" * 64)
    assert e.value.code == "digest"
    with pytest.raises(service.Refused) as e:
        _publish(ec, p, reason="")
    out = _publish(ec, p)
    assert out["event"] == "published" and out["prepared_by"] == PREPARER["id"]
    b = out["bundle"]
    assert b["publication"] == {"prepared_by": PREPARER["id"], "signed_by": PUBLISHER["id"],
                                "step_id": out["step"]["step_id"], "decided_at": b["publication"]["decided_at"],
                                "content_digest": p["content_digest"]}
    assert service.published_bundle("syn-dib-01", reader(PUBLISHER), cfg=ec["cfg"]) == b
    with pytest.raises(service.Refused) as e:
        _publish(ec, p)
    assert e.value.code == "already_decided"


def test_an_agent_only_prepares_and_a_changed_configuration_needs_a_new_report(ec):
    from tools.evidence_collectors import service

    agent = {"id": "agent:ent02-drafter", "role": "service"}
    p = service.prepare("syn-dib-01", agent, service.mcp_reader("ent02-drafter"), cfg=ec["cfg"], now=NOW,
                        resolve=fake_resolve)
    assert p["actor_kind"] == "agent"
    with pytest.raises(service.Refused):
        service.publish("syn-dib-01", decision="publish", reason="x", read_digest=p["content_digest"], actor=agent,
                        reader=service.mcp_reader("ent02-drafter"), cfg=ec["cfg"])
    cfg2 = copy.deepcopy(ec["cfg"])
    cfg2["systems"][0]["collectors"]["splunk"]["required_retention_days"] = 30
    with pytest.raises(service.Refused) as e:
        _publish(ec, p, cfg=cfg2)
    assert e.value.code == "config_changed", "the pin covers what decides the report, not only its text"


def test_the_ledger_is_append_only_hash_chained_on_the_shared_helper_and_holds_no_secret(ec):
    import inspect

    from tools.evidence_collectors import ledger, service

    p = _prepare(ec)
    _publish(ec, p)
    conn = ledger.connection()
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE ec_events SET reason = 'x'")
        conn.rollback() if hasattr(conn, "rollback") else None
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM ec_events")
        conn.rollback() if hasattr(conn, "rollback") else None
        rows = ledger.read(conn)
    finally:
        conn.close()
    assert [r["event"] for r in rows] == ["prepared", "published"]
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    text = json.dumps(rows)
    for v in SECRETS.values():
        assert v not in text
    # A tampered report is a chain problem, never walked through.
    bad = dict(rows[0], payload_json=rows[0]["payload_json"].replace("satisfied", "other_than_satisfied", 1))
    assert ledger.chain([bad, rows[1]])["_problems"]
    assert "stream_chain.order_streams" in inspect.getsource(ledger.chain)
    v = service.system_view("syn-dib-01", reader(PUBLISHER), cfg=ec["cfg"])
    assert v["chain"]["intact"] and v["published"]["content_digest"] == p["content_digest"]


def test_what_a_reader_may_not_see_is_counted_never_described(ec):
    from tools.evidence_collectors import service

    v = service.systems_view(reader(PUBLISHER), cfg=ec["cfg"])
    assert [s["id"] for s in v["systems"]] == ["syn-dib-01"] and v["held_back"] == 2
    assert "syn-secret-01" not in json.dumps(v) and "syn-other-01" not in json.dumps(v)
    msgs = set()
    for sid in ("syn-secret-01", "syn-other-01", "no-such-01"):
        with pytest.raises(service.Refused) as e:
            service.system_view(sid, reader(PUBLISHER), cfg=ec["cfg"])
        msgs.add((e.value.status, e.value.code, e.value.message))
    assert msgs == {(404, "not_found", "no such system")}, "unknown and hidden read the same"
    assert service.systems_view(None, cfg=ec["cfg"])["systems"] == []
    assert service.systems_view(reader(dict(PUBLISHER, clearance_level="BOGUS")), cfg=ec["cfg"])["systems"] == []
    m = service.systems_view(service.mcp_reader("x"), cfg=ec["cfg"])
    assert {s["id"] for s in m["systems"]} == {"syn-dib-01", "syn-other-01"} and m["held_back"] == 1


# ── console, twin, MCP ───────────────────────────────────────────────────────


def _page_app(user):
    """The blueprint as the dashboard mounts it, its component guard included: a tenant's person needs IL4 and a
    canvas grant, given here as an administrator would (canvas_access.grant_access)."""
    from tools.dashboard.app import app as dash
    from tools.evidence_collectors.blueprint import ec_bp
    from tools.security.canvas_access import grant_access

    user = dict(user, impact_level="IL5")
    grant_access(user["tenant_id"], "user", user["id"], "evidence_collectors", "read", granted_by="test-admin")

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

    app.register_blueprint(ec_bp)
    return app.test_client()


def test_the_routes_and_the_page(ec):
    p = _prepare(ec)
    a, b, o = _page_app(PREPARER), _page_app(PUBLISHER), _page_app(OUTSIDER)
    html = b.get("/evidence-collectors/?system=syn-dib-01").get_data(as_text=True)
    for want in ("decl-wft-ec-evidence-publish", "icos-objective-evidence/v1", "evidence_collectors.ec_api_publish",
                 "evidence_collectors.ec_api_prepare", 'data-system="syn-dib-01"', p["content_digest"],
                 "2 system(s) not shown", "AWS Config", "Microsoft Graph", "Splunk", "SCIM"):
        assert want in html, want
    for v in SECRETS.values():
        assert v not in html
    assert "syn-secret-01" not in html and "syn-other-01" not in html
    assert "/api/" not in (ROOT / "tools/dashboard/templates/evidence_collectors/index.html").read_text(encoding="utf-8")
    assert o.get("/evidence-collectors/api/systems/syn-dib-01").status_code == 404
    r = a.post("/evidence-collectors/api/systems/syn-dib-01/publish",
               json={"decision": "publish", "reason": "mine", "digest": p["content_digest"]})
    assert r.status_code in (403, 409)
    r = b.post("/evidence-collectors/api/systems/syn-dib-01/publish",
               json={"decision": "publish", "reason": "read it (test)", "digest": p["content_digest"]})
    assert r.status_code == 200, r.get_data(as_text=True)
    got = b.get("/evidence-collectors/api/systems/syn-dib-01/bundle").get_json()
    assert got["publication"]["signed_by"] == PUBLISHER["id"]
    assert o.get("/evidence-collectors/api/systems/syn-dib-01/bundle").status_code == 404


def test_the_twin_observes_with_threads_and_replays(ec):
    import tools.twin_core.adapters.evidence_collectors  # noqa: F401
    from tools.twin_core import governance

    adapter = governance.adapter_for("evidence_collectors")
    assert adapter.observes == ("evidence_collectors.objective_evidence",)
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["evidence_collectors.ledger"]["status"] == "verified"
    assert chk["evidence_collectors.syn-dib-01"]["status"] == "unverified", "nothing published is never a pass"
    assert "evidence_collectors.syn-secret-01" not in chk, "above the twin's marking: counted, not described"
    real = datetime.now(timezone.utc)          # the twin judges against the clock, so the sources speak in it
    st = ec["stub"].state
    st["aws"] = {k: (v[0], real - timedelta(hours=1)) for k, v in st["aws"].items()}
    st["aws"]["encrypted-volumes"] = ("NON_COMPLIANT", real - timedelta(hours=1))
    for d in st["devices"]:
        d["lastSyncDateTime"] = (real - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = _prepare(ec, now=real)
    pub = _publish(ec, p)
    env2 = governance.observe(adapter)
    chk = {x["id"]: x for x in env2["extra"]["checks"]}
    assert chk["evidence_collectors.syn-dib-01"]["status"] == "verified"
    f = [v for v in env2["violations"] if v["rule_id"] == "evidence_collectors.other_than_satisfied"]
    assert f and all(v["thread_id"] == f"evidence_collectors:{pub['event_id']}" for v in f)
    res = governance.resolve_thread(f[0]["thread_id"])
    assert res["resolved"] and res["record"]["event"] == "published" and "payload_json" not in res["record"]
    assert governance.replay(adapter, env2["extra"]["observation_id"])["match"]
    b, o = _page_app(PUBLISHER), _page_app(OUTSIDER)
    assert b.get("/evidence-collectors/api/twin", query_string={"thread": f[0]["thread_id"]}).status_code == 200
    r = o.get("/evidence-collectors/api/twin", query_string={"thread": f[0]["thread_id"]})
    assert r.status_code == 404 and r.get_json()["reason"] == "no such thread"


def test_the_mcp_tools_read_and_only_prepare(ec):
    from tools.conformance.feature_audit import approval_policy
    from tools.mcp.gap_handlers import handle_evidence_collectors_status
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert {"evidence_collectors_status", "evidence_collectors_prepare"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["evidence_collectors_status"] is True
    assert tool_authorization("evidence_collectors_status")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["evidence_collectors_prepare"] is False
    policy = approval_policy(ROOT, "args/agent_approval_policy.yaml")
    assert policy["tiers"].get("evidence_collectors_prepare", policy["default"]) in policy["halts"]
    from tools.evidence_collectors import service

    service.prepare("syn-dib-01", actor(PREPARER), reader(PREPARER), cfg=ec["cfg"], now=NOW, resolve=fake_resolve)
    one = handle_evidence_collectors_status({"system": "syn-dib-01"})
    assert one["system"]["prepared"]["observations"] and "error" not in one
    assert handle_evidence_collectors_status({"system": "syn-secret-01"}) == {
        "error": "no such system", "code": "not_found", "refused": True}
    text = json.dumps(one)
    for v in SECRETS.values():
        assert v not in text
