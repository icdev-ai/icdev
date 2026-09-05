# CUI // SP-CTI
"""flx-az-01 — the floci-az Azure seam, connector and twin.

RUNS WITHOUT A LIVE EMULATOR, on purpose. Every behavioural assertion below is
driven by a fake, because CI has no Azure emulator and a test that silently
skips there is an UNMEASURED test wearing a green tick (the skip-census rule in
CLAUDE.md). The measured facts these tests pin were established live on
2026-09-05 against ``floci/floci-az:0.12.0`` and are recorded in
``docs/spikes/flx-az-parity.md``; what is pinned here is that the CODE still
encodes them.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from tools.cloud import emulator_az
from tools.databridge.connectors import floci_az_connector as conn_mod
from tools.databridge.connectors.floci_az_connector import (
    PER_RG_TABLES,
    PROBE_TABLES,
    SUBSCRIPTION_TABLES,
    TABLES,
    FlociAzConnector,
    _arm_value,
    table_scope,
)
from tools.twin_core.adapters import floci_az as twin_mod

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── the seam encodes the MEASURED facts ──────────────────────────────────────


def test_health_path_is_flocis_own_not_localstacks():
    """MEASURED: ``/_localstack/health`` returns 501 on floci-az.

    It is NOT a LocalStack drop-in, so it inherits no compat contract. A seam
    that reused the AWS health path would probe a route the emulator answers
    501 on and report every healthy emulator unreachable.
    """
    assert emulator_az.HEALTH_PATH == "/_floci/health"
    assert "localstack" not in emulator_az.HEALTH_PATH.lower()


def test_port_and_region_are_azures_not_awss():
    assert emulator_az.CONTAINER_PORT == 4577
    # NOT us-gov-west-1: that is AWS's spelling and matches no Azure region.
    assert emulator_az.DEFAULT_REGION == "usgovvirginia"


def test_health_body_carries_no_service_map():
    """MEASURED: the body is ``{status, edition, version}`` with no services key.

    Stated as a POSITIVE constant so a reader cannot take the absence of a
    service list for "nothing is running".
    """
    assert emulator_az.HEALTH_HAS_SERVICE_MAP is False
    # ...and the connector must therefore declare no `services` table.
    assert "services" not in TABLES


def test_health_version_is_not_the_release():
    """MEASURED: ``/_floci/health`` reports ``"dev"``; the release is in the image env."""
    assert emulator_az.HEALTH_REPORTS_REAL_VERSION is False


def test_no_iac_execution_is_claimed():
    """The card's explicit prohibition. ICDEV has no Azure IaC executor.

    Asserted at three levels because a claim in only one of them can drift: the
    seam constant, the connector's declared capability, and the refusal itself.
    """
    assert emulator_az.IAC_EXECUTION_SUPPORTED is False
    assert FlociAzConnector().capabilities.supports_write is False
    assert not (REPO_ROOT / "tools" / "cloud" / "azure_config_executor.py").exists(), (
        "An Azure IaC executor now exists — revisit IAC_EXECUTION_SUPPORTED and "
        "the connector's write() refusal rather than leaving them stale."
    )


def test_declared_unreachable_services_are_named_not_merged_into_absent():
    """appconfig / eventgrid / functions / monitor: banner says enabled, no route reaches them."""
    assert {"appconfig", "eventgrid", "functions", "monitor"} <= (
        emulator_az.DECLARED_UNREACHABLE_SERVICES
    )


def test_aci_and_vm_are_not_container_backed():
    """MEASURED: both report ``docker: mocked (no docker)`` even WITH a socket.

    Listing them as container-backed would make a socket-less host refuse two
    services that behave identically either way — a fabricated refusal.
    """
    assert "aci" not in emulator_az.CONTAINER_BACKED_SERVICES
    assert "vm" not in emulator_az.CONTAINER_BACKED_SERVICES


def test_service_bus_amqp_port_is_5673_not_5672():
    """MEASURED, and the card said otherwise. Event Hubs is 5672; Service Bus 5673."""
    assert emulator_az.in_proxy_range(5672)
    assert emulator_az.in_proxy_range(5673)
    # The ACR registry range the card omitted entirely.
    assert emulator_az.in_proxy_range(5000)
    assert emulator_az.in_proxy_range(5099)
    # The API port must never fall inside a proxy range.
    assert not emulator_az.in_proxy_range(emulator_az.CONTAINER_PORT)


# ── the subscription-scope trap ──────────────────────────────────────────────


def test_resource_list_paths_are_never_subscription_scoped():
    """THE HEADLINE MEASURED DEFECT, pinned structurally.

    A subscription-scoped ARM list returns ``200 {"value":[]}`` for a populated
    estate. Every generated path must therefore name a resource group.
    """
    assert emulator_az.SUBSCRIPTION_SCOPED_LIST_IS_EMPTY is True
    paths = emulator_az.resource_list_paths("virtual_networks", ["rg-a", "rg-b"])
    assert len(paths) == 2
    for path in paths:
        assert "/resourcegroups/" in path, f"subscription-scoped path leaked: {path}"


def test_empty_resource_group_list_yields_no_paths():
    """No groups means nothing to ask — NEVER a subscription-scoped fallback.

    A fallback here would be the whole defect: it would silently produce the
    one query shape that returns a fabricated empty.
    """
    assert emulator_az.resource_list_paths("virtual_networks", []) == []


def test_only_measured_answering_arm_types_are_declared():
    """Every declared ARM type answered 200 live; the 404 ones are absent."""
    for absent in ("Microsoft.Web/sites", "Microsoft.EventHub/namespaces",
                   "Microsoft.AppConfiguration/configurationStores"):
        assert absent not in {p for p, _ in emulator_az.ARM_RESOURCE_TYPES.values()}
    assert ("Microsoft.Network/virtualNetworks", "2023-05-01") == (
        emulator_az.ARM_RESOURCE_TYPES["virtual_networks"]
    )


def test_docker_backed_is_tristate_and_none_is_not_false():
    """``None`` (cannot tell) must PERMIT; only a PROVEN absence refuses."""
    assert emulator_az.docker_backed({"FLOCI_AZ_DOCKER_SOCKET": "/nope/absent.sock"}) is False
    assert emulator_az.docker_backed({"DOCKER_HOST": "tcp://host:2375"}) is True
    # A proven-absent socket refuses a container-backed DATA plane...
    absent = {"FLOCI_AZ_DOCKER_SOCKET": "/nope/absent.sock"}
    assert emulator_az.data_plane_supported("functions", absent) is False
    # ...but never a service that is not container-backed.
    assert emulator_az.data_plane_supported("blob", absent) is True


# ── the connector ────────────────────────────────────────────────────────────


class _FakeConnector(FlociAzConnector):
    """A connector whose HTTP layer is a scripted dict. No socket, no emulator."""

    def __init__(self, responses: dict, raises: dict | None = None) -> None:
        super().__init__()
        self._responses = responses
        self._raises = raises or {}
        self._config = {}
        self._endpoint = "http://fake:4577"

    def _http_get_noauth(self, url: str):  # type: ignore[override]
        for fragment, exc in self._raises.items():
            if fragment in url:
                raise RuntimeError(exc)
        for fragment, body in self._responses.items():
            if fragment in url:
                return body
        raise RuntimeError(f"unscripted URL: {url}")


def _rg_body(*names):
    return {"value": [{"name": n, "id": f"/rg/{n}", "type": "rg"} for n in names]}


@pytest.fixture()
def emulator_on(monkeypatch):
    """Switch the seam ON for the read tests.

    NOT a convenience. ``read()`` consults ``emulator_az.enabled()`` FIRST and
    returns a ``disabled`` response before any HTTP layer is reached, which is
    the air-gap-safe default working exactly as designed -- without this fixture
    every read test below asserts against that refusal rather than against the
    fan-out it means to exercise.
    """
    monkeypatch.setenv("FLOCI_AZ_ENABLED", "true")


def test_grant_matches_connector_tables_exactly():
    """A table added to the connector is a NEW authorization decision.

    Pinned against the module-level ``TABLES`` so the decision cannot be skipped
    by adding a table and forgetting the manifest.
    """
    manifest = yaml.safe_load(
        (REPO_ROOT / "args" / "databridge_agent_access.yaml").read_text(encoding="utf-8")
    )
    grant = next(c for c in manifest["connectors"] if c["name"] == "floci_az")
    assert tuple(grant["tables"]) == TABLES
    assert grant["agents"] == ["twin_observatory_analyst"], (
        "An empty agent list grants EVERY agent including runtime-generated SMEs."
    )


def test_table_scope_partitions_every_table():
    assert set(PROBE_TABLES) | set(SUBSCRIPTION_TABLES) | set(PER_RG_TABLES) == set(TABLES)
    for table in TABLES:
        assert table_scope(table) in ("probe", "subscription", "resource_group")


def test_per_rg_read_fans_out_and_merges(emulator_on):
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({
        "resourcegroups?api-version": _rg_body("rg-a", "rg-b"),
        "rg-a/providers/Microsoft.Network/virtualNetworks": {"value": [{"name": "v1"}]},
        "rg-b/providers/Microsoft.Network/virtualNetworks": {"value": [{"name": "v2"}]},
    })
    resp = c.read(ConnectorRequest(table_name="virtual_networks"))
    assert resp.status == "ok"
    assert resp.row_count == 2
    assert resp.metadata["resource_groups_enumerated"] is True


def test_resource_group_enumeration_failure_is_error_never_empty_ok(emulator_on):
    """THE DEFECT THIS CONNECTOR IS SHAPED AROUND.

    With no group list there is nothing to ask. Returning ``ok`` with
    ``row_count: 0`` would assert an empty estate that was never measured —
    indistinguishable, to every downstream reader, from a real clean result.
    """
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({}, raises={"resourcegroups?api-version": "boom"})
    resp = c.read(ConnectorRequest(table_name="virtual_networks"))
    assert resp.status == "error", "an unmeasured scope must never report ok"
    assert resp.row_count == 0
    assert resp.metadata["resource_groups_enumerated"] is False
    assert "enumerating resource groups failed" in " ".join(resp.errors)


def test_a_genuinely_empty_estate_still_reports_ok(emulator_on):
    """The counterpart, and it is why the test above is not just 'always error'.

    Groups enumerated, every group empty — that IS a measurement, and it must
    read as one.
    """
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector({
        "resourcegroups?api-version": _rg_body("rg-a"),
        "rg-a/providers/Microsoft.Network/virtualNetworks": {"value": []},
    })
    resp = c.read(ConnectorRequest(table_name="virtual_networks"))
    assert resp.status == "ok"
    assert resp.row_count == 0
    assert resp.metadata["resource_groups_enumerated"] is True


def test_partial_fanout_is_partial_not_ok(emulator_on):
    """A short list presented as a complete one is the same defect, smaller."""
    from tools.databridge.connector import ConnectorRequest

    c = _FakeConnector(
        {
            "resourcegroups?api-version": _rg_body("rg-a", "rg-b"),
            "rg-a/providers/Microsoft.Network/virtualNetworks": {"value": [{"name": "v1"}]},
        },
        raises={"rg-b/providers": "unreachable"},
    )
    resp = c.read(ConnectorRequest(table_name="virtual_networks"))
    assert resp.status == "partial"
    assert resp.row_count == 1
    assert resp.metadata["resource_groups_failed"], "failing groups must be NAMED"


def test_write_is_refused_naming_the_missing_executor():
    from tools.databridge.connector import ConnectorRequest

    resp = FlociAzConnector().write(ConnectorRequest(table_name="resources"), {})
    assert resp.status == "unsupported", "not `error` — this is a capability gap"
    assert "no Azure IaC executor" in " ".join(resp.errors)


def test_arm_value_refuses_to_manufacture_a_row():
    """A body that is not the ARM envelope yields NO rows.

    Wrapping it as one row would put an error document into an inventory.
    """
    assert _arm_value({"value": [{"a": 1}]}) == [{"a": 1}]
    assert _arm_value({"error": {"message": "nope"}}) == []
    assert _arm_value(["not", "an", "envelope"]) == []
    assert _arm_value(None) == []


# ── the twin ─────────────────────────────────────────────────────────────────


class _Outcome:
    def __init__(self, ok=True, status="ok", rows=0, errors=None):
        self.ok = ok
        self.connector_status = status
        self.row_count = rows
        self.connector_errors = errors or []
        self.error = ""
        self.audited = True


def test_twin_targets_azure_not_aws():
    """The point of the card. ``azure`` normalizes to the ``azure_gov`` presets."""
    from tools.twin_core.schema import normalize_csp

    assert twin_mod.TARGET_CSP == "azure"
    assert normalize_csp(twin_mod.TARGET_CSP) == "azure_gov"
    assert twin_mod.TARGET_REGION == "usgovvirginia"

    presets = yaml.safe_load(
        (REPO_ROOT / "args" / "twin_target_presets.yaml").read_text(encoding="utf-8")
    )
    preset = presets["presets"][twin_mod.DEFAULT_TARGET_PRESET]
    assert preset["csp"] == "azure"
    assert preset["region"] == twin_mod.TARGET_REGION


def test_twin_snapshot_table_is_separate_from_the_aws_twins():
    from tools.twin_core.adapters import floci as aws_twin

    assert twin_mod.FlociAzTwinAdapter.snapshot_table == "floci_az_twin_snapshots"
    assert (
        twin_mod.FlociAzTwinAdapter.snapshot_table != aws_twin.FlociTwinAdapter.snapshot_table
    ), "merging the two estates makes an AWS query silently return Azure rows"


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (_Outcome(ok=False), twin_mod.READ_DENIED),
        (_Outcome(status="disabled"), twin_mod.READ_DISABLED),
        (_Outcome(status="ok", rows=3), twin_mod.READ_ANSWERED),
        (_Outcome(status="partial", rows=1), twin_mod.READ_PARTIAL),
        (_Outcome(status="error", errors=["boom"]), twin_mod.READ_ERROR),
        # An unrecognised status is NOT an answer.
        (_Outcome(status="brand_new_state"), twin_mod.READ_ERROR),
    ],
)
def test_classify_read_ladder(outcome, expected):
    assert twin_mod.classify_read(outcome) == expected


def test_unmeasured_scope_is_its_own_read_outcome():
    """It is neither an answer nor a plain failure, and the basis says so."""
    outcome = _Outcome(
        status="error",
        errors=["Cannot list 'virtual_networks': enumerating resource groups failed (boom)."],
    )
    assert twin_mod.classify_read(outcome) == twin_mod.READ_SCOPE_UNMEASURED


def test_connector_still_emits_the_phrase_the_twin_classifies_on():
    """Pins the coupling in BOTH modules so a reword fails loudly, not silently.

    The twin matches on connector prose because ``FetchOutcome`` carries no
    field for "which precondition failed"; this is what stops that coupling
    rotting into a silent reclassification.
    """
    source = inspect.getsource(conn_mod.FlociAzConnector._read_per_rg)
    assert "enumerating resource groups failed" in source


@pytest.mark.parametrize(
    "reads,verdict,basis",
    [
        ({}, "unknown", "unmeasured"),
        ({"health": twin_mod.READ_DISABLED}, "unknown", "disabled"),
        ({"health": twin_mod.READ_ERROR}, "unknown", "unreachable"),
        (
            {"health": twin_mod.READ_ANSWERED, "virtual_networks": twin_mod.READ_ERROR},
            "fail",
            "emulator_errors",
        ),
        (
            {"health": twin_mod.READ_ANSWERED,
             "virtual_networks": twin_mod.READ_SCOPE_UNMEASURED},
            "warn",
            twin_mod.READ_SCOPE_UNMEASURED,
        ),
        (
            {"health": twin_mod.READ_ANSWERED, "virtual_networks": twin_mod.READ_PARTIAL},
            "warn",
            "partial_inventory",
        ),
        (
            {"health": twin_mod.READ_ANSWERED, "virtual_networks": twin_mod.READ_ANSWERED},
            "pass",
            "all_tables_answered",
        ),
    ],
)
def test_classify_verdict_ladder(reads, verdict, basis):
    assert twin_mod.classify_verdict(reads) == (verdict, basis)


def test_an_unmeasured_scope_can_never_score_pass():
    """The whole point: zero rows from an unmeasured scope is not a clean estate."""
    verdict, _ = twin_mod.classify_verdict(
        {"health": twin_mod.READ_ANSWERED, "resources": twin_mod.READ_SCOPE_UNMEASURED}
    )
    assert verdict != "pass"


def test_persist_snapshot_takes_no_provenance_argument():
    """STRUCTURAL, not behavioural, and deliberately so.

    A behavioural test over today's callers — which pass none — would still pass
    the day somebody threads a caller-supplied provenance through. Reading the
    signature and the call is what refuses that.
    """
    sig = inspect.signature(twin_mod.FlociAzTwinAdapter._persist_snapshot)
    assert list(sig.parameters) == ["self", "snap"]

    source = inspect.getsource(twin_mod.FlociAzTwinAdapter._persist_snapshot)
    tree = ast.parse(source.lstrip())
    # The provenance bound into the INSERT must be the module constant.
    assert "PROVENANCE," in source, "provenance must be the module constant, not snap[...]"
    assert 'snap["provenance"]' not in source
    assert any(isinstance(n, ast.Name) and n.id == "PROVENANCE" for n in ast.walk(tree))


def test_provenance_is_emulated_and_never_observed():
    from tools.twin_core.schema import PROVENANCE_EMULATED

    assert twin_mod.PROVENANCE == PROVENANCE_EMULATED == "emulated"


def test_health_is_excluded_from_resource_count():
    """Counting the emulator's own health row would inflate an empty estate."""
    assert "health" not in twin_mod._ESTATE_TABLES
    assert "subscriptions" not in twin_mod._ESTATE_TABLES


# ── declaration parity ───────────────────────────────────────────────────────


def test_compose_image_matches_the_seam():
    """YAML cannot import a Python constant, so the two are kept in step by hand."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    svc = compose["services"]["floci-az"]
    assert svc["image"] == emulator_az.IMAGE
    assert svc["profiles"] == ["floci-az"]
    assert emulator_az.HEALTH_PATH in " ".join(svc["healthcheck"]["test"])
    # Loopback-only: an emulator holding the host docker socket must not be
    # reachable off-host.
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"])


def test_compose_does_not_publish_the_proxy_ranges():
    """Publishing ~1,100 ports collides with the floci profile's own Redis range."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    published = compose["services"]["floci-az"]["ports"]
    assert len(published) == 1, f"only the API port should be published, got {published}"


def test_image_tag_is_pinned_never_latest():
    assert emulator_az.IMAGE_TAG != "latest"
    assert emulator_az.IMAGE_DIGEST.startswith("sha256:")


def test_component_registry_flag_matches_the_seam():
    """``icdev enable floci-az`` must write the flag the seam actually reads."""
    from tools.config.component_registry import ComponentRegistry

    comp = ComponentRegistry().get("floci_az")
    assert comp.env_flag == "FLOCI_AZ_ENABLED"
    assert comp.cli_name == "floci-az"
    assert comp.default_enabled is False
    for env in ({}, {"FLOCI_AZ_ENABLED": "true"}, {"FLOCI_AZ_ENABLED": "0"}):
        assert comp.is_enabled(env) is emulator_az.enabled(env)


def test_azure_state_directory_is_gitignored():
    """``data/floci/`` ends in a slash and does NOT cover ``data/floci-az``.

    Without its own entry, per-machine emulator state lands in this PUBLIC repo.
    """
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/floci-az/" in ignore


def test_connection_row_declares_a_host_ceiling():
    conns = yaml.safe_load(
        (REPO_ROOT / "args" / "databridge_connections.yaml").read_text(encoding="utf-8")
    )
    row = next(c for c in conns["connections"] if c["id"] == "floci-az-emulator-local")
    assert row["connector_name"] == "floci_az"
    assert row["sync_direction"] == "read"
    assert set(row["config"]["egress_allowlist"]) == {"localhost", "127.0.0.1", "::1"}
