#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-gov-02 — Distribution and Delivery: version-specific SBOM retrieval.

Under test: ``tools/compliance/sbom_distribution.py`` and the three retrieval
routes it backs in ``tools/supply_chain/blueprint.py``.

The 2026 Minimum Elements fold the retired Access Control element into
Distribution and Delivery with a two-sided rule, and both sides are asserted
here because only testing one of them produces a plausible-looking wrong
implementation:

* access control **may** limit sharing with unauthorized parties — so there are
  DENY tests, at the HTTP boundary and not merely on the helper: an
  unauthenticated caller, a caller whose role has no supply-chain need, and a
  caller whose clearance does not dominate the artifact's marking;
* access control **must not** prevent sharing between authorized parties, nor
  block integration into trusted security tooling — so there are matching ALLOW
  tests: every supply-chain role, a service account, and a higher-clearance
  caller reading a lower-classification artifact (read-down, which an
  exact-match predicate would wrongly withhold).

"Exact bytes" is tested as byte equality against the file on disk, using a
fixture whose formatting no serializer would reproduce. That is a correctness
requirement, not fastidiousness: sbx-sig-01 signs the artifact's bytes, so a
document that round-tripped through json.load/json.dump would reach the
recipient with a signature that no longer verifies.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from flask import Flask, g

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

PROJECT_ID = "proj-sbx-gov-02"

#: Deliberately non-canonical: 4-space indent, a trailing newline, a non-ASCII
#: character and keys out of sorted order. json.dumps would not reproduce this
#: byte-for-byte under any default, so a test that passes proves the route
#: streamed the file rather than re-encoding a parsed copy.
SBOM_DOCUMENT = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "serialNumber": "urn:uuid:0f9a1c4e-2b6d-4c1a-9f3e-11ceb00da7a5",
    "version": 1,
    "metadata": {
        "timestamp": "2026-08-08T07:00:00Z",
        "properties": [
            {"name": "icdev:classification", "value": "CUI // SP-CTI"},
            {"name": "icdev:distribution",
             "value": "Distribution D -- Authorized DoD Personnel Only"},
        ],
    },
    "components": [{"type": "library", "name": "requêtes", "version": "2.31.0"}],
}


def _artifact_bytes() -> bytes:
    return (json.dumps(SBOM_DOCUMENT, indent=4, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sbom_env(tmp_path, monkeypatch):
    """A temp DB holding three SBOM records, and their artifacts on disk.

    The schema comes from ``tests/conftest.py::MINIMAL_ICDEV_SCHEMA`` rather
    than from DDL pasted in here, so the tables cannot drift away from the
    shape the rest of the suite — and the migration — produce. Only the schema
    creation talks to sqlite3 directly; every assertion afterwards goes through
    ``storage.get_connection`` so the ``%s``→``?`` translation the production
    queries rely on is exercised too.
    """
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_BASE_URL", "https://icdev.example.mil")

    raw = sqlite3.connect(str(db_path))
    raw.executescript(MINIMAL_ICDEV_SCHEMA)
    raw.commit()
    raw.close()

    art_dir = tmp_path / "compliance"
    art_dir.mkdir()
    payload = _artifact_bytes()

    v1 = art_dir / "sbom_v1.cdx.json"
    v1.write_bytes(b'{"bomFormat":"CycloneDX","specVersion":"1.5","version":1,"note":"older"}')
    v2 = art_dir / "sbom_v2.cdx.json"
    v2.write_bytes(payload)
    secret = art_dir / "sbom_secret.cdx.json"
    secret.write_bytes(b'{"bomFormat":"CycloneDX","specVersion":"1.5","classified":true}')

    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT INTO projects (id, name, type, classification, directory_path) "
        "VALUES (%s, %s, %s, %s, %s)",
        (PROJECT_ID, "SBX Gov 02", "api", "CUI", str(tmp_path)),
    )
    rows = [
        # (version, sbom_version, file_path, classification, tenant_id)
        ("1.0", None, str(v1), "CUI", None),
        ("2.0", None, str(v2), "CUI", None),
        ("3.0", None, str(secret), "SECRET", None),
    ]
    for version, sbom_version, path, marking, tenant in rows:
        conn.execute(
            "INSERT INTO sbom_records "
            "(project_id, version, sbom_version, format, file_path, component_count, "
            " vulnerability_count, classification, tenant_id, generated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (PROJECT_ID, version, sbom_version, "cyclonedx", path, 1, 0,
             marking, tenant, f"2026-08-0{version[0]}T00:00:00Z"),
        )
    conn.commit()

    ids = {}
    for row in conn.execute(
        "SELECT id, version FROM sbom_records WHERE project_id = %s", (PROJECT_ID,)
    ).fetchall():
        ids[str(row[1])] = row[0]

    try:
        yield {"conn": conn, "ids": ids, "payload": payload, "artifact": v2,
               "db_path": db_path, "dir": art_dir}
    finally:
        try:
            conn.commit()
        except Exception:
            pass
        conn.close()


@pytest.fixture
def client(sbom_env):
    """A bare Flask app carrying the supply-chain blueprint.

    The dashboard's auth hook is not registered — ``current_user`` is set
    directly, which is what that hook produces anyway — so these tests exercise
    the route's own decision rather than the login machinery.
    """
    from tools.supply_chain.blueprint import create_supply_chain_blueprint

    app = Flask(__name__)
    app.config["TESTING"] = True
    state = {"user": None}

    @app.before_request
    def _inject_user():
        g.current_user = state["user"]

    app.register_blueprint(create_supply_chain_blueprint())
    with app.test_client() as c:
        c.icdev_user = state  # tests mutate state["user"] to change identity
        yield c


def _as(client, **user):
    client.icdev_user["user"] = user or None


def _url(version="2.0"):
    return f"/api/supply_chain/sbom/{PROJECT_ID}/{version}"


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------

def test_retrieval_url_is_absolute_and_version_specific(monkeypatch):
    monkeypatch.setenv("ICDEV_BASE_URL", "https://icdev.example.mil/")
    from tools.compliance import sbom_distribution as dist

    url = dist.retrieval_url("proj-a", "2.0")
    assert url == "https://icdev.example.mil/api/supply_chain/sbom/proj-a/2.0"
    # Two versions of the same project must not share an address — that is the
    # whole content of "version-specific".
    assert dist.retrieval_url("proj-a", "3.0") != url


def test_retrieval_url_encodes_path_segments(monkeypatch):
    """A slash in an id addresses one segment; it does not reshape the route."""
    monkeypatch.setenv("ICDEV_BASE_URL", "https://icdev.example.mil")
    from tools.compliance import sbom_distribution as dist

    url = dist.retrieval_url("group/proj", "1.0+build.7")
    assert url.endswith("/api/supply_chain/sbom/group%2Fproj/1.0%2Bbuild.7")


# ---------------------------------------------------------------------------
# Access decision — DENY
# ---------------------------------------------------------------------------

def test_unauthenticated_caller_is_denied():
    from tools.compliance import sbom_distribution as dist

    decision = dist.evaluate_access({"classification": "CUI"}, None)
    assert decision.allowed is False
    assert decision.status == 401
    assert decision.reason == dist.REASON_UNAUTHENTICATED


@pytest.mark.parametrize("role", ["bd", "capture_mgr", "contract_mgr", "reviewer", "unknown"])
def test_role_without_supply_chain_need_is_denied(role):
    from tools.compliance import sbom_distribution as dist

    decision = dist.evaluate_access(
        {"classification": "CUI"}, {"id": "u1", "role": role, "clearance_level": "CUI"}
    )
    assert decision.allowed is False
    assert decision.status == 403
    assert decision.reason == dist.REASON_ROLE_NOT_AUTHORIZED


def test_classification_above_clearance_is_withheld():
    """The legitimate withholding case the element preserves."""
    from tools.compliance import sbom_distribution as dist

    decision = dist.evaluate_access(
        {"classification": "SECRET"},
        {"id": "u1", "role": "developer", "clearance_level": "CUI"},
    )
    assert decision.allowed is False
    assert decision.status == 403
    assert decision.reason == dist.REASON_CLASSIFICATION_WITHHELD
    assert "SECRET" in decision.detail


def test_other_tenants_artifact_is_withheld():
    from tools.compliance import sbom_distribution as dist

    decision = dist.evaluate_access(
        {"classification": "CUI", "tenant_id": "acme"},
        {"id": "u1", "role": "isso", "clearance_level": "CUI", "tenant_id": "globex"},
    )
    assert decision.allowed is False
    assert decision.reason == dist.REASON_TENANT_WITHHELD


# ---------------------------------------------------------------------------
# Access decision — ALLOW (the "must not prevent sharing" half)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", sorted({"admin", "isso", "ciso", "auditor", "pm",
                                         "developer", "co", "cor", "component_admin",
                                         "migration_engineer", "service"}))
def test_every_authorized_role_is_served(role):
    from tools.compliance import sbom_distribution as dist

    assert role in dist.SBOM_RETRIEVAL_ROLES
    decision = dist.evaluate_access(
        {"classification": "CUI"}, {"id": "u1", "role": role, "clearance_level": "CUI"}
    )
    assert decision.allowed is True, f"{role} is an authorized party and must not be blocked"


def test_service_account_may_integrate_sbom_data():
    """Trusted security tooling authenticates as a service account."""
    from tools.compliance import sbom_distribution as dist

    decision = dist.evaluate_access(
        {"classification": "CUI"},
        {"id": "cortex-svc:scanner", "role": "service", "clearance_level": "CUI"},
    )
    assert decision.allowed is True


def test_higher_clearance_reads_down_to_a_cui_artifact():
    """Read-down, not exact match — a SECRET holder is not denied CUI data."""
    from tools.compliance import sbom_distribution as dist

    decision = dist.evaluate_access(
        {"classification": "CUI"},
        {"id": "u1", "role": "isso", "clearance_level": "SECRET"},
    )
    assert decision.allowed is True


def test_record_without_a_tenant_stays_shared():
    from tools.compliance import sbom_distribution as dist

    decision = dist.evaluate_access(
        {"classification": "CUI", "tenant_id": None},
        {"id": "u1", "role": "developer", "clearance_level": "CUI", "tenant_id": "acme"},
    )
    assert decision.allowed is True


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_resolution_is_version_specific(sbom_env):
    from tools.compliance import sbom_distribution as dist

    conn = sbom_env["conn"]
    for version in ("1.0", "2.0", "3.0"):
        record = dist.resolve_record(conn, project_id=PROJECT_ID, version=version)
        assert record is not None
        assert record["id"] == sbom_env["ids"][version]


def test_resolution_prefers_the_2026_sbom_version_column(sbom_env):
    """When sbx-fld-01 starts writing sbom_version, that column wins.

    Set up so the two columns disagree: a NEW row whose ``sbom_version`` is
    "1.0" while an OLDER row's legacy ``version`` is also "1.0". The standard's
    field is the one that must answer.
    """
    from tools.compliance import sbom_distribution as dist

    conn = sbom_env["conn"]
    conn.execute(
        "INSERT INTO sbom_records (project_id, version, sbom_version, format, "
        "file_path, classification) VALUES (%s, %s, %s, %s, %s, %s)",
        (PROJECT_ID, "9.9", "1.0", "cyclonedx", str(sbom_env["artifact"]), "CUI"),
    )
    conn.commit()

    record = dist.resolve_record(conn, project_id=PROJECT_ID, version="1.0")
    assert record["version"] == "9.9", "the sbom_version column must be preferred"


def test_resolution_of_an_unknown_version_returns_none(sbom_env):
    from tools.compliance import sbom_distribution as dist

    assert dist.resolve_record(sbom_env["conn"], project_id=PROJECT_ID, version="42.0") is None
    assert dist.resolve_record(sbom_env["conn"], project_id="no-such", version="1.0") is None


def test_list_records_carries_a_url_per_record(sbom_env):
    from tools.compliance import sbom_distribution as dist

    records = dist.list_records(sbom_env["conn"], project_id=PROJECT_ID)
    assert len(records) == 3
    urls = {r["retrieval_url"] for r in records}
    assert len(urls) == 3, "each version needs its own address"
    assert all(u.startswith("https://icdev.example.mil/api/supply_chain/sbom/") for u in urls)
    assert all(r["retrievable"] for r in records)


# ---------------------------------------------------------------------------
# HTTP — the version-specific URL
# ---------------------------------------------------------------------------

def test_version_specific_url_returns_the_exact_bytes(client, sbom_env):
    _as(client, id="u1", role="developer", clearance_level="CUI")
    response = client.get(_url("2.0"))

    assert response.status_code == 200
    assert response.data == sbom_env["payload"]
    assert response.data == sbom_env["artifact"].read_bytes()


def test_version_specific_url_serves_the_addressed_version(client, sbom_env):
    """Not "the newest" — the one named in the URL."""
    _as(client, id="u1", role="developer", clearance_level="CUI")

    v1 = client.get(_url("1.0"))
    assert v1.status_code == 200
    assert b"older" in v1.data
    assert v1.data != sbom_env["payload"]

    v2 = client.get(_url("2.0"))
    assert v2.data == sbom_env["payload"]


def test_row_keyed_permalink_returns_the_same_bytes(client, sbom_env):
    _as(client, id="u1", role="developer", clearance_level="CUI")
    record_id = sbom_env["ids"]["2.0"]
    response = client.get(f"/api/supply_chain/sbom/record/{record_id}")

    assert response.status_code == 200
    assert response.data == sbom_env["payload"]


def test_response_carries_the_classification_and_distribution_markings(client, sbom_env):
    _as(client, id="u1", role="isso", clearance_level="CUI")
    response = client.get(_url("2.0"))

    assert response.headers["X-ICDEV-Classification"] == "CUI // SP-CTI"
    assert response.headers["X-ICDEV-Distribution"].startswith("Distribution D")
    assert response.headers["X-ICDEV-SBOM-Version"] == "2.0"
    assert response.mimetype == "application/vnd.cyclonedx+json"
    assert "sbom_" in response.headers["Content-Disposition"]
    # The markings inside the document are the authoritative copy and survive
    # the transfer untouched.
    doc = json.loads(response.data.decode("utf-8"))
    props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert props["icdev:classification"] == "CUI // SP-CTI"
    assert props["icdev:distribution"].startswith("Distribution D")


def test_response_digest_matches_the_served_bytes(client, sbom_env):
    import hashlib

    _as(client, id="u1", role="developer", clearance_level="CUI")
    response = client.get(_url("2.0"))
    assert response.headers["X-ICDEV-SBOM-SHA256"] == hashlib.sha256(response.data).hexdigest()


# ---------------------------------------------------------------------------
# HTTP — DENY
# ---------------------------------------------------------------------------

def test_http_denies_the_unauthenticated_caller(client, sbom_env):
    _as(client)  # no user
    response = client.get(_url("2.0"))

    assert response.status_code == 401
    assert response.get_json()["reason"] == "unauthenticated"
    assert sbom_env["payload"] not in response.data


@pytest.mark.parametrize("role", ["bd", "capture_mgr", "contract_mgr", "reviewer"])
def test_http_denies_a_role_with_no_supply_chain_need(client, sbom_env, role):
    """The DENY case at the boundary, not just in the helper."""
    _as(client, id="u-bd", role=role, clearance_level="CUI")
    response = client.get(_url("2.0"))

    assert response.status_code == 403
    body = response.get_json()
    assert body["reason"] == "role_not_authorized"
    # No part of the artifact leaks through the error body.
    assert SBOM_DOCUMENT["serialNumber"].encode() not in response.data
    assert response.data != sbom_env["payload"]


def test_http_denies_a_role_with_no_need_on_the_permalink_too(client, sbom_env):
    """Both addresses enforce; a permalink is not a side door."""
    _as(client, id="u-bd", role="bd", clearance_level="CUI")
    response = client.get(f"/api/supply_chain/sbom/record/{sbom_env['ids']['2.0']}")

    assert response.status_code == 403
    assert response.get_json()["reason"] == "role_not_authorized"


def test_http_withholds_an_artifact_above_the_callers_clearance(client, sbom_env):
    _as(client, id="u1", role="developer", clearance_level="CUI")
    response = client.get(_url("3.0"))

    assert response.status_code == 403
    assert response.get_json()["reason"] == "classification_withheld"
    assert b"classified" not in response.data


def test_http_releases_that_same_artifact_to_a_cleared_caller(client, sbom_env):
    """The withholding is about the clearance, not about the endpoint."""
    _as(client, id="u2", role="isso", clearance_level="SECRET")
    response = client.get(_url("3.0"))

    assert response.status_code == 200
    assert b"classified" in response.data


def test_denied_response_tells_the_recipient_who_to_ask(client, sbom_env):
    """A withholding a recipient cannot query is not a documented process."""
    _as(client, id="u1", role="developer", clearance_level="CUI")
    body = client.get(_url("3.0")).get_json()
    assert body.get("contact")


# ---------------------------------------------------------------------------
# HTTP — not found / missing artifact
# ---------------------------------------------------------------------------

def test_unknown_version_is_404(client, sbom_env):
    _as(client, id="u1", role="developer", clearance_level="CUI")
    response = client.get(_url("42.0"))
    assert response.status_code == 404
    assert response.get_json()["reason"] == "not_found"


def test_missing_artifact_is_404_not_500(client, sbom_env):
    """The row survives the file. That is a 404 about the artifact, not a crash."""
    _as(client, id="u1", role="developer", clearance_level="CUI")
    sbom_env["artifact"].unlink()

    response = client.get(_url("2.0"))
    assert response.status_code == 404
    assert response.get_json()["reason"] == "artifact_missing"


# ---------------------------------------------------------------------------
# Version index
# ---------------------------------------------------------------------------

def test_version_index_lists_every_version_with_its_url(client, sbom_env):
    _as(client, id="u1", role="isso", clearance_level="SECRET")
    body = client.get(f"/api/supply_chain/sbom/versions/{PROJECT_ID}").get_json()

    assert body["count"] == 3
    assert {v["sbom_version_effective"] for v in body["versions"]} == {"1.0", "2.0", "3.0"}
    assert all(v["accessible"] for v in body["versions"])
    assert all("file_path" not in v for v in body["versions"]), "host paths must not be published"


def test_version_index_marks_what_this_caller_may_not_have(client, sbom_env):
    _as(client, id="u1", role="developer", clearance_level="CUI")
    body = client.get(f"/api/supply_chain/sbom/versions/{PROJECT_ID}").get_json()

    by_version = {v["sbom_version_effective"]: v for v in body["versions"]}
    assert by_version["2.0"]["accessible"] is True
    assert by_version["3.0"]["accessible"] is False
    assert by_version["3.0"]["access"] == "classification_withheld"


def test_catalog_listing_carries_urls_and_an_honest_conformance_field(client, sbom_env):
    _as(client, id="u1", role="developer", clearance_level="CUI")
    rows = client.get("/api/supply_chain/sbom").get_json()

    assert rows
    for row in rows:
        assert row["retrieval_url"]
        assert "file_path" not in row
        # sbx-sig-02 has not landed, so the honest answer is "no score", never 0.
        assert row["conformance"]["available"] is False
        assert row["conformance"]["score"] is None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _audit_events(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return [dict(zip(("event_type", "actor", "details"), r)) for r in conn.execute(
            "SELECT event_type, actor, details FROM audit_trail "
            "WHERE event_type LIKE 'sbom.%'"
        ).fetchall()]
    finally:
        conn.close()


def test_a_release_is_audited(client, sbom_env):
    _as(client, id="u-release", role="developer", clearance_level="CUI")
    client.get(_url("2.0"))

    events = _audit_events(sbom_env["db_path"])
    assert [e for e in events if e["event_type"] == "sbom.distributed"], \
        "a released SBOM must leave an audit row"
    row = [e for e in events if e["event_type"] == "sbom.distributed"][0]
    assert row["actor"] == "u-release"
    assert json.loads(row["details"])["sha256"]


def test_a_withholding_is_audited(client, sbom_env):
    """A denial that leaves no trace is indistinguishable from an outage."""
    _as(client, id="u-denied", role="developer", clearance_level="CUI")
    client.get(_url("3.0"))

    events = _audit_events(sbom_env["db_path"])
    denials = [e for e in events if e["event_type"] == "sbom.distribution_denied"]
    assert denials, "a withheld SBOM must leave an audit row"
    assert json.loads(denials[0]["details"])["reason"] == "classification_withheld"


def test_the_audit_event_types_are_admitted_by_the_constraint():
    """The constant and the generated CHECK must agree.

    audit_trail.event_type carries a CHECK derived from VALID_EVENT_TYPES. If
    the two separate, the INSERT raises, log_event's caller swallows it, and
    distribution reports success while recording nothing — the failure mode
    migration 20260808071512 exists to prevent.
    """
    from tools.audit.audit_logger import VALID_EVENT_TYPES, event_type_check_sql

    sql = event_type_check_sql()
    for event_type in ("sbom.distributed", "sbom.distribution_denied"):
        assert event_type in VALID_EVENT_TYPES
        assert f"'{event_type}'" in sql


# ---------------------------------------------------------------------------
# Generator — the URL travels inside the document
# ---------------------------------------------------------------------------

def test_generated_document_states_where_it_can_be_fetched(monkeypatch):
    """An SBOM that has left ICDEV must still name its authoritative copy."""
    monkeypatch.setenv("ICDEV_BASE_URL", "https://icdev.example.mil")
    from tools.compliance.sbom_generator import _build_cyclonedx_sbom
    from tools.compliance.sbom_distribution import retrieval_url

    url = retrieval_url("proj-a", "2.0")
    sbom, _ = _build_cyclonedx_sbom(
        {"id": "proj-a", "name": "Proj A"}, [], retrieval_url=url,
    )

    refs = [r for r in sbom.get("externalReferences", []) if r["type"] == "bom"]
    assert refs and refs[0]["url"] == url
    props = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    assert props["icdev:retrieval-url"] == url
    # The pre-existing handling instructions are untouched by the addition.
    assert props["icdev:classification"] == "CUI // SP-CTI"
    assert props["icdev:distribution"].startswith("Distribution D")


def test_no_retrieval_url_means_no_external_reference():
    """A placeholder address is worse than none — it sends a recipient nowhere."""
    from tools.compliance.sbom_generator import _build_cyclonedx_sbom

    sbom, _ = _build_cyclonedx_sbom({"id": "proj-a", "name": "Proj A"}, [])
    assert "externalReferences" not in sbom
    names = {p["name"] for p in sbom["metadata"]["properties"]}
    assert "icdev:retrieval-url" not in names
