# CUI // SP-CTI
"""Unit tests for tools/network/enclave_scanner.py (ndc-qa-01).

The scanner is subprocess-heavy. These tests NEVER run a real scan: every
subprocess.run call is monkeypatched to return canned CompletedProcess objects
(argv-list form, as the module invokes it), and platform.system / socket are
pinned for determinism. Coverage:

  - IP helpers (_is_rfc1918 / _is_loopback)
  - classify_enclave aggregation across IL2 / IL4 / IL6 shapes
  - evaluate_gate pass / low-confidence-fail / missing-IL paths
  - OS parsers: _interfaces_windows, _interfaces_unix, read_arp_table,
    read_routing_table (Windows + Linux) driven by canned command output
  - EnclaveScanner.passive_scan integration (all subprocess mocked)
  - EnclaveScanner.save_results DB persistence (temp SQLite)
  - EnclaveScanner.scan active-without-targets fallback (arg construction)
"""

import subprocess
import sqlite3

import pytest

from tools.db.storage import StorageConnection
from tools.network import enclave_scanner as es


# ── Canned command output ──────────────────────────────────────────────────────

_IPCONFIG_ALL = """
Windows IP Configuration

Ethernet adapter Ethernet0:

   Connection-specific DNS Suffix  . : corp.local
   IPv4 Address. . . . . . . . . . . : 10.1.2.3(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 10.1.2.1
"""

_ARP_A_WINDOWS = """
Interface: 10.1.2.3 --- 0x2
  Internet Address      Physical Address      Type
  10.1.2.1              00-11-22-33-44-55     dynamic
  10.1.2.255            ff-ff-ff-ff-ff-ff     static
"""

_ROUTE_PRINT = """
===========================================================================
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      10.1.2.1       10.1.2.3     25
       10.1.2.0    255.255.255.0         On-link       10.1.2.3    281
===========================================================================
"""

_IP_ADDR_SHOW = """1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN
    inet 127.0.0.1/8 scope host lo
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP
    inet 10.5.6.7/24 brd 10.5.6.255 scope global eth0
"""

_IP_NEIGH_SHOW = """10.5.6.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
10.5.6.99 dev eth0  FAILED
"""

_IP_ROUTE_SHOW = """default via 10.5.6.1 dev eth0 proto dhcp metric 100
10.5.6.0/24 dev eth0 proto kernel scope link src 10.5.6.7 metric 100
"""


def _make_fake_run(responses):
    """Return a subprocess.run replacement dispatching on the argv prefix.

    responses: {command-prefix: (returncode, stdout)}. Unknown commands get a
    non-zero CompletedProcess so fallback branches behave as 'command absent'.
    """
    def fake_run(argv, *args, **kwargs):
        assert isinstance(argv, (list, tuple)), "module must call subprocess.run with argv list"
        key = " ".join(argv)
        for prefix, (rc, out) in responses.items():
            if key.startswith(prefix):
                return subprocess.CompletedProcess(argv, rc, out, "")
        return subprocess.CompletedProcess(argv, 1, "", "not-found")
    return fake_run


@pytest.fixture(autouse=True)
def _pin_socket(monkeypatch):
    """Deterministic hostname/FQDN so gov-DNS detection never depends on host."""
    monkeypatch.setattr(es.socket, "getfqdn", lambda *a: "host.corp.local")
    monkeypatch.setattr(es.socket, "gethostname", lambda: "host")


# ── IP helpers ─────────────────────────────────────────────────────────────────


def test_is_rfc1918():
    assert es._is_rfc1918("10.0.0.1") is True
    assert es._is_rfc1918("192.168.1.1") is True
    assert es._is_rfc1918("172.16.5.5") is True
    assert es._is_rfc1918("8.8.8.8") is False
    assert es._is_rfc1918("not-an-ip") is False


def test_is_loopback():
    assert es._is_loopback("127.0.0.1") is True
    assert es._is_loopback("10.0.0.1") is False
    assert es._is_loopback("garbage") is False


# ── classify_enclave ───────────────────────────────────────────────────────────


def test_classify_enclave_public_is_il2():
    interfaces = [{"ip": "8.8.8.8", "is_private": False, "is_loopback": False}]
    routes = [{"is_default": True, "gateway": "8.8.8.1", "gateway_is_private": False}]
    result = es.classify_enclave(interfaces, routes, [], hostname="web01")
    assert result["il_level"] == 2
    assert result["public_interfaces"] == 1


def test_classify_enclave_isolated_sipr_is_il6():
    interfaces = [{"ip": "10.9.9.9", "netmask": "255.255.255.0",
                   "is_private": True, "is_loopback": False}]
    routes = []  # no default route -> fully isolated
    result = es.classify_enclave(interfaces, routes, [], hostname="sipr-host-01")
    assert result["il_level"] == 6
    assert result["confidence"] >= 0.5
    assert any("IL6 indicator" in i for i in result["indicators"])
    assert "10.9.9.0/24" in result["boundary_segments"]


def test_classify_enclave_private_gateway_is_il4():
    interfaces = [{"ip": "10.1.2.3", "netmask": "255.255.255.0",
                   "is_private": True, "is_loopback": False}]
    routes = [{"is_default": True, "gateway": "10.1.2.1", "gateway_is_private": True}]
    result = es.classify_enclave(interfaces, routes, [], hostname="app01")
    assert result["il_level"] == 4
    assert result["private_interfaces"] == 1


# ── evaluate_gate ──────────────────────────────────────────────────────────────


def test_evaluate_gate_pass():
    results = {"enclave_classification": {
        "il_level": 4, "confidence": 0.9, "public_interfaces": 0}}
    gate = es.evaluate_gate(results)
    assert gate["pass"] is True
    assert gate["findings"] == []


def test_evaluate_gate_low_confidence_fails():
    results = {"enclave_classification": {
        "il_level": 4, "confidence": 0.3, "public_interfaces": 0}}
    gate = es.evaluate_gate(results)
    assert gate["pass"] is False
    assert any("confidence" in f for f in gate["findings"])


def test_evaluate_gate_missing_il_level_fails():
    results = {"enclave_classification": {"confidence": 0.9}}
    gate = es.evaluate_gate(results)
    assert gate["pass"] is False
    assert any("IL level could not be determined" in f for f in gate["findings"])


def test_evaluate_gate_boundary_violation():
    results = {"enclave_classification": {
        "il_level": 5, "confidence": 0.8, "public_interfaces": 2}}
    gate = es.evaluate_gate(results)
    assert gate["pass"] is False
    assert any("boundary violation" in f for f in gate["findings"])


# ── OS parsers: Windows ────────────────────────────────────────────────────────


def test_interfaces_windows_parses_ipconfig(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Windows")
    monkeypatch.setattr(es.subprocess, "run",
                        _make_fake_run({"ipconfig": (0, _IPCONFIG_ALL)}))
    ifaces = es.detect_local_interfaces()
    assert any(i["ip"] == "10.1.2.3" for i in ifaces)
    eth = next(i for i in ifaces if i["ip"] == "10.1.2.3")
    assert eth["is_private"] is True
    assert eth["netmask"] == "255.255.255.0"
    assert eth["source"] == "ipconfig"


def test_read_arp_table_windows(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Windows")
    monkeypatch.setattr(es.subprocess, "run",
                        _make_fake_run({"arp": (0, _ARP_A_WINDOWS)}))
    entries = es.read_arp_table()
    ips = {e["ip"] for e in entries}
    assert "10.1.2.1" in ips
    # Broadcast MAC entry must be filtered out.
    assert "10.1.2.255" not in ips
    gw = next(e for e in entries if e["ip"] == "10.1.2.1")
    assert gw["mac"] == "00:11:22:33:44:55"  # normalized to colons


def test_read_routing_table_windows_default_route(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Windows")
    monkeypatch.setattr(es.subprocess, "run",
                        _make_fake_run({"route": (0, _ROUTE_PRINT)}))
    routes = es.read_routing_table()
    defaults = [r for r in routes if r.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["gateway"] == "10.1.2.1"
    assert defaults[0]["gateway_is_private"] is True


# ── OS parsers: Linux ──────────────────────────────────────────────────────────


def test_interfaces_unix_parses_ip_addr(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Linux")
    monkeypatch.setattr(es.subprocess, "run",
                        _make_fake_run({"ip addr": (0, _IP_ADDR_SHOW)}))
    ifaces = es.detect_local_interfaces()
    eth = next(i for i in ifaces if i["name"] == "eth0")
    assert eth["ip"] == "10.5.6.7"
    assert eth["prefix"] == 24
    assert eth["is_private"] is True
    lo = next(i for i in ifaces if i["ip"] == "127.0.0.1")
    assert lo["is_loopback"] is True


def test_read_arp_table_linux_ip_neigh(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Linux")
    monkeypatch.setattr(es.subprocess, "run",
                        _make_fake_run({"ip neigh": (0, _IP_NEIGH_SHOW)}))
    entries = es.read_arp_table()
    # Only the REACHABLE lladdr entry is parseable; FAILED line is skipped.
    assert len(entries) == 1
    assert entries[0]["ip"] == "10.5.6.1"
    assert entries[0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert entries[0]["interface"] == "eth0"


def test_read_routing_table_linux(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Linux")
    monkeypatch.setattr(es.subprocess, "run",
                        _make_fake_run({"ip route": (0, _IP_ROUTE_SHOW)}))
    routes = es.read_routing_table()
    defaults = [r for r in routes if r["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["gateway"] == "10.5.6.1"
    assert defaults[0]["destination"] == "0.0.0.0/0"


# ── EnclaveScanner integration (subprocess fully mocked) ───────────────────────


def test_passive_scan_integration(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Linux")
    monkeypatch.setattr(es.subprocess, "run", _make_fake_run({
        "ip addr": (0, _IP_ADDR_SHOW),
        "ip neigh": (0, _IP_NEIGH_SHOW),
        "ip route": (0, _IP_ROUTE_SHOW),
    }))
    scanner = es.EnclaveScanner()
    result = scanner.scan(mode="passive", save=False)

    assert result["scan_mode"] == "passive"
    assert result["scan_id"]
    assert result["stats"]["interfaces"] >= 1
    assert result["stats"]["arp_entries"] == 1
    # eth0 (10.5.6.7) and arp host (10.5.6.1) both surface as discovered hosts;
    # loopback is excluded.
    host_ips = {h["ip"] for h in result["discovered_hosts"]}
    assert "10.5.6.7" in host_ips
    assert "10.5.6.1" in host_ips
    assert "127.0.0.1" not in host_ips
    assert "enclave_classification" in result


def test_save_results_persists_to_db(monkeypatch):
    from tools.network.db import init_db

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row

    class _NoClose(StorageConnection):
        def close(self):  # keep the in-memory DB alive across calls
            pass

    shared = _NoClose(raw, "sqlite")
    monkeypatch.setattr(init_db, "get_connection", lambda *a, **k: shared)

    scanner = es.EnclaveScanner()
    results = {
        "scan_id": scanner.scan_id,
        "scan_mode": "passive",
        "hostname": "host.corp.local",
        "scanned_at": scanner.scanned_at,
        "enclave_classification": {
            "il_level": 4, "classification": "IL4 — CUI", "confidence": 0.7,
            "boundary_segments": ["10.1.2.0/24"], "indicators": ["private"],
        },
        "stats": {"discovered_hosts": 3, "interfaces": 1},
    }
    returned = scanner.save_results(results)
    assert returned == scanner.scan_id

    row = raw.execute(
        "SELECT il_level, host_count, classification FROM enclave_scans WHERE id=?",
        (scanner.scan_id,),
    ).fetchone()
    assert row is not None
    assert row["il_level"] == 4
    assert row["host_count"] == 3


def test_scan_active_without_targets_falls_back_to_passive(monkeypatch):
    monkeypatch.setattr(es.platform, "system", lambda: "Linux")
    monkeypatch.setattr(es.subprocess, "run", _make_fake_run({
        "ip addr": (0, _IP_ADDR_SHOW),
        "ip neigh": (0, _IP_NEIGH_SHOW),
        "ip route": (0, _IP_ROUTE_SHOW),
    }))
    scanner = es.EnclaveScanner()
    # Active mode requested but no targets -> module logs a warning and runs
    # passive; scan_mode stays "passive" (no real active scan attempted).
    result = scanner.scan(mode="active", targets=None, save=False)
    assert result["scan_mode"] == "passive"
