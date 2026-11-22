# CUI // SP-CTI
"""The reviewed configuration of the store publisher (``args/store_publisher.yaml``).

Every check fails closed: an unknown key, kind, marking or role, an id that is not strict ASCII, a ceiling that needs
an accreditation, a path that is absolute or climbs out -- each is refused, never guessed at. A caller names a
PACKAGE; which files a package is made of comes only from this file (fixed names per kind, under ``package_root``),
read through ``tools/common/confined_read.py``.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from icdev.core.paths import repo_root

from tools.common.confined_read import ConfinedError, read_confined, rel_ok
from tools.store_publisher.constants import CONFIG_RELPATH, CONFIG_SCHEMA, ICOS_MARKINGS, KINDS

BASE_DIR = repo_root(__file__)
PACKAGE_ID = re.compile(r"[a-z][a-z0-9-]{1,40}", re.ASCII)          # ICDEV[OS] package.ID
PUBLISHER_ID = PACKAGE_ID
TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", re.ASCII)         # ICDEV[OS] package.TOOL
ORG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", re.ASCII)
HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
COMMIT = re.compile(r"[0-9a-f]{40}", re.ASCII)
KID = re.compile(r"[0-9a-f]{16}", re.ASCII)
SOURCE_URL = re.compile(r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?", re.ASCII)   # ICDEV[OS] Provenance.source
KEY_SPEC = re.compile(r"keyring:[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}", re.ASCII)
TOP = {"schema", "publisher", "signing", "limits", "packager_roles", "package_root", "packages"}
PACKAGE_KEYS = {"id", "synthetic", "kind", "org", "title", "description", "marking", "marking_ceiling",
                "egress_ceiling", "tools", "budget", "inputs", "sources", "provenance", "url", "aibom",
                "distribution_sha256"}
BUDGET = {"turns": (1, 64), "calls": (0, 256), "tokens": (1, 2_000_000), "seconds": (1, 3600)}
LIMITS = ("max_file_bytes", "max_sources", "max_package_bytes")
#: The payload file(s) of each kind, fixed: a package's files are never chosen by a caller.
PAYLOAD_FILES = {"agent": ("prompt.md",), "skill": ("instructions.md",), "crew": ("crew.json",),
                 "mcp_server": ("tools.json",), "plugin": ("manifest.json", "config.json")}
COMMON_FILES = ("sbom.cdx.json", "dependency-scan.json", "redteam.json")
_RANK = {m: i for i, m in enumerate(ICOS_MARKINGS)}


class ConfigError(ValueError):
    pass


def load(path: Path | None = None) -> dict:
    p = path or BASE_DIR / CONFIG_RELPATH
    raw = p.read_bytes()
    cfg = yaml.safe_load(raw) or {}
    validate(cfg)
    cfg["_digest"] = hashlib.sha256(raw).hexdigest()
    return cfg


def _int(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def validate(cfg: dict) -> None:
    def no(why: str):
        raise ConfigError(f"{CONFIG_RELPATH}: {why}")

    if not isinstance(cfg, dict) or cfg.get("schema") != CONFIG_SCHEMA:
        no(f"schema must be {CONFIG_SCHEMA}")
    extra = set(cfg) - TOP
    if extra:
        no(f"unknown key(s) {', '.join(sorted(map(str, extra)))[:200]}")
    pub = cfg.get("publisher") or {}
    if not isinstance(pub, dict) or set(pub) - {"id", "name"} or not PUBLISHER_ID.fullmatch(str(pub.get("id") or "")) \
            or not (isinstance(pub.get("name"), str) and 0 < len(pub["name"].strip()) <= 120):
        no("publisher is {id, name}: the publisher id ICDEV[OS] pins and its name")
    sg = cfg.get("signing") or {}
    if not isinstance(sg, dict) or set(sg) - {"key", "kid"}:
        no("signing has key and kid only")
    if sg.get("key") is not None and not KEY_SPEC.fullmatch(str(sg["key"])):
        no("signing.key is keyring:<service>/<name> (or null until the site sets it)")
    if sg.get("kid") is not None and not KID.fullmatch(str(sg["kid"])):
        no("signing.kid is the 16-hex key id ICDEV[OS] pins")
    lim = cfg.get("limits") or {}
    if not isinstance(lim, dict) or set(lim) != set(LIMITS) or not all(_int(lim[k], 1, 16 * 1024 * 1024) for k in LIMITS):
        no(f"limits has {', '.join(LIMITS)}, each a positive integer")
    roles = cfg.get("packager_roles")
    if not isinstance(roles, list) or not roles or not all(isinstance(r, str) and r.strip() for r in roles) \
            or "service" in roles:
        no("packager_roles is a non-empty list of dashboard roles (never service): who may make the publisher's key sign")
    try:
        from tools.dashboard.auth import VALID_DASHBOARD_ROLES
    except Exception:  # noqa: BLE001 — the role vocabulary unreadable: every role unknown, fail closed
        VALID_DASHBOARD_ROLES = frozenset()
    unknown = sorted(set(roles) - set(VALID_DASHBOARD_ROLES))
    if unknown:
        no(f"packager_roles names roles the dashboard does not know: {', '.join(unknown)[:120]}")
    if not rel_ok(str(cfg.get("package_root") or "")):
        no("package_root is a repo-relative path")
    seen: set[str] = set()
    for p in cfg.get("packages") or []:
        _package(p, seen, no)


def _package(p, seen: set, no) -> None:
    if not isinstance(p, dict) or set(p) - PACKAGE_KEYS:
        no(f"a package has unknown keys: {sorted(set(p) - PACKAGE_KEYS) if isinstance(p, dict) else p}"[:200])
    pid = str(p.get("id") or "")
    if not PACKAGE_ID.fullmatch(pid) or pid in seen:
        no(f"package id {pid[:41]!r} is not a unique lowercase ASCII id of at most 41 characters")
    seen.add(pid)
    if p.get("kind") not in KINDS:
        no(f"{pid}: kind is one of {', '.join(KINDS)}")
    if "synthetic" in p and not isinstance(p["synthetic"], bool):
        no(f"{pid}: synthetic is true or false")
    if not ORG.fullmatch(str(p.get("org") or "")):
        no(f"{pid}: org (the ICDEV[IT] organisation that owns it) is required, strict ASCII")
    for k, hi in (("title", 120), ("description", 2000)):
        if not (isinstance(p.get(k), str) and 0 < len(p[k].strip()) <= hi):
            no(f"{pid}: {k} is 1..{hi} characters")
    for k in ("marking", "marking_ceiling", "egress_ceiling"):
        if p.get(k) not in ICOS_MARKINGS:
            no(f"{pid}: {k} is one of {', '.join(ICOS_MARKINGS)} (cui and above needs an accreditation: the owner's)")
    if _RANK[p["egress_ceiling"]] > _RANK[p["marking_ceiling"]] or _RANK[p["marking"]] > _RANK[p["marking_ceiling"]]:
        no(f"{pid}: marking and egress_ceiling are at most marking_ceiling")
    tools = p.get("tools") or []
    if not isinstance(tools, list) or len(tools) > 128 or len(set(map(str, tools))) != len(tools) \
            or not all(isinstance(t, str) and TOOL.fullmatch(t) for t in tools):
        no(f"{pid}: tools is a list of distinct tool names")
    if p["kind"] in ("agent", "crew", "mcp_server") and not tools:
        no(f"{pid}: a {p['kind']} declares its tools")
    b = p.get("budget")
    if p["kind"] in ("agent", "crew") and b is None:
        no(f"{pid}: a {p['kind']} declares its run budget")
    if b is not None and (not isinstance(b, dict) or set(b) != set(BUDGET)
                          or not all(_int(b[k], *BUDGET[k]) for k in BUDGET)):
        no(f"{pid}: budget is {{turns, calls, tokens, seconds}} within ICDEV[OS]'s bounds")
    if not rel_ok(str(p.get("inputs") or "")):
        no(f"{pid}: inputs is a relative path under package_root")
    srcs = p.get("sources") or []
    if p["kind"] in ("mcp_server", "plugin"):
        if not isinstance(srcs, list) or not srcs or not all(rel_ok(str(s)) for s in srcs) \
                or len(set(srcs)) != len(srcs):
            no(f"{pid}: a {p['kind']} lists its source files (relative, under inputs/src)")
    elif srcs:
        no(f"{pid}: an {p['kind']} carries no code, so no sources")
    if p["kind"] == "plugin" and not HEX64.fullmatch(str(p.get("distribution_sha256") or "")):
        no(f"{pid}: a plugin names the SHA-256 of the distribution the deployment installs")
    if p["kind"] != "plugin" and "distribution_sha256" in p:
        no(f"{pid}: only a plugin names a distribution")
    pv = p.get("provenance") or {}
    if not isinstance(pv, dict) or set(pv) != {"source", "commit"} or not SOURCE_URL.fullmatch(str(pv["source"])) \
            or len(str(pv["source"])) > 300 or not COMMIT.fullmatch(str(pv["commit"])):
        no(f"{pid}: provenance is {{source: https://..., commit: <40 hex>}}")
    if "url" in p and not (SOURCE_URL.fullmatch(str(p["url"])) and len(str(p["url"])) <= 300):
        no(f"{pid}: url is an https URL")
    if "aibom" in p and not isinstance(p["aibom"], bool):
        no(f"{pid}: aibom is true (read aibom.cdx.json) or false")


def package(cfg: dict, pid: str) -> dict | None:
    return next((p for p in cfg.get("packages") or [] if p.get("id") == pid), None)


def root(cfg: dict) -> Path:
    return (BASE_DIR / cfg["package_root"]).resolve()


def read_input(cfg: dict, p: dict, rel: str) -> bytes:
    """One input file of a declared package, confined to ``<package_root>/<inputs>/``."""
    try:
        return read_confined(root(cfg), f"{p['inputs']}/{rel}", max_bytes=int(cfg["limits"]["max_file_bytes"]))
    except ConfinedError as exc:
        raise ConfigError(str(exc)) from exc


__all__ = ["COMMON_FILES", "ConfigError", "PAYLOAD_FILES", "load", "package", "read_input", "root", "validate"]
