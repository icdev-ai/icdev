# CUI // SP-CTI
"""Tests for tools.migration_canvas.inventory_scanner."""
import json


from tools.migration_canvas.inventory_scanner import (
    _parse_csv_row,
    parse_csv,
    parse_json,
    write_inventory,
    _norm,
)


def test_norm_lowercases_keys():
    result = _norm({"Hostname": "srv01", "IP Address": "10.0.0.1"})
    assert "hostname" in result
    assert "ip_address" in result


def test_parse_csv_row_basic():
    row = {"hostname": "srv01", "ip_address": "10.0.0.1", "os": "RHEL 8", "cpu_cores": "4", "ram_gb": "16"}
    r = _parse_csv_row(row)
    assert r is not None
    assert r["hostname"] == "srv01"
    assert r["ip_address"] == "10.0.0.1"
    assert r["cpu_cores"] == 4
    assert r["ram_gb"] == 16


def test_parse_csv_row_missing_hostname():
    r = _parse_csv_row({"ip": "10.0.0.1"})
    assert r is None


def test_parse_csv_file(tmp_path):
    csv_file = tmp_path / "servers.csv"
    csv_file.write_text("hostname,ip_address,os,cpu_cores,ram_gb,disk_gb\nsrv01,10.0.0.1,RHEL,4,16,100\nsrv02,10.0.0.2,Windows,8,32,200\n", encoding="utf-8")
    rows = parse_csv(csv_file)
    assert len(rows) == 2
    assert rows[0]["hostname"] == "srv01"
    assert rows[1]["cpu_cores"] == 8


def test_parse_json_file(tmp_path):
    json_file = tmp_path / "servers.json"
    data = [{"hostname": "srv01", "ip": "10.0.0.1", "os": "Linux", "cpu_cores": 4, "ram_gb": 8, "disk_gb": 50}]
    json_file.write_text(json.dumps(data), encoding="utf-8")
    rows = parse_json(json_file)
    assert len(rows) == 1
    assert rows[0]["hostname"] == "srv01"


def test_write_inventory_dry_run():
    servers = [{"id": "1", "hostname": "srv01", "ip_address": "10.0.0.1",
                "os": "RHEL", "cpu_cores": 4, "ram_gb": 8, "disk_gb": 100,
                "environment": "prod", "status": "active", "tags": "[]", "created_at": "2026-01-01"}]
    result = write_inventory(servers, session_id="test-sid", dry_run=True)
    assert result["dry_run"] is True
    assert result["inserted"] == 1
    assert result["skipped"] == 0


def test_write_inventory_empty_dry_run():
    result = write_inventory([], session_id="test-sid", dry_run=True)
    assert result["inserted"] == 0
