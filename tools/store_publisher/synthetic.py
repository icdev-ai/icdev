# CUI // SP-CTI
"""The labelled SYNTHETIC inputs of the store publisher's demonstration packages (icx-ent-04).

Written under ``package_root`` for the tests, the e2e seed and ``cli.py seed-synthetic``: an agent's prompt and an MCP
server's tool listing and source, each package's CycloneDX SBOM, the factory scanner's dependency findings over it, and
the publisher's red-team results. Nothing here is a real product, publisher or finding; every file says SYNTHETIC.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tools.store_publisher import config as cfgmod
from tools.store_publisher.constants import REDTEAM_REQUIRED

SERVER = '''"""SYNTHETIC MCP server (icx-ent-04): one read-only tool over a labelled fixture."""
import json

FIXTURE = {"syn-ledger": {"coverage": 0.82, "label": "SYNTHETIC"}}


def coverage_lookup(system: str) -> str:
    return json.dumps(FIXTURE.get(system, {"label": "SYNTHETIC", "coverage": None}))
'''


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sbom(name: str, comps: list[tuple[str, str]]) -> dict:
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "metadata": {"component": {"type": "application", "name": name, "version": "SYNTHETIC"}},
            "components": [{"type": "library", "name": n, "version": v, "purl": f"pkg:pypi/{n}@{v}",
                            "bom-ref": f"{n}@{v}"} for n, v in comps]}


def _put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"{path}: a symlink is never written through")
    path.write_text(text, encoding="utf-8")


def write_package(base: Path, p: dict, *, now: dt.datetime) -> Path:
    d = base / p["inputs"]
    comps = {"agent": [("pyyaml", "6.0.2")], "mcp_server": [("pydantic", "2.9.2"), ("pyyaml", "6.0.2")]}.get(
        p["kind"], [("pyyaml", "6.0.2")])
    _put(d / "sbom.cdx.json", json.dumps(_sbom(p["id"], comps), indent=2))
    _put(d / "dependency-scan.json", json.dumps({
        "produced_at": _iso(now - dt.timedelta(days=1)), "tool": "osv-scanner (SYNTHETIC findings)",
        "findings": [{"id": "SYN-ENT04-0001", "package": comps[0][0], "severity": "low"}]}, indent=2))
    _put(d / "redteam.json", json.dumps({
        "produced_at": _iso(now - dt.timedelta(days=2)), "tool": "icdev red team (SYNTHETIC results)",
        "results": dict.fromkeys(REDTEAM_REQUIRED[p["kind"]], "passed")}, indent=2))
    if p["kind"] == "agent":
        _put(d / "prompt.md", "SYNTHETIC agent (icx-ent-04): answer questions about the ledger system's twin coverage "
                              "from the tools you are given; propose nothing and change nothing.\n")
    elif p["kind"] == "skill":
        _put(d / "instructions.md", "SYNTHETIC skill (icx-ent-04): summarise a twin coverage report in three lines.\n")
    elif p["kind"] == "crew":
        _put(d / "crew.json", json.dumps({"schema": "icos-crew/v1", "id": p["id"], "title": "SYNTHETIC crew",
                                          "use_case": "store-synthetic", "roles": []}, indent=2))
    elif p["kind"] == "mcp_server":
        _put(d / "tools.json", json.dumps([{"name": t, "description": f"SYNTHETIC: read the coverage of a labelled "
                                                                       f"fixture system ({t})",
                                            "inputSchema": {"type": "object", "properties": {
                                                "system": {"type": "string"}}}} for t in p["tools"]], indent=2))
        for s in p.get("sources") or []:
            _put(d / "src" / s, SERVER)
    elif p["kind"] == "plugin":
        _put(d / "manifest.json", json.dumps({"name": p["id"], "version": "SYNTHETIC", "entry": "plugin:main",
                                              "label": "SYNTHETIC plugin (icx-ent-04)"}, indent=2))
        _put(d / "config.json", json.dumps({"release_ceiling": p["egress_ceiling"], "label": "SYNTHETIC"}, indent=2))
        for s in p.get("sources") or []:
            _put(d / "src" / s, SERVER)
    return d


def write_all(cfg: dict | None = None, *, now: dt.datetime | None = None) -> dict:
    """Write the inputs of every SYNTHETIC package of ``cfg`` under its package root."""
    cfg = cfg or cfgmod.load()
    now = now or dt.datetime.now(dt.timezone.utc)
    base = cfgmod.root(cfg)
    base.mkdir(parents=True, exist_ok=True)
    out = {}
    for p in cfg.get("packages") or []:
        if p.get("synthetic") is True:
            out[p["id"]] = str(write_package(base, p, now=now).relative_to(cfgmod.BASE_DIR))
    return {"written": out}


__all__ = ["write_all", "write_package"]
