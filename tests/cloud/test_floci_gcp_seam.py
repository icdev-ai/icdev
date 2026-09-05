# CUI // SP-CTI
"""The floci-gcp seam, and the ENV CONTRACT that makes it different (flx-gcp-01).

RED AT THE MERGE BASE: `tools/cloud/emulator_gcp.py` does not exist there, so
every test in this file fails at collection.

WHY THIS FILE IS MOSTLY ABOUT ENVIRONMENT
-----------------------------------------
The AWS and Azure seams hand a caller a URL, because boto3 and the Azure SDK
take an endpoint override. **GCP client libraries do not.** They read standard
`*_EMULATOR_HOST` variables, so this seam's product is ENVIRONMENT, and the one
thing that can go wrong is the exported set -- wrong name, wrong form, or
exported when nothing is listening.

Nothing on the emulator can catch that: measured 2026-09-05, `grep -a
EMULATOR_HOST` over its native binary returns NOTHING, so it neither reads nor
validates any of these names. A test over the exported set is the only half we
control, which is why the card asked for one.

EVERY CONSTANT PINNED HERE WAS MEASURED, and the spike records the derivation:
docs/spikes/flx-gcp-parity.md. Two of them were pinned WRONG in an earlier
draft of that spike and corrected by re-probing -- see
`test_the_unreachable_set_excludes_the_services_a_second_probe_found`.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
import yaml

from tools.cloud import emulator_gcp
from tools.databridge.connectors.floci_gcp_connector import (
    PROBE_TABLES,
    RESOURCE_TABLES,
    TABLES,
    FlociGcpConnector,
    table_scope,
)
from tools.twin_core.adapters import floci_gcp as twin_mod

_ROOT = Path(__file__).resolve().parents[2]
_SEAM_SRC = _ROOT / "tools" / "cloud" / "emulator_gcp.py"
_COMPOSE = _ROOT / "docker-compose.yml"
_SPIKE = _ROOT / "docs" / "spikes" / "flx-gcp-parity.md"

#: A switched-ON deployment with nothing else declared. Passed explicitly so no
#: test in this file depends on the ambient environment of the runner.
ON = {"FLOCI_GCP_ENABLED": "true"}


@pytest.fixture(autouse=True)
def _forget_one_shot_warnings():
    """The seam warns once per process key; without this a test that expects a
    fallback passes or fails depending on which test ran first."""
    emulator_gcp.reset_warnings()
    yield
    emulator_gcp.reset_warnings()


# -- 1. THE ENV CONTRACT ----------------------------------------------------


def test_the_exported_set_is_exactly_the_six_variables_the_card_names():
    """THE CARD'S OWN ASSERTION: the seam exports emulator-host variables
    rather than an endpoint URL, and this is the set."""
    assert set(emulator_gcp.emulator_host_env(ON)) == {
        "PUBSUB_EMULATOR_HOST",
        "FIRESTORE_EMULATOR_HOST",
        "DATASTORE_EMULATOR_HOST",
        "STORAGE_EMULATOR_HOST",
        "SECRET_MANAGER_EMULATOR_HOST",
        "FIREBASE_AUTH_EMULATOR_HOST",
    }
    # The declaration and the export cannot drift: one is built from the other.
    assert set(emulator_gcp.EMULATOR_HOST_VAR_NAMES) == set(
        emulator_gcp.emulator_host_env(ON)
    )


def test_the_form_is_not_uniform_and_storage_is_the_odd_one_out():
    """THE FINDING THE CARD DID NOT HAVE. Five variables take a bare
    `host:port`; `STORAGE_EMULATOR_HOST` takes a URL WITH SCHEME.

    Both halves are supported by measurement (spike §7): the gRPC lane was
    addressed without a scheme -- a channel to `localhost:4588` served eight
    real methods -- and the REST lane with one, which the emulator stated
    itself by returning `selfLink` values of the form
    `http://localhost:4588/storage/v1/b/...`.

    Exporting a bare `localhost:4588` for STORAGE would have a client compose
    `localhost:4588/storage/v1/b`, which is not a URL.
    """
    env = emulator_gcp.emulator_host_env(ON)

    assert env["STORAGE_EMULATOR_HOST"] == "http://localhost:4588"
    assert env["STORAGE_EMULATOR_HOST"].startswith("http://")

    for name in (
        "PUBSUB_EMULATOR_HOST",
        "FIRESTORE_EMULATOR_HOST",
        "DATASTORE_EMULATOR_HOST",
        "SECRET_MANAGER_EMULATOR_HOST",
        "FIREBASE_AUTH_EMULATOR_HOST",
    ):
        assert env[name] == "localhost:4588", name
        assert "://" not in env[name], f"{name} must carry no scheme"

    # The split is DECLARED per variable, not implied by the values above, so a
    # future edit that changes a value has to change the declaration too.
    by_name = {v.name: v for v in emulator_gcp.EMULATOR_HOST_VARS}
    assert by_name["STORAGE_EMULATOR_HOST"].form == emulator_gcp.FORM_URL
    assert by_name["PUBSUB_EMULATOR_HOST"].form == emulator_gcp.FORM_HOST_PORT


def test_every_variable_targets_the_one_port_that_serves_both_transports():
    """The card was RIGHT about the target even where it was wrong about the
    form: only 4588/tcp listens in the container, and the gRPC server is
    multiplexed onto it. So every variable names the same host:port whatever
    its transport."""
    assert emulator_gcp.GRPC_SHARES_THE_HTTP_PORT is True
    for name, value in emulator_gcp.emulator_host_env(ON).items():
        assert value.endswith("localhost:4588"), (name, value)


def test_nothing_is_exported_when_the_emulator_is_switched_off():
    """Exporting these names while nothing is listening points every GCP client
    in the child process at a dead port -- strictly worse than leaving them
    unset, because the client then cannot fall back to its real endpoint."""
    assert emulator_gcp.emulator_host_env({}) == {}
    assert emulator_gcp.emulator_host_env({"FLOCI_GCP_ENABLED": "false"}) == {}
    # ...and the deliberate bypass still answers, so the empty above is the
    # switch and not a broken builder.
    assert emulator_gcp.emulator_host_env_forced({}), "the forced variant must answer"


def test_exporting_never_mutates_the_ambient_environment():
    """The seam RETURNS a mapping. Which processes see these names is the
    caller's decision; a seam that wrote os.environ would make it for every
    thread in the process, including ones talking to real GCP."""
    before = dict(os.environ)
    emulator_gcp.emulator_host_env(ON)
    emulator_gcp.emulator_host_env_forced(ON)
    assert dict(os.environ) == before

    src = _SEAM_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # os.environ[...] = x  /  os.environ.update(...)  /  setdefault / pop
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            assert "environ" not in ast.dump(node.value), ast.dump(node)
        if isinstance(node, ast.Attribute) and node.attr in {
            "update",
            "setdefault",
            "pop",
            "clear",
        }:
            assert "environ" not in ast.dump(node.value), ast.dump(node)


def test_the_exported_values_follow_a_relocated_endpoint():
    """An operator who moved the emulator must not get a mapping pointing at
    the default. Derived from the endpoint, not from DEFAULT_PORT."""
    moved = {**ON, "FLOCI_GCP_ENDPOINT": "http://emulator.internal:9999"}
    env = emulator_gcp.emulator_host_env(moved)
    assert env["PUBSUB_EMULATOR_HOST"] == "emulator.internal:9999"
    assert env["STORAGE_EMULATOR_HOST"] == "http://emulator.internal:9999"


def test_an_unparseable_endpoint_falls_back_rather_than_raising():
    """A getter that raises turns an operator's typo into an unhandled
    exception inside whatever swallowing handler surrounds the call."""
    assert emulator_gcp.host_port({**ON, "FLOCI_GCP_ENDPOINT": "not-a-url"}) == (
        "localhost:4588"
    )


def test_the_seam_does_not_READ_the_emulator_host_variables():
    """They are an OUTPUT of this seam, never an input. If the seam read them,
    an operator who set PUBSUB_EMULATOR_HOST by hand could silently redirect
    ICDEV's own reads to somewhere ICDEV never configured."""
    hostile = {
        **ON,
        "PUBSUB_EMULATOR_HOST": "evil.example:1",
        "STORAGE_EMULATOR_HOST": "http://evil.example:1",
        "FIRESTORE_EMULATOR_HOST": "evil.example:1",
    }
    assert emulator_gcp.endpoint(hostile) == emulator_gcp.DEFAULT_ENDPOINT
    assert emulator_gcp.host_port(hostile) == "localhost:4588"
    assert emulator_gcp.emulator_host_env(hostile)["PUBSUB_EMULATOR_HOST"] == (
        "localhost:4588"
    )


def test_every_declared_variable_carries_its_provenance():
    """`basis` keeps a measured fact and a declared one apart. The TRANSPORT
    was exercised on this host; the client-side READING of these variables was
    not -- no google-cloud-* library is installed, and installing one to check
    would add an undeclared dependency to the environment tsg-iso-03 governs.

    Presenting six equally-verified facts when one half was never exercised is
    the defect this whole card series exists to refuse.
    """
    for var in emulator_gcp.EMULATOR_HOST_VARS:
        assert var.basis == "declared", var
        assert var.transport in {"grpc", "rest"}, var
        assert var.form in {emulator_gcp.FORM_HOST_PORT, emulator_gcp.FORM_URL}, var


# -- 2. No IaC execution claim ----------------------------------------------


def test_no_iac_execution_is_claimed():
    """ICDEV has tools/cloud/aws_config_executor.py and no GCP analogue.
    Declaring execution support no executor backs is the declared-but-
    unconsumed defect this platform ships most."""
    assert emulator_gcp.IAC_EXECUTION_SUPPORTED is False
    assert not list(_ROOT.glob("tools/cloud/gcp_config_executor.py"))


def test_the_seam_cannot_execute_anything():
    """Structural, not behavioural: a seam that grew a subprocess call would
    still pass a test that only checked the constant above."""
    tree = ast.parse(_SEAM_SRC.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for forbidden in ("subprocess", "docker", "shutil", "boto3"):
        assert forbidden not in imported, f"the seam imports {forbidden}"


# -- 3. The measured traps --------------------------------------------------


def test_firestore_and_datastore_are_grpc_only_and_have_no_rest_table():
    """MEASURED: every REST path tried returned 404 or 405, while the same
    operations over gRPC answered. ICDEV's connector reads over HTTP, so it
    cannot read them -- and a REST 404 is indistinguishable from "no such
    resource", which is why this is stated rather than rediscovered."""
    assert emulator_gcp.GRPC_ONLY_SERVICES == {"firestore", "datastore"}
    for table in ("firestore", "datastore", "documents", "entities"):
        assert table not in emulator_gcp.REST_RESOURCE_PATHS
    # ...and composing a path for one RAISES rather than returning a plausible
    # URL that 404s.
    with pytest.raises(KeyError):
        emulator_gcp.resource_path("firestore", ON)


def test_grpc_only_is_not_merged_with_unreachable():
    """Different findings, different repairs: one works over a transport we do
    not speak, the other has no route we could find."""
    assert not (
        emulator_gcp.GRPC_ONLY_SERVICES & emulator_gcp.DECLARED_UNREACHABLE_SERVICES
    )


def test_the_unreachable_set_excludes_the_services_a_second_probe_found():
    """THE CORRECTION, pinned so it cannot be undone.

    An earlier draft of the spike recorded firebaseauth, sts and gke as
    unreachable. All three work: firebaseauth answered 405 to a GET and 200 to
    the right POST, sts answered 415 to JSON and validated a form-encoded body,
    and GKE is fully functional at the /container/v1 prefix. Each would have
    been a FABRICATED ABSENCE.
    """
    for service in ("firebaseauth", "sts", "gke", "cloudrun", "gcs", "pubsub"):
        assert service not in emulator_gcp.DECLARED_UNREACHABLE_SERVICES, service
    assert emulator_gcp.DECLARED_UNREACHABLE_SERVICES == {"cloudtasks"}


def test_gke_is_composed_at_its_own_prefix_and_never_the_kafka_owned_path():
    """MEASURED: Google's documented GKE path is served here by the Managed
    Kafka handler -- a create against it spawned a Redpanda container and the
    list returns a body carrying `bootstrapAddress`. A GKE client on that path
    gets Kafka clusters and no error."""
    assert emulator_gcp.GKE_PATH_PREFIX == "/container/v1"
    path = emulator_gcp.resource_path("gke_clusters", ON)
    assert path.startswith("/container/v1/")
    assert path == "/container/v1/projects/floci-local/locations/us-central1/clusters"

    collision = "/v1/projects/{project}/locations/{location}/clusters"
    assert emulator_gcp.PATH_COLLISIONS[collision] == "kafka"

    # No lane composes the colliding form. The predicate is the COLLIDING PATH,
    # not the `/v1/.../locations/` prefix: `key_rings` legitimately lives at
    # `/v1/projects/{p}/locations/{l}/keyRings` and answers correctly there, so
    # a prefix-wide ban would refuse a working lane -- the same fabricated
    # refusal this file pins elsewhere, committed by its own test.
    colliding = collision.format(project="floci-local", location="us-central1")
    for table in emulator_gcp.REST_RESOURCE_PATHS:
        assert emulator_gcp.resource_path(table, ON) != colliding, table


def test_cloud_run_is_named_as_the_service_that_lies_without_docker():
    """MEASURED: cloudsql and kafka return 500 without a socket; cloudrun
    returns 200 and a service body indistinguishable from a real deployment.
    That single row is why docker-backing is decided in the seam."""
    assert emulator_gcp.FABRICATED_SUCCESS_WITHOUT_DOCKER == {"cloudrun"}
    assert emulator_gcp.FABRICATED_SUCCESS_WITHOUT_DOCKER <= (
        emulator_gcp.CONTAINER_BACKED_SERVICES
    )


def test_the_health_service_map_is_declared_not_to_be_a_health_signal():
    """MEASURED: byte-identical on a deployment that provably cannot start a
    container, and "running" is the only value ever observed. TWO constants,
    because they say different things -- the map parses AND it is not evidence.
    """
    assert emulator_gcp.HEALTH_HAS_SERVICE_MAP is True
    assert emulator_gcp.HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY is True


def test_the_azure_subscription_scope_trap_is_not_inherited():
    """floci-az needs a per-resource-group fan-out because a subscription-scoped
    list returns an empty body for a populated estate. MEASURED here: project-
    scoped lists reflect writes, so this seam composes ONE path per table. A
    sibling seam must not lend a trap the measurement did not find."""
    assert not hasattr(emulator_gcp, "SUBSCRIPTION_SCOPED_LIST_IS_EMPTY")
    for table in emulator_gcp.REST_RESOURCE_PATHS:
        assert isinstance(emulator_gcp.resource_path(table, ON), str)


def test_no_host_proxy_port_ranges_are_declared():
    """MEASURED: a spawned Cloud SQL container exposes 5432/tcp UNPUBLISHED and
    the API hands back a Docker bridge address. floci-az forwards ~1,100 host
    ports; this emulator forwards none, so the Azure constant is not copied
    across empty."""
    assert emulator_gcp.SPAWNED_SERVICES_ARE_BRIDGE_ADDRESSED is True
    assert not hasattr(emulator_gcp, "PROXY_PORT_RANGES")


# -- 4. Project identity ----------------------------------------------------


def test_a_project_id_is_configuration_because_it_cannot_be_discovered():
    """MEASURED: `GET /v1/projects` returns 404 while `GET /v1/projects/{id}`
    returns a real project. A caller with no project id has nothing to fall
    back on, so this getter always answers."""
    assert emulator_gcp.PROJECT_LIST_IS_UNSUPPORTED is True
    assert emulator_gcp.project_id({}) == "floci-local"
    assert emulator_gcp.project_id({"FLOCI_GCP_PROJECT_ID": "my-real-project"}) == (
        "my-real-project"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "a/b",
        "proj?x=1",
        "UPPER-CASE",
        "sh",  # too short
        "trailing-",
        "",
    ],
)
def test_a_hostile_project_id_cannot_reach_the_composed_url(hostile):
    """The project id is INTERPOLATED INTO A URL PATH, and this validation is
    the only thing between an env var and that URL. A value carrying `/` or
    `..` would compose a path pointing somewhere else entirely."""
    env = {**ON, "FLOCI_GCP_PROJECT_ID": hostile}
    assert emulator_gcp.project_id(env) == emulator_gcp.DEFAULT_PROJECT_ID
    assert hostile not in emulator_gcp.resource_path("buckets", env) or hostile == ""


# -- 5. The docker tri-state ------------------------------------------------


def test_docker_backed_is_tri_state_and_none_is_not_false():
    """A Windows named pipe is not reliably stat-able, so an unproven socket is
    None. Returning False there would be a fabricated refusal for a working
    daemon -- and for cloudrun, the fabrication would point the other way."""
    absent = {"FLOCI_GCP_DOCKER_SOCKET": "unix:///nonexistent-icdev-gcp/docker.sock"}
    assert emulator_gcp.docker_backed(absent) is False
    assert emulator_gcp.docker_basis(absent) == emulator_gcp.BASIS_SOCKET_ABSENT
    assert emulator_gcp.data_plane_supported("cloudsql", absent) is False
    assert emulator_gcp.data_plane_supported("cloudrun", absent) is False
    # A lane that spawns nothing is unaffected by the socket.
    assert emulator_gcp.data_plane_supported("gcs", absent) is True

    remote = {"FLOCI_GCP_DOCKER_SOCKET": "tcp://dockerhost:2375"}
    assert emulator_gcp.docker_backed(remote) is True
    assert emulator_gcp.docker_basis(remote) == emulator_gcp.BASIS_DECLARED_REMOTE


def test_listing_inventory_is_not_the_same_question_as_reaching_a_data_plane():
    """MEASURED on floci-az and unchanged here: listing metadata spawns no
    container. An inventory reader that consulted data_plane_supported would
    refuse to list Cloud SQL instances on a socket-less host, which is a
    fabricated refusal for a lane that answers."""
    absent = {"FLOCI_GCP_DOCKER_SOCKET": "unix:///nonexistent-icdev-gcp/docker.sock"}
    assert emulator_gcp.resource_path("sql_instances", {**ON, **absent}) == (
        "/sql/v1beta4/projects/floci-local/instances"
    )


# -- 6. Row extraction ------------------------------------------------------


def test_rows_are_read_through_the_seam_because_the_key_is_not_uniform():
    """MEASURED: nine lanes answer a keyed empty and six answer a bare `{}`.
    `body["items"]` raises on half of them."""
    assert emulator_gcp.rows_from("buckets", {"kind": "storage#buckets"}) == []
    assert emulator_gcp.rows_from("buckets", {"items": [{"name": "b"}]}) == [
        {"name": "b"}
    ]
    assert emulator_gcp.rows_from("topics", {"topics": [{"name": "t"}]}) == [
        {"name": "t"}
    ]
    assert emulator_gcp.rows_from("gke_clusters", {}) == []
    # `project` returns ONE object, not a list.
    assert emulator_gcp.rows_from("project", {"projectId": "floci-local"}) == [
        {"projectId": "floci-local"}
    ]
    assert emulator_gcp.rows_from("project", {}) == []
    # A non-dict body is not a row.
    assert emulator_gcp.rows_from("buckets", "<html>404</html>") == []


# -- 7. The pin against what is actually deployed ---------------------------


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def test_the_compose_image_and_the_seam_image_are_the_same_literal(compose):
    """YAML cannot import a Python constant, so the two are kept in step by
    hand -- and this is the test that makes "by hand" safe."""
    svc = compose["services"]["floci-gcp"]
    assert svc["image"] == emulator_gcp.IMAGE == "floci/floci-gcp:0.8.0"
    assert not svc["image"].endswith(":latest")


def test_the_compose_healthcheck_probes_the_seams_health_path(compose):
    """Three sibling emulators, three different health paths. Derived from the
    seam so the two cannot drift."""
    joined = " ".join(compose["services"]["floci-gcp"]["healthcheck"]["test"])
    assert emulator_gcp.HEALTH_PATH in joined
    assert str(emulator_gcp.CONTAINER_PORT) in joined
    # The siblings' paths must NOT appear -- both 404 on this emulator.
    assert "/_localstack/health" not in joined
    assert "/_floci/health" not in joined


def test_the_digest_is_recorded_for_an_air_gap_bundle_check():
    """A tag-only check reads a `docker load`ed bundle as absent."""
    assert emulator_gcp.IMAGE_DIGEST.startswith("sha256:")
    assert len(emulator_gcp.IMAGE_DIGEST) == len("sha256:") + 64


def test_the_spike_exists_and_is_dated():
    """The card required a measurement BEFORE any code. A seam whose constants
    cite a document that does not exist is a claim with no derivation."""
    text = _SPIKE.read_text(encoding="utf-8", errors="replace")
    assert "Measured 2026-09-05" in text
    assert emulator_gcp.IMAGE_DIGEST in text
    assert emulator_gcp.IMAGE in text


# ══════════════════════════════════════════════════════════════════════════
# 8. THE CONNECTOR
# ══════════════════════════════════════════════════════════════════════════


class _FakeConnector(FlociGcpConnector):
    """A connector whose HTTP layer is a scripted dict. No socket, no emulator."""

    def __init__(self, responses: dict, raises: dict | None = None) -> None:
        super().__init__()
        self._responses = responses
        self._raises = raises or {}
        self._config = {}
        self._endpoint = "http://fake:4588"

    def _http_get_noauth(self, url: str):  # type: ignore[override]
        for fragment, exc in self._raises.items():
            if fragment in url:
                raise RuntimeError(exc)
        for fragment, body in self._responses.items():
            if fragment in url:
                return body
        raise RuntimeError(f"unscripted URL: {url}")


#: The measured health body shape: a services map whose every value is "running".
_HEALTH_BODY = {
    "services": {n: "running" for n in ("gcs", "pubsub", "firestore", "cloudsql")},
    "version": "0.8.0",
}


@pytest.fixture()
def emulator_on(monkeypatch):
    """Switch the seam ON for the read tests.

    NOT a convenience. ``read()`` consults ``emulator_gcp.enabled()`` FIRST and
    returns a ``disabled`` response before any HTTP layer is reached, which is
    the air-gap-safe default working exactly as designed -- without this fixture
    every read test below asserts against that refusal rather than against the
    read it means to exercise.
    """
    monkeypatch.setenv("FLOCI_GCP_ENABLED", "true")


def test_grant_matches_connector_tables_exactly():
    """A table added to the connector is a NEW authorization decision.

    Pinned against the module-level ``TABLES`` so the decision cannot be skipped
    by adding a table and forgetting the manifest.
    """
    manifest = yaml.safe_load(
        (_ROOT / "args" / "databridge_agent_access.yaml").read_text(encoding="utf-8")
    )
    grant = next(c for c in manifest["connectors"] if c["name"] == "floci_gcp")
    assert tuple(grant["tables"]) == TABLES
    assert grant["agents"] == ["twin_observatory_analyst"], (
        "An empty agent list grants EVERY agent including runtime-generated SMEs."
    )


def test_table_scope_partitions_every_table():
    assert set(PROBE_TABLES) | set(RESOURCE_TABLES) == set(TABLES)
    for table in TABLES:
        assert table_scope(table) in ("emulator", "project")


def test_the_connector_declares_no_table_for_a_grpc_only_service():
    """A table that reliably returned `[]` for a service holding data is a
    fabricated empty -- the exact defect this connector is shaped around."""
    for service in emulator_gcp.GRPC_ONLY_SERVICES:
        assert service not in TABLES
        assert service not in emulator_gcp.TABLE_SERVICE.values()


def test_a_read_is_one_request_because_project_scope_reflects_writes(emulator_on):
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({"/storage/v1/b": {"items": [{"name": "b1"}, {"name": "b2"}]}})
    resp = c.read(ConnectorRequest(table_name="buckets"))
    assert resp.status == "ok"
    assert resp.row_count == 2
    assert resp.metadata["empty_is_a_real_answer"] is True
    assert resp.metadata["scope"] == "project"


def test_a_genuinely_empty_estate_reports_ok_with_zero_rows(emulator_on):
    """The MEASURED empty shape: GCS answers `{"kind": "storage#buckets"}` with
    no `items` key at all. That is a real answer, not a failure."""
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({"/storage/v1/b": {"kind": "storage#buckets"}})
    resp = c.read(ConnectorRequest(table_name="buckets"))
    assert resp.status == "ok"
    assert resp.row_count == 0
    assert resp.metadata["empty_is_a_real_answer"] is True


def test_enabled_services_returns_names_only_and_says_it_is_not_health(emulator_on):
    """THE TABLE WHOSE NAME IS DOING SAFETY WORK.

    The always-"running" status must not reach a caller: a status field that
    always says the same thing is a constant wearing a measurement's name, and
    somebody would render it as a health badge.
    """
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({"/health": _HEALTH_BODY})
    resp = c.read(ConnectorRequest(table_name="enabled_services"))
    assert resp.status == "ok"
    assert resp.row_count == 4
    assert all(set(row) == {"service"} for row in resp.data), resp.data
    assert "running" not in json.dumps(resp.data)
    assert resp.metadata["is_enablement_not_health"] is True
    # The two unreadable sets ride along, so a caller reading this table is told
    # what it does NOT cover.
    assert resp.metadata["grpc_only_services"] == sorted(emulator_gcp.GRPC_ONLY_SERVICES)


def test_a_health_body_with_no_service_map_is_an_error_not_an_empty_list(emulator_on):
    """"Which services are declared" becomes UNANSWERABLE, which is not the
    same as answered with none."""
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({"/health": {"version": "0.8.0"}})
    resp = c.read(ConnectorRequest(table_name="enabled_services"))
    assert resp.status == "error"
    assert resp.row_count == 0


def test_an_unreachable_lane_is_an_error_never_an_empty_ok(emulator_on):
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({}, raises={"/storage/v1/b": "HTTP 404 Not Found"})
    resp = c.read(ConnectorRequest(table_name="buckets"))
    assert resp.status == "error"
    assert resp.row_count == 0


def test_the_connector_is_disabled_by_default_and_makes_no_call():
    """Air-gap safe: with the switch off, read() returns before the HTTP layer.
    The fake would RAISE on an unscripted URL, so reaching it would fail here."""
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({})
    resp = c.read(ConnectorRequest(table_name="buckets"))
    assert resp.status == "disabled"
    assert resp.row_count == 0


def test_write_is_refused_naming_the_missing_executor():
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({})
    resp = c.write(ConnectorRequest(table_name="buckets"), data={})
    assert resp.status == "unsupported"
    assert "no GCP IaC executor" in " ".join(resp.errors)
    assert c.capabilities.supports_write is False


def test_the_endpoint_host_ceiling_is_enforced_where_the_destination_is_decided():
    """A seam mis-set toward a host the connection does not allow is refused
    rather than dialled -- and refusing once must not become allowing on the
    second call, which the `if self._endpoint` early return would permit if the
    check ran after the assignment."""
    c = _FakeConnector({})
    c._config = {"egress_allowlist": ["localhost"], "endpoint": "http://169.254.169.254"}
    c._endpoint = ""
    with pytest.raises(PermissionError):
        c._ensure_configured()
    with pytest.raises(PermissionError):
        c._ensure_configured()


# ══════════════════════════════════════════════════════════════════════════
# 9. THE TWIN
# ══════════════════════════════════════════════════════════════════════════


class _Outcome:
    def __init__(self, ok=True, status="ok", rows=0, errors=None):
        self.ok = ok
        self.connector_status = status
        self.row_count = rows
        self.connector_errors = errors or []
        self.error = ""
        self.audited = True


def test_twin_targets_gcp_and_its_preset_scope_is_not_cosmetic():
    """THE POINT OF THE CARD, and the preset half is a measurement.

    Every GCP entry in the service catalog carries `govcloud_available: false`
    and `assured_workloads: true`. A `government` scope would therefore mark all
    of them unavailable -- fabricated findings on the first delta anyone
    simulates -- and a `commercial` scope would silently drop the government
    question. Both are WRONG answers rather than missing ones.
    """
    from tools.twin_core.schema import normalize_csp

    assert twin_mod.TARGET_CSP == "gcp"
    assert normalize_csp(twin_mod.TARGET_CSP) == "gcp"
    assert twin_mod.TARGET_REGION == emulator_gcp.DEFAULT_REGION == "us-central1"

    presets = yaml.safe_load(
        (_ROOT / "args" / "twin_target_presets.yaml").read_text(encoding="utf-8")
    )
    preset = presets["presets"][twin_mod.DEFAULT_TARGET_PRESET]
    assert preset["csp"] == "gcp"
    assert preset["region"] == twin_mod.TARGET_REGION
    assert preset["region_scope"] == "assured_workloads"


def test_the_catalog_still_justifies_that_scope():
    """The scope above is only right while the catalog says what it said on
    2026-09-05. If GCP gains `govcloud_available` entries, re-decide rather than
    discovering it through a wall of findings."""
    catalog = json.loads(
        (_ROOT / "context" / "cloud" / "csp_service_registry.json").read_text(
            encoding="utf-8"
        )
    )
    gcp = catalog["services"]["gcp"]
    assert gcp, "the GCP catalog is empty; the preset would score nothing"
    assert not any(m.get("govcloud_available") for m in gcp.values())
    assert all(m.get("assured_workloads") for m in gcp.values())


def test_the_assured_workloads_scope_is_additive_and_moves_no_existing_verdict():
    """The evaluator gained a branch for a scope NO shipped preset used, so the
    `government` and `commercial` answers are unchanged."""
    from tools.twin_core.target_presets import _available_in_target

    gov_only = {"govcloud_available": True, "assured_workloads": False}
    aw_only = {"govcloud_available": False, "assured_workloads": True}

    assert _available_in_target(gov_only, {"region_scope": "government"}) is True
    assert _available_in_target(aw_only, {"region_scope": "government"}) is False
    # The new branch, and the only place its verdict differs.
    assert _available_in_target(aw_only, {"region_scope": "assured_workloads"}) is True
    assert _available_in_target(gov_only, {"region_scope": "assured_workloads"}) is False


def test_twin_snapshot_table_is_separate_from_both_siblings():
    from tools.twin_core.adapters import floci as aws_twin
    from tools.twin_core.adapters import floci_az as az_twin

    assert twin_mod.FlociGcpTwinAdapter.snapshot_table == "floci_gcp_twin_snapshots"
    assert len({
        twin_mod.FlociGcpTwinAdapter.snapshot_table,
        az_twin.FlociAzTwinAdapter.snapshot_table,
        aws_twin.FlociTwinAdapter.snapshot_table,
    }) == 3, "merging estates makes a query for one CSP silently return another's"


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (_Outcome(ok=False), "denied"),
        (_Outcome(status="disabled"), "disabled"),
        (_Outcome(status="ok", rows=3), "answered"),
        (_Outcome(status="error"), "error"),
        # A status this ladder does not recognise -- INCLUDING the Azure twin's
        # `partial`, which this connector never emits -- must not become an
        # answer. Defaulting to `answered` is how a new connector state would
        # silently become a `pass`.
        (_Outcome(status="partial"), "error"),
        (_Outcome(status="something_new"), "error"),
    ],
)
def test_classify_read_ladder(outcome, expected):
    assert twin_mod.classify_read(outcome) == expected


def test_a_denial_is_tested_before_the_connector_status():
    """A refused call never reached the connector, so its status says nothing.
    Reading it as "answered with no rows" is the conflation the ladder exists
    to refuse."""
    assert twin_mod.classify_read(_Outcome(ok=False, status="ok", rows=0)) == "denied"


@pytest.mark.parametrize(
    "reads,verdict,basis",
    [
        ({}, "unknown", "unmeasured"),
        ({"health": "answered", "buckets": "answered"}, "pass", "all_tables_answered"),
        ({"health": "answered", "buckets": "error"}, "fail", "emulator_errors"),
        # The emulator's OWN probe failing is unreachability, not a service fault.
        ({"health": "error", "buckets": "answered"}, "unknown", "unreachable"),
        ({"health": "disabled"}, "unknown", "disabled"),
    ],
)
def test_classify_verdict_ladder(reads, verdict, basis):
    assert twin_mod.classify_verdict(reads) == (verdict, basis)


def test_denial_basis_is_resolved_from_structured_facts_never_prose():
    """The governed door collapses "switched off" and "on but unreachable" into
    one refusal. The verdict is `unknown` either way; the BASIS differs because
    the repairs do."""
    assert twin_mod.denial_basis(False, True) == "broker_denied"
    assert twin_mod.denial_basis(True, False) == "disabled"
    assert twin_mod.denial_basis(True, True) == "unreachable"
    assert twin_mod.denial_basis(None, None) == "broker_denied"


def test_persist_snapshot_takes_no_provenance_argument():
    """STRUCTURAL, and it has to be: a behavioural test over today's callers --
    which pass none -- would still pass the day somebody threads a kwarg
    through."""
    src = (
        _ROOT / "tools" / "twin_core" / "adapters" / "floci_gcp.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_persist_snapshot"
    )
    names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    assert names == ["self", "snap"], names
    assert fn.args.kwarg is None, "**kwargs would let a provenance through"


def test_provenance_is_emulated_and_never_observed():
    from tools.twin_core.schema import PROVENANCE_EMULATED

    assert twin_mod.PROVENANCE == PROVENANCE_EMULATED == "emulated"


def test_emulator_scoped_tables_are_excluded_from_the_resource_count():
    """`enabled_services` alone returns 23 rows on an emulator holding NOTHING.
    Counting it would make an empty estate report a populated one -- the
    fabricated-population mirror of a fabricated empty."""
    for table in ("health", "enabled_services", "project"):
        assert table not in twin_mod._ESTATE_TABLES, table
    assert "buckets" in twin_mod._ESTATE_TABLES


def test_resource_count_is_none_not_zero_when_nothing_was_measured(monkeypatch):
    """An unreachable emulator holds an UNKNOWN number of resources; 0 asserts
    it holds none."""
    adapter = twin_mod.FlociGcpTwinAdapter()
    monkeypatch.setattr(adapter, "_read_table", lambda *a, **k: _Outcome(ok=False))
    monkeypatch.setattr(adapter, "_persist_snapshot", lambda snap: False)
    snap = adapter.take_snapshot("local")
    assert snap["resource_count"] is None
    assert snap["verdict"] == "unknown"
    assert snap["resource_count_is_complete"] is False
    assert snap["provenance"] == "emulated"


def test_a_snapshot_names_what_it_could_not_read(monkeypatch):
    """An estate summary that silently omitted the gRPC-only services would be
    scoped without saying so."""
    adapter = twin_mod.FlociGcpTwinAdapter()
    monkeypatch.setattr(adapter, "_read_table", lambda *a, **k: _Outcome(rows=1))
    monkeypatch.setattr(adapter, "_persist_snapshot", lambda snap: False)
    snap = adapter.take_snapshot("local")
    assert snap["unread_services"]["grpc_only"] == ["datastore", "firestore"]
    assert snap["unread_services"]["no_route_found"] == ["cloudtasks"]
    assert snap["project"] == emulator_gcp.DEFAULT_PROJECT_ID


def test_latest_status_over_nothing_is_unknown_never_pass(monkeypatch):
    """The twin has never looked, which is not a clean bill of health."""
    adapter = twin_mod.FlociGcpTwinAdapter()
    monkeypatch.setattr(adapter, "list_snapshots", lambda *a, **k: [])
    status = adapter.latest_status("local")
    assert status["verdict"] == "unknown"
    assert status["verdict_basis"] == "no_snapshot"


def test_cloud_run_is_scored_higher_than_the_services_that_fail_loudly(monkeypatch):
    """MEASURED: a socket-less Cloud Run deploy returns 200 and starts nothing,
    while Cloud SQL and Kafka return 500. A silent failure is the worse finding
    and the severity says so."""
    monkeypatch.setattr(emulator_gcp, "docker_backed", lambda env=None: False)
    adapter = twin_mod.FlociGcpTwinAdapter()

    out = adapter.simulate_delta("local", {"services": ["cloudrun", "cloudsql"]})
    by_detail = {v.get("detail"): v for v in out["violations"]}
    assert by_detail["cloudrun"]["severity"] == "high"
    assert by_detail["cloudsql"]["severity"] == "medium"
    assert "200" in by_detail["cloudrun"]["recommendation"]


def test_simulate_delta_separates_the_three_emulator_findings(monkeypatch):
    """Three different repairs, so three findings -- never one bucket:
    nothing to mount / a transport we do not speak / mount a socket."""
    monkeypatch.setattr(emulator_gcp, "docker_backed", lambda env=None: False)
    adapter = twin_mod.FlociGcpTwinAdapter()

    out = adapter.simulate_delta(
        "local", {"services": ["cloudtasks", "firestore", "cloudsql"]}
    )
    rules = {v.get("detail"): v["rule_id"] for v in out["violations"]}
    assert rules["cloudtasks"] == "floci-gcp-service-declared-unreachable"
    assert rules["firestore"] == "floci-gcp-service-grpc-only"
    assert rules["cloudsql"] == "floci-gcp-service-unsupported-locally"


def test_an_empty_delta_scores_no_service_finding_and_is_never_pass():
    """The simulation is static, so a delta naming no service is honestly
    unscored rather than clean.

    THE ASSERTION IS ON THIS ADAPTER'S OWN FINDINGS, not on the headline
    verdict, and the reason is a real pre-existing condition rather than a
    weakened test. ``_target_augment`` also scores TARGET STALENESS, and the
    shared service catalog (`context/cloud/csp_service_registry.json`) was last
    reviewed 2026-02-21 -- past the 180-day `staleness_warn_days` -- so EVERY
    twin that consults a preset currently returns `warn` for an empty delta.
    Measured 2026-09-05: the Azure twin does exactly the same.

    That warning is CORRECT and is deliberately not silenced. Bumping
    `last_updated` to make this test read `unknown` would assert a catalog
    review that nobody performed -- the "edit the threshold so the surface
    agrees" move this repo forbids. What this test pins is that the emulator
    layer contributed NOTHING, and that a free `pass` is impossible either way.
    """
    adapter = twin_mod.FlociGcpTwinAdapter()
    out = adapter.simulate_delta("local", {})

    emulator_rules = [
        v["rule_id"] for v in out["violations"] if str(v.get("rule_id", "")).startswith("floci-gcp-")
    ]
    assert emulator_rules == [], emulator_rules
    assert out["verdict"] != "pass"
    assert out["extra"]["provenance"] == "emulated"
    assert out["extra"]["iac_execution_supported"] is False


def test_provenance_rides_on_every_envelope_including_a_clean_one():
    """A consumer that only learns the estate was emulated when something is
    wrong will read a clean simulation as evidence about a real deployment."""
    adapter = twin_mod.FlociGcpTwinAdapter()
    for delta in ({}, {"services": ["gcs"]}):
        assert adapter.simulate_delta("local", delta)["extra"]["provenance"] == "emulated"


def test_the_twin_reads_only_through_the_broker():
    """A direct connector read returns the same rows with NO authorization check
    and NO audit row -- the ungoverned side channel cef-fnd-03 exists to close.
    Structural, because such a read would look completely ordinary."""
    src = (
        _ROOT / "tools" / "twin_core" / "adapters" / "floci_gcp.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "fetch" in called, "the twin must read through broker.fetch"
    # The connector class itself is never imported -- only its declarations.
    assert "FlociGcpConnector" not in src
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "tools.databridge.connectors.floci_gcp_connector"
        for alias in node.names
    }
    assert imported <= {"PROBE_TABLES", "TABLES", "table_scope"}, imported
