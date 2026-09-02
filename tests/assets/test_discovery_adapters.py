# CUI // SP-CTI
"""Characterization tests for the asset discovery adapters (rmf-disc-01).

Every adapter is exercised against the SAME mock targets the runnable harness
uses (``tools/assets/discovery_adapters/harness.py``), so the harness an
operator runs on a live deployment and the suite CI runs can never drift into
describing different behaviour.

CHARACTERIZATION, NOT SPECIFICATION. ``tools/network/discovery.py`` had zero
importers, so these assertions record what the code DOES — including the two
things it does that are wrong (``_infer_vendor``'s "Unknown" sentinel and
``_infer_device_type``'s "server" fallback). Those are pinned as the upstream
behaviour AND pinned as not reaching an asset record.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tools.assets.discovery_adapters import (
    DISCOVERING_STATES,
    HEALTH_STATES,
    NOT_MEASURED_STATES,
    AdapterHealth,
    AdapterRegistry,
    DiscoveredDevice,
)
from tools.assets.discovery_adapters import harness as H
from tools.assets.discovery_adapters.base import (
    UNRECOGNISED_VENDOR,
    normalize_inference,
)
from tools.assets.discovery_adapters.csv_adapter import CSVDiscoveryAdapter
from tools.assets.discovery_adapters.gns3_adapter import GNS3DiscoveryAdapter
from tools.assets.discovery_adapters.netbox_adapter import NetBoxDiscoveryAdapter
from tools.assets.discovery_adapters.runner import (
    SecretRefusal,
    collect,
    resolve_config,
    run,
)
from tools.assets.discovery_adapters.sink import persist, table_columns
from tools.assets.discovery_adapters.snmp_adapter import SNMPDiscoveryAdapter
from tools.assets.discovery_adapters.ssh_adapter import SSHDiscoveryAdapter
from tools.db.storage import StorageConnection

# The ni_devices shape from tools/network/db/init_db.py — the SQLite fallback.
# It has NO `source`, `rack`, `criticality` or `classification` column; the live
# PostgreSQL table does. The sink must write correctly against both.
DDL_INIT_DB = """
CREATE TABLE ni_devices (
    id TEXT PRIMARY KEY, topology_id TEXT, node_id TEXT NOT NULL, label TEXT NOT NULL,
    device_type TEXT NOT NULL, vendor TEXT, model TEXT, firmware_version TEXT,
    eol_date TEXT, eos_date TEXT, purchase_date TEXT, purchase_cost REAL DEFAULT 0,
    annual_maintenance_cost REAL DEFAULT 0, replacement_cost REAL DEFAULT 0,
    site TEXT, rack_location TEXT, criticality_score REAL DEFAULT 0,
    downstream_count INTEGER DEFAULT 0, notes TEXT, properties_json TEXT DEFAULT '{}',
    created_at TEXT, updated_at TEXT
);
"""

# The live PostgreSQL shape, measured 2026-09-02 from information_schema.
DDL_LIVE = """
CREATE TABLE ni_devices (
    id TEXT PRIMARY KEY, topology_id TEXT, node_id TEXT NOT NULL, label TEXT,
    device_type TEXT, vendor TEXT, model TEXT, firmware_version TEXT,
    eol_date TEXT, eos_date TEXT, purchase_date TEXT, purchase_cost REAL,
    replacement_cost REAL, criticality TEXT, site TEXT, rack TEXT,
    maintenance_contract TEXT, contract_expiry TEXT, notes TEXT,
    created_at TEXT, updated_at TEXT, classification TEXT DEFAULT 'CUI',
    downstream_count INTEGER, properties_json TEXT, annual_maintenance_cost REAL,
    criticality_score REAL, rack_location TEXT, source TEXT
);
"""

CSV_COLUMNS = {
    "node_id": ["Hostname"],
    "label": ["Hostname"],
    "device_type": ["Role"],
    "vendor": ["Manufacturer"],
    "model": ["Part Number"],
    "firmware_version": ["OS Version"],
    "serial": ["SN"],
    "site": ["Facility"],
    "rack": ["Cabinet"],
    "ip_address": ["Mgmt IP"],
}


def _conn(tmp_path, ddl: str = DDL_INIT_DB, name: str = "nd.db"):
    """A throwaway SQLite ni_devices, wrapped the way the canvas wraps one.

    Never `get_connection()` with no db_path: that writes the ambient
    data/icdev.db of whatever checkout the test runs in.
    """
    raw = sqlite3.connect(str(tmp_path / name))
    raw.row_factory = sqlite3.Row
    raw.executescript(ddl)
    return StorageConnection(raw, "sqlite")


# ── The contract itself ───────────────────────────────────────────────────────


def test_all_five_adapters_are_registered():
    assert AdapterRegistry.names() == ["csv", "gns3", "netbox", "snmp", "ssh"]


def test_health_states_are_disjoint_categories():
    """The three "we did not look" states are never in the discovering set."""
    assert set(DISCOVERING_STATES).isdisjoint(NOT_MEASURED_STATES)
    assert set(DISCOVERING_STATES) <= set(HEALTH_STATES)
    assert set(NOT_MEASURED_STATES) <= set(HEALTH_STATES)


def test_an_unknown_health_state_is_refused():
    with pytest.raises(ValueError):
        AdapterHealth(adapter="x", fabric="f", state="ok")


def test_unmeasured_is_never_a_verdict_about_the_source():
    for state in NOT_MEASURED_STATES:
        health = AdapterHealth(adapter="x", fabric="f", state=state)
        assert health.measured is False
        assert health.can_discover is False


def test_stable_id_separates_the_same_hostname_on_two_fabrics():
    a = DiscoveredDevice(node_id="sw-01", adapter="csv", fabric="nipr")
    b = DiscoveredDevice(node_id="sw-01", adapter="csv", fabric="sipr")
    c = DiscoveredDevice(node_id="sw-01", adapter="csv", fabric="nipr")
    assert a.stable_id() != b.stable_id()
    assert a.stable_id() == c.stable_id()


def test_a_device_with_no_identity_is_refused():
    with pytest.raises(ValueError):
        DiscoveredDevice(node_id="   ")


# ── csv ───────────────────────────────────────────────────────────────────────


def test_csv_maps_declared_headers_and_skips_an_identityless_row():
    with H.mock_csv() as path:
        adapter = CSVDiscoveryAdapter(
            fabric="mock", config={"path": str(path), "columns": CSV_COLUMNS}
        )
        health = adapter.health()
        devices = adapter.discover()

    assert health.state == "healthy"
    # Three data rows; the third has an empty Hostname and cannot become an
    # asset. It is skipped, never given an invented id.
    assert [d.node_id for d in devices] == ["core-sw-01", "edge-fw-01"]
    first = devices[0]
    assert (first.vendor, first.model, first.site, first.rack) == (
        "Acme Networks",
        "AX-9000",
        "Site A",
        "R12",
    )
    # A column in no declared mapping is preserved rather than dropped.
    assert first.properties["csv_extra"]["Owner"] == "netops"


def test_csv_missing_file_is_unreachable_and_missing_path_is_unconfigured(tmp_path):
    assert CSVDiscoveryAdapter(fabric="f", config={}).health().state == "unconfigured"
    absent = CSVDiscoveryAdapter(
        fabric="f", config={"path": str(tmp_path / "nope.csv")}
    ).health()
    assert absent.state == "unreachable"


def test_csv_header_without_the_key_column_is_degraded_not_healthy(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("Widget,Colour\na,b\n", encoding="utf-8")
    health = CSVDiscoveryAdapter(
        fabric="f", config={"path": str(path), "columns": CSV_COLUMNS}
    ).health()
    assert health.state == "degraded"
    assert "node_id" in health.detail


# ── netbox ────────────────────────────────────────────────────────────────────


def test_netbox_reads_vendor_and_model_that_get_devices_would_drop():
    with H.mock_netbox() as url:
        adapter = NetBoxDiscoveryAdapter(
            fabric="mock", config={"url": url, "token": "mock-token"}
        )
        health = adapter.health()
        devices = adapter.discover()

    assert health.state == "healthy"
    assert health.source_version == "4.1.0-mock"
    assert [d.node_id for d in devices] == ["101", "102"]
    core = devices[0]
    assert (core.vendor, core.model, core.firmware_version) == (
        "Acme Networks",
        "AX-9000",
        "4.2.1",
    )
    # v4 `role` and v3 `device_role` must both map through the same table.
    assert core.device_type == "switch-l3"
    assert devices[1].device_type == "firewall"
    assert core.ip_address == "10.10.0.1"


def test_netbox_get_devices_still_drops_vendor_and_model():
    """Pins WHY get_devices_raw had to exist. If this ever fails, the canvas
    mapper grew the fields and the adapter can go back to the public mapper."""
    from tools.network.netbox_client import NetBoxClient

    with H.mock_netbox() as url:
        mapped = NetBoxClient(url=url, token="t").get_devices()
    assert mapped and "vendor" not in mapped[0] and "model" not in mapped[0]


def test_netbox_without_credentials_is_unconfigured_not_unreachable():
    health = NetBoxDiscoveryAdapter(fabric="f", config={"url": "http://x"}).health()
    assert health.state == "unconfigured"
    assert "token" in health.detail


def test_netbox_pointing_at_nothing_is_unreachable():
    # Port 1 on loopback: nothing listens, and nothing outside this host is
    # contacted.
    health = NetBoxDiscoveryAdapter(
        fabric="f", config={"url": "http://127.0.0.1:1", "token": "t", "timeout": 2}
    ).health()
    assert health.state == "unreachable"


# ── gns3 ──────────────────────────────────────────────────────────────────────


def test_gns3_nodes_are_labelled_lab_evidence():
    with H.mock_gns3() as url:
        adapter = GNS3DiscoveryAdapter(fabric="mock-lab", config={"url": url})
        health = adapter.health()
        devices = adapter.discover()

    assert health.state == "healthy"
    assert health.source_version == "2.2.49-mock"
    assert [d.label for d in devices] == ["R1", "SW1"]
    # A lab node is not a deployed switch and must never read as one.
    assert all(d.properties["evidence_kind"] == "lab" for d in devices)
    assert devices[0].model == "c7200"


# ── snmp ──────────────────────────────────────────────────────────────────────


def test_snmp_without_pysnmp_is_unavailable_and_names_the_dependency(monkeypatch):
    """An ABSENT dependency is `unavailable`, never `unreachable` — one is an
    install, the other is a firewall.

    The absent state is FORCED rather than inherited from the host: on a runner
    that happens to have pysnmp this assertion would otherwise not run at all,
    and a test that quietly stops asserting on some machines is the skip defect
    with extra steps. pysnmp is in fact absent here (2026-09-02), which is why
    this is also the state the shipped deployment reports.
    """
    from tools.network import discovery

    monkeypatch.setattr(discovery, "_HAS_PYSNMP", False)
    health = SNMPDiscoveryAdapter(
        fabric="f", config={"targets": ["10.0.0.1"]}
    ).health()
    assert health.state == "unavailable"
    assert health.dependency == "pysnmp"
    assert "pysnmp" in health.detail
    # It IS a fact — just not one about the device — so it is measured, and it
    # is not a state from which anything may be discovered.
    assert health.measured is True
    assert health.can_discover is False


def test_ssh_without_netmiko_is_unavailable_and_names_the_dependency(monkeypatch):
    from tools.network import discovery

    monkeypatch.setattr(discovery, "_HAS_NETMIKO", False)
    health = SSHDiscoveryAdapter(
        fabric="f", config={"targets": ["10.0.0.1"], "username": "svc"}
    ).health()
    assert health.state == "unavailable"
    assert health.dependency == "netmiko"
    assert health.can_discover is False


def test_snmp_runs_the_real_discover_snmp_against_a_mock_mib():
    adapter = SNMPDiscoveryAdapter(
        fabric="mock", config={"targets": ["10.10.0.1"], "community": "mock"}
    )
    with H.mock_snmp_target():
        health = adapter.health()
        devices = adapter.discover()

    assert health.state == "healthy"
    assert len(devices) == 1
    device = devices[0]
    assert device.node_id == "core-sw-01"
    # The real interface assembly: three parallel walks joined on OID index.
    interfaces = device.properties["interfaces"]
    assert [i["name"] for i in interfaces] == [
        "GigabitEthernet0/0",
        "GigabitEthernet0/1",
    ]
    assert interfaces[0]["speed_bps"] == 1000000000
    assert [i["oper_status"] for i in interfaces] == ["up", "down"]
    # The real neighbour correlation: LLDP then CDP, matched by OID suffix.
    neighbors = device.properties["neighbors"]
    assert [(n["neighbor"], n["protocol"]) for n in neighbors] == [
        ("edge-fw-01", "lldp"),
        ("access-sw-04", "cdp"),
    ]
    assert neighbors[0]["remote_port"] == "GigabitEthernet0/1"
    assert neighbors[1]["platform"] == "AX-1000"


def test_snmp_target_that_does_not_answer_yields_no_device():
    adapter = SNMPDiscoveryAdapter(fabric="f", config={"targets": ["10.10.0.1"]})
    from tools.network import discovery

    saved = (discovery._HAS_PYSNMP, discovery._snmp_get)
    discovery._HAS_PYSNMP = True
    discovery._snmp_get = lambda *a, **k: None
    try:
        assert adapter.health().state == "unreachable"
        assert adapter.discover() == []
    finally:
        discovery._HAS_PYSNMP, discovery._snmp_get = saved


# ── ssh ───────────────────────────────────────────────────────────────────────


def test_ssh_health_never_logs_in():
    """A health probe that opened a session would double the auth attempts
    against every device on every sweep."""
    adapter = SSHDiscoveryAdapter(
        fabric="f", config={"targets": ["10.10.0.1"], "username": "svc"}
    )
    with H.mock_ssh_target() as connected:
        health = adapter.health()
    assert health.state == "degraded"
    assert health.state != "healthy"
    assert connected == []


def test_ssh_runs_the_real_cdp_parser_against_a_mock_device():
    adapter = SSHDiscoveryAdapter(
        fabric="mock",
        config={"targets": ["10.10.0.1"], "username": "svc", "device_type": "cisco_ios"},
    )
    with H.mock_ssh_target() as connected:
        devices = adapter.discover()

    assert len(devices) == 1
    device = devices[0]
    assert device.node_id == "core-sw-01"  # from find_prompt(), '#' stripped
    neighbors = device.properties["neighbors"]
    assert [n["neighbor"] for n in neighbors] == ["access-sw-04", "edge-fw-01"]
    assert neighbors[0]["platform"] == "Acme AX-1000"
    assert neighbors[0]["remote_port"] == "GigabitEthernet0/24"
    assert device.properties["interfaces"][0]["ip"] == "10.10.0.1"
    # The netmiko driver name is config, not a discovered attribute, and is not
    # rebound by the per-device inference.
    assert device.properties["netmiko_device_type"] == "cisco_ios"
    assert connected[0].disconnected is True


def test_ssh_without_username_is_unconfigured():
    with H.mock_ssh_target():
        health = SSHDiscoveryAdapter(
            fabric="f", config={"targets": ["10.0.0.1"]}
        ).health()
    assert health.state == "unconfigured"
    assert "username" in health.detail


# ── The sysDescr inference defect ─────────────────────────────────────────────


def test_upstream_inference_still_returns_its_sentinels():
    """Characterizes the DEFECT, so a later upstream fix is visible here."""
    from tools.network import discovery

    descr = "Acme Networks AXOS Software, Version 4.2.1"
    assert discovery._infer_vendor(descr) == UNRECOGNISED_VENDOR
    # An unrecognised ROUTER is filed as a server — a real type, not a sentinel.
    assert discovery._infer_device_type(descr) == "server"


def test_the_sentinel_never_becomes_an_asset_attribute():
    device_type, vendor, provenance = normalize_inference("server", UNRECOGNISED_VENDOR)
    assert (device_type, vendor) == ("", "")
    assert provenance["recognised_by_sysdescr_table"] is False
    # The raw inference is kept: it is evidence about the inference, not about
    # the device, and deleting it would hide that the device was unclassified.
    assert provenance["vendor_raw"] == UNRECOGNISED_VENDOR
    assert provenance["device_type_raw"] == "server"


def test_a_lab_import_is_never_labelled_discovery():
    """The label routes evidence: `topology_ingest` is excluded from the
    de-facto learner's inventory feed, `discovery` is its strongest input."""
    from tools.assets.discovery_adapters import (
        CSVDiscoveryAdapter as _Csv,
        GNS3DiscoveryAdapter as _Gns3,
        NetBoxDiscoveryAdapter as _Netbox,
        SNMPDiscoveryAdapter as _Snmp,
        SSHDiscoveryAdapter as _Ssh,
    )

    assert _Gns3.evidence_source == "topology_ingest"
    assert _Snmp.evidence_source == "discovery"
    assert _Ssh.evidence_source == "discovery"
    assert _Csv.evidence_source == "csv"
    assert _Netbox.evidence_source == "netbox"
    with H.mock_gns3() as url:
        devices = _Gns3(fabric="lab", config={"url": url}).discover()
    assert all(d.source_label == "topology_ingest" for d in devices)


def test_the_evidence_label_survives_a_per_instance_rename():
    """The runner renames an adapter to its declared instance id. The label is
    a property of the KIND of source and must not follow the rename."""
    from tools.assets.discovery_adapters import GNS3DiscoveryAdapter

    adapter = GNS3DiscoveryAdapter(fabric="lab", config={"url": "http://x"})
    adapter.name = "gns3-lab"
    device = adapter._device("n1")
    assert device.adapter == "gns3-lab"
    assert device.source_label == "topology_ingest"


def test_an_unattributed_device_persists_as_null_not_a_guess(tmp_path):
    conn = _conn(tmp_path, DDL_LIVE, "null_source.db")
    persist([DiscoveredDevice(node_id="mystery")], conn=conn)
    assert conn.execute("SELECT source FROM ni_devices").fetchone()["source"] is None
    conn.close()


def test_a_recognised_vendor_passes_through_untouched():
    assert normalize_inference("router", "Cisco")[:2] == ("router", "Cisco")


def test_snmp_device_carries_no_fabricated_vendor():
    adapter = SNMPDiscoveryAdapter(fabric="mock", config={"targets": ["10.10.0.1"]})
    with H.mock_snmp_target():
        device = adapter.discover()[0]
    assert device.vendor == ""
    assert device.device_type == ""
    assert device.properties["inference"]["vendor_raw"] == UNRECOGNISED_VENDOR


# ── The sink ──────────────────────────────────────────────────────────────────


def test_sink_writes_the_intersection_of_both_schema_shapes(tmp_path):
    device = DiscoveredDevice(
        node_id="sw-1",
        label="sw-1",
        vendor="Acme Networks",
        model="AX-9000",
        rack="R12",
        adapter="csv-inventory-export",
        fabric="enterprise",
        source_label="csv",
    )

    legacy = _conn(tmp_path, DDL_INIT_DB, "legacy.db")
    legacy_report = persist([device], conn=legacy)
    assert legacy_report.inserted == 1
    assert legacy_report.errors == []
    # No `source` column on this shape, so provenance rides in properties_json.
    assert "source" not in legacy_report.columns
    row = legacy.execute("SELECT * FROM ni_devices").fetchone()
    assert row["rack_location"] == "R12"
    assert (
        json.loads(row["properties_json"])["discovery"]["adapter"]
        == "csv-inventory-export"
    )
    legacy.close()

    live = _conn(tmp_path, DDL_LIVE, "live.db")
    live_report = persist([device], conn=live)
    assert live_report.inserted == 1
    assert "source" in live_report.columns
    row = live.execute("SELECT * FROM ni_devices").fetchone()
    # The EVIDENCE CLASS in rmf-disc-02's vocabulary — what defacto_learner's
    # `exclude_when` is keyed on — and not an instance name of our own.
    assert row["source"] == "csv"
    assert row["rack_location"] == "R12"
    live.close()


def test_sink_is_idempotent_across_sweeps(tmp_path):
    conn = _conn(tmp_path)
    devices = [
        DiscoveredDevice(node_id="a", adapter="csv", fabric="f"),
        DiscoveredDevice(node_id="b", adapter="csv", fabric="f"),
    ]
    first = persist(devices, conn=conn)
    second = persist(devices, conn=conn)
    assert (first.inserted, first.updated) == (2, 0)
    assert (second.inserted, second.updated) == (0, 2)
    assert conn.execute("SELECT COUNT(*) AS n FROM ni_devices").fetchone()["n"] == 2
    conn.close()


def test_sink_reports_rather_than_swallows_a_missing_table(tmp_path):
    raw = sqlite3.connect(str(tmp_path / "empty.db"))
    raw.row_factory = sqlite3.Row
    conn = StorageConnection(raw, "sqlite")
    report = persist([DiscoveredDevice(node_id="a")], conn=conn)
    assert report.written == 0
    assert report.errors and "ni_devices" in report.errors[0]
    conn.close()


def test_table_columns_reads_the_live_shape(tmp_path):
    conn = _conn(tmp_path, DDL_LIVE, "cols.db")
    columns = table_columns(conn)
    assert {"source", "rack", "classification"} <= columns
    assert table_columns(conn, "no_such_table") == set()
    conn.close()


# ── Credentials ───────────────────────────────────────────────────────────────


def test_a_literal_credential_is_refused_not_warned_about():
    with pytest.raises(SecretRefusal):
        resolve_config({"token": "s3cr3t-in-a-public-repo"})


def test_a_reference_resolves_and_an_unset_one_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("RMF_DISC_TEST_TOKEN", "from-env")
    assert resolve_config({"token": "env:RMF_DISC_TEST_TOKEN"})["token"] == "from-env"
    assert resolve_config({"token": "env:RMF_DISC_TEST_UNSET"})["token"] == ""
    secret_file = tmp_path / "tok"
    secret_file.write_text("from-file\n", encoding="utf-8")
    assert resolve_config({"token": "file:%s" % secret_file})["token"] == "from-file"


def test_a_refused_credential_disables_only_its_own_instance():
    config = {
        "fabrics": [
            {
                "id": "f1",
                "adapters": [
                    {
                        "id": "bad",
                        "adapter": "netbox",
                        "enabled": True,
                        "config": {"url": "http://x", "token": "literal"},
                    }
                ],
            },
            {
                "id": "f2",
                "adapters": [
                    {"id": "off", "adapter": "csv", "enabled": False, "config": {}}
                ],
            },
        ]
    }
    reports = collect(config, discover=False)
    assert reports[0].results[0].health.state == "unconfigured"
    assert "public git history" in reports[0].results[0].health.detail
    # The other fabric is still reported — one bad declaration must not take
    # the whole sweep down.
    assert reports[1].results[0].health.state == "disabled"


# ── The shipped declaration ───────────────────────────────────────────────────


def test_the_shipped_config_declares_snmp_and_ssh_disabled():
    from tools.assets.discovery_adapters.runner import load_config

    config = load_config()
    assert config.get("_error") is None, config.get("_error")
    declared = {
        entry["adapter"]: entry
        for fabric in config["fabrics"]
        for entry in fabric["adapters"]
    }
    assert set(declared) == {"csv", "netbox", "snmp", "ssh", "gns3"}
    assert declared["snmp"]["enabled"] is False
    assert declared["ssh"]["enabled"] is False


def test_no_credential_literal_ships_in_the_declaration():
    """The declaration is in a PUBLIC repository."""
    from tools.assets.discovery_adapters.runner import load_config

    config = load_config()
    for fabric in config["fabrics"]:
        for entry in fabric["adapters"]:
            # Raises SecretRefusal if any secret-bearing key holds a literal.
            resolve_config(entry.get("config") or {})


# ── Per-fabric reporting ──────────────────────────────────────────────────────


#: The runner REFUSES a literal credential, so every config that goes through
#: it references this variable instead. Set by ``netbox_token`` below.
TOKEN_ENV = "RMF_DISC_NETBOX_TOKEN"


@pytest.fixture()
def netbox_token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "mock-token")
    return "env:%s" % TOKEN_ENV


def _two_fabric_config(csv_path, netbox_url, netbox_token="env:%s" % TOKEN_ENV):
    return {
        "_config_path": "<test>",
        "fabrics": [
            {
                "id": "enterprise",
                "name": "Enterprise fabric",
                "classification": "UNCLASSIFIED",
                "adapters": [
                    {
                        "id": "csv-inventory-export",
                        "adapter": "csv",
                        "enabled": True,
                        "config": {"path": str(csv_path), "columns": CSV_COLUMNS},
                    },
                    {
                        "id": "netbox-dcim",
                        "adapter": "netbox",
                        "enabled": True,
                        "config": {"url": netbox_url, "token": netbox_token},
                    },
                    {
                        "id": "snmp-poll",
                        "adapter": "snmp",
                        "enabled": False,
                        "config": {"targets": ["10.10.0.1"]},
                    },
                ],
            },
            {
                "id": "lab-gns3",
                "name": "GNS3 lab",
                "classification": "UNCLASSIFIED",
                "adapters": [
                    {
                        "id": "gns3-lab",
                        "adapter": "gns3",
                        "enabled": False,
                        "config": {"url": ""},
                    }
                ],
            },
        ],
    }


def test_ni_devices_populates_via_csv_and_netbox_with_snmp_disabled(
    tmp_path, netbox_token
):
    """The card's acceptance criterion, end to end.

    Against a throwaway database: ni_devices on the live board holds 24 rows
    all marked source='synthetic', and the de-facto standard learner reads that
    table as OBSERVED inventory.
    """
    conn = _conn(tmp_path, DDL_LIVE, "sweep.db")
    with H.mock_csv() as csv_path, H.mock_netbox() as netbox_url:
        result = run(
            _two_fabric_config(csv_path, netbox_url), write=True, conn=conn
        )

    enterprise = result["fabrics"][0]
    states = {a["adapter"]: a["health"]["state"] for a in enterprise["adapters"]}
    assert states == {
        "csv-inventory-export": "healthy",
        "netbox-dcim": "healthy",
        "snmp-poll": "disabled",
    }
    assert enterprise["device_count"] == 4
    assert enterprise["persisted"]["inserted"] == 4
    assert enterprise["persisted"]["errors"] == []

    rows = conn.execute("SELECT node_id, source FROM ni_devices ORDER BY node_id").fetchall()
    assert [r["node_id"] for r in rows] == ["101", "102", "core-sw-01", "edge-fw-01"]
    assert {r["source"] for r in rows} == {"netbox", "csv"}
    # snmp is disabled, so nothing it would have found is in the table — and
    # `discovery` is the label its rows would have carried.
    assert not any(r["source"] == "discovery" for r in rows)
    # The adapter INSTANCE and the fabric are still recoverable, from the
    # column that exists on both schema shapes.
    provenance = {
        json.loads(r["properties_json"])["discovery"]["adapter"]
        for r in conn.execute("SELECT properties_json FROM ni_devices").fetchall()
    }
    assert provenance == {"csv-inventory-export", "netbox-dcim"}
    conn.close()


def test_health_is_reported_per_fabric_and_fabrics_are_never_blended(
    tmp_path, netbox_token
):
    conn = _conn(tmp_path, DDL_LIVE, "perfabric.db")
    with H.mock_csv() as csv_path, H.mock_netbox() as netbox_url:
        result = run(
            _two_fabric_config(csv_path, netbox_url), write=True, conn=conn
        )
    conn.close()

    by_fabric = {f["fabric"]: f for f in result["fabrics"]}
    assert set(by_fabric) == {"enterprise", "lab-gns3"}

    assert by_fabric["enterprise"]["state"] == "covered"
    assert by_fabric["enterprise"]["health_by_state"] == {"healthy": 2, "disabled": 1}

    # The lab fabric has one disabled source. It is NOT covered by its
    # neighbour's health, and its device count is None, never 0.
    lab = by_fabric["lab-gns3"]
    assert lab["state"] == "unmeasured"
    assert lab["device_count"] is None
    assert lab["sources_measured"] == 0
    assert result["totals"]["fabric_states"] == {"covered": 1, "unmeasured": 1}


def test_a_fabric_whose_only_source_is_unreachable_is_blind_not_unmeasured(
    netbox_token,
):
    config = {
        "fabrics": [
            {
                "id": "dark",
                "adapters": [
                    {
                        "id": "netbox-dcim",
                        "adapter": "netbox",
                        "enabled": True,
                        "config": {
                            "url": "http://127.0.0.1:1",
                            "token": netbox_token,
                            "timeout": 2,
                        },
                    }
                ],
            }
        ]
    }
    report = collect(config, discover=False)[0]
    assert report.results[0].health.state == "unreachable"
    # Asked and got nothing is a DIFFERENT fact from never asked.
    assert report.state == "blind"
    assert report.device_count is None


def test_an_enabled_but_misdeclared_source_stops_a_covered_claim():
    config = {
        "fabrics": [
            {
                "id": "f",
                "adapters": [
                    {
                        "id": "netbox-dcim",
                        "adapter": "netbox",
                        "enabled": True,
                        "config": {"url": "http://x"},  # no token
                    }
                ],
            }
        ]
    }
    report = collect(config, discover=False)[0]
    assert report.misconfigured == 1
    assert report.state == "unmeasured"


def test_health_mode_discovers_nothing_and_dry_run_writes_nothing(
    tmp_path, netbox_token
):
    conn = _conn(tmp_path, DDL_LIVE, "modes.db")
    with H.mock_csv() as csv_path, H.mock_netbox() as netbox_url:
        config = _two_fabric_config(csv_path, netbox_url)
        health_only = run(config, discover=False, write=False, conn=conn)
        dry = run(config, discover=True, write=False, conn=conn)

    assert health_only["mode"] == "health"
    assert health_only["totals"]["devices_discovered"] is None
    assert all(
        a["device_count"] is None
        for f in health_only["fabrics"]
        for a in f["adapters"]
    )

    assert dry["mode"] == "dry-run"
    assert dry["totals"]["devices_discovered"] == 4
    assert dry["totals"]["devices_written"] is None
    assert conn.execute("SELECT COUNT(*) AS n FROM ni_devices").fetchone()["n"] == 0
    conn.close()


def test_a_missing_config_is_an_error_not_an_empty_clean_sweep(tmp_path):
    from tools.assets.discovery_adapters.runner import load_config, main

    config = load_config(tmp_path / "nope.yaml")
    assert config["_error"] == "not found"
    assert config["fabrics"] == []
    # Exit 2: a sweep that could not be produced is never a sweep that found
    # nothing.
    assert main(["--health", "--json", "--config", str(tmp_path / "nope.yaml")]) == 2


# ── The harness itself ────────────────────────────────────────────────────────


def test_the_harness_exercises_every_registered_adapter():
    result = H.characterize_all()
    assert result["errors"] == []
    assert sorted(result["adapters_exercised"]) == AdapterRegistry.names()
    # Every case produced at least one device from its mock, so a case that
    # silently stopped reaching its target cannot pass.
    assert all(obs["device_count"] >= 1 for obs in result["observations"])


def test_the_harness_restores_the_transport_it_substituted():
    from tools.network import discovery

    before = (discovery._HAS_PYSNMP, discovery._snmp_get, discovery._HAS_NETMIKO)
    with H.mock_snmp_target():
        assert discovery._HAS_PYSNMP is True
    with H.mock_ssh_target():
        assert discovery._HAS_NETMIKO is True
    after = (discovery._HAS_PYSNMP, discovery._snmp_get, discovery._HAS_NETMIKO)
    assert before == after
