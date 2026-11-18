# CUI // SP-CTI
"""ICDEV[IT]'s own AI-BOM, in SPDX 3.0.1 and CycloneDX 1.7, for ICDEV[OS]; published by a person (osx-cop-02).

ICDEV[IT] runs AI components of its own: the router configurations that decide where a prompt goes, the models those
route to and the models it fine-tuned and registered itself, the datasets and gold sets it trains and judges them on,
and its prompt templates. This module describes them, deterministically, as ONE AI bill of materials in TWO public
standards at once (:func:`build`):

* an **SPDX 3.0.1** JSON-LD document using the AI and Dataset profiles: a model is an ``ai_AIPackage``, a dataset or a
  gold set a ``dataset_DatasetPackage``, a router config or a prompt a ``software_File``; each verified by its SHA-256;
* a **CycloneDX 1.7** ML-BOM: a model is a ``machine-learning-model`` component with a model card, a dataset, gold set,
  router config or prompt a ``data`` component; each with its SHA-256.

Both are validated against the OFFICIAL published schemas, vendored unmodified with their licences
(``context/standards/spdx/3.0.1``, ``context/standards/cyclonedx/1.7``), each refused unless it is the pinned file. A
prompt is carried by its path and digest, never its text; a model by its declared router entry (an external model: the
digest of that entry) or its registered adapter hash (a model ICDEV[IT] fine-tuned); a dataset by its content hash.

The two documents travel in one ``icos-aibom/v1`` bundle (schema ``context/icos/aibom.schema.json``, owned by ICDEV[OS]
and vendored here verbatim, pinned by SHA-256). ``content_digest`` is SHA-256 over the canonical JSON (keys sorted,
separators ``,`` and ``:``) of the bundle without ``content_digest``, ``publication`` and ``produced_at``: ICDEV[OS]
recomputes it. Deterministic given its inputs: the same files, registry rows and ``as_of`` give the same digest (the
documents' own timestamps are the ``as_of`` date, never the clock).

**Publishing is a person's decision** (``decl-wft-aibom-publish``). Anyone (or an agent) PREPARES the AI-BOM, which
records it, at its digest, in the append-only, hash-chained ``aibom_export_events``; a person who did not prepare it
PUBLISHES it or refuses, with a reason, bound to that digest. Only then does the bundle carry its ``publication``.
Nothing publishes itself.

CLI: ``tools/compliance/aibom_export.py``. Page: ``/ai-bom/``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

import yaml  # noqa: E402

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)
CONFIG_RELPATH = "args/aibom_export.yaml"
E2E_ENV = "ICDEV_AIBOM_SOURCES_E2E"
SCHEMA = "icos-aibom/v1"
SCHEMA_RELPATH = "context/icos/aibom.schema.json"
#: ICDEV[OS]'s published schema (contracts/schemas/aibom.schema.json, contract SCHEMA_CONTRACT), vendored verbatim.
#: The file digest is what this module refuses a changed file by; the BODY digest (the canonical JSON without
#: ``x-icos-contract``) is what a bundle pins, so a contract release that does not change it keeps pins valid.
SCHEMA_SHA256 = "65f11f2e90781f3771e91e1572b16941f7a7eb2ddc7ea4db33d8346f5068f107"
SCHEMA_BODY_SHA256 = "7ee94d35c87a301ab45611f3b7a667c9a2c2041c0ea5d23d2fe6fdbcbc2d9e4d"
SCHEMA_CONTRACT = "1.195.0"
PRODUCER = "icdev-it/aibom_export"
SYSTEM = "icdev-it"
SYSTEM_NAME = "ICDEV[IT]"
CLASSIFICATION = "CUI // SP-CTI"
TABLE = "aibom_export_events"
GENESIS = "0" * 64

TEMPLATE = "decl-wft-aibom-publish"
SURFACE = "aibom.aibom_api_publish"

#: The official schemas, vendored unmodified (each directory's NOTICE.md names source, tag and licence).
SPDX_VERSION = "3.0.1"
SPDX_SCHEMA_RELPATH = "context/standards/spdx/3.0.1/spdx-json-schema.json"
SPDX_SCHEMA_SHA256 = "582c64e809d5b3ef9bd0c4de13a32391b47b0284a3e8d199569fb96f649234b1"
SPDX_CONTEXT = "https://spdx.org/rdf/3.0.1/spdx-context.jsonld"
CDX_VERSION = "1.7"
CDX_DIR_RELPATH = "context/standards/cyclonedx/1.7"
CDX_SCHEMAS = {
    "bom-1.7.schema.json": "73308edec3ab2d38bfffd993e96a042b594314143b6971a6e9ed98bbb6bd76ce",
    "spdx.schema.json": "4b345e2329f209f34e960ae2a8e7cb46a166907e6a45e94978565925dc47b359",
    "jsf-0.82.schema.json": "8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae",
    "cryptography-defs.schema.json": "027b059a729a06d591bac79a584ef04f83fc32d91a826fdba6ad3c98a10e5b44",
}

KINDS = ("router-config", "model", "dataset", "gold-set", "prompt")
NAME_RE = re.compile(r"^[A-Za-z0-9._/@+-]{1,200}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ID_PREFIX = "urn:icdev:aibom:component:"
SPDX_TYPE = {"router-config": "software_File", "model": "ai_AIPackage", "dataset": "dataset_DatasetPackage",
             "gold-set": "dataset_DatasetPackage", "prompt": "software_File"}
SPDX_PURPOSE = {"router-config": "configuration", "model": "model", "dataset": "data", "gold-set": "data",
                "prompt": "configuration"}
CDX_TYPE = {"router-config": "data", "model": "machine-learning-model", "dataset": "data", "gold-set": "data",
            "prompt": "data"}
CDX_DATA_TYPE = {"router-config": "configuration", "dataset": "dataset", "gold-set": "dataset", "prompt": "definition"}

EVENTS = ("prepared", "published", "refused")
ACTOR_KINDS = ("person", "agent", "bootstrap")
_COLS = ("event_id", "system", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
         "content_digest", "bundle_json", "detail_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in _COLS if c != "row_hash")


class AIBOMRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def canonical(value: Any) -> str:
    """ICDEV[OS]'s canonical JSON (``icdev_os/models/aibommodel.canonical``): keys sorted, no whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def content_digest(bundle: dict) -> str:
    """SHA-256 of the bundle WITHOUT ``content_digest``, ``publication`` and ``produced_at``; ICDEV[OS] recomputes it."""
    return digest({k: v for k, v in bundle.items() if k not in ("content_digest", "publication", "produced_at")})


def body_digest(schema_doc: dict) -> str:
    """The pin: SHA-256 of the schema's canonical JSON without ``x-icos-contract``."""
    return digest({k: v for k, v in schema_doc.items() if k != "x-icos-contract"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _iso(value: Any) -> Optional[str]:
    """An ISO 8601 UTC time (``...Z``) from a stored timestamp, or None when there is none."""
    text = _text(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_of(value: Any) -> str:
    text = _text(value) or datetime.now(timezone.utc).date().isoformat()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    try:
        if m is None:
            raise ValueError(text)
        return date.fromisoformat(m.group(1)).isoformat()
    except ValueError:
        raise AIBOMRefused(400, "as_of", f"as_of must be a date, YYYY-MM-DD, not {text!r}") from None


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("icdev")
    except Exception:  # noqa: BLE001 — an unpackaged checkout still says what it is
        return "unpackaged"


# ── configuration and schemas ────────────────────────────────────────────────


def config_path() -> Path:
    """The declared AI components: ``args/aibom_export.yaml``, or (tests, e2e) a file under tests/ named by
    ``ICDEV_AIBOM_SOURCES_E2E``. Any other path is refused, never followed."""
    alt = _text(os.environ.get(E2E_ENV))
    if not alt:
        return BASE_DIR / CONFIG_RELPATH
    p = Path(alt)
    p = (p if p.is_absolute() else BASE_DIR / p).resolve()
    if not p.is_relative_to((BASE_DIR / "tests").resolve()) or p.suffix != ".yaml" or not p.is_file():
        raise AIBOMRefused(500, "config", f"{E2E_ENV} names {alt!r}: a .yaml file under tests/, or nothing")
    return p


def _rel_list(doc: dict, key: str) -> list[str]:
    out = []
    for v in doc.get(key) or []:
        t = _text(v)
        if not t or t.startswith("/") or ".." in Path(t).parts:
            raise AIBOMRefused(500, "config", f"{key} entry {v!r} is not a path inside the repository")
        out.append(t)
    return out


def load_config(path: Optional[Path] = None) -> dict:
    p = path or config_path()
    raw = p.read_bytes()
    doc = yaml.safe_load(raw) or {}
    return {"router_configs": _rel_list(doc, "router_configs"), "models_from_router": bool(doc.get("models_from_router")),
            "registry": bool(doc.get("registry")), "datasets": _rel_list(doc, "datasets"),
            "gold_sets": _rel_list(doc, "gold_sets"), "prompts": _rel_list(doc, "prompts"),
            "publish": doc.get("publish") or {}, "_sha256": hashlib.sha256(raw).hexdigest(),
            "_path": p.relative_to(BASE_DIR).as_posix() if p.is_relative_to(BASE_DIR) else str(p)}


def _pinned(relpath: str, sha: str) -> dict:
    raw = (BASE_DIR / relpath).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != sha:
        raise AIBOMRefused(500, "schema", f"{relpath} is not the pinned schema (sha256 {got}, pinned {sha})")
    return json.loads(raw)


def schema() -> dict:
    """The vendored ICOS schema, refused unless it is the pinned file and its body the pinned body."""
    doc = _pinned(SCHEMA_RELPATH, SCHEMA_SHA256)
    if body_digest(doc) != SCHEMA_BODY_SHA256:
        raise AIBOMRefused(500, "schema", f"{SCHEMA_RELPATH}'s body is not the pinned body {SCHEMA_BODY_SHA256}")
    return doc


@lru_cache(maxsize=1)
def _spdx_validator():
    import jsonschema

    return jsonschema.Draft202012Validator(_pinned(SPDX_SCHEMA_RELPATH, SPDX_SCHEMA_SHA256))


@lru_cache(maxsize=1)
def _cdx_validator():
    """CycloneDX's schema refers to its siblings by relative id; each is resolved from the vendored file, never fetched."""
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    docs = {n: _pinned(f"{CDX_DIR_RELPATH}/{n}", sha) for n, sha in CDX_SCHEMAS.items()}
    reg = Registry().with_resources((d["$id"], Resource.from_contents(d, default_specification=DRAFT7))
                                    for d in docs.values())
    return jsonschema.Draft7Validator(docs["bom-1.7.schema.json"], registry=reg)


def _errs(validator, doc) -> list[str]:
    return sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"[:300]
                  for e in validator.iter_errors(doc))


def spdx_errors(doc: Any) -> list[str]:
    """Why a document does not meet SPDX 3.0.1's published JSON schema; empty when it does."""
    return _errs(_spdx_validator(), doc) if isinstance(doc, dict) else ["(root): not a JSON object"]


def cyclonedx_errors(doc: Any) -> list[str]:
    """Why a BOM does not meet CycloneDX 1.7's published JSON schema (or is not a 1.7 BOM); empty when it does."""
    if not isinstance(doc, dict):
        return ["(root): not a JSON object"]
    errs = _errs(_cdx_validator(), doc)
    if doc.get("specVersion") != CDX_VERSION:
        errs.append(f"specVersion: {doc.get('specVersion')!r} is not {CDX_VERSION!r}")
    return errs


def validate(bundle: dict) -> list[str]:
    """The bundle's errors: the ICOS envelope schema, then each document against its OFFICIAL schema, then its digest,
    pin and publication. Empty: ICDEV[OS] would take it."""
    import jsonschema

    v = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())
    errors = _errs(v, bundle)
    if errors:
        return errors
    errors += [f"spdx/{e}" for e in spdx_errors(bundle.get("spdx"))]
    errors += [f"cyclonedx/{e}" for e in cyclonedx_errors(bundle.get("cyclonedx"))]
    if errors:
        return errors
    if bundle.get("content_digest") != content_digest(bundle):
        errors.append("content_digest is not the digest of the bundle's content")
    if (bundle.get("schema_pin") or {}).get("body_sha256") != SCHEMA_BODY_SHA256:
        errors.append("schema_pin is not the pinned contract's body")
    pub = bundle.get("publication")
    if pub and pub.get("content_digest") != bundle["content_digest"]:
        errors.append("the publication was taken at another digest")
    return errors


# ── the AI components ────────────────────────────────────────────────────────


def _file_sha(relpath: str) -> str:
    return hashlib.sha256((BASE_DIR / relpath).read_bytes()).hexdigest()


def _component(kind: str, name: str, digest_: str, *, supplier: Optional[str] = None,
               purpose: Optional[str] = None) -> dict:
    if not NAME_RE.match(name):
        raise AIBOMRefused(422, "component", f"{kind} {name!r} cannot be named in an AI-BOM ({NAME_RE.pattern})")
    if not HEX_RE.match(digest_):
        raise AIBOMRefused(422, "component", f"{kind} {name} has no SHA-256 digest")
    c = {"id": f"{kind}:{name}", "kind": kind, "name": name, "digest": digest_}
    if supplier:
        c["supplier"] = supplier[:120]
    if purpose:
        c["purpose"] = purpose[:300]
    return c


def _files(relpaths: Iterable[str], suffixes: tuple[str, ...] = ()) -> list[str]:
    out = []
    for rel in relpaths:
        p = BASE_DIR / rel
        if p.is_dir():
            out += sorted(f.relative_to(BASE_DIR).as_posix() for f in p.rglob("*")
                          if f.is_file() and (not suffixes or f.suffix in suffixes))
        elif p.is_file():
            out.append(rel)
        else:
            raise AIBOMRefused(422, "missing", f"{rel} is declared in the AI-BOM config and does not exist")
    return out


def _registry_rows(conn) -> tuple[list[dict], list[dict], list[dict]]:
    """Registered models (``ft_model_versions``) and datasets (``ft_datasets``), and what has no digest (left out, and
    said). Archived rows are not components."""
    def rows(sql: str) -> list[dict]:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(r) if isinstance(r, dict) else dict(zip(cols, tuple(r))) for r in cur.fetchall()]

    models = rows("SELECT model_name, version, base_model, adapter_hash, status FROM ft_model_versions "
                  "WHERE status <> 'archived' ORDER BY model_name, version")
    datasets = rows("SELECT id, name, purpose, content_hash, status FROM ft_datasets "
                    "WHERE status <> 'archived' ORDER BY id")
    out_m, out_d, omitted = [], [], []
    for m in models:
        h = _text(m.get("adapter_hash")).lower()
        name = f"ft/{_text(m['model_name'])}@v{int(m.get('version') or 1)}"
        if HEX_RE.match(h) and NAME_RE.match(name):
            out_m.append(_component("model", name, h, supplier=SYSTEM_NAME,
                                    purpose=f"fine-tuned from {_text(m.get('base_model'))} ({_text(m.get('status'))})"))
        else:
            omitted.append({"kind": "model", "name": name, "why": "no SHA-256 adapter hash is registered"})
    for d in datasets:
        h = _text(d.get("content_hash")).lower()
        name = _text(d["id"])
        if HEX_RE.match(h) and NAME_RE.match(name):
            out_d.append(_component("dataset", name, h, purpose=f"{_text(d.get('name'))} ({_text(d.get('purpose'))})"))
        else:
            omitted.append({"kind": "dataset", "name": name, "why": "no SHA-256 content hash is registered"})
    return out_m, out_d, omitted


def _connection():
    """The platform database without the row-security predicate (the ledger has no label column: it is the
    platform's record of what it released, as coe_conformance_events is)."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def inventory(cfg: Optional[dict] = None, conn=None) -> dict:
    """Every declared AI component with its digest, sorted by id, and what was left out and why."""
    cfg = cfg or load_config()
    comps: list[dict] = []
    omitted: list[dict] = []
    for rel in cfg["router_configs"]:
        comps.append(_component("router-config", rel, _file_sha(_files([rel])[0]),
                                purpose="routing of prompts to models (the governed router's configuration)"))
        if cfg["models_from_router"]:
            doc = yaml.safe_load((BASE_DIR / rel).read_text(encoding="utf-8")) or {}
            for name, entry in sorted((doc.get("models") or {}).items()):
                if not isinstance(entry, dict):
                    continue
                comps.append(_component("model", str(name), digest(entry), supplier=_text(entry.get("provider")),
                                        purpose=f"an external model the router may send a prompt to ({rel})"))
    if cfg["registry"]:
        own = conn is None
        conn = conn or _connection()
        try:
            m, d, om = _registry_rows(conn)
        except Exception as exc:  # noqa: BLE001 — an unreadable registry is said, never guessed at
            raise AIBOMRefused(503, "registry", f"the model and dataset registry could not be read: {exc}") from exc
        finally:
            if own:
                conn.close()
        comps += m + d
        omitted += om
    for rel in _files(cfg["datasets"]):
        comps.append(_component("dataset", rel, _file_sha(rel), purpose="a dataset file"))
    for rel in _files(cfg["gold_sets"]):
        comps.append(_component("gold-set", rel, _file_sha(rel), purpose="a gold set models and retrieval are judged on"))
    for rel in _files(cfg["prompts"], (".md", ".txt", ".yaml", ".j2")):
        comps.append(_component("prompt", rel, _file_sha(rel), purpose="a prompt template, carried by its digest only"))
    seen: dict[str, dict] = {}
    for c in comps:
        if c["id"] in seen:
            raise AIBOMRefused(422, "duplicate", f"{c['id']} is declared twice")
        seen[c["id"]] = c
    return {"components": sorted(comps, key=lambda c: c["id"]), "omitted": omitted}


# ── the two documents ────────────────────────────────────────────────────────


def _agent_id(name: str) -> str:
    return "urn:icdev:aibom:agent:" + re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()


def spdx_document(components: list[dict], *, as_of: str, version: str) -> dict:
    """The SPDX 3.0.1 JSON-LD document (AI, Dataset and Software profiles). Its time is the ``as_of`` date."""
    created = f"{as_of}T00:00:00Z"
    ci = "_:creationinfo"
    me = _agent_id(SYSTEM_NAME)
    root = "urn:icdev:aibom:system:icdev-it"
    graph: list[dict] = [
        {"type": "CreationInfo", "@id": ci, "specVersion": SPDX_VERSION, "created": created, "createdBy": [me],
         "comment": f"generated by {PRODUCER} {version}"},
        {"type": "Organization", "spdxId": me, "name": SYSTEM_NAME, "creationInfo": ci},
    ]
    suppliers = sorted({c["supplier"] for c in components if c.get("supplier") and c["supplier"] != SYSTEM_NAME})
    graph += [{"type": "Organization", "spdxId": _agent_id(s), "name": s, "creationInfo": ci} for s in suppliers]
    elements = []
    for c in components:
        e = {"type": SPDX_TYPE[c["kind"]], "spdxId": ID_PREFIX + c["id"], "name": c["name"], "creationInfo": ci,
             "software_primaryPurpose": SPDX_PURPOSE[c["kind"]],
             "verifiedUsing": [{"type": "Hash", "algorithm": "sha256", "hashValue": c["digest"]}]}
        if c.get("purpose"):
            e["summary"] = c["purpose"]
        if c.get("supplier"):
            e["suppliedBy"] = _agent_id(c["supplier"])
        if c["kind"] in ("dataset", "gold-set"):
            e["dataset_datasetType"] = ["structured" if c["kind"] == "gold-set" else "text"]
        if c["kind"] == "model":
            e["ai_informationAboutApplication"] = c.get("purpose") or "a model ICDEV[IT] uses"
        elements.append(e)
    ids = [e["spdxId"] for e in elements]
    graph.append({"type": "software_Package", "spdxId": root, "name": f"{SYSTEM_NAME} AI components",
                  "creationInfo": ci, "software_primaryPurpose": "application", "software_packageVersion": version})
    graph += elements
    graph.append({"type": "Relationship", "spdxId": "urn:icdev:aibom:relationship:contains", "creationInfo": ci,
                  "from": root, "relationshipType": "contains", "to": ids})
    graph.append({"type": "SpdxDocument", "spdxId": "urn:icdev:aibom:document", "creationInfo": ci,
                  "name": f"{SYSTEM_NAME} AI-BOM as of {as_of}", "profileConformance": ["core", "software", "ai", "dataset"],
                  "rootElement": [root], "element": [me, *[_agent_id(s) for s in suppliers], root, *ids,
                                                     "urn:icdev:aibom:relationship:contains"]})
    return {"@context": SPDX_CONTEXT, "@graph": graph}


def cyclonedx_bom(components: list[dict], *, as_of: str, version: str) -> dict:
    """The CycloneDX 1.7 ML-BOM. Its serial number is derived from the components, its timestamp is the ``as_of``
    date: the same components give the same BOM."""
    out = []
    for c in components:
        x: dict = {"type": CDX_TYPE[c["kind"]], "bom-ref": c["id"], "name": c["name"],
                   "hashes": [{"alg": "SHA-256", "content": c["digest"]}]}
        if c.get("supplier"):
            x["supplier"] = {"name": c["supplier"]}
        if c.get("purpose"):
            x["description"] = c["purpose"]
        if c["kind"] == "model":
            x["modelCard"] = {"considerations": {"useCases": [c.get("purpose") or "a model ICDEV[IT] uses"]}}
            if c["name"].startswith("ft/"):
                x["modelCard"]["modelParameters"] = {"approach": {"type": "supervised"}}
        else:
            x["data"] = [{"type": CDX_DATA_TYPE[c["kind"]], "name": c["name"]}]
        out.append(x)
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "urn:icdev:aibom:" + digest([[c["id"], c["digest"]] for c in components]))
    return {"bomFormat": "CycloneDX", "specVersion": CDX_VERSION, "serialNumber": f"urn:uuid:{serial}", "version": 1,
            "metadata": {"timestamp": f"{as_of}T00:00:00Z",
                         "tools": {"components": [{"type": "application", "name": PRODUCER, "version": version}]},
                         "component": {"type": "application", "bom-ref": f"system:{SYSTEM}", "name": SYSTEM_NAME,
                                       "version": version}},
            "components": out,
            "dependencies": [{"ref": f"system:{SYSTEM}", "dependsOn": [c["id"] for c in components]}]}


def build(*, as_of: Optional[str] = None, at: Optional[str] = None, cfg: Optional[dict] = None, conn=None) -> dict:
    """The ``icos-aibom/v1`` bundle of ICDEV[IT]'s declared AI components. A draft: ``publication`` is null. Validated
    against the pinned ICOS schema and both official schemas before it is returned."""
    cfg = cfg or load_config()
    inv = inventory(cfg, conn)
    comps = inv["components"]
    if not comps:
        raise AIBOMRefused(422, "empty", f"{cfg['_path']} declares no AI component: nothing to describe")
    if at and _iso(at) is None:
        raise AIBOMRefused(400, "at", f"--at must be an ISO 8601 time, not {at!r}")
    day = _as_of(as_of)
    version = _version()
    b = {
        "schema": SCHEMA,
        "schema_pin": {"contract": SCHEMA_CONTRACT, "body_sha256": SCHEMA_BODY_SHA256},
        "producer": {"system": SYSTEM, "tool": PRODUCER, "version": version},
        "produced_at": _iso(at) if at else _iso(_now()),
        "classification": CLASSIFICATION,
        "as_of": day,
        "system": SYSTEM_NAME,
        "standards": {"spdx": SPDX_VERSION, "cyclonedx": CDX_VERSION},
        "components": comps,
        "spdx": spdx_document(comps, as_of=day, version=version),
        "cyclonedx": cyclonedx_bom(comps, as_of=day, version=version),
        "summary": {"router_configs": sum(c["kind"] == "router-config" for c in comps),
                    "models": sum(c["kind"] == "model" for c in comps),
                    "datasets": sum(c["kind"] == "dataset" for c in comps),
                    "gold_sets": sum(c["kind"] == "gold-set" for c in comps),
                    "prompts": sum(c["kind"] == "prompt" for c in comps)},
        "publication": None,
    }
    b["content_digest"] = content_digest(b)
    errs = validate(b)
    if errs:
        raise AIBOMRefused(422, "invalid", f"the AI-BOM does not meet its schemas: {'; '.join(errs[:3])}")
    return b


# ── the ledger: prepared, then a person publishes ────────────────────────────


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def read_events(conn=None) -> list[dict]:
    own = conn is None
    conn = conn or _connection()
    try:
        sql = f"SELECT {', '.join(_COLS)} FROM {TABLE} WHERE system = %s"  # nosec B608 — fixed table and columns
        rows = conn.execute(sql + " ORDER BY created_at, event_id", (SYSTEM,)).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(r) if isinstance(r, dict) else dict(zip(_COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """The rows in hash-chain order (``ordered``), plus ``problems`` (a row that does not verify or is off the chain)."""
    items = list(rows)
    nxt = {str(r["prev_hash"]): r for r in items}
    ordered, prev, problems = [], GENESIS, []
    while prev in nxt:
        r = nxt.pop(prev)
        ordered.append(r)
        prev = str(r["row_hash"])
    for r in items:
        if _row_hash(r) != r["row_hash"]:
            problems.append({"event_id": r["event_id"], "problem": "row hash does not match its content"})
        elif r["event"] == "prepared":
            try:
                if content_digest(json.loads(r["bundle_json"])) != r["content_digest"]:
                    raise ValueError
            except (ValueError, TypeError):
                problems.append({"event_id": r["event_id"], "problem": "the bundle does not match its digest"})
    for r in nxt.values():
        problems.append({"event_id": r["event_id"], "problem": "not on the AI-BOM's hash chain"})
    return {"ordered": ordered, "problems": problems}


def _load_json(text: Any, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _detail(row: dict) -> dict:
    return _load_json(row.get("detail_json"), {}) or {}


def replay(ordered: list[dict]) -> dict:
    """The ledger state: the latest prepared AI-BOM, whether a person decided it, the latest published."""
    prepared = published = None
    history = []
    for r in ordered:
        det = _detail(r)
        h = {k: r[k] for k in ("event_id", "event", "actor", "actor_kind", "actor_role", "step_id", "reason",
                               "content_digest", "created_at", "row_hash", "prev_hash")} | {"detail": det}
        history.append(h)
        if r["event"] == "prepared":
            prepared = {**h, "decided": None}
        elif prepared and det.get("prepared_event") == prepared["event_id"]:
            prepared["decided"] = h
            if r["event"] == "published":
                published = {**h, "prepared_event": prepared["event_id"], "prepared_by": prepared["actor"]}
    return {"prepared": prepared, "published": published, "history": history}


def _state(conn) -> tuple[dict, list[dict], list[dict]]:
    ch = chain(read_events(conn))
    return replay(ch["ordered"]), ch["problems"], ch["ordered"]


def _head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def _append(conn, *, event: str, actor: str, actor_kind: str, content: str, prev_hash: str,
            bundle: Optional[dict] = None, actor_role: str = "", step_id: str = "", reason: str = "",
            detail: Optional[dict] = None) -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    row = {"event_id": f"aib-{uuid.uuid4().hex[:16]}", "system": SYSTEM, "event": event, "actor": actor,
           "actor_kind": actor_kind, "actor_role": actor_role or "", "step_id": step_id or "",
           "reason": reason or "", "content_digest": content,
           "bundle_json": canonical(bundle) if bundle is not None else "",
           "detail_json": canonical(detail or {}), "prev_hash": prev_hash, "created_at": _now()}
    row["row_hash"] = _row_hash(row)
    try:
        conn.execute(f"INSERT INTO {TABLE} ({', '.join(_COLS)}) VALUES ({', '.join(['%s'] * len(_COLS))})",
                     tuple(row[c] for c in _COLS))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        except Exception:  # noqa: BLE001
            pass
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise AIBOMRefused(409, "concurrent", "the AI-BOM changed while you were deciding: reload it") from exc
        raise AIBOMRefused(503, "store_unavailable",
                           f"the AI-BOM export ledger could not be written, so nothing changed ({exc})") from exc
    return row


def _actor_kind(actor: dict) -> str:
    if _text(actor.get("id")).startswith("bootstrap:"):
        return "bootstrap"
    if actor.get("role") == "service" or _text(actor.get("id")).startswith("agent:"):
        return "agent"
    return "person"


def prepare(actor: Optional[dict], *, as_of: Optional[str] = None, note: str = "", conn=None,
            bundle: Optional[dict] = None) -> dict:
    """Build the AI-BOM and record it, at its digest, as PREPARED. Decides nothing.

    Anyone signed in, or an agent (recorded as one). A person who did not prepare it publishes it or refuses.
    """
    if not isinstance(actor, dict) or not actor.get("id"):
        raise AIBOMRefused(401, "no_actor", "preparing the AI-BOM needs a signed-in person or a named agent")
    own = conn is None
    conn = conn or _connection()
    try:
        b = bundle if bundle is not None else build(as_of=as_of, conn=conn)
        _, problems, ordered = _state(conn)
        if problems:
            raise AIBOMRefused(409, "chain_broken", f"the AI-BOM ledger does not verify: {problems[0]['problem']}")
        row = _append(conn, event="prepared", actor=_text(actor["id"]), actor_kind=_actor_kind(actor),
                      actor_role=_text(actor.get("role")), content=b["content_digest"], bundle=b, reason=note,
                      detail={"as_of": b["as_of"], "summary": b["summary"]}, prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    return {"system": SYSTEM, "event_id": row["event_id"], "content_digest": b["content_digest"],
            "prepared_by": row["actor"], "actor_kind": row["actor_kind"], "as_of": b["as_of"],
            "summary": b["summary"], "template": TEMPLATE,
            "next": "a person who did not prepare it publishes it or refuses, on /ai-bom/"}


def builders(state: dict) -> dict[str, str]:
    """Who may not publish the prepared AI-BOM (``separate_from: builders``): whoever prepared it."""
    p = state.get("prepared") or {}
    who = _text(p.get("actor"))
    return {who: "prepared this AI-BOM"} if who else {}


def publish(*, decision: str, reason: str, read_digest: str, actor: Optional[dict], conn=None) -> dict:
    """A person who did not prepare it publishes the prepared AI-BOM to ICDEV[OS], or refuses it.

    The person's step of ``decl-wft-aibom-publish``, with a reason, bound to the digest of the prepared AI-BOM they
    read. A published AI-BOM carries its ``publication``; a refused one never leaves.
    """
    from tools.workflow_hitl import declared

    if not isinstance(actor, dict):
        raise AIBOMRefused(401, "no_person", "publishing is a signed-in person's declared step on /ai-bom/")
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _state(conn)
        if problems:
            raise AIBOMRefused(409, "chain_broken", f"the AI-BOM ledger does not verify: {problems[0]['problem']}")
        p = st["prepared"]
        if p is None:
            raise AIBOMRefused(409, "nothing_prepared", "no AI-BOM is prepared: prepare one first")
        if p.get("decided"):
            raise AIBOMRefused(409, "already_decided", f"the prepared AI-BOM {p['content_digest'][:12]} was already "
                                                       f"{p['decided']['event']}: prepare a new one")
        if _text(read_digest) != p["content_digest"]:
            raise AIBOMRefused(409, "digest", f"the prepared AI-BOM is {p['content_digest'][:12]}, not what you read "
                                              "(reload it)")
        dec = _text(decision).lower()
        try:
            taken = declared.take_step(
                TEMPLATE, surface=SURFACE, decision=dec, subject=f"aibom:{SYSTEM}", reason=_text(reason),
                actor=actor, separated_from=builders(st),
                payload={"system": SYSTEM, "content_digest": p["content_digest"], "prepared_event": p["event_id"]})
        except declared.StepRefused as exc:
            raise AIBOMRefused(exc.status, exc.code, exc.message) from exc
        event = "published" if taken["affirmative"] else "refused"
        row = _append(conn, event=event, actor=taken["decided_by"], actor_kind="person",
                      actor_role=_text(actor.get("role")), step_id=taken["step_id"], reason=_text(reason),
                      content=p["content_digest"], detail={"prepared_event": p["event_id"], "decision": dec},
                      prev_hash=_head(ordered))
    finally:
        if own:
            conn.close()
    out = {"system": SYSTEM, "event": event, "event_id": row["event_id"], "content_digest": p["content_digest"],
           "step": taken, "decided_by": taken["decided_by"], "prepared_by": p["actor"]}
    if event == "published":
        out["bundle"] = published_bundle()
    return out


def published_bundle(conn=None) -> Optional[dict]:
    """The newest PUBLISHED AI-BOM, as prepared, with its ``publication``; None when none is."""
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, ordered = _state(conn)
    finally:
        if own:
            conn.close()
    pub = st["published"]
    if pub is None or problems:
        return None
    prep = next((r for r in ordered if r["event_id"] == pub["prepared_event"]), None)
    if prep is None:
        return None
    b = json.loads(prep["bundle_json"])
    b["publication"] = {"signed_by": pub["actor"], "role": pub["actor_role"] or None, "step_id": pub["step_id"],
                        "decided_at": _iso(pub["created_at"]), "template": TEMPLATE,
                        "content_digest": pub["content_digest"], "prepared_by": pub["prepared_by"],
                        "event_id": pub["event_id"]}
    return b


# ── reading ──────────────────────────────────────────────────────────────────


def ledger_view(conn=None) -> dict:
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems, _ = _state(conn)
    finally:
        if own:
            conn.close()
    p = st["prepared"]
    return {"prepared": p, "published": st["published"], "history": st["history"], "builders": builders(st),
            "chain": {"intact": not problems, "problems": problems},
            "next": ["publish"] if p and not p.get("decided") else ["prepare"], "template": TEMPLATE}


def view(*, as_of: Optional[str] = None, conn=None) -> dict:
    """The page's view: the config, the AI components (and what was left out), the AI-BOM as it would be prepared now
    (or why not), the two standards and the ledger. Reads; prepares nothing."""
    cfg = load_config()
    try:
        inv, why = inventory(cfg, conn), None
    except AIBOMRefused as exc:
        inv, why = {"components": [], "omitted": []}, exc.to_dict()
    b = None
    if why is None:
        try:
            b = build(as_of=as_of, cfg=cfg, conn=conn)
        except AIBOMRefused as exc:
            why = exc.to_dict()
    return {"system": SYSTEM, "system_name": SYSTEM_NAME, "config": {"path": cfg["_path"], "sha256": cfg["_sha256"]},
            "components": inv["components"], "omitted": inv["omitted"], "bundle": b, "unbuilt": why,
            "ledger": ledger_view(conn), "template": TEMPLATE, "schema": SCHEMA, "schema_path": SCHEMA_RELPATH,
            "contract": SCHEMA_CONTRACT, "schema_sha256": SCHEMA_SHA256,
            "standards": {"spdx": {"version": SPDX_VERSION, "schema": SPDX_SCHEMA_RELPATH,
                                   "sha256": SPDX_SCHEMA_SHA256},
                          "cyclonedx": {"version": CDX_VERSION, "schema": f"{CDX_DIR_RELPATH}/bom-1.7.schema.json",
                                        "sha256": CDX_SCHEMAS["bom-1.7.schema.json"]}}}


# ── twin ─────────────────────────────────────────────────────────────────────


def twin_evidence() -> dict:
    """What the governance twin reads: the ledger state and the newest published AI-BOM's facts."""
    ev: dict[str, Any] = {"aibom": None, "errors": {}}
    try:
        cfg = load_config()
        declared_any = bool(cfg["router_configs"] or cfg["registry"] or cfg["datasets"] or cfg["gold_sets"]
                            or cfg["prompts"])
        ch = chain(read_events())
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["aibom"] = str(exc)
        return ev
    st = replay(ch["ordered"])
    pub = st["published"]
    facts = None
    if pub is not None:
        prep = next((r for r in ch["ordered"] if r["event_id"] == pub["prepared_event"]), None)
        b = _load_json(prep["bundle_json"], {}) if prep else {}
        facts = {"as_of": b.get("as_of"), "summary": b.get("summary")}
    ev["aibom"] = {"declared": declared_any,
                   "prepared": st["prepared"] and {k: st["prepared"][k] for k in (
                       "event_id", "actor", "created_at", "content_digest")} | {
                       "decided": bool(st["prepared"].get("decided"))},
                   "published": pub and {k: pub[k] for k in ("event_id", "actor", "prepared_by", "created_at",
                                                             "content_digest")},
                   "facts": facts}
    ev["problems"] = ch["problems"]
    ev["policy"] = cfg.get("publish") or {}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)`` for the AI-BOM export. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    a = ev.get("aibom")
    checks = [check("compliance.aibom.ledger", "AI-BOM export ledger (aibom_export_events)",
                    a is not None, errors.get("aibom") or "ledger not read")]
    findings: list[dict] = []
    summary = {"aibom_published": 0, "aibom_components": None}
    if a is None:
        return checks, findings, summary
    # Nothing declared is nothing observed: never a pass.
    checks.append(check("compliance.aibom.declared", "An AI component declared for the AI-BOM",
                        bool(a.get("declared")), "args/aibom_export.yaml declares no AI component"))
    pol = {**(ev.get("policy") or {}), **(policy or {})}
    stale = float(pol.get("stale_days") or 30)
    waiting = float(pol.get("waiting_warn_days") or 7)
    for p in ev.get("problems") or []:
        findings.append(finding("aibom.chain_broken", "critical", f"AI-BOM ledger: {p['problem']}",
                                thread_id=f"aibom:{p['event_id']}",
                                recommendation="The export history cannot be trusted; the ledger is append-only, so "
                                               "find who wrote the row and treat it as an incident."))
    pub, prep = a.get("published"), a.get("prepared")
    # Never published is unverified, never passed.
    checks.append(check("compliance.aibom.published", "A published AI-BOM of ICDEV[IT]'s AI components",
                        pub is not None, "no AI-BOM has been published: unverified"))
    if prep and not prep.get("decided"):
        age = age_hours(prep.get("created_at"), observed_at)
        if age is not None and age > waiting * 24:
            findings.append(finding("aibom.awaiting_publish", "low",
                                    f"a prepared AI-BOM has waited {age / 24:.0f} days for a person",
                                    thread_id=f"aibom:{prep['event_id']}",
                                    recommendation="A person who did not prepare it publishes it or refuses."))
    if pub is not None:
        summary["aibom_published"] = 1
        summary["aibom_components"] = sum(((a.get("facts") or {}).get("summary") or {}).values()) or None
        thread = f"aibom:{pub['event_id']}"
        if pub.get("actor") and pub.get("actor") == pub.get("prepared_by"):
            findings.append(finding("aibom.self_published", "critical",
                                    "the AI-BOM was published by the person who prepared it", thread_id=thread,
                                    recommendation="Treat it as unpublished; a person who did not prepare it "
                                                   "publishes a new one."))
        age = age_hours(pub.get("created_at"), observed_at)
        if age is not None and age > stale * 24:
            findings.append(finding("aibom.stale", "medium", f"the newest published AI-BOM is {age / 24:.0f} days old",
                                    thread_id=thread,
                                    recommendation="Prepare the AI-BOM again and have a person publish it."))
    return checks, findings, summary
