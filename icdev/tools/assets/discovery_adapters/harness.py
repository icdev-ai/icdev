# CUI // SP-CTI
"""Characterization harness — exercise every adapter against a MOCK (rmf-disc-01).

    python -m tools.assets.discovery_adapters.harness --json
    python -m tools.assets.discovery_adapters.harness --adapter snmp
    python -m tools.assets.discovery_adapters.harness --list

WHY A HARNESS AND NOT ONLY TESTS

``tools/network/discovery.py`` is 946 lines with zero importers: nothing has
ever run it, so there is no behaviour to regress from — only behaviour to
DISCOVER. A characterization harness is the tool for that shape: it pins what
the code does today so a later change has something to disagree with. It is a
module rather than a pytest file because it must be runnable on a deployment
where the pytest suite is not — an operator commissioning a new fabric runs it
to see what each adapter WOULD do before pointing one at real gear. The tests
in ``tests/assets/`` consume the same fixtures, so the two can never drift.

MOCKS ARE PLACED AT THE TRANSPORT SEAM, NEVER AT THE ADAPTER

  csv     a real temp file — a CSV parser mocked at the parser is not tested
  netbox  a real local HTTP server speaking NetBox's JSON, hit by the real
          ``NetBoxClient`` over the real ``urllib``
  gns3    a real local HTTP server speaking GNS3 v2's JSON
  snmp    ``discovery._snmp_get`` / ``_snmp_walk`` substituted with a mock MIB,
          so ``discover_snmp``'s REAL OID suffix arithmetic, interface assembly
          and CDP/LLDP correlation run
  ssh     ``discovery.ConnectHandler`` substituted with a fake device serving
          canned ``show cdp neighbors detail`` / ``show version`` text, so the
          REAL parsers run

Substituting ``discover_snmp`` itself would prove only that the adapter forwards
its arguments — which was never in doubt. The point is to run the orphaned code.

NO LIVE PROBING. Every target here is either a temp file or a socket bound to
127.0.0.1 on an ephemeral port. Nothing in this module can reach production gear.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

from tools.assets.discovery_adapters.base import AdapterRegistry  # noqa: F401
from tools.assets.discovery_adapters.csv_adapter import CSVDiscoveryAdapter
from tools.assets.discovery_adapters.gns3_adapter import GNS3DiscoveryAdapter
from tools.assets.discovery_adapters.netbox_adapter import NetBoxDiscoveryAdapter
from tools.assets.discovery_adapters.snmp_adapter import SNMPDiscoveryAdapter
from tools.assets.discovery_adapters.ssh_adapter import SSHDiscoveryAdapter

# ── Mock payloads ─────────────────────────────────────────────────────────────

#: A CSV whose headers deliberately do NOT match the canonical field names, so
#: the run proves the declared mapping is what does the work.
MOCK_CSV = (
    "Hostname,Role,Manufacturer,Part Number,OS Version,SN,Facility,Cabinet,Mgmt IP,Owner\n"
    "core-sw-01,switch-l3,Acme Networks,AX-9000,4.2.1,SN00001,Site A,R12,10.10.0.1,netops\n"
    "edge-fw-01,firewall,Bastion Systems,BF-200,9.0.3,SN00002,Site A,R12,10.10.0.2,secops\n"
    ",switch-l2,Acme Networks,AX-1000,4.2.1,SN00003,Site B,R01,10.20.0.9,netops\n"
)

#: NetBox device objects, in the shape the v3/v4 DCIM API returns. The first
#: carries manufacturer/model, which ``NetBoxClient.get_devices`` drops.
MOCK_NETBOX_DEVICES: list[dict[str, Any]] = [
    {
        "id": 101,
        "url": "http://netbox.mock/api/dcim/devices/101/",
        "name": "core-sw-01",
        "role": {"slug": "core-switch", "name": "Core Switch"},
        "device_type": {
            "model": "AX-9000",
            "slug": "ax-9000",
            "manufacturer": {"name": "Acme Networks", "slug": "acme"},
        },
        "serial": "SN00001",
        "site": {"name": "Site A"},
        "rack": {"name": "R12"},
        "platform": {"name": "AcmeOS"},
        "status": {"value": "active"},
        "primary_ip": {"address": "10.10.0.1/24"},
        "custom_fields": {"firmware_version": "4.2.1"},
        "tags": [{"name": "production"}],
    },
    {
        "id": 102,
        "url": "http://netbox.mock/api/dcim/devices/102/",
        "name": "edge-fw-01",
        # v3 spelling of the role key — both must map.
        "device_role": {"slug": "firewall", "name": "Firewall"},
        "device_type": {
            "model": "BF-200",
            "slug": "bf-200",
            "manufacturer": {"name": "Bastion Systems"},
        },
        "serial": "SN00002",
        "site": {"name": "Site A"},
        "rack": None,
        "platform": None,
        "status": {"value": "active"},
        "primary_ip": None,
        "custom_fields": {},
        "tags": [],
    },
]

MOCK_GNS3_PROJECTS: list[dict[str, Any]] = [
    {"project_id": "proj-aaa", "name": "enclave-lab", "status": "opened"}
]

MOCK_GNS3_NODES: list[dict[str, Any]] = [
    {
        "node_id": "node-1",
        "name": "R1",
        "node_type": "dynamips",
        "status": "started",
        "compute_id": "local",
        "properties": {"platform": "c7200"},
    },
    {
        "node_id": "node-2",
        "name": "SW1",
        "node_type": "ethernet_switch",
        "status": "stopped",
        "compute_id": "local",
        "properties": {},
    },
]

#: OID -> value, as ``_snmp_get`` returns them.
MOCK_SNMP_SCALARS: dict[str, str] = {
    "1.3.6.1.2.1.1.1.0": "Acme Networks AXOS Software, Version 4.2.1",
    "1.3.6.1.2.1.1.5.0": "core-sw-01",
    "1.3.6.1.2.1.1.2.0": "1.3.6.1.4.1.99999.1.1",
}

#: OID prefix -> [(full oid, value)], as ``_snmp_walk`` returns them.
MOCK_SNMP_WALKS: dict[str, list[tuple[str, str]]] = {
    "1.3.6.1.2.1.2.2.1.2": [
        ("1.3.6.1.2.1.2.2.1.2.1", "GigabitEthernet0/0"),
        ("1.3.6.1.2.1.2.2.1.2.2", "GigabitEthernet0/1"),
    ],
    "1.3.6.1.2.1.2.2.1.5": [
        ("1.3.6.1.2.1.2.2.1.5.1", "1000000000"),
        ("1.3.6.1.2.1.2.2.1.5.2", "1000000000"),
    ],
    "1.3.6.1.2.1.2.2.1.8": [
        ("1.3.6.1.2.1.2.2.1.8.1", "1"),
        ("1.3.6.1.2.1.2.2.1.8.2", "2"),
    ],
    "1.0.8802.1.1.2.1.4.1.1.9": [
        ("1.0.8802.1.1.2.1.4.1.1.9.0.1.1", "edge-fw-01"),
    ],
    # OID_LLDP_REM_PORT_DESC. It is .1.1.8, NOT .1.1.7 — the first draft of
    # this mock used .7 and the LLDP remote port came back empty, which read as
    # a defect in discover_snmp and was a defect in the mock.
    "1.0.8802.1.1.2.1.4.1.1.8": [
        ("1.0.8802.1.1.2.1.4.1.1.8.0.1.1", "GigabitEthernet0/1"),
    ],
    "1.3.6.1.4.1.9.9.23.1.2.1.1.6": [
        ("1.3.6.1.4.1.9.9.23.1.2.1.1.6.1.1", "access-sw-04"),
    ],
    "1.3.6.1.4.1.9.9.23.1.2.1.1.8": [
        ("1.3.6.1.4.1.9.9.23.1.2.1.1.8.1.1", "AX-1000"),
    ],
}

MOCK_SSH_OUTPUT: dict[str, str] = {
    "show cdp neighbors detail": (
        "-------------------------\n"
        "Device ID: access-sw-04\n"
        "  IP address: 10.10.0.4\n"
        "Platform: Acme AX-1000,  Capabilities: Switch\n"
        "Interface: GigabitEthernet0/2,  Port ID (outgoing port): GigabitEthernet0/24\n"
        "-------------------------\n"
        "Device ID: edge-fw-01\n"
        "  IP address: 10.10.0.2\n"
        "Platform: Bastion BF-200,  Capabilities: Router\n"
        "Interface: GigabitEthernet0/1,  Port ID (outgoing port): ge-0/0/1\n"
    ),
    "show lldp neighbors detail": "",
    "show version": "Acme Networks AXOS Software, Version 4.2.1\nuptime is 41 weeks\n",
    "show ip interface brief": (
        "Interface              IP-Address      OK? Method Status    Protocol\n"
        "GigabitEthernet0/0     10.10.0.1       YES NVRAM  up        up\n"
        "GigabitEthernet0/1     unassigned      YES NVRAM  up        up\n"
    ),
}


# ── Mock targets ──────────────────────────────────────────────────────────────


def _json_server(routes: dict[str, Any]) -> tuple[HTTPServer, str]:
    """Serve ``routes`` (path -> payload) as JSON on 127.0.0.1:<ephemeral>.

    Bound to the loopback interface on an OS-assigned port: this harness cannot
    be pointed at anything, by construction.
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            path = self.path.split("?", 1)[0]
            if path not in routes:
                self.send_error(404, "no mock route for %s" % path)
                return
            body = json.dumps(routes[path]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            return  # keep the harness output clean

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[0], server.server_address[1]
    return server, "http://%s:%d" % (host, port)


@contextlib.contextmanager
def mock_netbox() -> Iterator[str]:
    """A local HTTP server answering the NetBox endpoints the client calls."""
    server, url = _json_server(
        {
            "/api/": {"netbox-version": "4.1.0-mock"},
            "/api/dcim/devices/": {
                "count": len(MOCK_NETBOX_DEVICES),
                "next": None,
                "results": MOCK_NETBOX_DEVICES,
            },
        }
    )
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def mock_gns3() -> Iterator[str]:
    """A local HTTP server answering the GNS3 v2 endpoints the adapter calls."""
    server, url = _json_server(
        {
            "/v2/version": {"version": "2.2.49-mock", "local": True},
            "/v2/projects": MOCK_GNS3_PROJECTS,
            "/v2/projects/proj-aaa/nodes": MOCK_GNS3_NODES,
        }
    )
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def mock_csv(content: str = MOCK_CSV) -> Iterator[Path]:
    """A real CSV file in a temp directory."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "inventory.csv"
        path.write_text(content, encoding="utf-8")
        yield path


@contextlib.contextmanager
def mock_snmp_target() -> Iterator[None]:
    """Substitute the SNMP TRANSPORT so the real ``discover_snmp`` body runs.

    ``_HAS_PYSNMP`` is forced True because the guard it protects is the module's
    check that a transport exists — and here one does, it is just a mock. The
    original values are restored unconditionally.
    """
    from tools.network import discovery

    def _get(target: str, oid: str, *_a: Any, **_kw: Any) -> str | None:
        return MOCK_SNMP_SCALARS.get(str(oid))

    def _walk(target: str, oid: str, *_a: Any, **_kw: Any) -> list[tuple[str, str]]:
        return list(MOCK_SNMP_WALKS.get(str(oid), []))

    saved = (discovery._HAS_PYSNMP, discovery._snmp_get, discovery._snmp_walk)
    discovery._HAS_PYSNMP = True
    discovery._snmp_get = _get
    discovery._snmp_walk = _walk
    try:
        yield
    finally:
        (
            discovery._HAS_PYSNMP,
            discovery._snmp_get,
            discovery._snmp_walk,
        ) = saved


class MockSSHDevice:
    """Stands in for a netmiko ``ConnectHandler`` connection."""

    def __init__(self, **params: Any) -> None:
        self.params = params
        self.commands: list[str] = []
        self.disconnected = False

    def enable(self) -> None:
        return None

    def find_prompt(self) -> str:
        return "core-sw-01#"

    def send_command(self, command: str, **_kw: Any) -> str:
        self.commands.append(command)
        if command not in MOCK_SSH_OUTPUT:
            raise RuntimeError("mock device has no output for %r" % command)
        return MOCK_SSH_OUTPUT[command]

    def disconnect(self) -> None:
        self.disconnected = True


@contextlib.contextmanager
def mock_ssh_target(
    factory: Callable[..., Any] | None = None,
) -> Iterator[list[MockSSHDevice]]:
    """Substitute netmiko's ``ConnectHandler`` so the real SSH parsers run.

    Yields the list of devices that were "connected to", so a caller can assert
    on what was sent as well as on what came back.
    """
    from tools.network import discovery

    connected: list[MockSSHDevice] = []

    def _connect(**params: Any) -> Any:
        device = (factory or MockSSHDevice)(**params)
        connected.append(device)
        return device

    saved_flag = discovery._HAS_NETMIKO
    saved_handler = getattr(discovery, "ConnectHandler", None)
    discovery._HAS_NETMIKO = True
    discovery.ConnectHandler = _connect
    try:
        yield connected
    finally:
        discovery._HAS_NETMIKO = saved_flag
        if saved_handler is None:
            with contextlib.suppress(AttributeError):
                del discovery.ConnectHandler
        else:
            discovery.ConnectHandler = saved_handler


# ── Characterization cases ────────────────────────────────────────────────────


@dataclass
class Observation:
    """What one adapter did against its mock. Facts only — no pass/fail.

    A characterization run has no expected value to compare against; that is
    what makes it a characterization run. The assertions live in
    ``tests/assets/test_discovery_adapters.py`` and read these same fixtures.
    """

    adapter: str
    health_state: str
    health_detail: str
    device_count: int
    devices: list[dict[str, Any]]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "health_state": self.health_state,
            "health_detail": self.health_detail,
            "device_count": self.device_count,
            "devices": self.devices,
            "notes": self.notes,
        }


def characterize_csv() -> Observation:
    with mock_csv() as path:
        adapter = CSVDiscoveryAdapter(
            fabric="mock",
            config={
                "path": str(path),
                "columns": {
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
                },
            },
        )
        health = adapter.health()
        devices = adapter.discover()
    return Observation(
        adapter="csv",
        health_state=health.state,
        health_detail=health.detail,
        device_count=len(devices),
        devices=[d.to_dict() for d in devices],
        notes=[
            "3 data rows, one with an empty Hostname — a row with no identity "
            "cannot become an asset and is skipped rather than given an "
            "invented id.",
            "`Owner` is in no declared mapping and lands in properties.csv_extra.",
        ],
    )


def characterize_netbox() -> Observation:
    with mock_netbox() as url:
        adapter = NetBoxDiscoveryAdapter(
            fabric="mock", config={"url": url, "token": "mock-token"}
        )
        health = adapter.health()
        devices = adapter.discover()
    return Observation(
        adapter="netbox",
        health_state=health.state,
        health_detail=health.detail,
        device_count=len(devices),
        devices=[d.to_dict() for d in devices],
        notes=[
            "Real NetBoxClient over real urllib against a loopback HTTP server.",
            "device 101 uses the v4 `role` key, 102 the v3 `device_role` key.",
            "vendor/model come from device_type.manufacturer/model, which "
            "NetBoxClient.get_devices() drops — hence get_devices_raw.",
        ],
    )


def characterize_gns3() -> Observation:
    with mock_gns3() as url:
        adapter = GNS3DiscoveryAdapter(fabric="mock-lab", config={"url": url})
        health = adapter.health()
        devices = adapter.discover()
    return Observation(
        adapter="gns3",
        health_state=health.state,
        health_detail=health.detail,
        device_count=len(devices),
        devices=[d.to_dict() for d in devices],
        notes=[
            "Every device carries properties.evidence_kind == 'lab'.",
            "project_ids empty => every project on the server is enumerated.",
        ],
    )


def characterize_snmp() -> Observation:
    adapter = SNMPDiscoveryAdapter(
        fabric="mock", config={"targets": ["10.10.0.1"], "community": "mock"}
    )
    absent = adapter.health()
    with mock_snmp_target():
        health = adapter.health()
        devices = adapter.discover()
    return Observation(
        adapter="snmp",
        health_state=health.state,
        health_detail=health.detail,
        device_count=len(devices),
        devices=[d.to_dict() for d in devices],
        notes=[
            "WITHOUT the mock transport this host reports %r (%s) — an absent "
            "dependency, not an unreachable device."
            % (absent.state, absent.dependency or "n/a"),
            "With the mock MIB the REAL discover_snmp runs: interface assembly "
            "from three parallel walks, and LLDP+CDP neighbour correlation by "
            "OID suffix.",
            "FINDING, found by this harness: sysDescr 'Acme Networks AXOS "
            "Software' is not in _infer_vendor's 16-entry table, so it returns "
            "the SENTINEL 'Unknown' and _infer_device_type falls back to the "
            "REAL type 'server'. Written straight through, that manufactures a "
            "vendor called Unknown with a market share in the de-facto learner, "
            "and files an unrecognised router as a server. normalize_inference "
            "reports neither and keeps the raw pair in properties.inference.",
        ],
    )


def characterize_ssh() -> Observation:
    adapter = SSHDiscoveryAdapter(
        fabric="mock",
        config={"targets": ["10.10.0.1"], "username": "svc-discovery"},
    )
    absent = adapter.health()
    with mock_ssh_target() as connected:
        health = adapter.health()
        devices = adapter.discover()
    return Observation(
        adapter="ssh",
        health_state=health.state,
        health_detail=health.detail,
        device_count=len(devices),
        devices=[d.to_dict() for d in devices],
        notes=[
            "WITHOUT netmiko this host reports %r (%s)."
            % (absent.state, absent.dependency or "n/a"),
            "health() is `degraded`, never `healthy`: it does NOT log in, so "
            "nothing has answered.",
            "commands actually sent: %s" % ", ".join(connected[0].commands)
            if connected
            else "no connection was made",
            "The REAL _parse_cdp_detail runs over canned CLI text.",
            "Same sysDescr sentinel as snmp: unrecognised => device_type and "
            "vendor are both empty, with the raw pair in properties.inference.",
        ],
    )


CASES: dict[str, Callable[[], Observation]] = {
    "csv": characterize_csv,
    "netbox": characterize_netbox,
    "gns3": characterize_gns3,
    "snmp": characterize_snmp,
    "ssh": characterize_ssh,
}


def characterize_all(only: str = "") -> dict[str, Any]:
    names = [only] if only else list(CASES)
    observations: list[dict[str, Any]] = []
    errors: list[str] = []
    for name in names:
        case = CASES.get(name)
        if case is None:
            errors.append("no characterization case for adapter %r" % name)
            continue
        try:
            observations.append(case().to_dict())
        except Exception as exc:  # noqa: BLE001 — a failed case is a finding
            errors.append("%s raised %s: %s" % (name, type(exc).__name__, exc))
    return {
        "adapters_exercised": [o["adapter"] for o in observations],
        "observations": observations,
        "errors": errors,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def _render(result: dict[str, Any]) -> str:
    lines = ["Discovery adapter characterization — all targets are mocks", ""]
    for obs in result["observations"]:
        lines.append(
            "%-8s health=%-12s devices=%d"
            % (obs["adapter"], obs["health_state"], obs["device_count"])
        )
        if obs["health_detail"]:
            lines.append("    %s" % obs["health_detail"])
        for device in obs["devices"]:
            lines.append(
                "    - %-14s type=%-16s vendor=%-16s model=%s"
                % (
                    device["label"],
                    device["device_type"] or "-",
                    device["vendor"] or "-",
                    device["model"] or "-",
                )
            )
        for note in obs["notes"]:
            lines.append("    note: %s" % note)
        lines.append("")
    for err in result["errors"]:
        lines.append("ERROR %s" % err)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exercise every discovery adapter against a mock target"
    )
    parser.add_argument("--adapter", default="", help="run one case (%s)" % ", ".join(CASES))
    parser.add_argument("--list", action="store_true", help="list cases and exit")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps(sorted(CASES)) if args.json else "\n".join(sorted(CASES)))
        return 0

    result = characterize_all(args.adapter)
    print(json.dumps(result, indent=2, default=str) if args.json else _render(result))
    # Exit 1 on a case that could not run: a harness that crashed is never the
    # same as a harness that observed nothing interesting.
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
