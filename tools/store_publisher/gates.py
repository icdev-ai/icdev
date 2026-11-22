# CUI // SP-CTI
"""ICDEV's seven marketplace scanner gates, run on a store package (icx-ent-04).

The gates are ``tools/marketplace/asset_scanner.py``'s (``sast_scan``, ``secret_detection``, ``dependency_audit``,
``cui_marking_validation``, ``sbom_generation``, ``supply_chain_provenance``, ``digital_signature``). They run on the
package ITSELF: its content is written once into a fresh private directory (the bytes scanned are the bytes that
ship), the scanner's own analyses read it (``sast_findings``, ``secret_findings``, ``cui_banner_files``: read, never
run, recorded nowhere), and the package-shaped gates read the package's SBOM, dependency report, provenance and card.
The results ship in the package's ``scan`` as the PUBLISHER's own report: ICDEV[OS] re-runs its gates and only shows
ours as claimed.

Every gate says one of ``pass``, ``fail``, ``warning``, ``unverified`` or ``not_applicable``. A gate that cannot run,
or ran on nothing it needs, is ``unverified`` -- never a pass. ``not_applicable`` is only "there is nothing of this kind
in the package" (an agent, a crew or a skill carries no code to analyse).

Two of the scanner's gates are NOT called as written, on purpose: its ``dependency_audit`` runs ``pip-audit`` over a
requirements file, which resolves and installs the requirements into a virtual environment (an untrusted build on the
host) and reaches the network; and its ``sbom_generation`` writes an SBOM rather than checking one. Here
``dependency_audit`` judges the factory scanner's findings over exactly the package's SBOM, and ``sbom_generation``
checks the SBOM the package carries, each with the scanner gate's name and blocking rule.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from tools.store_publisher.constants import BLOCKING_SEVERITIES, DEPENDENCY_MAX_AGE_D, ICOS_MARKINGS, SCANNER

#: An exact version: nothing a resolver could widen (no range, wildcard or space).
EXACT_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,99}", re.ASCII)


def gate(status: str, **detail) -> dict:
    return {"status": status, **detail}


def when(v) -> dt.datetime | None:
    """A zoned ISO 8601 time as UTC; None for an empty, unparseable or zoneless one (never a string comparison)."""
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.astimezone(dt.timezone.utc) if t.tzinfo else None


# ── the package on disk, once ────────────────────────────────────────────────


def _write(base: Path, rel: str, text: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "x", encoding="utf-8") as fh:          # a fresh file: never through a planted name
        fh.write(text)


def materialise(body: dict, kind: str) -> Path:
    """The package's content in a fresh private directory: ``package.json`` and each payload text and source file."""
    d = Path(tempfile.mkdtemp(prefix="icdev-store-gates-"))
    os.chmod(d, 0o700)
    _write(d, "package.json", json.dumps(body, indent=2, sort_keys=True))
    pl = body.get("payload") or {}
    if kind == "agent":
        _write(d, "payload/prompt.md", str(pl.get("prompt") or ""))
    elif kind == "skill":
        _write(d, "payload/instructions.md", str(pl.get("instructions") or ""))
    elif kind == "crew":
        _write(d, "payload/crew.json", json.dumps(pl.get("crew") or {}, indent=2, sort_keys=True))
    elif kind == "mcp_server":
        _write(d, "payload/tools.json", json.dumps(pl.get("tools") or [], indent=2, sort_keys=True))
    elif kind == "plugin":
        _write(d, "payload/manifest.json", json.dumps(pl.get("manifest") or {}, indent=2, sort_keys=True))
        _write(d, "payload/config.json", json.dumps(pl.get("config") or {}, indent=2, sort_keys=True))
    for s in pl.get("sources") or []:
        _write(d, f"src/{s['path']}", str(s.get("content") or ""))
    return d


# ── each gate ────────────────────────────────────────────────────────────────


def sast_scan(d: Path, kind: str, body: dict) -> dict:
    from tools.marketplace.asset_scanner import sast_findings

    srcs = (body.get("payload") or {}).get("sources") or []
    if kind in ("agent", "crew", "skill") and not srcs:
        return gate("not_applicable", reason="an agent, a crew or a skill carries no code")
    if not srcs:
        return gate("unverified", reason="a package that runs code carries no source to analyse")
    out = sast_findings(d / "src")
    f = out.get("findings") or {}
    detail = {"engine": out.get("engine"), "files": out.get("files"), "critical": f.get("critical", 0),
              "high": f.get("high", 0), "issues": (f.get("issues") or [])[:10]}
    # review M2: a source that is not Python is not analysed here, and an unanalysed source never passes
    other = sorted(str(s["path"]) for s in srcs if not str(s["path"]).endswith(".py"))
    if out["status"] == "fail":
        return gate("fail", not_analysed=other[:20], **detail)
    if out["status"] == "skipped" or other:
        return gate("unverified", reason="a source that is not Python is not analysed here", not_analysed=other[:20],
                    **detail)
    if out["status"] == "unverified":
        return gate("unverified", reason="a source file was not analysed (unparsed, or not in the analyser's report)",
                    not_analysed=sorted({*(f.get("unparsed") or []), *(f.get("unanalysed") or [])})[:20], **detail)
    return gate(out["status"], **detail)


def secret_detection(d: Path) -> dict:
    from tools.marketplace.asset_scanner import secret_findings

    found = secret_findings(d)
    # the preview is a secret's first characters: the report names the file, line and kind only
    return gate("pass" if not found else "fail", findings=len(found),
                where=[{k: x[k] for k in ("file", "line", "type")} for x in found[:10]])


def dependency_audit(body: dict, now: dt.datetime) -> dict:
    from tools.idp.dora import digest

    sbom = body.get("sbom") or {}
    rep = body.get("dependencies") or {}
    comps = sbom.get("components") or []
    if not comps:
        return gate("not_applicable", reason="the SBOM lists no component to audit")
    if rep.get("sbom_sha256") != digest(sbom):
        return gate("fail", reason="the dependency report is not over this SBOM")
    t = when(rep.get("produced_at"))
    if t is None or t > now + dt.timedelta(minutes=5):
        return gate("unverified", reason="the dependency report's time is missing, unparseable or in the future")
    if now - t > dt.timedelta(days=DEPENDENCY_MAX_AGE_D):
        return gate("unverified", reason=f"the dependency report is older than {DEPENDENCY_MAX_AGE_D} days")
    names = {str(c.get("name")) for c in comps if isinstance(c, dict)}
    findings = rep.get("findings") or []
    strangers = sorted({str(f.get("package")) for f in findings} - names)
    if strangers:
        return gate("fail", reason="the report names a package the SBOM does not list", packages=strangers[:10])
    blocking = sorted(f["id"] for f in findings if f.get("severity") in BLOCKING_SEVERITIES)
    unrated = sorted(f["id"] for f in findings if f.get("severity") is None)
    if blocking:
        return gate("fail", reason="a finding of a blocking severity", findings=blocking[:20], tool=rep.get("tool"))
    if unrated:
        return gate("unverified", reason="a finding with no severity cannot be judged non-blocking",
                    findings=unrated[:20], tool=rep.get("tool"))
    return gate("pass", components=len(comps), findings=len(findings), tool=rep.get("tool"),
                note="CISA KEV is not checked here: ICDEV[OS] checks the report against its KEV feed")


def cui_marking_validation(d: Path, body: dict) -> dict:
    from tools.marketplace.asset_scanner import cui_banner_files

    marks = {k: body.get(k) for k in ("marking", "marking_ceiling", "egress_ceiling")}
    off = sorted(k for k, v in marks.items() if v not in ICOS_MARKINGS)
    if off:
        return gate("fail", reason="a marking is not one a package may carry (public or internal)", fields=off)
    banners = cui_banner_files(d)
    if banners:
        return gate("fail", reason="a CUI banner in a package marked below CUI", files=banners[:10])
    return gate("pass", **marks)


def _sbom_errors(doc, *, aibom: bool = False) -> list[str]:
    if not isinstance(doc, dict) or doc.get("bomFormat") != "CycloneDX" or doc.get("specVersion") not in ("1.6", "1.7"):
        return ["not a CycloneDX 1.6 or 1.7 document"]
    out = []
    comps = doc.get("components") or []
    if not isinstance(comps, list):
        return ["components is not a list"]
    for c in comps:
        if not isinstance(c, dict) or not all(str(c.get(k) or "").strip() for k in ("name", "version", "purl")):
            out.append(f"a component without a name, version and package URL: {str((c or {}).get('name'))[:60]}")
    if aibom and not any(isinstance(c, dict) and c.get("type") == "machine-learning-model"
                         for c in [(doc.get("metadata") or {}).get("component") or {}, *comps]):
        out.append("an AI-BOM names no machine-learning-model")
    return out


def sbom_generation(body: dict) -> dict:
    errs = _sbom_errors(body.get("sbom"))
    if body.get("aibom") is not None:
        errs += [f"AI-BOM: {e}" for e in _sbom_errors(body["aibom"], aibom=True)]
    if errs:
        return gate("fail", problems=errs[:10])
    return gate("pass", spec=(body.get("sbom") or {}).get("specVersion"),
                components=len((body.get("sbom") or {}).get("components") or []))


def supply_chain_provenance(body: dict) -> dict:
    pv = body.get("provenance") or {}
    if not (str(pv.get("source") or "").startswith("https://") and re.fullmatch(r"[0-9a-f]{40}", str(pv.get("commit")),
                                                                                  re.ASCII)):
        return gate("fail", reason="no source and commit the package was made from")
    unpinned = sorted(str(c.get("name")) for c in (body.get("sbom") or {}).get("components") or []
                      if isinstance(c, dict) and not EXACT_VERSION.fullmatch(str(c.get("version") or "")))
    if unpinned:
        # the scanner gate's own rule: an unpinned dependency is a warning, for a person to weigh
        return gate("warning", reason="not pinned to an exact version", packages=unpinned[:20], **pv)
    return gate("pass", **pv)


def digital_signature(body: dict, signer, pinned_kid: str | None) -> dict:
    """The publisher's key signs a trial card over this package and it verifies under that key (and the key is the
    one ICDEV[OS] pins, when the config names its kid). The real card is verified again after it is signed."""
    from tools.store_publisher import card as cmod
    from tools.store_publisher.package import package_digest

    if signer is None:
        return gate("unverified", reason="no publisher key: nothing was signed")
    try:
        spki = signer.spki()
        kid = cmod.kid(spki)
        if pinned_kid and kid != pinned_kid:
            return gate("fail", reason="the key is not the one ICDEV[OS] pins for this publisher", kid=kid)
        trial = cmod.sign_card({"name": body.get("id"), "metadata": {"trial": package_digest(body)}}, signer)
        cmod.verify_card(trial, {kid: spki})
    except cmod.CardError as exc:
        return gate("fail", reason=f"the key does not sign and verify ({exc.code})")
    return gate("pass", alg=cmod.ALG, kid=kid)


def run(body: dict, kind: str, *, signer, pinned_kid: str | None, now: dt.datetime) -> dict[str, dict]:
    """Every gate's result over ``body`` (the package without its card and its scan)."""
    d = materialise(body, kind)
    try:
        out = {}
        for name in SCANNER:
            try:
                out[name] = {
                    "sast_scan": lambda: sast_scan(d, kind, body),
                    "secret_detection": lambda: secret_detection(d),
                    "dependency_audit": lambda: dependency_audit(body, now),
                    "cui_marking_validation": lambda: cui_marking_validation(d, body),
                    "sbom_generation": lambda: sbom_generation(body),
                    "supply_chain_provenance": lambda: supply_chain_provenance(body),
                    "digital_signature": lambda: digital_signature(body, signer, pinned_kid),
                }[name]()
            except Exception as exc:  # noqa: BLE001 — a gate that cannot run is unverified, never a pass
                out[name] = gate("unverified", reason=f"the gate could not run ({type(exc).__name__})")
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


__all__ = ["materialise", "run", "when"]
