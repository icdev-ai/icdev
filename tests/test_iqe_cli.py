"""CLI tests for tools/iqe/run.py — 4 cases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.iqe.executor import register_collection
from tools.iqe.run import main

_DEVICES = [
    {"hostname": "rtr-01", "vendor": "Cisco", "model": "ASR-9k", "os_version": "17.3.2"},
    {"hostname": "sw-01", "vendor": "Juniper", "model": "EX4300", "os_version": "20.4R1"},
]


def _stub(_conn: object) -> list[dict]:
    return list(_DEVICES)


@pytest.fixture(autouse=True)
def _register() -> None:
    register_collection("network.devices", _stub)


# 1 — --json emits a valid JSON list ----------------------------------------

def test_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--query-string", "foreach d in network.devices select d.hostname, d.vendor", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert data[0]["hostname"] == "rtr-01"


# 2 — default (--human) emits a formatted table -----------------------------

def test_human_table_output(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--query-string", "foreach d in network.devices select d.hostname, d.vendor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hostname" in out
    assert "rtr-01" in out
    assert "Cisco" in out


# 3 — parse error exits 1 ---------------------------------------------------

def test_parse_error_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--query-string", "this is not valid iqe syntax"])
    assert rc == 1
    assert "parse error" in capsys.readouterr().err


# 4 — --query file runs vendor_inventory.iqe and exits 0 --------------------

def test_query_file_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    path = Path("context/iqe/queries/network/vendor_inventory.iqe")
    rc = main(["--query", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hostname" in out
