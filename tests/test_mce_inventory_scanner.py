# CUI // SP-CTI
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.migration_canvas.server_migration — parse_server_inventory."""

from tools.migration_canvas.server_migration import parse_server_inventory, _infer_os_family


# ── helpers ──────────────────────────────────────────────────────────────────

def _inv(fmt, raw):
    return parse_server_inventory(raw, fmt)


# ── test cases ───────────────────────────────────────────────────────────────

def test_parse_csv_basic():
    raw = "vcpus,ram_gb,disk_gb,disk_type,nic_count,nic_speed_gbps,os\n4,16,200,SSD,2,10,Windows Server 2019"
    result = _inv("csv", raw)
    assert result["ok"] is True
    assert result["errors"] == []
    inv = result["inventory"]
    assert inv["vcpus"] == 4
    assert inv["ram_gb"] == 16.0
    assert inv["total_disk_gb"] == 200.0
    assert inv["os_family"] == "windows"


def test_parse_json_valid():
    import json
    data = {
        "vcpus": 8, "ram_gb": 32, "total_disk_gb": 500,
        "disk_type": "NVMe", "nic_count": 2, "primary_nic_gbps": 25,
        "os_name": "Ubuntu 22.04", "os_arch": "x86_64",
        "nics": [{"name": "eth0"}], "disks": [{"path": "/dev/sda"}],
        "services": ["nginx", "postgres"],
    }
    result = _inv("json", json.dumps(data))
    assert result["ok"] is True
    inv = result["inventory"]
    assert inv["vcpus"] == 8
    assert inv["ram_gb"] == 32
    assert inv["os_family"] == "linux"
    assert result["nics"] == [{"name": "eth0"}]
    assert result["services"] == ["nginx", "postgres"]


def test_parse_vmware_ovf_minimal():
    # Item elements must be in the RASD namespace — that's what _parse_vmware_ovf iterates.
    ovf = (
        '<?xml version="1.0"?>'
        '<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"'
        ' xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData">'
        '<VirtualSystem>'
        '<OperatingSystemSection>'
        '<Description>Red Hat Enterprise Linux 8</Description>'
        '</OperatingSystemSection>'
        '<VirtualHardwareSection>'
        '<rasd:Item>'
        '<rasd:ResourceType>3</rasd:ResourceType>'
        '<rasd:VirtualQuantity>4</rasd:VirtualQuantity>'
        '</rasd:Item>'
        '<rasd:Item>'
        '<rasd:ResourceType>4</rasd:ResourceType>'
        '<rasd:VirtualQuantity>8192</rasd:VirtualQuantity>'
        '</rasd:Item>'
        '</VirtualHardwareSection>'
        '</VirtualSystem>'
        '</Envelope>'
    )
    result = _inv("vmware_export", ovf)
    assert result["ok"] is True
    inv = result["inventory"]
    assert inv["vcpus"] == 4
    assert inv["ram_gb"] == 8.0
    assert inv["os_family"] == "linux"


def test_parse_json_invalid_returns_error():
    result = _inv("json", "not valid json {{")
    assert result["ok"] is False
    assert len(result["errors"]) > 0
    assert any("json" in e.lower() or "invalid" in e.lower() for e in result["errors"])


def test_parse_csv_empty_returns_error():
    result = _inv("csv", "vcpus,ram_gb\n")
    assert result["ok"] is False
    assert len(result["errors"]) > 0


def test_infer_os_family_all_families():
    cases = [
        ("Windows Server 2022", "windows"),
        ("RHEL 8", "linux"),
        ("Ubuntu 20.04 LTS", "linux"),
        ("Solaris 11", "unix"),
        ("AIX 7.2", "unix"),
        ("Custom Appliance OS", "other"),
    ]
    for os_name, expected in cases:
        got = _infer_os_family(os_name)
        assert got == expected, f"_infer_os_family({os_name!r}) = {got!r}, want {expected!r}"
