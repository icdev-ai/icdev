# CUI // SP-CTI
"""The reviewed configuration of the signed factory builds (``args/factory_builds.yaml``) and its confined inputs.

Every check fails closed: an unknown key, kind, marking or VEX status, a target id or system id that is not strict
ASCII, a path that is absolute, climbs out, passes through a symlink, is not a regular file with one link, or is larger
than the limit -- each is refused, never guessed at. A caller names a TARGET; which files a build reads comes only from
this file, and each file is read once: the bytes hashed are the bytes used.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

import yaml
from icdev.core.paths import repo_root

from tools.factory_builds.constants import CONFIG_RELPATH, CONFIG_SCHEMA, KINDS, MARKINGS

BASE_DIR = repo_root(__file__)
TARGET_ID = re.compile(r"[a-z][a-z0-9-]{1,62}", re.ASCII)
SYSTEM_ID = TARGET_ID
REL = re.compile(r"[A-Za-z0-9.][A-Za-z0-9._/-]{0,299}", re.ASCII)
SUBJECT_NAME = re.compile(r"[a-z0-9][a-z0-9._:/@-]{0,299}", re.ASCII)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,99}", re.ASCII)
VULN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", re.ASCII)
VEX_STATUSES = ("not_affected", "affected", "fixed", "under_investigation")
JUSTIFICATIONS = ("component_not_present", "vulnerable_code_not_present", "vulnerable_code_not_in_execute_path",
                  "vulnerable_code_cannot_be_controlled_by_adversary", "inline_mitigations_already_exist")
TOP = {"schema", "builder_id", "build_type", "signing", "keyless", "transparency_log", "limits", "artefact_root",
       "models_root", "targets"}
TARGET_KEYS = {"id", "synthetic", "kind", "system_id", "org", "marking", "name", "subject_name", "layout", "package",
               "model", "sbom_project", "scan", "vex", "runtime"}


class ConfigError(ValueError):
    pass


def _rel_ok(rel: str) -> bool:
    """Relative, strict ASCII, no empty, ``.`` or ``..`` component."""
    return bool(REL.fullmatch(rel)) and all(part not in ("", ".", "..") for part in rel.split("/"))


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(path: Path | None = None) -> dict:
    p = path or BASE_DIR / CONFIG_RELPATH
    raw = p.read_bytes()
    cfg = yaml.safe_load(raw) or {}
    validate(cfg)
    cfg["_digest"] = digest(raw)
    return cfg


def validate(cfg: dict) -> None:
    def no(why: str):
        raise ConfigError(f"{CONFIG_RELPATH}: {why}")

    if not isinstance(cfg, dict) or cfg.get("schema") != CONFIG_SCHEMA:
        no(f"schema must be {CONFIG_SCHEMA}")
    extra = set(cfg) - TOP
    if extra:
        no(f"unknown key(s) {', '.join(sorted(extra))}")
    sg = cfg.get("signing") or {}
    if sg.get("mode") not in ("key", "keyless"):
        no("signing.mode is key or keyless")
    key = sg.get("key")
    if key is not None and not (isinstance(key, str) and re.fullmatch(r"(kms|keyring):[A-Za-z0-9/_.:=?&-]{1,300}",
                                                                      key, re.ASCII)):
        no("signing.key is kms:<key>?region=<region> or keyring:<service>/<name> (a file: key is for tests only)")
    tl = cfg.get("transparency_log") or {}
    if tl.get("mode") not in ("off", "rekor") or (tl.get("mode") == "rekor" and not str(tl.get("url") or "").strip()):
        no("transparency_log.mode is off, or rekor with a url")
    for root in ("artefact_root", "models_root"):
        if not isinstance(cfg.get(root), str) or not _rel_ok(cfg[root]):
            no(f"{root} is a repo-relative path")
    seen = set()
    for t in cfg.get("targets") or []:
        if not isinstance(t, dict) or set(t) - TARGET_KEYS:
            no(f"a target has unknown keys: {', '.join(sorted(set(t) - TARGET_KEYS)) if isinstance(t, dict) else t}")
        tid = str(t.get("id") or "")
        if not TARGET_ID.fullmatch(tid) or tid in seen:
            no(f"target id {tid[:40]!r} is not a unique lowercase ASCII id")
        seen.add(tid)
        if t.get("kind") not in KINDS:
            no(f"{tid}: kind is one of {', '.join(KINDS)}")
        if not SYSTEM_ID.fullmatch(str(t.get("system_id") or "")):
            no(f"{tid}: system_id is an ICDEV[OS] system id")
        if t.get("marking") not in MARKINGS:
            no(f"{tid}: marking is one of {', '.join(MARKINGS)}")
        if not str(t.get("org") or "").strip():
            no(f"{tid}: org (the owning tenant) is required")
        if not NAME.fullmatch(str(t.get("name") or "")) or not SUBJECT_NAME.fullmatch(str(t.get("subject_name") or "")):
            no(f"{tid}: name and subject_name are strict ASCII")
        need = {"image": ("layout", "sbom_project"), "agent": ("package", "sbom_project"),
                "iac": ("package", "sbom_project"), "model": ("model",)}[t["kind"]]
        for k in need:
            if not t.get(k):
                no(f"{tid}: a {t['kind']} target needs {k}")
        for k in ("layout", "package", "sbom_project", "scan"):
            if t.get(k) is not None and not _rel_ok(str(t[k])):
                no(f"{tid}: {k} is a relative path under artefact_root")
        for vid, v in (t.get("vex") or {}).items():
            if not VULN_ID.fullmatch(str(vid)) or not isinstance(v, dict) or v.get("status") not in VEX_STATUSES:
                no(f"{tid}: vex.{str(vid)[:40]} needs a status")
            if v["status"] == "not_affected" and v.get("justification") not in JUSTIFICATIONS:
                no(f"{tid}: vex.{vid} not_affected needs an OpenVEX justification")
            if v["status"] == "affected" and not str(v.get("action") or "").strip():
                no(f"{tid}: vex.{vid} affected needs an action")


def target(cfg: dict, tid: str) -> dict | None:
    return next((t for t in cfg.get("targets") or [] if t.get("id") == tid), None)


def root(cfg: dict, which: str = "artefact_root") -> Path:
    return (BASE_DIR / cfg[which]).resolve()


def read_confined(base: Path, rel: str, *, max_bytes: int) -> bytes:
    """The bytes of ``base/rel``: a regular file with one link, under ``base``, no symlink on the way, within the size
    limit. Read once, so what is hashed is what is used."""
    if not _rel_ok(rel):
        raise ConfigError("a path is relative, ASCII and inside its root")
    p = base
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            raise ConfigError(f"{rel}: a symlink is never followed")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ConfigError(f"{rel}: not a regular file with one link")
        if st.st_size > max_bytes:
            raise ConfigError(f"{rel}: larger than {max_bytes} bytes")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(max_bytes + 1)
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise ConfigError(f"{rel}: larger than {max_bytes} bytes")
    return data


def confined_dir(base: Path, rel: str) -> Path:
    """A directory under ``base``, no symlink on the way."""
    if not _rel_ok(rel):
        raise ConfigError("a path is relative, ASCII and inside its root")
    p = base
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            raise ConfigError(f"{rel}: a symlink is never followed")
    if not p.is_dir():
        raise ConfigError(f"{rel}: not a directory")
    return p


__all__ = ["ConfigError", "confined_dir", "load", "read_confined", "root", "target", "validate"]
