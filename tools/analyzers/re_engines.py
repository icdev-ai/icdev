#!/usr/bin/env python3
# CUI // SP-CTI
"""Reverse-engineering engine registry + license gate (rea-eng-01, project card ``rea``).

WHAT THIS ANSWERS. Before any REA run (morluto/rea, a local stdio MCP server
driving Ghidra / Hopper / IDA), ONE question: may THIS engine be used HERE, by
THIS user, and on what license basis? The answer is a decision dict with
``allowed`` and a NAMED ``reason`` -- never a bare bool -- and a ``provenance``
block that every downstream evidence record carries, so a result always says
which engine and which declared license basis produced it.

GHIDRA IS NEVER REFUSED ON LICENSE GROUNDS. It is Apache-2.0, redistributable,
and the default everywhere. Whether it is INSTALLED is a separate field
(``installed``); absence is reported, not dressed as a refusal.

HOPPER AND IDA ARE BRING-YOUR-OWN, AND THE USER OWNS THE LICENSE QUESTION.
Operator decision 2026-10-11: "let users maintain their own license". ICDEV is
Apache-2.0 integration code; it never grants, sells, stores or verifies rights
to a commercial engine. Each user writes a declaration OUTSIDE the repo
(``~/.config/icdev/re_licenses.yaml``, or ``$ICDEV_RE_LICENSES``)::

    own_machine: true          # this host is my own, single-operator machine
    engines:
      ida:
        edition: pro           # free | home | pro | classroom | oem
        use: commercial        # commercial | non_commercial
      hopper:
        edition: personal      # personal | computer | demo
        use: commercial
        user: alice            # personal: the named licensee
        host: my-laptop        # computer: the licensed host

and this module enforces the declaration's CONSISTENCY with the vendor's
published terms (quoted and linked from ``args/re_engines.yaml`` in every
refusal). It cannot know whether the user really holds the license, and it does
not pretend to.

THE REFUSALS, EACH NAMED (``REASON_*`` below):

    undeclared                 a commercial engine with no declaration. NOT a
                               silent fallback: the message says how to declare
    ida_free_commercial        IDA Free declared for commercial use
    shared_deployment          Hopper, or non-OEM IDA, on a SaaS / multi-tenant
                               deployment (ICDEV_SAAS_MODE / ICDEV_MULTI_TENANT)
    host_not_declared_own      a single-user license on a host the user has not
                               declared their own (``own_machine: true``)
    named_user_* / licensed_host_*   Hopper personal / computer binding
    platform_unsupported       Hopper on Windows
    declaration_contradicts_install  declared IDA edition != the detected one
    ...and the malformed / in-repo declaration cases.

NO KEY IS EVER READ, STORED OR REPEATED. Only ``edition``, ``use``, ``user`` and
``host`` are read per engine. Any other field (a serial, a key, a license-file
path) is ignored and reported by NAME in ``ignored_fields`` -- its VALUE never
enters a decision or a provenance record.

NOTHING HERE DOWNLOADS, INSTALLS OR EXECUTES ANYTHING. Detection is file
existence plus ``application.properties`` / package metadata reads; no
subprocess, no network import. Asserted from this module's AST by
``tests/analyzers/test_re_engines.py``, which also asserts no ``tools/`` module
carries a Hopper or IDA vendor URL.

AN UNMEASURED VALUE IS ``None``, NEVER A GUESS. ``version``, ``edition_detected``
and ``idalib`` read None when this module could not determine them.

Usage:
    python -m tools.analyzers.re_engines --json              # every engine
    python -m tools.analyzers.re_engines --engine ida --json # one decision
    python -m tools.analyzers.re_engines --template          # a declaration to copy

Exit codes: 0 a report was produced (a refusal IS a report); 2 none could be.
"""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import platform as _platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

__all__ = [
    "ENGINES",
    "declaration_path",
    "deployment_context",
    "detect_engine",
    "detect_engines",
    "evaluate",
    "evaluate_all",
    "host_platform",
    "load_declaration",
    "load_registry",
]

ENGINES = ("ghidra", "hopper", "ida")

REASON_OSS = "oss_redistributable"
REASON_ALLOWED = "declared_license_consistent"
REASON_UNKNOWN_ENGINE = "unknown_engine"
REASON_PLATFORM = "platform_unsupported"
REASON_UNDECLARED = "undeclared"
REASON_DECL_MALFORMED = "declaration_malformed"
REASON_DECL_IN_REPO = "declaration_in_repo"
REASON_EDITION_INVALID = "edition_invalid"
REASON_USE_INVALID = "use_invalid"
REASON_IDA_FREE_COMMERCIAL = "ida_free_commercial"
REASON_NON_COMMERCIAL_ONLY = "edition_non_commercial_only"
REASON_SHARED = "shared_deployment"
REASON_HOST_NOT_OWN = "host_not_declared_own"
REASON_USER_UNDECLARED = "named_user_undeclared"
REASON_USER_MISMATCH = "named_user_mismatch"
REASON_HOST_UNDECLARED = "licensed_host_undeclared"
REASON_HOST_MISMATCH = "licensed_host_mismatch"
REASON_CONTRADICTS_INSTALL = "declaration_contradicts_install"

ENV_DECLARATION = "ICDEV_RE_LICENSES"
DEFAULT_DECLARATION = "~/.config/icdev/re_licenses.yaml"
ENV_IDA_HOME = "ICDEV_IDA_HOME"
ENV_IDADIR = "IDADIR"  # Hex-Rays' own convention, honoured second
ENV_HOPPER_PATH = "ICDEV_HOPPER_PATH"
ENGINE_FIELDS = ("edition", "use", "user", "host")

_TRUTHY = ("1", "true", "yes", "on")
_IDA_EXECUTABLES = ("idat64", "idat", "ida64", "ida")
_IDALIB_FILES = ("idalib.dll", "libidalib.so", "libidalib.dylib")
_HOPPER_DEFAULTS = {
    "linux": ("/opt/hopper/bin/Hopper",),
    "macos": (
        "/Applications/Hopper Disassembler v4.app",
        "/Applications/Hopper Disassembler.app",
    ),
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_user() -> Optional[str]:
    """The OS account this process runs as; None when the OS cannot say
    (``getpass.getuser`` raises in a container with no passwd entry)."""
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 -- unknown user: a named license cannot match
        return None


def host_platform() -> str:
    """``windows`` | ``linux`` | ``macos`` -- the vocabulary of ``platforms:``."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def load_registry(path: Optional[str] = None) -> Dict[str, Any]:
    """Read ``args/re_engines.yaml`` (repo copy first, packaged copy second)."""
    if path is None:
        from icdev.core.paths import config_path

        path = str(config_path("args/re_engines.yaml", anchor=__file__))
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data.get("engines"), dict):
        raise ValueError(f"{path}: no engines: mapping")
    return data


# ---------------------------------------------------------------------------
# Deployment: the EXISTING tenancy signals, not a new one
# ---------------------------------------------------------------------------

def deployment_context(
    env: Optional[Mapping[str, str]] = None,
    registry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Is this a shared / SaaS / multi-tenant deployment?

    Reads the signals the platform already sets: ``ICDEV_SAAS_MODE`` (the Helm
    chart sets it; ``saas/db/db_compat.py`` reads it) and
    ``ICDEV_MULTI_TENANT`` (``rag/retriever.py``). Either truthy -> shared.
    """
    env = os.environ if env is None else env
    registry = registry or load_registry()
    names = registry.get("shared_deployment_signals") or []
    signals = {n: str(env.get(n, "")).strip().lower() in _TRUTHY for n in names}
    return {"shared": any(signals.values()), "signals": signals}


# ---------------------------------------------------------------------------
# Detection -- file reads only; nothing is run
# ---------------------------------------------------------------------------

def _detect_ghidra(env: Mapping[str, str]) -> Dict[str, Any]:
    from icdev.tools.analyzers.ghidra_headless import ghidra_version, locate_ghidra

    loc = locate_ghidra(env.get("ICDEV_GHIDRA_HOME"))
    pyghidra_version: Optional[str] = None
    pyghidra = importlib.util.find_spec("pyghidra") is not None
    if pyghidra:
        try:
            from importlib.metadata import version as _dist_version

            pyghidra_version = _dist_version("pyghidra")
        except Exception:  # noqa: BLE001 -- a spec without metadata: version unknown
            pyghidra_version = None
    return {
        "installed": bool(loc.get("headless")) or pyghidra,
        "path": loc.get("headless"),
        "version": ghidra_version(loc.get("home")),
        "pyghidra": pyghidra,
        "pyghidra_version": pyghidra_version,
        "source": loc.get("source"),
        "reason": loc.get("reason"),
    }


def _detect_ida(env: Mapping[str, str]) -> Dict[str, Any]:
    source = ENV_IDA_HOME if env.get(ENV_IDA_HOME) else (ENV_IDADIR if env.get(ENV_IDADIR) else None)
    if source is None:
        return {"installed": False, "path": None, "version": None, "idalib": None,
                "edition_detected": None, "source": None, "reason": "not_declared_in_env"}
    home = Path(str(env[source])).expanduser()
    exe = None
    for stem in _IDA_EXECUTABLES:
        for name in (stem + ".exe", stem) if host_platform() == "windows" else (stem,):
            if (home / name).is_file():
                exe = home / name
                break
        if exe:
            break
    idalib = any((home / f).is_file() for f in _IDALIB_FILES) or (home / "idalib").is_dir()
    # Hex-Rays installs the freeware build as "IDA Freeware <ver>". Only a
    # POSITIVE match is reported; anything else is None, not "pro".
    lowered = home.name.lower()
    edition = "free" if ("freeware" in lowered or "ida free" in lowered) else None
    return {
        "installed": exe is not None,
        "path": str(exe) if exe else None,
        "version": None,
        "idalib": idalib if exe is not None else None,
        "edition_detected": edition,
        "source": source,
        "reason": None if exe else "home_invalid",
    }


def _detect_hopper(env: Mapping[str, str], plat: str) -> Dict[str, Any]:
    if plat not in _HOPPER_DEFAULTS:
        return {"installed": False, "path": None, "version": None, "source": None,
                "reason": REASON_PLATFORM}
    declared = env.get(ENV_HOPPER_PATH)
    candidates = [(declared, ENV_HOPPER_PATH)] if declared else [
        (p, "default_location") for p in _HOPPER_DEFAULTS[plat]
    ]
    for raw, source in candidates:
        p = Path(str(raw)).expanduser()
        if p.exists():
            return {"installed": True, "path": str(p), "version": None, "source": source,
                    "reason": None}
    return {"installed": False, "path": None, "version": None,
            "source": ENV_HOPPER_PATH if declared else "default_location",
            "reason": "home_invalid" if declared else "not_found"}


def detect_engine(engine: str, env: Optional[Mapping[str, str]] = None,
                  plat: Optional[str] = None) -> Dict[str, Any]:
    env = os.environ if env is None else env
    plat = plat or host_platform()
    if engine == "ghidra":
        return _detect_ghidra(env)
    if engine == "ida":
        return _detect_ida(env)
    if engine == "hopper":
        return _detect_hopper(env, plat)
    raise KeyError(engine)


def detect_engines(env: Optional[Mapping[str, str]] = None,
                   plat: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    return {e: detect_engine(e, env, plat) for e in ENGINES}


# ---------------------------------------------------------------------------
# The user-owned declaration
# ---------------------------------------------------------------------------

def declaration_path(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return Path(str(env.get(ENV_DECLARATION) or DEFAULT_DECLARATION)).expanduser()


def _inside_a_repo(path: Path) -> Optional[str]:
    """The git work tree a declaration sits in, if any (it could be committed).

    The user's home directory itself is exempt: a dotfiles repo at ``~`` is
    common, and ``~/.config`` is the place this file is meant to live.
    """
    home = Path.home().resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for parent in resolved.parents:
        if parent == home:
            return None
        if (parent / ".git").exists():
            return str(parent)
    return None


def load_declaration(path: Optional[Path] = None,
                     env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Read the user's declaration. Returns ``{status, path, own_machine,
    engines, ignored_fields, error}`` -- status ``ok`` | ``absent`` |
    ``malformed`` | ``in_repo``. Only ENGINE_FIELDS survive per engine."""
    path = Path(path) if path is not None else declaration_path(env)
    out: Dict[str, Any] = {"status": "absent", "path": str(path), "own_machine": False,
                           "engines": {}, "ignored_fields": [], "error": None}
    if not path.is_file():
        return out
    repo = _inside_a_repo(path)
    if repo:
        out.update(status="in_repo", error=f"declaration is inside the git work tree {repo}")
        return out
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        out.update(status="malformed", error=type(exc).__name__)
        return out
    if not isinstance(raw, dict) or not isinstance(raw.get("engines", {}), dict):
        out.update(status="malformed", error="expected a mapping with an engines: mapping")
        return out
    out["own_machine"] = raw.get("own_machine") is True
    ignored: List[str] = sorted(k for k in raw if k not in ("own_machine", "engines"))
    for name, body in (raw.get("engines") or {}).items():
        if not isinstance(body, dict):
            out.update(status="malformed", error=f"engines.{name} is not a mapping")
            out["engines"] = {}
            return out
        kept = {}
        for k, v in body.items():
            if k in ENGINE_FIELDS:
                kept[k] = None if v is None else str(v).strip()
            else:
                ignored.append(f"engines.{name}.{k}")
        out["engines"][str(name)] = kept
    out["ignored_fields"] = ignored
    out["status"] = "ok"
    return out


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _decision(engine: str, allowed: bool, reason: str, message: str, *,
              term: Optional[Dict[str, Any]] = None, spec: Optional[Dict[str, Any]] = None,
              declared: Optional[Dict[str, Any]] = None, declaration: Optional[Dict[str, Any]] = None,
              detection: Optional[Dict[str, Any]] = None,
              deployment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    spec = spec or {}
    declared = declared or {}
    provenance = {
        "engine": engine,
        "license": spec.get("license"),
        "license_class": spec.get("license_class"),
        "license_basis": ("oss" if not spec.get("needs_declaration")
                          else ("user_declaration" if declared else "none")),
        "declared_edition": declared.get("edition"),
        "declared_use": declared.get("use"),
        "engine_version": (detection or {}).get("version"),
        "decided_at": _iso_now(),
    }
    return {
        "engine": engine,
        "allowed": allowed,
        "reason": reason,
        "message": message,
        "term": term,
        "installed": (detection or {}).get("installed"),
        "detection": detection,
        "deployment": deployment,
        "declaration": None if declaration is None else {
            "status": declaration.get("status"),
            "path": declaration.get("path"),
            "own_machine": declaration.get("own_machine"),
            "ignored_fields": list(declaration.get("ignored_fields") or []),
        },
        "provenance": provenance,
    }


def _how_to_declare(engine: str, path: str) -> str:
    example = {"ida": "edition: pro  # free|home|pro|classroom|oem",
               "hopper": "edition: personal  # personal|computer|demo"}.get(engine, "edition: ...")
    return (f"{engine} is a bring-your-own commercial engine and is not used until YOU declare "
            f"your own license in {path} (or the file named by ${ENV_DECLARATION}): "
            f"own_machine: true / engines: {engine}: {example}, use: commercial|non_commercial. "
            f"Run `python -m tools.analyzers.re_engines --template` for a full example. "
            f"ICDEV stores no keys -- only your declaration.")


def evaluate(
    engine: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    registry: Optional[Dict[str, Any]] = None,
    declaration: Optional[Dict[str, Any]] = None,
    detection: Optional[Dict[str, Any]] = None,
    plat: Optional[str] = None,
    user: Optional[str] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """May ``engine`` be used here? A decision with a NAMED reason, always."""
    env = os.environ if env is None else env
    registry = registry or load_registry()
    plat = plat or host_platform()
    spec = (registry.get("engines") or {}).get(engine)
    if spec is None:
        return _decision(engine, False, REASON_UNKNOWN_ENGINE,
                         f"{engine!r} is not declared in args/re_engines.yaml ({', '.join(ENGINES)})")
    deployment = deployment_context(env, registry)
    if detection is None:
        detection = detect_engine(engine, env, plat) if engine in ENGINES else None
    vendor = spec.get("terms") or {}
    common = dict(spec=spec, detection=detection, deployment=deployment)

    if not spec.get("needs_declaration"):
        # Ghidra: never refused on license grounds, on any deployment.
        return _decision(engine, True, REASON_OSS,
                         f"{spec.get('display_name', engine)} is {spec.get('license')}; no declaration needed",
                         term={"text": vendor.get("text"), "url": vendor.get("url")}, **common)

    if plat not in (spec.get("platforms") or []):
        return _decision(engine, False, REASON_PLATFORM,
                         f"{engine} is not supported on {plat} (platforms: {spec.get('platforms')})",
                         **common)

    if declaration is None:
        declaration = load_declaration(env=env)
    common["declaration"] = declaration
    status = declaration.get("status")
    if status == "in_repo":
        return _decision(engine, False, REASON_DECL_IN_REPO,
                         f"the license declaration {declaration.get('path')} is inside a git work tree "
                         f"and could be committed; move it under ~/.config/icdev/", **common)
    if status == "malformed":
        return _decision(engine, False, REASON_DECL_MALFORMED,
                         f"the license declaration {declaration.get('path')} could not be read "
                         f"({declaration.get('error')})", **common)
    declared = (declaration.get("engines") or {}).get(engine)
    if not declared:
        return _decision(engine, False, REASON_UNDECLARED,
                         _how_to_declare(engine, str(declaration.get("path"))), **common)
    common["declared"] = declared

    editions = spec.get("editions") or {}
    edition = (declared.get("edition") or "").lower()
    use = (declared.get("use") or "").lower()
    if edition not in editions:
        return _decision(engine, False, REASON_EDITION_INVALID,
                         f"declared {engine} edition {edition or None!r} is not one of {sorted(editions)}",
                         **common)
    if use not in (registry.get("uses") or []):
        return _decision(engine, False, REASON_USE_INVALID,
                         f"declared use {use or None!r} is not one of {registry.get('uses')}", **common)
    ed = editions[edition]
    term = {"text": ed.get("term"), "url": ed.get("url") or vendor.get("url")}

    if ed.get("non_commercial_only") and use == "commercial":
        reason = REASON_IDA_FREE_COMMERCIAL if (engine, edition) == ("ida", "free") else REASON_NON_COMMERCIAL_ONLY
        return _decision(engine, False, reason,
                         f"refused: {ed.get('term')} You declared use=commercial. See {term['url']}",
                         term=term, **common)

    detected = (detection or {}).get("edition_detected")
    if detected and detected != edition:
        return _decision(engine, False, REASON_CONTRADICTS_INSTALL,
                         f"declared {engine} edition {edition!r} but the install at "
                         f"{(detection or {}).get('path')} is {detected!r}", term=term, **common)

    if deployment["shared"] and not ed.get("shared_ok"):
        shared_term = {"text": spec.get("shared_term"), "url": vendor.get("url")}
        on = [k for k, v in deployment["signals"].items() if v]
        return _decision(engine, False, REASON_SHARED,
                         f"refused: this is a shared/SaaS/multi-tenant deployment ({', '.join(on)}). "
                         f"{spec.get('shared_term')} See {vendor.get('url')}",
                         term=shared_term, **common)

    if not ed.get("shared_ok") and not declaration.get("own_machine"):
        return _decision(engine, False, REASON_HOST_NOT_OWN,
                         f"{engine} {edition} is a single-user license; declare this host your own "
                         f"with `own_machine: true` in {declaration.get('path')}", term=term, **common)

    binds = ed.get("binds")
    if binds == "user":
        named = declared.get("user")
        current = user if user is not None else _current_user()
        if not named:
            return _decision(engine, False, REASON_USER_UNDECLARED,
                             f"{engine} {edition}: {ed.get('term')} Declare `user:` (the licensee).",
                             term=term, **common)
        if named.lower() != str(current).lower():
            return _decision(engine, False, REASON_USER_MISMATCH,
                             f"{engine} {edition} is licensed to {named!r}; this process runs as {current!r}",
                             term=term, **common)
    elif binds == "host":
        licensed = declared.get("host")
        current = host if host is not None else _platform.node()
        if not licensed:
            return _decision(engine, False, REASON_HOST_UNDECLARED,
                             f"{engine} {edition}: {ed.get('term')} Declare `host:` (the licensed computer).",
                             term=term, **common)
        if licensed.lower() != str(current).lower():
            return _decision(engine, False, REASON_HOST_MISMATCH,
                             f"{engine} {edition} is licensed to host {licensed!r}; this host is {current!r}",
                             term=term, **common)

    return _decision(engine, True, REASON_ALLOWED,
                     f"{engine} {edition} ({use}) is consistent with the vendor terms you declared under; "
                     f"ICDEV does not verify that you hold this license", term=term, **common)


def evaluate_all(env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    env = os.environ if env is None else env
    registry = load_registry()
    declaration = load_declaration(env=env)
    return {e: evaluate(e, env=env, registry=registry, declaration=declaration) for e in ENGINES}


TEMPLATE = """\
# ICDEV reverse-engineering license declaration -- YOURS, not ICDEV's.
# Keep it outside any git repository. ICDEV reads only edition/use/user/host;
# never put a license key or serial here (it would be ignored anyway).
own_machine: true            # this host is my own single-operator machine
engines:
  ida:
    edition: pro             # free | home | pro | classroom | oem
    use: commercial          # commercial | non_commercial  (IDA Free: non_commercial only)
  hopper:
    edition: personal        # personal | computer | demo
    use: commercial
    user: {user}             # personal: the named licensee
    # host: {host}           # computer: the licensed host
"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine", choices=ENGINES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--template", action="store_true", help="print a declaration to copy")
    args = ap.parse_args(argv)
    if args.template:
        print(TEMPLATE.format(user=_current_user() or "<you>", host=_platform.node()), end="")
        return 0
    try:
        result: Any = evaluate(args.engine) if args.engine else evaluate_all()
    except Exception as exc:  # noqa: BLE001 -- exit 2: no report could be produced
        print(f"re_engines: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    for d in ([result] if args.engine else result.values()):
        mark = "ALLOW " if d["allowed"] else "REFUSE"
        print(f"{mark} {d['engine']:<7} {d['reason']:<32} installed={d['installed']}")
        print(f"        {d['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
