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

import contextlib
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

from tools.evidence_collectors.credentials import resolve as REAL_RESOLVE  # before any fixture patches it

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 19, 9, 0, 0, tzinfo=timezone.utc)
ORG = "syn-dib"
PREPARER = {"id": "u-ent02-preparer", "role": "reviewer", "clearance_level": "CUI", "tenant_id": ORG}
PUBLISHER = {"id": "u-ent02-publisher", "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
PUBLISHER2 = {"id": "u-ent02-publisher2", "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
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
    raw["limits"] = {**(raw.get("limits") or {}), "min_collect_interval_s": 0}   # the throttle has its own test
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
    monkeypatch.setattr(service, "_LAST_LIVE", {}, raising=False)
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
    q = {"question": "Show all evidence_collectors objectives"}
    mine = b.post("/evidence-collectors/api/iqe-query", json=q).get_json()
    assert mine["ok"] and mine["row_count"] == len(got["entries"]) and "note" not in json.dumps(mine["results"])
    theirs = o.post("/evidence-collectors/api/iqe-query", json=q).get_json()
    assert theirs["ok"] and theirs["row_count"] == 0, "another org's IQE sees none of it"


def test_the_twin_observes_with_threads_and_replays(ec):
    import tools.twin_core.adapters.evidence_collectors  # noqa: F401
    from tools.twin_core import governance

    adapter = governance.adapter_for("evidence_collectors")
    assert adapter.observes == ("evidence_collectors.objective_evidence",)
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["evidence_collectors.ledger"]["status"] == "verified"
    # The platform observation names synthetic systems (every system here is one); syn-secret-01 is above the twin's
    # marking: counted, not described.
    assert chk["evidence_collectors.syn-dib-01"]["status"] == "unverified", "nothing published is never a pass"
    assert set(chk) == {"evidence_collectors.ledger", "evidence_collectors.syn-dib-01",
                        "evidence_collectors.syn-other-01"}
    assert env["extra"]["summary"]["evidence_held_back"] == 1 and "syn-secret-01" not in json.dumps(env)
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


def test_the_reader_s_org_and_clearance_are_read_fresh_from_their_own_row(ec, monkeypatch):
    """A dashboard API key's principal carries no tenant_id; the person's org and clearance come from their row
    (tools.dashboard.auth.get_user_by_id; the e2e reads it from the real dashboard_users on PostgreSQL)."""
    from tools.dashboard import auth
    from tools.evidence_collectors import service

    rows = {"u-ent02-keyed": {"id": "u-ent02-keyed", "tenant_id": ORG, "clearance_level": "CUI"}}
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid, tenant_id=None: rows.get(uid))
    app = Flask(__name__)
    with app.test_request_context("/"):
        g.current_user = {"id": "u-ent02-keyed", "role": "isso", "clearance_level": "CUI"}   # validate_api_key's
        r = service.current_reader()
    assert r["org"] == ORG and r["clearance"] == "CUI"
    assert [s["id"] for s in service.systems_view(r, cfg=ec["cfg"])["systems"]] == ["syn-dib-01"]
    with app.test_request_context("/"):
        g.current_user = {"id": "u-ent02-no-row", "role": "isso"}             # no row, no tenant: sees nothing
        assert service.systems_view(service.current_reader(), cfg=ec["cfg"])["systems"] == []


def test_a_tenant_person_s_canvas_grant_is_read_inside_their_own_request(wf_db):
    """canvas_access.check_access read the grants through the row-security predicate of the signed-in person's
    context, which names a classification column canvas_access_grants does not have: the query raised and every
    tenant person was refused (icx-ent-02's e2e found it). The grants are read without the label predicate; each
    query names its tenant."""
    from tools.db import storage
    from tools.security import canvas_access

    canvas_access.grant_access(ORG, "user", "u-ent02-granted", "evidence_collectors", "read", granted_by="test-admin")
    real = storage.get_connection

    def predicated(*a, **k):
        conn = real(*a, **k)
        conn.set_security_context(type("Ctx", (), {"tenant_id": None, "classification": "CUI",
                                                   "compartments": frozenset()})())
        return conn

    storage.get_connection = predicated
    try:
        assert canvas_access.check_access("u-ent02-granted", ORG, "evidence_collectors", "read", "reviewer")
        assert not canvas_access.check_access("u-ent02-granted", "another-org", "evidence_collectors", "read",
                                              "reviewer"), "the tenant still scopes the grant"
    finally:
        storage.get_connection = real



# ── the adversarial review's findings (each a fail-closed test) ──────────────


def _publish_one(ec):
    p = _prepare(ec, now=datetime.now(timezone.utc))
    return p, _publish(ec, p)


def _fresh_sources(ec):
    real = datetime.now(timezone.utc)
    st = ec["stub"].state
    st["aws"] = {k: (v[0], real - timedelta(hours=1)) for k, v in st["aws"].items()}
    st["aws"]["encrypted-volumes"] = ("NON_COMPLIANT", real - timedelta(hours=1))
    for d in st["devices"]:
        d["lastSyncDateTime"] = (real - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_twin_route_shows_a_reader_only_their_systems_and_the_platform_twin_names_none(ec):
    """HIGH 1: /evidence-collectors/api/twin judged every org's systems for any person with the canvas grant."""
    import tools.twin_core.adapters.evidence_collectors  # noqa: F401
    from tools.twin_core import governance

    _fresh_sources(ec)
    _publish_one(ec)
    o = _page_app(OUTSIDER).get("/evidence-collectors/api/twin").get_json()
    text = json.dumps(o)
    assert "syn-dib-01" not in text and "sp800-" not in text and "evidence_collectors:ece-" not in text
    assert o["summary"]["evidence_held_back"] >= 1
    mine = _page_app(PUBLISHER).get("/evidence-collectors/api/twin").get_json()
    assert "evidence_collectors.syn-dib-01" in {c["id"] for c in mine["checks"]}
    assert any(f["rule_id"] == "evidence_collectors.other_than_satisfied" and "sp800-" in f["title"]
               for f in mine["findings"])
    # MED-LOW 3: a CUSTOMER system (not synthetic) reaches the platform observation (MCP twin_governance, the
    # observatory, replay) only as totals: no id, ordinal, event, time or digest of it.
    ec["cfg"]["systems"][0]["synthetic"] = False
    from tools.evidence_collectors import ledger

    rows = [r for r in ledger.read() if r["system_id"] == "syn-dib-01"]
    env = governance.observe(governance.adapter_for("evidence_collectors"), record=False)
    plat = json.dumps(env)
    assert "syn-dib-01" not in plat and "sp800-" not in plat and "system-1" not in plat
    for r in rows:
        for v in (r["event_id"], r["content_digest"], r["created_at"], r["row_hash"]):
            assert str(v) not in plat, "nothing that identifies a customer system's rows"
    summ = env["extra"]["summary"]
    # One customer system: below K, so nothing but "fewer than 5" (a tenant could subtract its own from a total).
    assert summ["customer_systems"] == "fewer than 5 customer orgs" and "customer_published" not in summ
    assert not [v for v in env["violations"] if v["rule_id"].startswith("evidence_collectors.customers")]
    assert governance.replay(governance.adapter_for("evidence_collectors"),
                             governance.observe(governance.adapter_for("evidence_collectors"))["extra"]
                             ["observation_id"])["match"], "the totals replay"


def test_an_agent_reads_and_prepares_only_synthetic_systems(ec):
    """MED-HIGH 2: any agent:* reader saw, and could collect from, every org's systems at or below CUI. An agent is
    in no org and names itself, so it sees and prepares synthetic systems only."""
    from tools.evidence_collectors import service
    from tools.mcp.gap_handlers import handle_evidence_collectors_prepare, handle_evidence_collectors_status

    cfg = ec["cfg"]
    real = service.mcp_reader("drafter")
    assert "syn-dib-01" in {s["id"] for s in service.systems_view(real, cfg=cfg)["systems"]}
    cfg["systems"][0]["synthetic"] = False                  # a customer's system
    v = service.systems_view(real, cfg=cfg)
    assert "syn-dib-01" not in {s["id"] for s in v["systems"]} and v["held_back"] == 2
    with pytest.raises(service.Refused) as e:
        service.prepare("syn-dib-01", {"id": "agent:drafter", "role": "service"}, real, cfg=cfg, now=NOW,
                        resolve=fake_resolve)
    assert (e.value.status, e.value.message) == (404, "no such system")
    cfg["systems"][0]["synthetic"] = "yes"                  # only the boolean true is synthetic
    assert "syn-dib-01" not in {s["id"] for s in service.systems_view(real, cfg=cfg)["systems"]}
    cfg["systems"][2]["synthetic"] = False
    for h in (handle_evidence_collectors_status, handle_evidence_collectors_prepare):
        assert h({"agent_id": "other", "system": "syn-other-01"}) == {
            "error": "no such system", "code": "not_found", "refused": True}
    assert "syn-other-01" not in json.dumps(handle_evidence_collectors_status({"agent_id": "other"}))
    from tools.evidence_collectors import config
    raw = {k: v for k, v in cfg.items() if not k.startswith("_")}
    raw["systems"][0]["agents"] = ["drafter"]
    with pytest.raises(config.ConfigError):
        config.validate(copy.deepcopy(raw)), "no per-agent grant: an agent's name is its caller's say-so"


def test_iqe_with_no_signed_in_reader_reads_nothing(ec):
    import tools.iqe.adapters.evidence_collectors as iqe

    _fresh_sources(ec)
    _publish_one(ec)
    assert iqe.objectives_adapter() == [], "no reader: fail closed"


def test_live_collection_is_throttled_per_system(ec):
    from tools.evidence_collectors import service

    ec["cfg"]["limits"] = {"min_collect_interval_s": 300}
    _prepare(ec)
    with pytest.raises(service.Refused) as e:
        _prepare(ec, now=NOW + timedelta(seconds=10))
    assert (e.value.status, e.value.code) == (429, "too_soon")
    service._LAST_LIVE.clear()                     # another process: the ledger's live report still counts
    with pytest.raises(service.Refused):
        _prepare(ec, now=NOW + timedelta(seconds=10))
    _prepare(ec, now=NOW + timedelta(seconds=301))
    # A refused attempt counts too: a failing source is not hammered.
    seen = len(ec["stub"].seen)
    with pytest.raises(service.Refused):
        _prepare(ec, now=NOW + timedelta(seconds=302))
    assert len(ec["stub"].seen) == seen, "a throttled attempt sends nothing"
    # Recorded observations (the e2e seed) are not a live read and are not throttled.
    service.prepare("syn-dib-01", actor(PREPARER), reader(PREPARER), cfg=ec["cfg"], now=NOW + timedelta(seconds=303),
                    observations=_collect(ec))


def test_a_twin_thread_names_no_system_person_or_reason(ec):
    """MED 3: the generic thread resolver returned system_id, actor and reason to any caller."""
    from tools.twin_core import governance

    _, pub = _publish_one(ec)
    res = governance.resolve_thread(f"evidence_collectors:{pub['event_id']}")
    assert res["resolved"]
    assert not {"system_id", "stream", "actor", "reason"} & set(res["record"])
    page = _page_app(PUBLISHER).get("/evidence-collectors/api/twin",
                                    query_string={"thread": f"evidence_collectors:{pub['event_id']}"}).get_json()
    assert page["record"]["actor"] == PUBLISHER["id"], "the page, for a reader who may see it, says who"


def test_splunk_retention_never_passes_on_a_non_finite_number(ec):
    from tools.evidence_collectors import config

    for bad in ("nan", "NaN", "inf", "Infinity", "-inf"):
        ec["stub"].state["indexes"]["audit"]["frozenTimePeriodInSecs"] = bad
        by = {o["fact"]: o for o in _collect(ec)}
        assert by["splunk:audit_retention_met"]["value"] is None, bad
    raw = {k: v for k, v in ec["cfg"].items() if not k.startswith("_")}
    raw["systems"][0]["collectors"]["splunk"]["required_retention_days"] = float("nan")
    with pytest.raises(config.ConfigError):
        config.validate(copy.deepcopy(raw))


def test_source_strings_become_tokens_and_counts_must_be_integers(ec, monkeypatch):
    """MED 5: what a source says is never echoed verbatim into a report, the page, MCP or the twin."""
    from tools.evidence_collectors.collectors import aws_config

    real = aws_config._all

    def evil(spec, op, names, key, creds, now):
        rows = real(spec, op, names, key, creds, now)
        for r in rows:
            if "Compliance" in r:
                r["Compliance"] = {"ComplianceType": "<script>x</script>" * 50,
                                   "ComplianceContributorCount": {"CappedCount": {"deep": "x" * 100}}}
        return rows

    monkeypatch.setattr(aws_config, "_all", evil)
    ec["stub"].state["issuer_override"] = None
    obs = _collect(ec)
    text = json.dumps(obs)
    assert "<script>" not in text
    a = next(o for o in obs if o["fact"] == "aws-config:CLOUD_TRAIL_ENABLED")
    assert a["value"] is None and a["detail"]["compliance_type"] == "other"
    assert a["detail"]["noncompliant_resources"] is None
    o = {x["fact"]: x for x in obs}
    assert o["oidc:pkce_s256"]["why"] in ("S256 is advertised", "S256 is not advertised")


def test_a_deep_non_finite_slow_or_oversized_response_is_an_unread_source(stub, monkeypatch):
    """MED 6 and the probes: nothing a source sends escapes as a crash or runs past a deadline or a byte budget."""
    import time

    from tools.evidence_collectors import transport

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return

        def do_GET(self):
            if self.path == "/slow":
                self.send_response(200)
                self.send_header("Content-Length", "10")
                self.end_headers()
                for _ in range(10):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.4)
                return
            body = {"/deep": b"[" * 100000 + b"]" * 100000, "/nan": b'{"a": NaN}', "/inf": b'{"a": Infinity}',
                    "/big": b'{"a": "' + b"x" * 5000 + b'"}'}[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        for p in ("/deep", "/nan", "/inf"):
            with pytest.raises(transport.CollectorError):
                transport.request_json("GET", base + p)
        monkeypatch.setattr(transport, "DEADLINE_S", 1.0)
        t0 = time.monotonic()
        with pytest.raises(transport.CollectorError, match="deadline"):
            transport.request_json("GET", base + "/slow")
        assert time.monotonic() - t0 < 3, "a slow drip is cut at the request's wall-clock deadline"
        with transport.budget(seconds=30, max_bytes=1000), pytest.raises(transport.CollectorError, match="budget"):
            transport.request_json("GET", base + "/big")
        with transport.budget(seconds=0.0001, max_bytes=10**6):
            time.sleep(0.01)
            with pytest.raises(transport.CollectorError, match="deadline"):
                transport.request_json("GET", base + "/nan")
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_review_s_lesser_findings_each_fail_closed(ec, monkeypatch):
    from tools.evidence_collectors import config, credentials, transport
    from tools.evidence_collectors.collectors.base import instant

    # A proxy in the environment is never used: the bearer token would travel through it.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert {o["fact"]: o for o in _collect(ec)}["scim:users_identified"]["value"] is True
    # Endpoints are tied to their services (or a loopback stub).
    raw = {k: v for k, v in ec["cfg"].items() if not k.startswith("_")}
    raw["systems"][0]["synthetic"] = False           # a customer's system: its service's own hosts
    for coll, key, bad in (("aws-config", "endpoint", "https://config.us-gov-west-1.evil.example"),
                           ("aws-config", "endpoint", "https://config.us-east-1.amazonaws.com"),
                           ("intune", "login_base", "https://login.evil.example"),
                           ("intune", "graph_base", "https://graph.evil.example")):
        r = copy.deepcopy(raw)
        r["systems"][0]["collectors"][coll][key] = bad
        with pytest.raises(config.ConfigError):
            config.validate(r)
    # Endpoints a REMOTE discovery document names must be https.
    from tools.evidence_collectors.collectors import oidc_scim
    assert oidc_scim._https("http://127.0.0.1:1/x", issuer="https://idp.example") is False
    # isEncrypted missing or not a bool is unverified; SCIM's "active" as a string is read as such.
    st = ec["stub"].state
    st["devices"][0].pop("isEncrypted")
    st["users"][0]["active"] = "false"
    st["users"][1]["active"] = "TRUE"
    st["users"][2]["groups"] = []
    by = {o["fact"]: o for o in _collect(ec)}
    assert by["intune:all_encrypted"]["value"] is None
    assert by["scim:users_identified"]["detail"]["active"] == 4
    # 'none' in any case is an unsigned ID token.
    real_req = transport.request_json

    def disco(method, url, **k):
        doc = real_req(method, url, **k)
        if url.endswith("openid-configuration"):
            doc["id_token_signing_alg_values_supported"] = ["RS256", "NONE"]
        return doc

    monkeypatch.setattr(transport, "request_json", disco)
    assert {o["fact"]: o for o in _collect(ec)}["oidc:signed_id_tokens"]["value"] is False
    monkeypatch.setattr(transport, "request_json", real_req)
    # A credential error repeats the scheme only, never the reference.
    import sys
    import types

    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_password=lambda svc, user: None))
    with pytest.raises(credentials.CredentialError) as e:
        REAL_RESOLVE("keyring:secret-service-name/secret-user")
    assert "secret-service-name" not in str(e.value) and "secret-user" not in str(e.value)
    # RFC 3339 exactly: a colon in the offset, an hour of 0-23, no space, the seconds.
    for bad in ("2026-11-19T00:00:00+0530", "2026-11-19T24:00:00Z", "2026-11-19T00:00:00 Z", "2026-11-19T00:00Z"):
        assert instant(bad) is None, bad
    assert instant("2026-11-19T00:00:00.1234567+05:30") is not None
    # A next link's scheme is read without case; a port out of range is refused.
    with pytest.raises(transport.CollectorError):
        transport.url_for("https://graph.microsoft.com", "HTTPS://evil.example/x")
    with pytest.raises(transport.CollectorError):
        transport.check_base("http://localhost:99999")
    # Identifiers are ASCII and matched whole: a Unicode digit or a trailing newline is refused.
    for bad_id in ("syn-dib-\u0661", "syn-dib-01\n"):
        r = copy.deepcopy(raw)
        r["systems"][0]["id"] = bad_id
        with pytest.raises(config.ConfigError):
            config.validate(r)
    with pytest.raises(credentials.CredentialError):
        credentials.check_ref("keyring:svc/us\u00e9r")


def test_a_decision_claims_the_ledger_before_its_step_and_a_lost_step_exports_nothing(ec, monkeypatch):
    """LOW: the step was taken, then the ledger written; a concurrent decision left a decided step with no effect."""
    from tools.evidence_collectors import ledger, service
    from tools.workflow_hitl import declared

    p = _prepare(ec)
    before = len(declared.list_steps())
    real_append = ledger.append

    def lost_race(conn, **k):
        if k["event"] in ("published", "refused"):
            raise ledger.LedgerRefused(409, "concurrent", "this system's evidence changed while you were deciding")
        return real_append(conn, **k)

    monkeypatch.setattr(ledger, "append", lost_race)
    with pytest.raises(service.Refused) as e:
        _publish(ec, p)
    assert e.value.code == "concurrent"
    assert len(declared.list_steps()) == before, "no step is recorded for a decision the ledger refused"
    monkeypatch.setattr(ledger, "append", real_append)

    def unrecorded(*a, **k):
        raise declared.StepRefused(503, "store_unavailable", "the decision could not be recorded")

    monkeypatch.setattr(declared, "take_step", unrecorded)
    with pytest.raises(service.Refused):
        _publish(ec, p)
    assert service.published_bundle("syn-dib-01", reader(PUBLISHER), cfg=ec["cfg"]) is None, \
        "a published row whose step is not decided exports nothing"


# ── the re-review's findings ──────────────────────────────────────────────────


def test_a_decision_whose_step_was_never_recorded_decides_nothing_anywhere(ec, monkeypatch):
    """MED 1: with take_step failing after the ledger row, the twin said verified, IQE returned the objectives and a
    re-publish was refused already_decided."""
    import tools.iqe.adapters.evidence_collectors as iqe
    import tools.twin_core.adapters.evidence_collectors  # noqa: F401
    from tools.evidence_collectors import service
    from tools.workflow_hitl import declared

    _fresh_sources(ec)
    p = _prepare(ec, now=datetime.now(timezone.utc))
    real_take = declared.take_step

    def unrecorded(*a, **k):
        raise declared.StepRefused(503, "store_unavailable", "the decision could not be recorded")

    monkeypatch.setattr(declared, "take_step", unrecorded)
    with pytest.raises(service.Refused):
        _publish(ec, p)
    monkeypatch.setattr(declared, "take_step", real_take)
    lost = next(r for r in service.ledger.read() if r["event"] == "published")
    v = service.system_view("syn-dib-01", reader(PUBLISHER), cfg=ec["cfg"])
    assert v["published"] is None and v["next"] == ["publish"], "the preparation is still open"
    mine = _page_app(PUBLISHER).get("/evidence-collectors/api/twin").get_json()
    chk = {c["id"]: c for c in mine["checks"]}
    assert chk["evidence_collectors.syn-dib-01"]["status"] == "unverified", "never verified on a lost step"
    marks = [r for r in service.ledger.read() if r["event"] == "step_lost"]
    assert [service.ledger.payload(r)["detail"]["decision_event"] for r in marks] == [lost["event_id"]], \
        "only an explicit step_lost row reopens the report"
    assert not [f for f in mine["findings"] if f["rule_id"] == "evidence_collectors.other_than_satisfied"]
    app = Flask(__name__)
    with app.test_request_context("/"):
        g.current_user = {"id": PUBLISHER["id"], "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
        assert iqe.objectives_adapter() == [], "IQE reads nothing a lost step published"
    # A person may decide it again, and then it is published everywhere.
    out = _publish(ec, p)
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == PUBLISHER["id"]
    with app.test_request_context("/"):
        g.current_user = {"id": PUBLISHER["id"], "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
        assert len(iqe.objectives_adapter()) == len(out["bundle"]["entries"])


def test_iqe_reads_nothing_from_a_chain_that_does_not_verify(ec, monkeypatch):
    import tools.iqe.adapters.evidence_collectors as iqe
    from tools.evidence_collectors import ledger

    _fresh_sources(ec)
    _publish_one(ec)
    later = _prepare(ec, now=datetime.now(timezone.utc) + timedelta(seconds=1))   # a row AFTER the publication
    real = ledger.read

    def tampered(*a, **k):
        rows = real(*a, **k)
        return [dict(r, reason="rewritten") if r["event_id"] == later["event_id"] else r for r in rows]

    monkeypatch.setattr(ledger, "read", tampered)
    app = Flask(__name__)
    with app.test_request_context("/"):
        g.current_user = {"id": PUBLISHER["id"], "role": "isso", "clearance_level": "CUI", "tenant_id": ORG}
        assert iqe.objectives_adapter() == []


def _drip_server(handler_body):
    import socket as so

    srv = so.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    stop = threading.Event()

    def run():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handler_body, args=(c, stop), daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return srv, stop


def test_the_deadline_holds_for_the_whole_exchange_headers_and_tls_too(monkeypatch):
    """MED 2: the deadline was checked only in the body loop: a header drip held a 2 s request for 24.5 s."""
    import time

    from tools.evidence_collectors import transport

    def header_drip(c, stop):
        with contextlib.suppress(OSError):
            c.recv(4096)
            for b in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 200:
                if stop.is_set():
                    break
                c.sendall(bytes([b]))
                time.sleep(0.3)
        c.close()

    def tls_drip(c, stop):
        with contextlib.suppress(OSError):
            c.recv(4096)                              # the ClientHello
            c.sendall(b"\x16\x03\x03\x40\x00")       # a handshake record of 16 KiB...
            for _ in range(200):                     # ...that arrives a byte at a time
                if stop.is_set():
                    break
                c.sendall(b"\x00")
                time.sleep(0.3)
        c.close()

    monkeypatch.setattr(transport, "DEADLINE_S", 1.5)
    for body, scheme in ((header_drip, "http"), (tls_drip, "https")):
        srv, stop = _drip_server(body)
        try:
            t0 = time.monotonic()
            with pytest.raises(transport.CollectorError, match="deadline"):
                transport.request_json("GET", f"{scheme}://127.0.0.1:{srv.getsockname()[1]}/x")
            assert time.monotonic() - t0 < 3.5, f"{scheme}: cut at the deadline, not per byte"
        finally:
            stop.set()
            srv.close()


def test_synthetic_is_a_boolean_and_a_synthetic_system_reads_only_loopback_stubs(ec):
    """LOW-MED 4: an agent may collect from a synthetic system, so a synthetic system can never name a real service
    (the shipped syn-dib-01 named AWS GovCloud and, by default, Microsoft's login and Graph hosts)."""
    from tools.evidence_collectors import config

    shipped = config.validate(__import__("yaml").safe_load((ROOT / "args/evidence_collectors.yaml").read_text()))
    assert all(s["synthetic"] is True for s in shipped["systems"] if s["id"] == "syn-dib-01")
    raw = {k: v for k, v in ec["cfg"].items() if not k.startswith("_")}
    cases = [("synthetic", "yes"), ("synthetic", 1)]
    for key, val in cases:
        r = copy.deepcopy(raw)
        r["systems"][0][key] = val
        with pytest.raises(config.ConfigError):
            config.validate(r)
    for coll, key, real in (("aws-config", "endpoint", "https://config.us-gov-west-1.amazonaws.com"),
                            ("intune", "login_base", "https://login.microsoftonline.us"),
                            ("intune", "graph_base", None),       # absent: the default is Microsoft's own host
                            ("oidc-scim", "issuer", "https://idp.syn-dib.example"),
                            ("splunk", "base", "https://siem.syn-dib.example:8089")):
        r = copy.deepcopy(raw)
        spec = r["systems"][0]["collectors"][coll]
        if real is None:
            spec.pop(key)
        else:
            spec[key] = real
        with pytest.raises(config.ConfigError, match="synthetic"):
            config.validate(r)
        r["systems"][0]["synthetic"] = False
        if real is not None and coll != "oidc-scim" and coll != "splunk":
            config.validate(copy.deepcopy(r))             # a customer's system may name the real service


def test_a_caller_named_step_id_is_for_a_new_step_only(ec):
    """LOW 5: take_step(step_id=) must not decide a step that already has a history (someone's proposal)."""
    from tools.evidence_collectors.constants import SURFACE, TEMPLATE
    from tools.workflow_hitl import declared

    sid = declared.propose(TEMPLATE, surface=SURFACE, subject="objective_evidence:syn-dib-01",
                           actor=actor(PREPARER))["step_id"]
    with pytest.raises(declared.StepRefused) as e:
        declared.take_step(TEMPLATE, surface=SURFACE, decision="publish", reason="x (test)", actor=actor(PUBLISHER),
                           separated_from={}, step_id=sid)
    assert e.value.code == "step_exists"
    with pytest.raises(declared.StepRefused):
        declared.take_step(TEMPLATE, surface=SURFACE, decision="publish", reason="x (test)", actor=actor(PUBLISHER),
                           separated_from={}, step_id="step-" + "\u0661" * 16)


# ── the verification's findings ───────────────────────────────────────────────


def test_a_decision_waiting_for_its_step_blocks_others_and_is_never_overturned(ec, monkeypatch):
    """MED: while A's decision row lacked its step, replay reopened the report, B decided too, and when A's step
    landed B's exported bundle was withdrawn and B's decision silently dropped."""
    from tools.evidence_collectors import service
    from tools.workflow_hitl import declared

    p = _prepare(ec)
    real = declared.take_step
    seen = {}

    def delayed(*a, **k):
        if k["actor"]["id"] == PUBLISHER["id"]:
            # A's ledger row is written, its step not yet: B tries now.
            v = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])
            seen["view"] = (v["next"], v["published"], (v["pending"] or {}).get("event"))
            with pytest.raises(service.Refused) as e:
                _publish(ec, p, who=PUBLISHER2)
            seen["b"] = e.value.code
            assert service.published_bundle("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"]) is None
        return real(*a, **k)

    monkeypatch.setattr(declared, "take_step", delayed)
    out = _publish(ec, p, decision="refuse", reason="A refuses (test)")
    assert seen == {"view": (["wait"], None, "refused"), "b": "pending"}, seen
    assert out["event"] == "refused" and out["decided_by"] == PUBLISHER["id"]
    v = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])
    assert v["prepared"]["decided"]["actor"] == PUBLISHER["id"] and v["published"] is None, "the result is A's"
    with pytest.raises(service.Refused) as e:
        _publish(ec, p, who=PUBLISHER2)
    assert e.value.code == "already_decided"


def test_an_unreadable_step_store_blocks_and_never_reopens(ec, monkeypatch):
    from tools.evidence_collectors import service
    from tools.workflow_hitl import declared

    _fresh_sources(ec)
    p, pub = _publish_one(ec)

    def unreadable(step_id):
        raise RuntimeError("store down")

    real_state = declared.step_state
    monkeypatch.setattr(declared, "step_state", unreadable)
    v = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])
    assert v["published"] is None and v["next"] == ["wait"], "unreadable: nothing published, nothing reopened"
    with pytest.raises(service.Refused) as e:
        _publish(ec, p, who=PUBLISHER2)
    assert e.value.code == "pending"
    with pytest.raises(service.Refused) as e:
        service.mark_step_lost("syn-dib-01", pub["event_id"], actor={"id": "agent:ops", "role": "service"},
                               asked_by="u-ent02-publisher2", os_user="ops", reason="crash (test)", cfg=ec["cfg"], now=NOW + timedelta(days=30))
    assert e.value.code == "store_unavailable", "an operator cannot void a decision whose step cannot be read"
    monkeypatch.setattr(declared, "step_state", real_state)
    assert service.published_bundle("syn-dib-01", reader(PUBLISHER), cfg=ec["cfg"]) is not None, "it was A's"


def test_the_operator_voids_a_crashed_decision_only_after_its_grace_and_only_when_unrecorded(ec, monkeypatch):
    from tools.evidence_collectors import cli, service
    from tools.workflow_hitl import declared

    p = _prepare(ec)

    def crash(*a, **k):
        raise SystemExit("the process died between the ledger row and the step")

    real_take = declared.take_step
    monkeypatch.setattr(declared, "take_step", crash)
    with pytest.raises(SystemExit):
        _publish(ec, p)
    monkeypatch.setattr(declared, "take_step", real_take)
    v = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])
    assert v["next"] == ["wait"]
    eid = v["pending"]["event_id"]
    ops = {"id": "agent:ops", "role": "service"}
    with pytest.raises(service.Refused) as e:
        service.mark_step_lost("syn-dib-01", eid, actor=ops, asked_by="u-ent02-publisher2", os_user="ops", reason="crash (test)", cfg=ec["cfg"])
    assert e.value.code == "too_soon", "an in-flight decision is never raced"
    with pytest.raises(service.Refused) as e:
        service.mark_step_lost("syn-dib-01", "ece-0000000000000000", actor=ops, asked_by="u-ent02-publisher2", os_user="ops", reason="x", cfg=ec["cfg"],
                               now=datetime.now(timezone.utc) + timedelta(hours=1))
    assert e.value.code == "not_pending"
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    out = service.mark_step_lost("syn-dib-01", eid, actor=ops, asked_by="u-ent02-publisher2", os_user="ops", reason="crash (test)", cfg=ec["cfg"], now=later)
    assert out["voided"] == eid
    assert service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])["next"] == ["publish"]
    assert _publish(ec, p, who=PUBLISHER2)["event"] == "published"
    assert "step-lost" in cli.__doc__


def test_customer_totals_need_five_systems_and_are_banded(ec):
    """LOW: exact customer totals let a tenant subtract its own system or match a single event's delta."""
    import tools.twin_core.adapters.evidence_collectors  # noqa: F401
    from tools.evidence_collectors import service
    from tools.twin_core import governance

    assert [service.band(n) for n in (0, 1, 4, 5, 9, 10, 24, 25, 99, 100)] == [
        "0", "1-4", "1-4", "5-9", "5-9", "10-24", "10-24", "25-99", "25-99", "100+"]
    _fresh_sources(ec)
    _publish_one(ec)
    base = ec["cfg"]["systems"][0]
    base["synthetic"] = False
    for i in range(2, 6):
        ec["cfg"]["systems"].append(dict(copy.deepcopy(base), id=f"cust-{i:02d}"))
    # Five customer systems of ONE org: still below K, which counts distinct orgs.
    one = governance.observe(governance.adapter_for("evidence_collectors"), record=False)["extra"]["summary"]
    assert one["customer_systems"] == "fewer than 5 customer orgs" and "customer_published" not in one
    for i, s_ in enumerate(ec["cfg"]["systems"][3:], start=2):
        s_["org"] = f"cust-org-{i}"
    env = governance.observe(governance.adapter_for("evidence_collectors"), record=False)
    summ = env["extra"]["summary"]
    assert (summ["customer_systems"], summ["customer_published"], summ["customer_unpublished"]) == ("5-9", "1-4", "1-4")
    assert summ["customer_other_than_satisfied"] in ("1-4", "5-9", "10-24")
    text = json.dumps(env)
    assert "cust-0" not in text and "syn-dib-01" not in text
    hi = [v for v in env["violations"] if v["rule_id"] == "evidence_collectors.customers.other_than_satisfied"]
    assert hi and hi[0]["thread_id"].startswith("twin_obs:")
    assert hi[0]["title"].split(" ")[0] in ("1-4", "5-9", "10-24", "25-99", "100+"), "a band, never a count"


# ── the final check's lesser findings ─────────────────────────────────────────


def test_a_decision_voided_while_its_step_was_recording_is_never_reported_as_success(ec, monkeypatch):
    """(1) An operator marked the decision lost while take_step hung; the step then landed: publish() said success."""
    from tools.evidence_collectors import service
    from tools.workflow_hitl import declared

    _fresh_sources(ec)
    p = _prepare(ec, now=datetime.now(timezone.utc))
    real = declared.take_step

    def slow(*a, **k):
        pend = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])["pending"]
        service.mark_step_lost("syn-dib-01", pend["event_id"], actor={"id": "agent:ops", "role": "service"},
                               reason="looked crashed (test)", asked_by="u-ent02-publisher2", os_user="ops",
                               cfg=ec["cfg"], now=datetime.now(timezone.utc) + timedelta(hours=1))
        return real(*a, **k)

    monkeypatch.setattr(declared, "take_step", slow)
    with pytest.raises(service.Refused) as e:
        _publish(ec, p)
    assert e.value.code == "voided"
    monkeypatch.setattr(declared, "take_step", real)
    v = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])
    assert v["published"] is None and v["next"] == ["publish"]
    mine = _page_app(PUBLISHER).get("/evidence-collectors/api/twin").get_json()
    assert any(f["rule_id"] == "evidence_collectors.step_recorded_after_void" for f in mine["findings"])


def test_no_new_report_supersedes_a_pending_decision_and_a_step_on_a_closed_report_is_a_finding(ec, monkeypatch):
    from tools.evidence_collectors import ledger, service
    from tools.workflow_hitl import declared

    p = _prepare(ec)

    def crash(*a, **k):
        raise SystemExit("died")

    real = declared.take_step
    monkeypatch.setattr(declared, "take_step", crash)
    with pytest.raises(SystemExit):
        _publish(ec, p)
    monkeypatch.setattr(declared, "take_step", real)
    with pytest.raises(service.Refused) as e:
        _prepare(ec, now=NOW + timedelta(seconds=1))
    assert e.value.code == "pending", "a new report never supersedes one a decision is being recorded on"
    # A recorded step on a decision about a report that was no longer open decides nothing, and is a finding.
    eid = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])["pending"]["event_id"]
    service.mark_step_lost("syn-dib-01", eid, actor={"id": "agent:ops", "role": "service"}, reason="crash (test)",
                           asked_by="u-ent02-publisher", os_user="ops", cfg=ec["cfg"],
                           now=datetime.now(timezone.utc) + timedelta(hours=1))
    out = _publish(ec, p, who=PUBLISHER2)
    p2 = _prepare(ec, now=NOW + timedelta(seconds=2))
    conn = ledger.connection()
    try:
        ordered, _ = ledger.stream_rows("system:syn-dib-01", conn)
        stray = ledger.append(conn, stream="system:syn-dib-01", event="published", system_id="syn-dib-01",
                              actor=PUBLISHER2["id"], actor_kind="person", step_id=out["step"]["step_id"],
                              detail={"prepared_event": p["event_id"]}, prev_hash=ledger.head(ordered))
    finally:
        conn.close()
    st = service.replay_checked(ledger.stream_rows("system:syn-dib-01", ledger.connection())[0])
    assert [h["event_id"] for h in st["recorded_superseded"]] == [stray["event_id"]]
    assert st["prepared"]["event_id"] == p2["event_id"] and not st["prepared"]["decided"]
    mine = _page_app(PUBLISHER).get("/evidence-collectors/api/twin").get_json()
    assert any(f["rule_id"] == "evidence_collectors.step_on_superseded" for f in mine["findings"])


def test_the_step_lost_cli_records_the_os_user_and_who_asked(ec):
    """(2) The operator's path named only --agent, which anyone running the CLI chooses."""
    from tools.evidence_collectors import cli, ledger, service

    p = _prepare(ec)
    from tools.workflow_hitl import declared

    real = declared.take_step
    declared.take_step = lambda *a, **k: (_ for _ in ()).throw(SystemExit("died"))
    try:
        with pytest.raises(SystemExit):
            _publish(ec, p)
    finally:
        declared.take_step = real
    eid = service.system_view("syn-dib-01", reader(PUBLISHER2), cfg=ec["cfg"])["pending"]["event_id"]
    assert cli.main(["step-lost", "--system", "syn-dib-01", "--event", eid, "--reason", "crash (test)"]) == 1, \
        "refused without the person who asked"
    import os
    import pwd

    assert cli._os_user() == pwd.getpwuid(os.getuid()).pw_name
    with pytest.raises(service.Refused):
        service.mark_step_lost("syn-dib-01", eid, actor={"id": "agent:ops", "role": "service"}, reason="x",
                               asked_by="", os_user="ops", cfg=ec["cfg"])
    service.mark_step_lost("syn-dib-01", eid, actor={"id": "agent:ops", "role": "service"}, reason="crash (test)",
                           asked_by="u-ent02-publisher2", os_user=cli._os_user(), cfg=ec["cfg"],
                           now=datetime.now(timezone.utc) + timedelta(hours=1))
    mark = next(r for r in ledger.read() if r["event"] == "step_lost")
    det = ledger.payload(mark)["detail"]
    assert det["os_user"] == cli._os_user() and det["asked_by"] == "u-ent02-publisher2"
    assert "asked by u-ent02-publisher2" in mark["reason"]
