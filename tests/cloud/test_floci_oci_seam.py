# CUI // SP-CTI
"""The floci-oci seam, and the MEASURED "not yet" that shaped it (flx-oci-01).

RED AT THE MERGE BASE: `tools/cloud/emulator_oci.py` does not exist there, so
every test in this file fails at collection.

WHY THIS FILE LEADS WITH A TEST ABOUT ICDEV RATHER THAN ABOUT THE EMULATOR
---------------------------------------------------------------------------
The card said floci-oci is the least proven of the four siblings and that a
measured "not yet" is a real result. Measured, the emulator was not the weak
half: eight services, every REST lane answering, writes reflected,
`compartmentId` honoured, controls discriminating. **ICDEV's OCI provider layer
was.** Its classes return constants with no network call, so no endpoint can
reach them -- and section 1 of `docs/spikes/flx-oci-parity.md` is the
derivation.

`emulator_oci.PROVIDER_LAYER_IS_STUBBED` records that finding, and a constant
recording a finding is worth nothing on its own -- it agrees with itself
forever. So `TestTheCardsDecision` below RE-DERIVES it from the provider
modules' own ASTs, sharing no code with the seam. If someone implements the
providers, those tests FAIL and say so, which is the signal to revisit the
constant and this card's conclusion. That is the point: the "not yet" is dated
and falsifiable rather than permanent.

EVERY CONSTANT PINNED HERE WAS MEASURED against floci/floci-oci:0.4.0 on
2026-09-05, and the spike records the derivation. One of them was pinned WRONG
in an earlier draft and corrected by re-probing -- see
`test_functions_is_a_declared_service_despite_the_startup_log`.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from tools.cloud import emulator_oci
from tools.databridge.connectors.floci_oci_connector import (
    PROBE_TABLES,
    RESOURCE_TABLES,
    TABLES,
    FlociOciConnector,
    table_scope,
)
from tools.twin_core.adapters import floci_oci as twin_mod

_ROOT = Path(__file__).resolve().parents[2]
_SEAM_SRC = _ROOT / "tools" / "cloud" / "emulator_oci.py"
_COMPOSE = _ROOT / "docker-compose.yml"
_SPIKE = _ROOT / "docs" / "spikes" / "flx-oci-parity.md"

#: A switched-ON deployment with nothing else declared. Passed explicitly so no
#: test in this file depends on the ambient environment of the runner.
ON = {"FLOCI_OCI_ENABLED": "true"}


@pytest.fixture(autouse=True)
def _forget_one_shot_warnings():
    """The seam warns once per process key; without this a test that expects a
    fallback passes or fails depending on which test ran first."""
    emulator_oci.reset_warnings()
    yield
    emulator_oci.reset_warnings()


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# 1. THE CARD'S DECISION, RE-DERIVED
# ══════════════════════════════════════════════════════════════════════════


class TestTheCardsDecision:
    """Independent re-derivation of `docs/spikes/flx-oci-parity.md` §1.

    NONE of these read `emulator_oci.PROVIDER_LAYER_IS_STUBBED` to decide -- they
    parse the provider modules and compare the ANSWER to the constant. A
    verifier that called what the surface calls would only prove the constant
    equals itself.
    """

    #: The OCI provider classes the card measured, and the method on each whose
    #: body proves the point.
    _STUBBED = {
        "tools/cloud/storage_provider.py": ("OCIObjectStorageProvider", "list_objects"),
        "tools/cloud/secrets_provider.py": ("OCISecretsProvider", "list_secrets"),
        "tools/cloud/iam_provider.py": ("OCIIAMProvider", "list_service_accounts"),
    }

    @staticmethod
    def _method(path: str, cls_name: str, meth_name: str) -> ast.FunctionDef:
        tree = ast.parse((_ROOT / path).read_text(encoding="utf-8"))
        cls = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == cls_name
        )
        return next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == meth_name
        )

    @pytest.mark.parametrize("path", sorted(_STUBBED))
    def test_the_oci_provider_returns_a_constant_and_makes_no_call(self, path):
        """The finding, re-derived: these methods cannot reach ANY endpoint.

        The body is a single `return <literal>`. There is no call node in it, so
        there is no socket for an endpoint to redirect -- which is why pointing
        `FLOCI_OCI_ENDPOINT` at a running emulator changes nothing here.

        IF THIS FAILS, someone implemented the provider. That is good news and
        it means this card's conclusion is stale: re-measure, then update
        `emulator_oci.PROVIDER_LAYER_IS_STUBBED` and the spike's §1 table.
        """
        cls_name, meth_name = self._STUBBED[path]
        fn = self._method(path, cls_name, meth_name)
        body = [n for n in fn.body if not isinstance(n, ast.Expr)]  # drop docstrings
        assert len(body) == 1 and isinstance(body[0], ast.Return), (
            f"{cls_name}.{meth_name} is no longer a single return -- re-measure §1"
        )
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        assert not calls, (
            f"{cls_name}.{meth_name} now makes a call; it may be reachable. "
            "Re-measure docs/spikes/flx-oci-parity.md §1."
        )

    def test_the_seam_constant_agrees_with_that_derivation(self):
        """The two sides are compared HERE, and only here."""
        assert emulator_oci.PROVIDER_LAYER_IS_STUBBED is True

    def test_the_only_endpoint_honouring_sites_target_a_service_this_emulator_lacks(self):
        """Counted from source, then checked against the seam's constant.

        `service_endpoint` is the OCI SDK's endpoint override. Exactly two sites
        pass one, both in the LLM stack, and both aim at Generative AI inference
        -- which floci-oci 404s on every path (measured). So even the two call
        sites that COULD be redirected have nothing to be redirected to.
        """
        sites = []
        for py in sorted((_ROOT / "tools").rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and any(
                    kw.arg == "service_endpoint" for kw in node.keywords
                ):
                    sites.append(py.relative_to(_ROOT).as_posix())
        assert emulator_oci.ICDEV_ENDPOINT_HONOURING_SITES == len(set(sites)), (
            f"the seam claims {emulator_oci.ICDEV_ENDPOINT_HONOURING_SITES} "
            f"endpoint-honouring sites; source has {sorted(set(sites))}"
        )
        assert all("llm" in s for s in sites), sites
        assert emulator_oci.GENERATIVE_AI_IS_ABSENT is True

    def test_no_canvas_or_page_was_wired_to_this_emulator(self):
        """The card's explicit instruction, asserted structurally.

        "ship the compose profile WITHOUT wiring a canvas to it". So the
        component is a `core_extension` with an empty `url_prefix` -- no page,
        which is also why the 8-point page-completeness gate does not apply.
        """
        registry = yaml.safe_load(
            (_ROOT / "args" / "component_registry.yaml").read_text(encoding="utf-8")
        )
        entry = next(c for c in registry["components"] if c["key"] == "floci_oci")
        assert entry["kind"] == "core_extension"
        assert not entry["url_prefix"]
        assert entry["env_flag"] == "FLOCI_OCI_ENABLED", (
            "must be the flag the seam READS, not the ICDEV_-prefixed default"
        )
        assert not (_ROOT / "tools" / "floci_oci").exists()
        assert not list((_ROOT / "tools" / "dashboard" / "templates").glob("floci_oci*"))


# ══════════════════════════════════════════════════════════════════════════
# 2. NO IaC EXECUTION CLAIM
# ══════════════════════════════════════════════════════════════════════════


def test_no_iac_execution_is_claimed():
    assert emulator_oci.IAC_EXECUTION_SUPPORTED is False
    assert not FlociOciConnector().capabilities.supports_write


def test_the_seam_cannot_execute_anything():
    """Structural, not behavioural. A behavioural test over today's code passes
    the day somebody adds a `subprocess.run`; this refuses the import."""
    tree = ast.parse(_SEAM_SRC.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("subprocess", "docker", "shutil", "os.system"):
        assert forbidden not in imported, f"the seam must not import {forbidden}"


def test_the_unsupported_reason_names_both_causes():
    """A capability gap and a failed call are different findings.

    TWO reasons here, not one: the absent executor AND the stubbed providers. A
    caller told only the first would go looking for an OCI executor to write.
    """
    reason = emulator_oci.unsupported_reason()
    assert "aws_config_executor" in reason
    assert "stub" in reason.lower()
    assert "flx-oci-parity" in reason


# ══════════════════════════════════════════════════════════════════════════
# 3. THE MEASURED TRAPS
# ══════════════════════════════════════════════════════════════════════════


def test_the_health_path_is_not_either_siblings_and_this_is_no_localstack_dropin():
    """Measured: `/_localstack/health` and `/_floci/health` both 404 here.

    The no-alias half is asserted over the module's STRING CONSTANTS rather than
    its raw text, so the prose in the docstring (which must be free to explain
    why there is no alias layer) cannot satisfy or break it.
    """
    assert emulator_oci.HEALTH_PATH == "/health"
    tree = ast.parse(_SEAM_SRC.read_text(encoding="utf-8"))
    constants = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    aliases = {c for c in constants if c.startswith("LOCALSTACK_")}
    assert not aliases, f"no LOCALSTACK_* alias layer may be wired here: {aliases}"
    assert "/_localstack/health" not in constants
    assert "/_floci/health" not in constants


def test_the_health_service_map_is_declared_not_to_be_a_health_signal():
    """TWO constants, and the second is what stops the first being misread.

    Measured: the map is byte-identical on a container whose docker socket
    points at a provably absent path, and `running` is its only observed value.
    """
    assert emulator_oci.HEALTH_HAS_SERVICE_MAP is True
    assert emulator_oci.HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY is True


def test_functions_is_a_declared_service_despite_the_startup_log():
    """The emulator's two self-reports disagree, and the LOG is the wrong one.

    An earlier draft of the spike took the service set from the startup
    `ServiceRegistry` line -- the obvious move, and what the floci-az seam had
    to do because that emulator publishes no map -- and so recorded `functions`
    as absent. `/health` lists it and `GET /20181201/applications` answers.
    """
    assert emulator_oci.SERVICE_LIST_SELF_REPORTS_DISAGREE is True
    assert "functions" in emulator_oci.SERVICE_LIST_LOG_LINE_OMITS
    assert "functions" in emulator_oci.SERVICES
    assert emulator_oci.TABLE_SERVICE["applications"] == "functions"


def test_oke_fabricates_active_WITH_docker_and_the_sibling_constant_is_measured_empty():
    """The mirror of the GCP sibling's constant, NOT a copy of it.

    floci-gcp needed `FABRICATED_SUCCESS_WITHOUT_DOCKER = {"cloudrun"}` because
    Cloud Run returns a fabricated 200 with no socket. Here the socket-ABSENT
    path is the honest one (OKE returns 500 and records nothing) and the
    fabrication happens WITH a socket. Keeping the empty set explicit is the
    point: an omitted constant reads as "not considered".
    """
    assert emulator_oci.FABRICATED_SUCCESS_WITHOUT_DOCKER == frozenset()
    assert emulator_oci.FABRICATED_ACTIVE_WITH_DOCKER == frozenset({"oke"})
    assert emulator_oci.OKE_LIFECYCLE_IS_UNVERIFIED is True
    assert emulator_oci.CONTAINER_BACKED_SERVICES == frozenset({"oke"})
    # Version-pinned, unlike the GCP sibling's two `:latest` tags -- so an
    # air-gap cache for this emulator is enumerable by digest.
    assert emulator_oci.CONTAINER_BACKED_IMAGES["oke"] == "rancher/k3s:v1.30.1-k3s1"
    assert ":latest" not in emulator_oci.CONTAINER_BACKED_IMAGES["oke"]


def test_the_azure_subscription_scope_trap_is_not_inherited():
    """Measured absent, so no fan-out and no `partial` rung is carried.

    A sibling seam inheriting the Azure fan-out "to be safe" would add a
    per-scope loop that nothing here needs and a comment citing a trap this
    emulator does not have.

    Asserted over the seam's API and its path TEMPLATES rather than by slicing
    its text: every lane is scoped by exactly the two nouns OCI has, so there is
    no third scope for a fan-out to iterate.
    """
    assert not hasattr(emulator_oci, "SUBSCRIPTION_SCOPE_IS_EMPTY")
    assert not hasattr(emulator_oci, "resource_paths_for_scopes")
    placeholders: set[str] = set()
    for template in emulator_oci.REST_RESOURCE_PATHS.values():
        placeholders |= set(re.findall(r"\{(\w+)\}", template))
    assert placeholders <= {"namespace", "compartment"}, placeholders
    # The twin's read ladder carries no `partial` rung either -- one request per
    # lane cannot half-answer.
    assert "partial" not in twin_mod.classify_read(
        type("O", (), {"ok": True, "connector_status": "partial"})()
    )


def test_no_host_proxy_port_ranges_are_declared():
    assert not hasattr(emulator_oci, "PROXY_PORT_RANGES")


def test_response_endpoints_are_never_followed():
    """Measured: a resource body advertises the CONTAINER's port, hard-coded,
    so it is wrong on any non-default host mapping."""
    assert emulator_oci.RESPONSE_ENDPOINTS_ARE_CONTAINER_LOCAL is True
    url = emulator_oci.resource_url("vaults", {**ON, "FLOCI_OCI_ENDPOINT": "http://h:9"})
    assert url.startswith("http://h:9"), url


# ══════════════════════════════════════════════════════════════════════════
# 4. TWO ENVELOPES, ONE EMULATOR
# ══════════════════════════════════════════════════════════════════════════


def test_queues_is_the_only_wrapped_lane_and_rows_from_handles_both():
    """A reader assuming either shape is wrong about the other."""
    wrapped = {t for t, k in emulator_oci.RESPONSE_ROW_KEY.items() if k is not None}
    assert wrapped == {"queues"}
    assert emulator_oci.rows_from("queues", {"items": [{"id": "q"}]}) == [{"id": "q"}]
    assert emulator_oci.rows_from("vaults", [{"id": "v"}]) == [{"id": "v"}]
    # ...and each shape yields [] when handed the OTHER lane's envelope, rather
    # than raising or inventing a row.
    assert emulator_oci.rows_from("queues", [{"id": "v"}]) == []
    assert emulator_oci.rows_from("vaults", {"items": [{"id": "q"}]}) == []


def test_every_rest_lane_declares_its_envelope():
    """A lane missing from the map would silently take the `items` default."""
    assert set(emulator_oci.RESPONSE_ROW_KEY) == set(emulator_oci.REST_RESOURCE_PATHS)


def test_an_error_body_never_becomes_rows():
    assert emulator_oci.rows_from("vaults", {"code": "NotAuthorized"}) == []
    assert emulator_oci.rows_from("vaults", None) == []


# ══════════════════════════════════════════════════════════════════════════
# 5. SCOPING NOUNS AND PATH SAFETY
# ══════════════════════════════════════════════════════════════════════════


def test_a_namespace_is_discoverable_unlike_the_gcp_project_id():
    """The one thing floci-oci offers that its GCP sibling does not.

    `GET /n/` returns the namespace as a bare JSON string, so the seam can offer
    a probe. GCP's `GET /v1/projects` 404s, which is why that seam could only
    read configuration.
    """
    assert hasattr(emulator_oci, "namespace_probed")
    assert emulator_oci.namespace({}) == emulator_oci.DEFAULT_NAMESPACE
    assert emulator_oci.namespace({"FLOCI_OCI_NAMESPACE": "acme"}) == "acme"


def test_the_compartment_defaults_to_the_tenancy_and_always_answers():
    """Every lane but two REQUIRES the parameter, so a caller with none has
    nothing to fall back on."""
    assert emulator_oci.compartment_id({}) == emulator_oci.DEFAULT_TENANCY_OCID
    assert (
        emulator_oci.compartment_id({"FLOCI_OCI_TENANCY_OCID": "ocid1.tenancy.oc1..x"})
        == "ocid1.tenancy.oc1..x"
    )
    # An explicit compartment wins over the tenancy fallback.
    assert (
        emulator_oci.compartment_id(
            {"FLOCI_OCI_TENANCY_OCID": "ocid1.tenancy.oc1..x",
             "FLOCI_OCI_COMPARTMENT_OCID": "ocid1.compartment.oc1..y"}
        )
        == "ocid1.compartment.oc1..y"
    )


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "a/b", "x?y=1", "has space", "frag#ment", "pct%00"],
)
def test_a_hostile_scoping_value_cannot_reach_the_composed_url(hostile):
    """These values are INTERPOLATED INTO A URL. `_path_safe` is the only thing
    between an env var and that request."""
    for var in ("FLOCI_OCI_NAMESPACE", "FLOCI_OCI_COMPARTMENT_OCID"):
        url = emulator_oci.resource_url("buckets", {**ON, var: hostile})
        assert hostile not in url, url


def test_a_lane_this_emulator_does_not_serve_raises_rather_than_composing():
    """Returning a plausible path for an absent service hands a caller a URL
    that 404s -- which reads as "no such resource", not "no such service"."""
    for absent in ("instances", "vcns", "databases", "loadbalancers"):
        with pytest.raises(KeyError):
            emulator_oci.resource_path(absent)


def test_the_lanes_that_require_a_compartment_are_recorded():
    """Measured: vaults and buckets 400 without it while streams returns 200,
    so a caller must not generalise from one lane."""
    assert emulator_oci.COMPARTMENT_REQUIRED_LANES == frozenset({"vaults", "buckets"})
    assert emulator_oci.COMPARTMENT_REQUIRED_LANES <= set(emulator_oci.REST_RESOURCE_PATHS)


# ══════════════════════════════════════════════════════════════════════════
# 6. THE DOCKER TRI-STATE
# ══════════════════════════════════════════════════════════════════════════


def test_docker_backed_is_tri_state_and_none_is_not_false():
    """`None` (cannot tell) must never be read as `False` (proven absent).

    Measured on this host: Docker Desktop 28.5.1 RUNNING and
    `os.path.exists(r"\\\\.\\pipe\\docker_engine")` False -- so a Windows host
    with no DOCKER_HOST is `None`, and returning `False` there would be a
    fabricated refusal for a working daemon.
    """
    assert emulator_oci.docker_backed({"DOCKER_HOST": "tcp://d:2375"}) is True
    assert emulator_oci.docker_backed({"DOCKER_HOST": "/nope/absent.sock"}) is False
    assert emulator_oci.docker_backed({"FLOCI_OCI_DOCKER_SOCKET": "weird"}) is None


def test_listing_inventory_is_not_the_same_question_as_reaching_a_data_plane():
    """Listing OKE clusters spawns nothing, so an inventory reader must not
    consult `data_plane_supported`."""
    absent = {"DOCKER_HOST": "/nope/absent.sock"}
    assert emulator_oci.data_plane_supported("oke", absent) is False
    # Not container-backed -> permitted regardless.
    for svc in ("objectstorage", "vault", "queue", "streaming"):
        assert emulator_oci.data_plane_supported(svc, absent) is True
    # Unknown socket PERMITS: the emulator's own error beats our guess.
    assert emulator_oci.data_plane_supported("oke", {"FLOCI_OCI_DOCKER_SOCKET": "?"}) is True


def test_status_ladder_is_ordered_by_severity():
    assert emulator_oci.status({}, probe=False) == emulator_oci.STATUS_DISABLED
    assert (
        emulator_oci.status({**ON, "DOCKER_HOST": "/nope/absent.sock"}, probe=False)
        == emulator_oci.STATUS_DEGRADED_NO_DOCKER
    )
    assert (
        emulator_oci.status({**ON, "DOCKER_HOST": "tcp://d:2375"}, probe=False)
        == emulator_oci.STATUS_ENABLED
    )


# ══════════════════════════════════════════════════════════════════════════
# 7. PERSISTENCE, AND THE THREE SPELLINGS THAT DO NOTHING
# ══════════════════════════════════════════════════════════════════════════


def test_the_storage_mode_variable_is_the_one_that_was_measured_to_work():
    """Three plausible spellings are silently ignored by the emulator, so an
    operator who used one believes they enabled persistence and did not."""
    assert emulator_oci.STORAGE_MODE_VAR == "FLOCI_OCI_STORAGE_MODE"
    assert emulator_oci.storage_mode({}) == "memory"
    assert emulator_oci.storage_mode({"FLOCI_OCI_STORAGE_MODE": "persistent"}) == "persistent"
    # A spelling the emulator ignores must not change ICDEV's answer either --
    # otherwise the two disagree and ICDEV reports persistence that is not on.
    assert emulator_oci.storage_mode({"FLOCI_OCI_PERSISTENCE": "persistent"}) == "memory"


def test_the_storage_mode_is_reported_where_an_operator_reads_health():
    """An accessor nothing calls is the declared-but-unconsumed defect at
    function scale, so `storage_mode()` has exactly one consumer: the
    connector's health picture.

    It matters there. `memory` -- the default -- means every bucket, vault and
    queue this connector reports is GONE when the container stops, which changes
    what a reader should conclude from an empty estate. And it is labelled
    DECLARED rather than probed: the emulator does not publish it on `/health`,
    so this is what the deployment ASKED for.
    """
    conn = _FakeConnector({"/health": _HEALTH_BODY})
    health = conn.health_check()
    assert health["status"] == "disabled", "off by default -- no probe was made"

    import os

    old = dict(os.environ)
    os.environ["FLOCI_OCI_ENABLED"] = "true"
    try:
        health = _FakeConnector({"/health": _HEALTH_BODY}).health_check()
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert health["storage_mode"] == "memory"
    assert health["storage_mode_is_declared_not_probed"] is True


def test_the_docker_rung_is_currently_unreachable_and_that_is_measured():
    """`simulate_delta`'s container-backed rung serves a WORKING container-backed
    service, and on floci-oci 0.4.0 that set is EMPTY.

    Both `CONTAINER_BACKED_SERVICES` and `FABRICATED_ACTIVE_WITH_DOCKER` are
    exactly `{"oke"}`, so the `high` rung above always wins and the `medium` one
    never fires. That is kept rather than deleted -- a later release adding a
    working container-backed service would otherwise silently get no
    docker-socket finding -- and asserted rather than left as silent dead code.

    IF THIS FAILS the two sets have diverged, which is good news: the rung is
    now live. Add a case exercising it and update this docstring.
    """
    working = set(emulator_oci.CONTAINER_BACKED_SERVICES) - set(
        emulator_oci.FABRICATED_ACTIVE_WITH_DOCKER
    )
    assert working == set(), (
        f"{sorted(working)} is now container-backed and NOT broken, so "
        "simulate_delta's `medium` rung is reachable -- add a test for it"
    )
    # ...and the consequence, exercised: even with the socket PROVEN absent, an
    # `oke` delta reports the `high` fabrication finding and never the `medium`
    # docker one.
    import os

    old = dict(os.environ)
    os.environ["DOCKER_HOST"] = "/nope/absent.sock"
    try:
        out = twin_mod.FlociOciTwinAdapter().simulate_delta("local", {"services": ["oke"]})
    finally:
        os.environ.clear()
        os.environ.update(old)
    rules = {v.get("rule_id") for v in out["violations"]}
    assert "floci-oci-service-fabricates-active" in rules
    assert "floci-oci-service-unsupported-locally" not in rules


def test_only_the_exercised_modes_are_declared_supported():
    """The card also claims `hybrid` and `wal`; they were NOT measured, so they
    are not enumerated. An unmeasured value passes through with a warning
    rather than being rewritten to `memory`."""
    assert emulator_oci.MEASURED_STORAGE_MODES == frozenset({"memory", "persistent"})
    assert emulator_oci.storage_mode({"FLOCI_OCI_STORAGE_MODE": "wal"}) == "wal"


# ══════════════════════════════════════════════════════════════════════════
# 8. THE PIN AGAINST WHAT IS ACTUALLY DEPLOYED
# ══════════════════════════════════════════════════════════════════════════


def test_the_compose_image_and_the_seam_image_are_the_same_literal(compose):
    assert compose["services"]["floci-oci"]["image"] == emulator_oci.IMAGE
    assert ":latest" not in emulator_oci.IMAGE


def test_the_compose_healthcheck_probes_the_seams_health_path(compose):
    test = compose["services"]["floci-oci"]["healthcheck"]["test"]
    assert any(emulator_oci.HEALTH_PATH in str(part) for part in test), test
    assert any(str(emulator_oci.CONTAINER_PORT) in str(part) for part in test), test


def test_the_published_port_is_loopback_only(compose):
    for port in compose["services"]["floci-oci"]["ports"]:
        assert str(port).startswith("127.0.0.1:"), port


def test_the_digest_is_recorded_for_an_air_gap_bundle_check():
    assert emulator_oci.IMAGE_DIGEST.startswith("sha256:")
    assert len(emulator_oci.IMAGE_DIGEST) == len("sha256:") + 64


def test_the_spike_exists_and_is_dated():
    """The card required a measurement BEFORE any code. A seam whose constants
    cite a document that does not exist is a claim with no derivation."""
    text = _SPIKE.read_text(encoding="utf-8", errors="replace")
    assert "Measured 2026-09-05" in text
    assert emulator_oci.IMAGE_DIGEST in text
    assert emulator_oci.IMAGE in text
    # §1 is the card's decision and must be present by name.
    assert "PROVIDER_LAYER_IS_STUBBED" in _SEAM_SRC.read_text(encoding="utf-8")


def test_the_state_directory_is_gitignored():
    """This repo is PUBLIC and `data/floci/` ends in a slash, so it matches the
    directory `floci` only -- none of the three sibling rules covers this one."""
    ignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/floci-oci/" in ignore


# ══════════════════════════════════════════════════════════════════════════
# 9. THE CONNECTOR
# ══════════════════════════════════════════════════════════════════════════


class _FakeConnector(FlociOciConnector):
    """A connector whose HTTP layer is a scripted dict. No socket, no emulator."""

    def __init__(self, responses: dict, raises: dict | None = None) -> None:
        super().__init__()
        self._responses = responses
        self._raises = raises or {}
        self._config = {}
        self._endpoint = "http://fake:4599"

    def _http_get_noauth(self, url: str):  # type: ignore[override]
        for fragment, exc in self._raises.items():
            if fragment in url:
                raise RuntimeError(exc)
        for fragment, body in self._responses.items():
            if fragment in url:
                return body
        raise RuntimeError(f"unscripted URL: {url}")


#: The measured health body: eight services, every value "running".
_HEALTH_BODY = {
    "services": {n: "running" for n in sorted(emulator_oci.SERVICES)},
    "edition": "community",
    "version": "0.4.0",
}


@pytest.fixture()
def emulator_on(monkeypatch):
    """Switch the seam ON for the read tests.

    NOT a convenience. `read()` consults `emulator_oci.enabled()` FIRST and
    returns a `disabled` response before any HTTP layer is reached, which is the
    air-gap-safe default working exactly as designed -- without this fixture
    every read test below asserts against that refusal rather than against the
    read it means to exercise.
    """
    monkeypatch.setenv("FLOCI_OCI_ENABLED", "true")


def _request(table: str, limit: int | None = None):
    from tools.databridge.connector import ConnectorRequest

    return ConnectorRequest(table_name=table, limit=limit)


def test_grant_matches_connector_tables_exactly():
    """A table added to the connector is a NEW authorization decision.

    Pinned against the module-level `TABLES` so the decision cannot be skipped
    by adding a table and forgetting the manifest.
    """
    manifest = yaml.safe_load(
        (_ROOT / "args" / "databridge_agent_access.yaml").read_text(encoding="utf-8")
    )
    grant = next(c for c in manifest["connectors"] if c["name"] == "floci_oci")
    assert tuple(grant["tables"]) == TABLES
    assert grant["agents"] == ["twin_observatory_analyst"], (
        "An empty agent list grants EVERY agent including runtime-generated SMEs."
    )


def test_the_connection_row_exists_and_pins_no_endpoint():
    """The seam decides WHERE; the row decides the CEILING on where."""
    conns = yaml.safe_load(
        (_ROOT / "args" / "databridge_connections.yaml").read_text(encoding="utf-8")
    )
    row = next(c for c in conns["connections"] if c["id"] == "floci-oci-emulator-local")
    assert row["connector_name"] == "floci_oci"
    assert row["sync_direction"] == "read"
    assert "endpoint" not in row["config"], (
        "a second endpoint here would be a second switch"
    )
    assert row["config"]["egress_allowlist"] == ["localhost", "127.0.0.1", "::1"]


def test_table_scope_partitions_every_table():
    assert set(PROBE_TABLES) | set(RESOURCE_TABLES) == set(TABLES)
    for table in TABLES:
        assert table_scope(table) in ("emulator", "compartment")


def test_the_connector_declares_a_table_only_for_a_measured_lane():
    assert set(RESOURCE_TABLES) == set(emulator_oci.REST_RESOURCE_PATHS)
    for table in RESOURCE_TABLES:
        assert emulator_oci.TABLE_SERVICE[table] in emulator_oci.SERVICES


def test_the_connector_is_disabled_by_default_and_makes_no_call():
    """Air-gap-safe: no network call and no exception when the switch is off."""
    conn = _FakeConnector(responses={})  # every URL would raise
    resp = conn.read(_request("vaults"))
    assert resp.status == "disabled"
    assert resp.row_count == 0


def test_a_read_is_one_request_because_compartment_scope_is_honoured(emulator_on):
    conn = _FakeConnector({"/20180608/vaults": [{"id": "ocid1.vault.oc1.iad.a"}]})
    resp = conn.read(_request("vaults"))
    assert resp.status == "ok"
    assert resp.row_count == 1
    assert resp.metadata["scope"] == "compartment"
    assert resp.metadata["service"] == "vault"


def test_a_genuinely_empty_estate_reports_ok_with_zero_rows(emulator_on):
    """Unlike the Azure sibling, an empty list here IS a real answer."""
    conn = _FakeConnector({"/20180608/vaults": []})
    resp = conn.read(_request("vaults"))
    assert resp.status == "ok"
    assert resp.row_count == 0
    assert resp.metadata["empty_is_a_real_answer"] is True


def test_the_queue_envelope_is_unwrapped_through_the_seam(emulator_on):
    conn = _FakeConnector({"/20210201/queues": {"items": [{"id": "q1"}, {"id": "q2"}]}})
    resp = conn.read(_request("queues"))
    assert resp.status == "ok"
    assert resp.row_count == 2


def test_a_cluster_row_carries_its_unverified_caveat(emulator_on):
    """An OKE row is a RECORD, never a running cluster. The caveat rides on the
    response so a caller reading rows out of DataBridge sees it."""
    conn = _FakeConnector(
        {"/20180222/clusters": [{"id": "ocid1.cluster.x", "lifecycleState": "ACTIVE"}]}
    )
    resp = conn.read(_request("clusters"))
    assert resp.status == "ok"
    assert resp.metadata["lifecycle_is_unverified"] is True
    assert "--token" in resp.metadata["note"]
    # ...and no other lane carries it.
    other = _FakeConnector({"/20180608/vaults": []}).read(_request("vaults"))
    assert "lifecycle_is_unverified" not in other.metadata


def test_enabled_services_returns_names_only_and_says_it_is_not_health(emulator_on):
    """A status field that always says the same thing is a constant wearing a
    measurement's name, and someone would render it as a health badge."""
    conn = _FakeConnector({"/health": _HEALTH_BODY})
    resp = conn.read(_request("enabled_services"))
    assert resp.status == "ok"
    assert all(set(r) == {"service"} for r in resp.data), resp.data
    assert "running" not in json.dumps(resp.data)
    assert resp.metadata["is_enablement_not_health"] is True
    assert resp.metadata["self_reports_disagree"] is True
    assert resp.metadata["omitted_from_startup_log"] == ["functions"]
    assert resp.metadata["known_broken_services"] == ["oke"]


def test_a_health_body_with_no_service_map_is_an_error_not_an_empty_list(emulator_on):
    """The body was read and did not carry the map, so "which services are
    declared" is UNANSWERABLE rather than answered with none."""
    conn = _FakeConnector({"/health": {"version": "0.4.0"}})
    resp = conn.read(_request("enabled_services"))
    assert resp.status == "error"
    assert resp.data == []


def test_an_unreachable_lane_is_an_error_never_an_empty_ok(emulator_on):
    conn = _FakeConnector({}, raises={"/20180608/vaults": "connection refused"})
    resp = conn.read(_request("vaults"))
    assert resp.status == "error"
    assert resp.row_count == 0


def test_write_is_refused_naming_both_reasons():
    resp = FlociOciConnector().write(_request("vaults"), {"x": 1})
    assert resp.status == "unsupported"
    joined = " ".join(resp.errors)
    assert "aws_config_executor" in joined
    assert "stubbed" in joined
    assert resp.metadata["provider_layer_is_stubbed"] is True


def test_the_endpoint_host_ceiling_is_enforced_where_the_destination_is_decided():
    """Checked once, where the destination is DECIDED, so it covers every table
    read rather than only the ones routed through one helper."""
    conn = FlociOciConnector()
    conn._config = {"egress_allowlist": ["localhost"]}
    with pytest.raises(PermissionError):
        conn._assert_endpoint_allowed("http://169.254.169.254/opc/v2/instance/")
    conn._assert_endpoint_allowed("http://localhost:4599")


def test_a_refused_endpoint_does_not_become_reachable_by_calling_twice():
    conn = FlociOciConnector()
    conn._config = {"egress_allowlist": ["localhost"], "endpoint": "http://evil:4599"}
    for _ in range(2):
        with pytest.raises(PermissionError):
            conn._ensure_configured()


# ══════════════════════════════════════════════════════════════════════════
# 10. THE TWIN
# ══════════════════════════════════════════════════════════════════════════


def test_twin_targets_oci_and_its_preset_needed_no_code_change():
    """The structural difference from the GCP sibling, asserted from the catalog.

    GCP needed a new `assured_workloads` scope because every GCP catalog entry
    carries `govcloud_available: false`. OCI has REAL government regions, so the
    existing `government` scope reads correctly and `target_presets.py` was not
    touched.
    """
    from tools.twin_core import target_presets as tp

    preset = tp.load_presets()["presets"][twin_mod.DEFAULT_TARGET_PRESET]
    assert preset["csp"] == "oci"
    assert preset["region_scope"] == "government"
    assert preset["region"] == twin_mod.TARGET_REGION

    catalog = json.loads(
        (_ROOT / "context" / "cloud" / "csp_service_registry.json").read_text(
            encoding="utf-8"
        )
    )
    oci = catalog["services"]["oci"]
    govcloud = [k for k, v in oci.items() if v.get("govcloud_available")]
    assert len(govcloud) >= 6, govcloud
    # ...and the ONE that is not is exactly the service ICDEV's two
    # endpoint-honouring OCI call sites target.
    assert oci["oci_genai"]["govcloud_available"] is False


def test_the_two_regions_are_kept_apart():
    """OCI's gov cloud is a separate PARTITION, not an overlay, so the region a
    snapshot READ and the region a simulation SCORES against genuinely differ.
    Reporting one number for both would claim a government read that never
    happened."""
    assert twin_mod.TARGET_REGION == "us-langley-1"
    assert emulator_oci.DEFAULT_REGION == "us-ashburn-1"
    assert twin_mod.TARGET_REGION != emulator_oci.DEFAULT_REGION


def test_twin_snapshot_table_is_separate_from_all_three_siblings():
    assert twin_mod.FlociOciTwinAdapter.snapshot_table == "floci_oci_twin_snapshots"
    for sibling in ("floci_twin_snapshots", "floci_az_twin_snapshots",
                    "floci_gcp_twin_snapshots"):
        assert twin_mod.FlociOciTwinAdapter.snapshot_table != sibling


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (type("O", (), {"ok": False})(), "denied"),
        (type("O", (), {"ok": True, "connector_status": "ok"})(), "answered"),
        (type("O", (), {"ok": True, "connector_status": "disabled"})(), "disabled"),
        (type("O", (), {"ok": True, "connector_status": "error"})(), "error"),
        # An unrecognised status is NOT an answer.
        (type("O", (), {"ok": True, "connector_status": "partial"})(), "error"),
        (type("O", (), {"ok": True, "connector_status": ""})(), "error"),
    ],
)
def test_classify_read_ladder(outcome, expected):
    assert twin_mod.classify_read(outcome) == expected


def test_a_denial_is_tested_before_the_connector_status():
    """A refused call never reached the connector, so its empty
    `connector_status` says nothing -- reading it as "answered with no rows" is
    the conflation this adapter exists to refuse."""
    denied = type("O", (), {"ok": False, "connector_status": "ok"})()
    assert twin_mod.classify_read(denied) == "denied"


@pytest.mark.parametrize(
    "reads,verdict,basis",
    [
        ({}, "unknown", "unmeasured"),
        ({"health": "answered", "vaults": "answered"}, "pass", "all_tables_answered"),
        ({"health": "answered", "vaults": "disabled"}, "unknown", "disabled"),
        ({"health": "error", "vaults": "answered"}, "unknown", "unreachable"),
        ({"health": "answered", "vaults": "error"}, "fail", "emulator_errors"),
    ],
)
def test_classify_verdict_ladder(reads, verdict, basis):
    assert twin_mod.classify_verdict(reads) == (verdict, basis)


def test_denial_basis_is_resolved_from_structured_facts_never_prose():
    """A basis keyed on an error string goes silently wrong the day that string
    changes. The verdict is `unknown` either way; the BASIS differs because the
    repairs differ."""
    assert twin_mod.denial_basis(False, True) == "broker_denied"
    assert twin_mod.denial_basis(True, False) == "disabled"
    assert twin_mod.denial_basis(True, True) == "unreachable"
    assert twin_mod.denial_basis(None, None) == "broker_denied"


def test_persist_snapshot_takes_no_provenance_argument():
    """AST, not behaviour. A behavioural test over today's callers -- which pass
    none -- would still pass the day somebody threads a kwarg through."""
    src = Path(twin_mod.__file__).read_text(encoding="utf-8")
    fn = next(
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == "_persist_snapshot"
    )
    args = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    assert "provenance" not in args, args
    assert fn.args.kwarg is None, "a **kwargs would let a provenance in"


def test_provenance_is_emulated_and_never_observed():
    from tools.twin_core.schema import PROVENANCE_EMULATED

    assert twin_mod.PROVENANCE == PROVENANCE_EMULATED
    assert twin_mod.PROVENANCE != "observed"


def test_the_migration_derives_its_check_from_the_python_constant():
    """"SQL CHECK constraints: derive from Python constants, never hardcode."
    A hand-written CHECK is a second copy whose failure mode is silent."""
    mig = (
        _ROOT / "tools" / "db" / "migrations"
        / "20260905115811_floci_oci_twin_snapshots" / "up.py"
    )
    src = mig.read_text(encoding="utf-8")
    assert "SNAPSHOT_PROVENANCES" in src
    assert "'emulated', 'observed'" not in src, "the vocabulary must not be respelled"
    assert "floci_oci_twin_snapshots" in src


def test_emulator_scoped_tables_are_excluded_from_the_resource_count():
    """`enabled_services` alone returns eight rows on an empty estate."""
    for probe in PROBE_TABLES:
        assert probe not in twin_mod._ESTATE_TABLES
    # ...but `compartments` IS counted: a compartment is a resource INSIDE the
    # tenancy, not the container the estate lives in. The opposite call from the
    # GCP twin's `project`, and deliberately so.
    assert "compartments" in twin_mod._ESTATE_TABLES


def test_the_unverified_table_set_is_derived_from_the_seam():
    """So a release that fixes OKE empties this by changing one constant."""
    assert twin_mod._UNVERIFIED_TABLES == ("clusters",)


def test_resource_count_is_none_not_zero_when_nothing_was_measured(monkeypatch):
    """An unreachable emulator holds an UNKNOWN number of resources; 0 asserts
    it holds none."""
    adapter = twin_mod.FlociOciTwinAdapter()
    monkeypatch.setattr(
        adapter, "_read_table",
        lambda *a, **k: type("O", (), {"ok": False, "error": "denied"})(),
    )
    monkeypatch.setattr(adapter, "_persist_snapshot", lambda snap: False)
    snap = adapter.take_snapshot("local")
    assert snap["resource_count"] is None
    assert snap["verdict"] == "unknown"
    assert snap["resource_count_is_complete"] is False


def test_a_snapshot_names_its_unverified_tables_even_when_clean(monkeypatch):
    """A reader who only learns the caveat from a FAILING snapshot will read a
    clean one as a working cluster."""
    adapter = twin_mod.FlociOciTwinAdapter()
    monkeypatch.setattr(
        adapter, "_read_table",
        lambda *a, **k: type(
            "O", (), {"ok": True, "connector_status": "ok", "row_count": 1}
        )(),
    )
    monkeypatch.setattr(adapter, "_persist_snapshot", lambda snap: False)
    snap = adapter.take_snapshot("local")
    assert snap["verdict"] == "pass"
    assert snap["unverified_tables"] == ["clusters"]
    assert "--token" in snap["unverified_reason"]
    assert snap["provenance"] == "emulated"


def test_latest_status_over_nothing_is_unknown_never_pass(monkeypatch):
    """The twin has never looked, which is not a clean bill of health."""
    adapter = twin_mod.FlociOciTwinAdapter()
    monkeypatch.setattr(adapter, "list_snapshots", lambda *a, **k: [])
    status = adapter.latest_status("local")
    assert status["verdict"] == "unknown"
    assert status["verdict_basis"] == "no_snapshot"
    assert status["provenance"] == "emulated"


def test_simulate_delta_scores_oke_high_regardless_of_the_socket():
    """A socket makes OKE WORSE, not better: without one the call fails honestly
    with a 500, with one it succeeds and lies. So unlike the GCP sibling's
    cloudrun rung, this severity is not conditional on the socket."""
    adapter = twin_mod.FlociOciTwinAdapter()
    for env_docker in (True, False):
        env = {"DOCKER_HOST": "tcp://d:2375" if env_docker else "/nope/absent.sock"}
        import os

        old = dict(os.environ)
        os.environ.update(env)
        try:
            out = adapter.simulate_delta("local", {"services": ["oke"]})
        finally:
            os.environ.clear()
            os.environ.update(old)
        sev = [v["severity"] for v in out["violations"]
               if v.get("rule_id") == "floci-oci-service-fabricates-active"]
        assert sev == ["high"], (env_docker, out["violations"])


def test_simulate_delta_over_no_service_never_returns_a_free_pass():
    """A static simulation naming no service is honestly unscored.

    ASSERTED AS "not pass", not as "== unknown", and the difference is a real
    measurement rather than a loosened assertion. On this tree an empty delta
    comes back `warn`, carrying ONE violation -- `target-staleness` for
    `oci_gov_il5`, raised by the shared `_target_augment` because the service
    catalog is older than the preset's freshness window. That is a true finding
    about the CATALOG which this card did not introduce and must not suppress;
    what matters here is that no SERVICE-PARITY violation is invented for a
    delta naming no service, and that nothing is scored `pass` for having asked
    nothing.
    """
    out = twin_mod.FlociOciTwinAdapter().simulate_delta("local", {"services": []})
    assert out["verdict"] != "pass"
    parity = [v for v in out["violations"] if v.get("category") == "service_parity"]
    assert parity == [], parity


def test_every_simulation_envelope_carries_its_provenance_and_the_stub_caveat():
    """Including a CLEAN one: a consumer that only learns the estate was
    emulated when something is wrong will read a clean simulation as evidence
    about a real deployment."""
    out = twin_mod.FlociOciTwinAdapter().simulate_delta("local", {"services": []})
    extra = out.get("extra", out)
    assert extra["provenance"] == "emulated"
    assert extra["target_csp"] == "oci"
    assert extra["provider_layer_is_stubbed"] is True
    assert extra["iac_execution_supported"] is False
    # BOTH regions, labelled.
    assert extra["target_region"] == "us-langley-1"
    assert "emulator_region" in extra


def test_the_twin_reads_only_through_the_broker():
    """Structural. Importing the connector class and calling read() would return
    the same rows with NO authorization check and NO audit row -- the ungoverned
    side channel cef-fnd-03 exists to close."""
    src = Path(twin_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "FlociOciConnector" not in imported_names, (
        "the twin must reach the connector through broker.fetch, never directly"
    )
    assert "broker" in src
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_read_table"
    )
    calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert any(c.func.attr == "fetch" for c in calls), ast.dump(fn)
