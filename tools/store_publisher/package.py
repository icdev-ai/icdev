# CUI // SP-CTI
"""The packager: one signed ``icos-store-package/v1`` from a declared package (icx-ent-04).

For a package declared in ``args/store_publisher.yaml`` this reads its inputs (fixed names per kind, confined under
``package_root``), builds the package ICDEV[OS]'s store imports (``icdev_os/store/package.py``), runs ICDEV's seven
marketplace scanner gates on it (``gates.py``: the publisher's own report, in ``scan``), and signs its A2A Agent Card
with the publisher's key (``card.py``). The card BINDS the package: its ``metadata["icos.store/package_digest"]`` is
the package's own digest (every field but the card and ``content_digest``), its name and version are the package's,
and its skills are EXACTLY the package's declared tools. ``content_digest`` is the SHA-256 of the canonical JSON of
everything but itself (``tools.idp.dora.digest``: sorted keys, no spaces -- ICDEV[OS]'s ``canon.digest``).

Before it is returned the package is validated against ICDEV[OS]'s pinned schema (a schema file whose SHA-256 is not
its pin is refused, never used) and checked again: the card verifies under the publisher's key and binds this
package. Building decides nothing; a person prepares it and another person publishes it (``service.py``).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

from tools.idp.dora import digest
from tools.store_publisher import card as cmod
from tools.store_publisher import config as cfgmod
from tools.store_publisher import gates
from tools.store_publisher.constants import (
    BINDING,
    REDTEAM_REQUIRED,
    SCHEMA,
    SCHEMA_RELPATH,
    SCHEMA_SHA256,
)

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+", re.ASCII)
FINDING_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", re.ASCII)
SEVERITIES = ("critical", "high", "medium", "low")
REDTEAM_RESULTS = ("passed", "failed", "unverified")
SKEW = dt.timedelta(minutes=5)


class PackageRefused(Exception):
    """The package cannot be built: ``code`` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def content_digest(doc: dict) -> str:
    return digest({k: v for k, v in doc.items() if k != "content_digest"})


def package_digest(doc: dict) -> str:
    """What the publisher's signed card binds: the package without its card and its content digest."""
    return digest({k: v for k, v in doc.items() if k not in ("content_digest", "agent_card")})


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── the pinned contract ──────────────────────────────────────────────────────


def schema_errors(doc: dict) -> list[str]:
    """``doc`` against ICDEV[OS]'s pinned store-package schema. Refused (``schema_unpinned``) if the file changed."""
    import jsonschema

    raw = (cfgmod.BASE_DIR / SCHEMA_RELPATH).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA256:
        raise PackageRefused("schema_unpinned", f"{SCHEMA_RELPATH} is not the pinned ICDEV[OS] contract: refused, "
                                                "never used")
    v = jsonschema.Draft202012Validator(json.loads(raw), format_checker=jsonschema.FormatChecker())
    return sorted(f"{'/'.join(map(str, e.path)) or 'document'}: {e.message}"[:240] for e in v.iter_errors(doc))


# ── the inputs ───────────────────────────────────────────────────────────────


def _json(cfg: dict, p: dict, rel: str):
    try:
        return json.loads(cfgmod.read_input(cfg, p, rel).decode("utf-8"),
                          parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"not JSON: {c}")))
    except cfgmod.ConfigError as exc:
        raise PackageRefused("input", str(exc)) from exc
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise PackageRefused("input", f"{p['inputs']}/{rel}: not a JSON document") from exc


def _text(cfg: dict, p: dict, rel: str) -> str:
    try:
        return cfgmod.read_input(cfg, p, rel).decode("utf-8")
    except cfgmod.ConfigError as exc:
        raise PackageRefused("input", str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise PackageRefused("input", f"{p['inputs']}/{rel}: not UTF-8 text") from exc


def _sources(cfg: dict, p: dict) -> list[dict]:
    srcs = p.get("sources") or []
    if len(srcs) > int(cfg["limits"]["max_sources"]):
        raise PackageRefused("input", f"more than {cfg['limits']['max_sources']} source files")
    return [{"path": s, "content": _text(cfg, p, f"src/{s}")} for s in srcs]


def payload(cfg: dict, p: dict) -> dict:
    kind = p["kind"]
    if kind == "agent":
        return {"prompt": _text(cfg, p, "prompt.md"), "harnesses": []}
    if kind == "skill":
        return {"instructions": _text(cfg, p, "instructions.md")}
    if kind == "crew":
        crew = _json(cfg, p, "crew.json")
        if not isinstance(crew, dict):
            raise PackageRefused("input", "crew.json is a crew declaration (an object)")
        return {"crew": crew}
    if kind == "mcp_server":
        listing = _json(cfg, p, "tools.json")
        if not isinstance(listing, list) or not listing or not all(
                isinstance(t, dict) and isinstance(t.get("name"), str) and isinstance(t.get("description"), str)
                for t in listing):
            raise PackageRefused("input", "tools.json is the server's tool listing: [{name, description, inputSchema}]")
        if sorted(t["name"] for t in listing) != sorted(p.get("tools") or []):
            raise PackageRefused("tools_mismatch", "an MCP server declares exactly the tools its listing names")
        return {"tools": listing, "sources": _sources(cfg, p)}
    manifest, config = _json(cfg, p, "manifest.json"), _json(cfg, p, "config.json")
    if not isinstance(manifest, dict) or not isinstance(config, dict):
        raise PackageRefused("input", "a plugin's manifest.json and config.json are objects")
    return {"manifest": manifest, "distribution_sha256": p["distribution_sha256"], "config": config,
            "sources": _sources(cfg, p)}


def _zoned(v, what: str, now: dt.datetime) -> str:
    t = gates.when(v)
    if t is None:
        raise PackageRefused("time", f"{what}: produced_at is not a zoned ISO 8601 time")
    if t > now + SKEW:
        raise PackageRefused("future", f"{what}: produced_at is in the future")
    return str(v)


def dependency_report(cfg: dict, p: dict, sbom: dict, now: dt.datetime) -> dict:
    """The factory scanner's findings over exactly this SBOM (``sbom_sha256``): ICDEV[OS] checks it is."""
    scan = _json(cfg, p, "dependency-scan.json")
    if not isinstance(scan, dict) or set(scan) - {"produced_at", "tool", "findings"} \
            or not (isinstance(scan.get("tool"), str) and 0 < len(scan["tool"].strip()) <= 200) \
            or not isinstance(scan.get("findings"), list) or len(scan["findings"]) > 500:
        raise PackageRefused("input", "dependency-scan.json is {produced_at, tool, findings: [{id, package, severity}]}")
    findings = []
    for f in scan["findings"]:
        if not isinstance(f, dict) or set(f) - {"id", "package", "severity"} \
                or not FINDING_ID.fullmatch(str(f.get("id") or "")) \
                or not (isinstance(f.get("package"), str) and 0 < len(f["package"]) <= 200) \
                or f.get("severity") not in (*SEVERITIES, None):
            raise PackageRefused("input", "a dependency finding is {id, package, severity: critical|high|medium|low|null}")
        findings.append({"id": f["id"], "package": f["package"], "severity": f.get("severity")})
    return {"produced_at": _zoned(scan.get("produced_at"), "dependency-scan.json", now), "tool": scan["tool"],
            "sbom_sha256": digest(sbom), "findings": findings}


def redteam_report(cfg: dict, p: dict, now: dt.datetime) -> dict:
    """The publisher's red-team results (ATTESTED; ICDEV[OS] never re-runs them). Every id the kind needs is answered."""
    raw = cfgmod.read_input(cfg, p, "redteam.json")
    rt = _json(cfg, p, "redteam.json")
    if not isinstance(rt, dict) or set(rt) - {"produced_at", "tool", "results"} \
            or not (isinstance(rt.get("tool"), str) and 0 < len(rt["tool"].strip()) <= 200) \
            or not isinstance(rt.get("results"), dict) or len(rt["results"]) > 64 \
            or not all(isinstance(k, str) and v in REDTEAM_RESULTS for k, v in rt["results"].items()):
        raise PackageRefused("input", "redteam.json is {produced_at, tool, results: {<OWASP id>: passed|failed|unverified}}")
    missing = sorted(set(REDTEAM_REQUIRED[p["kind"]]) - set(rt["results"]))
    if missing:
        raise PackageRefused("redteam_incomplete", f"the red-team report answers no result for {', '.join(missing[:6])}"
                                                   f"{' ...' if len(missing) > 6 else ''}: a {p['kind']} answers every "
                                                   "id ICDEV[OS] requires of its kind")
    return {"produced_at": _zoned(rt.get("produced_at"), "redteam.json", now), "tool": rt["tool"],
            "results": dict(sorted(rt["results"].items())), "report_sha256": hashlib.sha256(raw).hexdigest()}


# ── the build ────────────────────────────────────────────────────────────────


def build(p: dict, cfg: dict, *, signer, version: str, now: dt.datetime | None = None) -> dict:
    """``{"package": <icos-store-package/v1>, "record": {...}}``. Raises PackageRefused."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    version = str(version or "").strip()
    if not VERSION.fullmatch(version):
        raise PackageRefused("version", "a version is MAJOR.MINOR.PATCH")
    if signer is None:
        raise PackageRefused("no_key", "no publisher key: a package is never left unsigned")
    pinned_kid = (cfg.get("signing") or {}).get("kid")
    if pinned_kid and cmod.kid(signer.spki()) != pinned_kid:
        raise PackageRefused("key_mismatch", "the publisher key is not the one ICDEV[OS] pins (signing.kid)")
    sbom = _json(cfg, p, "sbom.cdx.json")
    aibom = _json(cfg, p, "aibom.cdx.json") if p.get("aibom") else None
    pub = cfg["publisher"]
    body = {
        "schema": SCHEMA, "id": p["id"], "version": version, "kind": p["kind"], "title": p["title"],
        "description": p["description"], "publisher": {"id": pub["id"], "name": pub["name"]},
        "marking": p["marking"], "marking_ceiling": p["marking_ceiling"], "egress_ceiling": p["egress_ceiling"],
        "tools": list(p.get("tools") or []), "budget": dict(p["budget"]) if p.get("budget") else None,
        "sbom": sbom, "aibom": aibom, "dependencies": dependency_report(cfg, p, sbom, now),
        "redteam": redteam_report(cfg, p, now), "provenance": dict(p["provenance"]), "payload": payload(cfg, p),
        "produced_at": iso(now),
    }
    results = gates.run(body, p["kind"], signer=signer, pinned_kid=pinned_kid, now=now)
    body["scan"] = {name: r["status"] for name, r in results.items()}
    card = {"name": p["id"], "version": version, "description": p["title"],
            "provider": {"organization": pub["name"]},
            "skills": [{"id": t, "name": t} for t in body["tools"]],
            "metadata": {BINDING: package_digest(body)}}
    if p.get("url"):
        card["url"] = p["url"]
    body["agent_card"] = cmod.sign_card(card, signer)
    body["content_digest"] = content_digest(body)
    errs = schema_errors(body)
    if errs:
        raise PackageRefused("off_contract", "the package does not meet ICDEV[OS]'s contract: " + "; ".join(errs[:3]))
    problems = self_check(body, signer.spki())
    if problems:
        raise PackageRefused("self_check", "; ".join(problems))
    if len(json.dumps(body)) > int(cfg["limits"]["max_package_bytes"]) - 4096:
        raise PackageRefused("too_large", "the package is larger than ICDEV[OS] imports")
    spki = signer.spki()
    rt = body["redteam"]["results"]
    record = {"version": version, "publisher": dict(pub), "kid": cmod.kid(spki), "public_jwk": cmod.jwk(spki),
              "key_source": getattr(signer, "source", ""), "gates": results,
              "redteam": {"tool": body["redteam"]["tool"], "failed": sorted(k for k, v in rt.items() if v == "failed"),
                          "unverified": sorted(k for k, v in rt.items() if v == "unverified")},
              "sbom": {"spec": sbom.get("specVersion"), "components": len(sbom.get("components") or [])},
              "produced_at": body["produced_at"], "schema_contract": SCHEMA}
    return {"package": body, "record": record}


def self_check(body: dict, spki: bytes) -> list[str]:
    """What is wrong with a built package, checked as ICDEV[OS] will: its digest, its card's signature under the
    publisher's key, the card's binding to THIS package, its name, version and skills."""
    out = []
    if body.get("content_digest") != content_digest(body):
        out.append("its content_digest is not its content's digest")
    c = body.get("agent_card") or {}
    try:
        cmod.verify_card(c, {cmod.kid(spki): spki})
    except cmod.CardError:
        out.append("the card does not verify under the publisher's key")
    if (c.get("metadata") or {}).get(BINDING) != package_digest(body):
        out.append("the card does not bind this package's content")
    if c.get("name") != body.get("id") or c.get("version") != body.get("version"):
        out.append("the card's name and version are not the package's")
    ids = [str(s.get("id")) for s in c.get("skills") or [] if isinstance(s, dict)]
    if sorted(ids) != sorted(body.get("tools") or []) or len(set(ids)) != len(ids):
        out.append("the card's skills are not exactly the package's declared tools")
    return out


__all__ = ["PackageRefused", "build", "content_digest", "package_digest", "schema_errors", "self_check"]
