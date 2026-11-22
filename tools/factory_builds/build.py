# CUI // SP-CTI
"""Build ONE signed ``icos-artefact/v1`` for a configured target (icx-ent-03).

    artefact.json                      icos-artefact/v1 (ICDEV[OS]'s pinned contract); its SHA-256 is the artefact digest
    sbom.cdx.json                      CycloneDX 1.7 (the vendored, pinned schema): ICDEV's own SBOM generator for an
                                       image, agent or IaC bundle; the AI-BOM (aibom.py) for a model
    provenance.slsa.json               an in-toto Statement v1, SLSA v1.2 provenance (predicate https://slsa.dev/provenance/v1)
    vex.openvex.json                   OpenVEX 0.2.0 (the vendored, pinned schema), one statement per scanned vulnerability
    bundles/signature.sigstore.json    the artefact's signature (cosign's https://sigstore.dev/cosign/sign/v1)
    bundles/sbom.sigstore.json         the SBOM attestation (https://cyclonedx.org/bom)
    bundles/provenance.sigstore.json   the provenance attestation
    bundles/vex.sigstore.json          the VEX attestation (https://openvex.dev/ns, as cosign attest --type openvex)
    bundles/model.sigstore.json        a model's weights, OpenSSF model-signing
    oci/                               an OCI image layout: one referrer manifest per bundle, its subject the artefact

Every statement's subject is the artefact's digest. The factory's key signs (``tools/crypto/artefact_signer.py``: KMS,
the keyring, or keyless with a configured issuer), and every bundle is verified under that key before the build is
returned: a build that does not verify is refused, never recorded. ``produced_at`` is the build's one time (the
provenance's startedOn and finishedOn); a PEM key signs deterministically, so the same inputs give the same bytes.

The SBOM's vulnerabilities are the factory scanner's (the target's ``scan``); the VEX states each one --
``under_investigation`` unless the reviewed config states it. That VEX is a SUPPLIER's claim: at ICDEV[OS] it can add
a block, never lift one. OpenVEX needs at least one statement, so a build with nothing to state is refused
(``vex_unexpressible``) rather than given an invented statement.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess  # nosec B404 — one fixed argv: git rev-parse HEAD of the factory checkout
from functools import lru_cache
from pathlib import Path
from typing import Callable

from tools.factory_builds import aibom, sigstore
from tools.factory_builds import config as cmod
from tools.factory_builds.constants import (
    BUNDLES,
    CYCLONEDX_DIR,
    MANIFEST,
    MODEL_SIG,
    OPENVEX_DIR,
    PROVENANCE,
    SBOM,
    SBOM_MEDIA_TYPE,
    SBOM_PREDICATE,
    SCHEMA,
    SCHEMA_RELPATH,
    SCHEMA_SHA256,
    SIGN_PREDICATE,
    SLSA_PREDICATE,
    SUBJECT_MEDIA,
    VEX,
    VEX_MEDIA_TYPE,
    VEX_PREDICATE,
)

VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,49}", re.ASCII)
COMMIT = re.compile(r"[0-9a-f]{7,64}", re.ASCII)
SEVERITIES = ("critical", "high", "medium", "low", "info", "none", "unknown")


class BuildRefused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ── the pinned contracts ────────────────────────────────────────────────────


def _pinned(rel: str, sha: str) -> dict:
    raw = (cmod.BASE_DIR / rel).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise BuildRefused("schema_unpinned", f"{rel} is not the pinned contract: re-vendor and re-pin it, never edit it")
    return json.loads(raw)


def artefact_errors(doc) -> list[str]:
    import jsonschema

    v = jsonschema.Draft202012Validator(_pinned(SCHEMA_RELPATH, SCHEMA_SHA256))
    return sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"[:300]
                  for e in v.iter_errors(doc))


@lru_cache(maxsize=1)
def _cyclonedx():
    import jsonschema
    import yaml
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    src = cmod.BASE_DIR / CYCLONEDX_DIR
    pins = yaml.safe_load((src / "SOURCE.yaml").read_text(encoding="utf-8"))["files"]
    res, main = [], None
    for name, pin in pins.items():
        if not name.endswith(".schema.json"):
            continue
        raw = (src / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
            raise BuildRefused("schema_unpinned", f"{CYCLONEDX_DIR}/{name} does not match its pin")
        doc = json.loads(raw)
        res.append((pin["id"], Resource(contents=doc, specification=DRAFT7)))
        if name == "bom-1.7.schema.json":
            main = doc
    return jsonschema.Draft7Validator(main, registry=Registry().with_resources(res))


def sbom_errors(doc) -> list[str]:
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    out = [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"[:300]
           for e in _cyclonedx().iter_errors(doc)]
    if doc.get("specVersion") != "1.7":
        out.append("specVersion is not 1.7")
    return out


@lru_cache(maxsize=1)
def _openvex():
    import jsonschema
    import yaml

    src = cmod.BASE_DIR / OPENVEX_DIR
    pin = yaml.safe_load((src / "SOURCE.yaml").read_text(encoding="utf-8"))["files"]["openvex_json_schema.json"]
    raw = (src / "openvex_json_schema.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        raise BuildRefused("schema_unpinned", f"{OPENVEX_DIR}/openvex_json_schema.json does not match its pin")
    return jsonschema.Draft202012Validator(json.loads(raw), format_checker=jsonschema.FormatChecker())


def vex_errors(doc) -> list[str]:
    return [f"{'/'.join(str(p) for p in e.absolute_path) or 'document'}: {e.message}"[:300]
            for e in _openvex().iter_errors(doc)]


# ── the factory checkout ────────────────────────────────────────────────────


def factory_commit() -> str:
    """The factory checkout's HEAD: what built this. Unreadable is refused, never guessed."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cmod.BASE_DIR, capture_output=True, text=True,  # nosec
                             timeout=10, check=True).stdout.strip()
    except Exception as exc:
        raise BuildRefused("no_commit", f"the factory's commit could not be read ({type(exc).__name__})") from exc
    if not COMMIT.fullmatch(out):
        raise BuildRefused("no_commit", "the factory's commit is not a git commit id")
    return out


# ── the subject ─────────────────────────────────────────────────────────────


def image_subject(target: dict, cfg: dict) -> dict:
    """The one manifest of the target's OCI image layout, checked against its blob."""
    base = cmod.root(cfg)
    lim = int(cfg["limits"]["max_file_bytes"])
    lay = target["layout"]
    try:
        idx = json.loads(cmod.read_confined(base, f"{lay}/index.json", max_bytes=lim))
        ms = idx.get("manifests") if isinstance(idx, dict) else None
        if not isinstance(ms, list) or len(ms) != 1 or not isinstance(ms[0], dict):
            raise BuildRefused("subject", "the image layout must name exactly one manifest")
        d = ms[0]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(d.get("digest") or ""), re.ASCII):
            raise BuildRefused("subject", "the image manifest's digest is not sha256")
        blob = cmod.read_confined(base, f"{lay}/blobs/sha256/{d['digest'].split(':', 1)[1]}", max_bytes=lim)
    except (cmod.ConfigError, OSError, ValueError) as exc:
        raise BuildRefused("subject", f"the image layout cannot be read ({exc})"[:300]) from exc
    if sigstore.oci_digest(blob) != d["digest"] or len(blob) != int(d.get("size") or -1):
        raise BuildRefused("subject", "the image manifest is not the digest and size its layout names")
    return {"name": target["subject_name"], "digest": d["digest"],
            "mediaType": str(d.get("mediaType") or "application/vnd.oci.image.manifest.v1+json"), "size": len(blob)}


def package_subject(target: dict, cfg: dict) -> dict:
    try:
        raw = cmod.read_confined(cmod.root(cfg), target["package"], max_bytes=int(cfg["limits"]["max_file_bytes"]))
    except (cmod.ConfigError, OSError) as exc:
        raise BuildRefused("subject", f"the package cannot be read ({exc})"[:300]) from exc
    return {"name": target["subject_name"], "digest": sigstore.oci_digest(raw),
            "mediaType": SUBJECT_MEDIA[target["kind"]], "size": len(raw)}


# ── the scan and the VEX ────────────────────────────────────────────────────


def scan(target: dict, cfg: dict) -> list[dict]:
    if not target.get("scan"):
        return []
    try:
        doc = json.loads(cmod.read_confined(cmod.root(cfg), target["scan"],
                                            max_bytes=int(cfg["limits"]["max_file_bytes"])))
    except (cmod.ConfigError, OSError, ValueError, RecursionError) as exc:
        raise BuildRefused("scan", f"the scan cannot be read ({type(exc).__name__})") from exc
    if not isinstance(doc, list) or len(doc) > int(cfg["limits"]["max_vulnerabilities"]):
        raise BuildRefused("scan", "the scan is a list of at most max_vulnerabilities findings")
    out, seen = [], set()
    for f in doc:
        if not isinstance(f, dict) or not cmod.VULN_ID.fullmatch(str(f.get("id") or "")) \
                or not isinstance(f.get("component"), str) or str(f.get("severity")) not in SEVERITIES:
            raise BuildRefused("scan", "a scan finding is {id, component, severity} with an OpenVEX-safe id")
        if f["id"] in seen:
            raise BuildRefused("scan", f"{f['id']} is listed twice")
        seen.add(f["id"])
        out.append({"id": f["id"], "component": f["component"][:200], "severity": f["severity"]})
    return out


def _attach(sbom: dict, findings: list[dict]) -> None:
    comps = [c for c in sbom.get("components") or [] if isinstance(c, dict)]
    refs = {}
    for c in comps:
        for k in (c.get("name"), c.get("purl")):
            if k:
                refs.setdefault(str(k), c.get("bom-ref"))
    vulns = []
    for f in findings:
        ref = refs.get(f["component"])
        if not ref:
            raise BuildRefused("scan_mismatch", f"the scan names {f['component'][:80]}, which the SBOM does not list")
        vulns.append({"id": f["id"], "source": {"name": "ICDEV[IT] factory scan"},
                      "ratings": [{"severity": f["severity"]}], "affects": [{"ref": ref}]})
    if vulns:
        sbom["vulnerabilities"] = vulns


def openvex(target: dict, subject: dict, findings: list[dict], at: str) -> dict:
    if not findings:
        raise BuildRefused("vex_unexpressible", "OpenVEX holds at least one statement and the factory scan found no "
                                                "vulnerability to state: nothing is invented (ICDEV[OS] needs a VEX)")
    hexd = subject["digest"].split(":", 1)[1]
    stated = target.get("vex") or {}
    sts = []
    for f in findings:
        s = stated.get(f["id"]) or {"status": "under_investigation"}
        st = {"vulnerability": {"name": f["id"]}, "products": [{"@id": f"{subject['name']}@sha256:{hexd}"}],
              "status": s["status"]}
        if s["status"] == "not_affected":
            st["justification"] = s["justification"]
        if s["status"] == "affected":
            st["action_statement"] = str(s["action"])[:500]
        if s["status"] == "under_investigation":
            st["status_notes"] = "found by the factory scan; nobody has stated its impact yet"
        sts.append(st)
    return {"@context": "https://openvex.dev/ns/v0.2.0", "@id": f"urn:icdev-it:factory:vex:{hexd}",
            "author": "ICDEV[IT] factory", "timestamp": at, "version": 1, "statements": sts}


# ── the SBOM ────────────────────────────────────────────────────────────────


#: The dependency manifests and lockfiles the SBOM generator reads (tools/compliance/dependency_resolver.py and
#: sbom_generator's declared parsers), by name at the project's top level, plus two fixed subpaths. Nothing else of the
#: project is ever read: no installed environment (.venv), no source, no include.
MANIFESTS = ("requirements.txt", "pyproject.toml", "setup.cfg", "Pipfile", "Pipfile.lock", "uv.lock", "poetry.lock",
             "pdm.lock", "package.json", "package-lock.json", "yarn.lock", "go.mod", "go.sum", "Cargo.toml",
             "Cargo.lock", "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile", "packages.config",
             "packages.lock.json", "Gemfile", "Gemfile.lock")
MANIFEST_SUFFIXES = (".csproj",)
MANIFEST_DIRS = {"gradle/dependency-locks": ".lockfile", "obj": "project.assets.json"}
MAX_MANIFESTS = 200


def confined_manifests(target: dict, cfg: dict, dest: Path) -> dict[str, str]:
    """Copy the target project's manifests into ``dest`` (a fresh per-run directory), each read once through
    ``read_confined`` (under the root, no symlink, a regular file with one link, size-capped, O_NONBLOCK). Returns
    ``{relative path: sha256}`` of what was copied: the SBOM is resolved from THIS copy, never the project tree."""
    base = cmod.root(cfg)
    rel = target["sbom_project"]
    lim = int(cfg["limits"]["max_file_bytes"])
    try:
        cmod.confined_dir(base, rel)
        names = sorted(os.listdir(base / rel))
    except (cmod.ConfigError, OSError) as exc:
        raise BuildRefused("sbom", f"the SBOM project cannot be read ({exc})"[:300]) from exc
    wanted = [n for n in names if n in MANIFESTS or n.endswith(MANIFEST_SUFFIXES)]
    for sub, pick in MANIFEST_DIRS.items():
        try:
            cmod.confined_dir(base, f"{rel}/{sub}")
        except cmod.ConfigError:
            continue        # absent (or a symlink): nothing of it is read
        wanted += [f"{sub}/{n}" for n in sorted(os.listdir(base / rel / sub)) if n == pick or n.endswith(pick)]
    if len(wanted) > MAX_MANIFESTS:
        raise BuildRefused("sbom", f"the SBOM project holds more than {MAX_MANIFESTS} manifests")
    copied: dict[str, str] = {}
    for name in wanted:
        if not cmod.REL.fullmatch(name):
            continue        # a name that is not strict ASCII is never a manifest the generator reads
        try:
            raw = cmod.read_confined(base, f"{rel}/{name}", max_bytes=lim)
        except cmod.ConfigError as exc:
            raise BuildRefused("sbom", f"a manifest cannot be read ({exc})"[:300]) from exc
        out = dest / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        copied[name] = hashlib.sha256(raw).hexdigest()
    return copied


def software_sbom(target: dict, cfg: dict, subject: dict, version: str, at: str,
                  generate: Callable | None = None) -> tuple[dict, dict]:
    import shutil
    import tempfile

    from tools.compliance import sbom_generator

    work = Path(tempfile.mkdtemp(prefix="icdev-fb-sbom-"))
    try:
        copied = confined_manifests(target, cfg, work)
        doc, summary = (generate or sbom_generator.cyclonedx_document)(
            {"id": target["id"], "name": target["name"], "version": version, "classification": target["marking"]},
            work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    hexd = subject["digest"].split(":", 1)[1]
    meta = doc.setdefault("metadata", {})
    root = meta.get("component") or {}
    root.update({"type": "container" if target["kind"] == "image" else "application", "name": target["subject_name"],
                 "version": version, "hashes": [{"alg": "SHA-256", "content": hexd}]})
    meta["component"], meta["timestamp"] = root, at
    serial = hashlib.sha256(f"{hexd}:{version}".encode()).hexdigest()
    doc["serialNumber"] = f"urn:uuid:{serial[:8]}-{serial[8:12]}-4{serial[13:16]}-8{serial[17:20]}-{serial[20:32]}"
    return doc, {"ecosystems": [{k: e.get(k) for k in ("ecosystem", "complete", "method", "component_count")}
                                for e in summary.get("ecosystems") or []], "manifests": copied}


# ── the provenance ──────────────────────────────────────────────────────────


def provenance(subject: dict, target: dict, cfg: dict, *, commit: str, version: str, at: str, sbom_sha256: str,
               inputs: dict) -> dict:
    """SLSA v1.2 Build provenance (predicate type https://slsa.dev/provenance/v1)."""
    pred = {"buildDefinition": {"buildType": cfg["build_type"],
                                "externalParameters": {"commit": commit, "version": version, "target": target["id"],
                                                       "kind": target["kind"], "system_id": target["system_id"]},
                                "internalParameters": {"config_sha256": cfg.get("_digest", ""),
                                                       "sbom_describes": inputs.get("sbom_describes", ""),
                                                       "inputs": inputs.get("digests", {})},
                                "resolvedDependencies": [{"uri": f"git+https://icdev.example/icdev-it@{commit}",
                                                          "digest": {"gitCommit": commit}}]},
            "runDetails": {"builder": {"id": cfg["builder_id"]},
                           "metadata": {"invocationId": f"{target['id']}:{version}:{at}", "startedOn": at,
                                        "finishedOn": at},
                           "byproducts": [{"name": SBOM, "digest": {"sha256": sbom_sha256},
                                           "mediaType": SBOM_MEDIA_TYPE}]}}
    return sigstore.statement(_subj(subject), SLSA_PREDICATE, pred)


def _subj(subject: dict) -> list[dict]:
    return [{"name": subject["name"], "digest": {"sha256": subject["digest"].split(":", 1)[1]}}]


# ── one build ───────────────────────────────────────────────────────────────


def build(target: dict, cfg: dict, *, signer, version: str, at: str, commit: str | None = None,
          conn=None, generate: Callable | None = None, tlog: Callable | None = None) -> dict:
    """``{"files": {path: bytes}, "digest", "record"}`` -- or :class:`BuildRefused`."""
    if not VERSION.fullmatch(str(version or "")):
        raise BuildRefused("version", "a version is strict ASCII, like 1.4.0")
    commit = commit or factory_commit()
    kind = target["kind"]
    record: dict = {"target": target["id"], "kind": kind, "system_id": target["system_id"], "version": version,
                    "commit": commit, "produced_at": at, "signing": _signing_record(signer),
                    "transparency_log": {"mode": (cfg.get("transparency_log") or {}).get("mode", "off"),
                                         "entries": [],
                                         "note": "off: no transparency log was reached; the bundles verify offline "
                                                 "against a pinned key"}}
    model_sig = None
    if kind == "model":
        try:
            found = aibom.promoted_version(target["model"], conn)
            aibom.check_binding(found, target)
            res = aibom.weights(found["version"]["adapter_path"], cmod.root(cfg, "models_root"),
                                max_files=int(cfg["limits"]["max_files"]))
        except aibom.AibomError as exc:
            raise BuildRefused(exc.code, exc.message) from exc
        subject = {"name": target["subject_name"], "digest": "sha256:" + sigstore.model_root(res),
                   "mediaType": SUBJECT_MEDIA["model"], "size": 0}
        sbom = aibom.build(found, target, subject_digest=subject["digest"], version=version, at=at)
        model_sig = sigstore.sign_model(res, subject["name"], signer)
        v, p = found["version"], found["promotion"]
        record["promotion"] = {"model_version_id": v["id"], "model_name": v["model_name"], "version": v["version"],
                               "promotion_log_id": p["id"], "action": p["action"], "promoted_by": p.get("actor") or "",
                               "function": p.get("function_name") or "", "promoted_at": str(p.get("created_at") or ""),
                               "weights_files": len(res)}
        describes = "the fine-tuned model's AI-BOM, from the fine-tuning registry (tools/factory_builds/aibom.py)"
    else:
        subject = image_subject(target, cfg) if kind == "image" else package_subject(target, cfg)
        sbom, summary = software_sbom(target, cfg, subject, version, at, generate)
        record["sbom"] = summary
        manifests = summary["manifests"]
        describes = ("the dependencies tools/compliance/sbom_generator.py resolves from a confined copy of the target's "
                     "sbom_project manifests (named, with their digests, in inputs.manifests); not a scan of the "
                     "artefact's filesystem")
    if kind == "model":
        manifests = {}
    findings = scan(target, cfg)
    _attach(sbom, findings)
    errs = sbom_errors(sbom)
    if errs:
        raise BuildRefused("sbom_invalid", "the SBOM is not CycloneDX 1.7: " + "; ".join(errs[:3]))
    vex = openvex(target, subject, findings, at)
    errs = vex_errors(vex)
    if errs:
        raise BuildRefused("vex_invalid", "the VEX is not OpenVEX 0.2.0: " + "; ".join(errs[:3]))

    files: dict[str, bytes] = {SBOM: sigstore.text(sbom), VEX: sigstore.text(vex)}
    if model_sig is not None:
        files[MODEL_SIG] = model_sig
    prov = provenance(subject, target, cfg, commit=commit, version=version, at=at,
                      sbom_sha256=hashlib.sha256(files[SBOM]).hexdigest(),
                      inputs={"sbom_describes": describes,
                              "digests": {"scan": len(findings), "manifests": manifests}})
    files[PROVENANCE] = sigstore.text(prov)
    subj = _subj(subject)
    stmts = {"signature": sigstore.statement(subj, SIGN_PREDICATE, {}),
             "sbom-attestation": sigstore.statement(subj, SBOM_PREDICATE, sbom),
             "provenance-attestation": prov, "vex-attestation": sigstore.statement(subj, VEX_PREDICATE, vex)}
    signed: dict[str, dict] = {}
    for role, (path, _) in BUNDLES.items():
        signed[role] = sigstore.sign(stmts[role], signer)
    if (cfg.get("transparency_log") or {}).get("mode") == "rekor":
        record["transparency_log"] = _submit(cfg, signed, signer, tlog)
    for role, (path, _) in BUNDLES.items():
        files[path] = sigstore.text(signed[role])
    mt = {SBOM: SBOM_MEDIA_TYPE, PROVENANCE: "application/vnd.in-toto+json", VEX: VEX_MEDIA_TYPE}
    listed = [{"path": SBOM, "role": "sbom"}, {"path": PROVENANCE, "role": "provenance"}, {"path": VEX, "role": "vex"},
              *([{"path": MODEL_SIG, "role": "model-signature",
                  "predicateType": "https://model_signing/signature/v1.0"}] if model_sig is not None else []),
              *({"path": p, "role": r, "predicateType": pt} for r, (p, pt) in BUNDLES.items())]
    from tools.crypto import artefact_signer as asig

    manifest = {"schema": SCHEMA, "kind": kind, "system_id": target["system_id"], "name": target["name"],
                "version": version, "commit": commit, "produced_at": at, "builder": cfg["builder_id"],
                "subject": subject, "key": {"alg": asig.ALG, "id": asig.key_id(signer.spki())},
                "files": [{**f, "mediaType": mt.get(f["path"], "application/vnd.dev.sigstore.bundle.v0.3+json"),
                           "sha256": hashlib.sha256(files[f["path"]]).hexdigest(), "size": len(files[f["path"]])}
                          for f in listed]}
    errs = artefact_errors(manifest)
    if errs:
        raise BuildRefused("contract", f"the manifest does not meet {SCHEMA}: " + "; ".join(errs[:3]))
    files[MANIFEST] = sigstore.text(manifest)
    for p, b in sigstore.layout(subject, [(files[p], pt) for _, (p, pt) in BUNDLES.items()], at).items():
        files[f"oci/{p}"] = b
    problems = self_check(files, signer.spki())
    if problems:
        raise BuildRefused("self_check", "the build does not verify under its own key: " + "; ".join(problems[:3]))
    return {"files": files, "digest": "sha256:" + hashlib.sha256(files[MANIFEST]).hexdigest(), "record": record}


def _signing_record(signer) -> dict:
    from tools.crypto import artefact_signer as asig

    out = {"mode": getattr(signer, "mode", "key"), "key_id": asig.key_id(signer.spki()),
           "public_key": asig.pem(signer.spki()), "source": getattr(signer, "source", "")}
    if out["mode"] == "keyless":
        out["identity"] = dict(signer.identity)
        out["note"] = ("keyless: ICDEV[OS]'s release register verifies key-based bundles against keys a system's owner "
                       "pinned; it refuses this build until it pins certificate identities")
    return out


def _submit(cfg: dict, signed: dict, signer, tlog: Callable | None) -> dict:
    from tools.crypto import artefact_signer as asig

    url = str(cfg["transparency_log"]["url"])
    verifier = asig.pem(signer.spki()) if getattr(signer, "mode", "key") != "keyless" else "\n".join(signer.chain_pem[:1])
    import time

    # review L8: ONE deadline over every submission of this build, not one per bundle
    end = time.monotonic() + float(cfg["limits"]["deadline_seconds"])

    def left() -> float:
        return end - time.monotonic()

    submit = tlog or (lambda env, ver: asig.submit_dsse(url, env, ver, deadline_s=max(left(), 0.001)))
    entries = []
    for role, b in signed.items():
        if left() <= 0:
            raise BuildRefused("tlog", "the transparency log was configured and did not take every bundle within the "
                                       "build's one deadline")
        try:
            got = submit(b["dsseEnvelope"], verifier)
        except asig.SigningError as exc:
            raise BuildRefused("tlog", f"the transparency log was configured and could not take the {role} bundle: "
                                       f"{exc}") from exc
        b["verificationMaterial"]["tlogEntries"] = [got["entry"]]
        entries.append({"role": role, "uuid": got["uuid"], "log_index": got["entry"]["logIndex"]})
    return {"mode": "rekor", "url": url, "entries": entries,
            "note": "submitted; the log's signed entry timestamps are recorded, NOT verified here (no log key is pinned)"}


def self_check(files: dict[str, bytes], spki: bytes) -> list[str]:
    """Every listed file at its digest, each bundle verifying under the build's key over the subject with its listed
    predicate, and the signed SBOM, provenance and VEX the files beside them."""
    try:
        m = json.loads(files[MANIFEST])
    except (KeyError, ValueError):
        return ["no manifest"]
    why = []
    want = m["subject"]["digest"].split(":", 1)[1]
    for f in m["files"]:
        if hashlib.sha256(files.get(f["path"], b"\0")).hexdigest() != f["sha256"]:
            why.append(f"{f['path']} is not at its digest")
    for role, (path, pt) in BUNDLES.items():
        try:
            st = sigstore.open_bundle(json.loads(files[path]), spki)
        except (sigstore.BundleError, KeyError, ValueError) as exc:
            why.append(f"{path}: {exc}")
            continue
        if st.get("predicateType") != pt or [s["digest"]["sha256"] for s in st["subject"]] != [want]:
            why.append(f"{path} is not about the artefact with {pt}")
        if role == "sbom-attestation" and st["predicate"] != json.loads(files[SBOM]):
            why.append("the signed SBOM is not sbom.cdx.json")
        if role == "vex-attestation" and st["predicate"] != json.loads(files[VEX]):
            why.append("the signed VEX is not vex.openvex.json")
        if role == "provenance-attestation" and st != json.loads(files[PROVENANCE]):
            why.append("the signed provenance is not provenance.slsa.json")
    if MODEL_SIG in files:
        try:
            st = sigstore.open_bundle(json.loads(files[MODEL_SIG]), spki, model_hint=True)
            res = st["predicate"]["resources"]
            if sigstore.model_root(res) != want or [s["digest"]["sha256"] for s in st["subject"]] != [want]:
                why.append("the model signature does not sign these weights")
        except (sigstore.BundleError, KeyError, ValueError, TypeError) as exc:
            why.append(f"{MODEL_SIG}: {exc}")
    return why


def digest_of(files: dict[str, bytes]) -> str:
    return "sha256:" + hashlib.sha256(files[MANIFEST]).hexdigest()


__all__ = ["BuildRefused", "artefact_errors", "build", "digest_of", "factory_commit", "openvex", "provenance",
           "sbom_errors", "self_check", "vex_errors"]
