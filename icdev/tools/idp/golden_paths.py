# CUI // SP-CTI
"""Golden paths: a new service that is conformant by construction (icx-coe-04).

The fifth card of the Engineering CoE programme. icx-coe-01 gave ICDEV a standards register with a
lifecycle (``tools/idp/standards.py``), icx-coe-02 exemptions and ADR records, icx-coe-03 scorecards per
repo against the standards in force (``tools/idp/conformance.py``). This module makes a NEW service
start conformant, and keeps the way it is made from falling behind. It REUSES that machinery:

**A path** (``args/golden_paths.yaml``) is a template that ``icdev scaffold service --path <id>``
renders (``tools/cli/scaffold.py``, through the shared template engine) and a declaration of the
standards it satisfies, by id AND version. The standards that APPLY to a path are DERIVED, never
listed: every standard in force in the register whose id matches the path's repo declaration in
``args/coe_conformance.yaml`` (:func:`required`). A path is BEHIND when one of them is missing from its
declaration, or declared at another version (:func:`behind`). ``--check`` fails CI on it, reading the
register's reviewed seeds; the page and the twin read the live ledger.

**The drift check** (:func:`drift`) scaffolds the path into a temporary tree laid over this checkout
(every untouched path is a symlink to the real one) and judges the RESULT with the platform's own
judges: the IDP scorecard (the same rules and fact collectors, ``tools/iqe/adapters/idp.rooted``),
``feature_audit`` (the five rules, on the scaffold's own url_map and MCP registry, probed in a
subprocess), and the generated tests, run red without the service and green with it. A standard
checked by a scorecard rule must PASS on the scaffold; one checked by a manual evidence step shows
``needs_evidence``, never pass; a machine check that measured nothing is ``unverified``. Anything
else, or a piece of the scaffold missing or malformed, is drift.

**Versions are decided by people** (``coe_golden_path_events``, append-only, hash-chained per path).
Anyone, or an agent, proposes a version and runs its drift check; a person who did not author it
publishes or refuses it (``decl-wft-coe-golden-path-publish``), and only when the gate holds: its
newest drift check passed, on the template as it is now, and it is not behind the standards in force.
A gate that cannot be evaluated blocks. Retiring a published version is a person's step too
(``decl-wft-coe-golden-path-retire``), never its author's. Seeds are recorded by the labelled bootstrap
identity, never a person.

CLI::

    python tools/idp/golden_paths.py --check [--source register|ledger]   # exit 1: a path is behind
    python tools/idp/golden_paths.py --drift [--path ID]                   # exit 1: a scaffold drifted
    python tools/idp/golden_paths.py --seed                                # record the seeds
    python tools/idp/golden_paths.py --json                                # every path, as the page reads it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 — fixed interpreter and arguments, never a shell
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
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
CONFIG_RELPATH = "args/golden_paths.yaml"
TABLE = "coe_golden_path_events"
GENESIS = "0" * 64
#: Where a scaffold leaves the snippets its registration splices into the shared registers.
SNIPPETS_DIR = ".golden"

TEMPLATES = {"publish": "decl-wft-coe-golden-path-publish", "retire": "decl-wft-coe-golden-path-retire"}
SURFACES = {"publish": "idp.idp_api_golden_path_publish", "retire": "idp.idp_api_golden_path_retire"}

EVENTS = ("seeded", "proposed", "drift_checked", "published", "refused", "retired", "kept")
ACTOR_KINDS = ("person", "agent", "bootstrap")
SEED_STATES = ("published", "proposed")
#: A version's state is the last of these events on it.
STATES = ("proposed", "published", "refused", "retired")
STANDARD_STATUSES = ("pass", "fail", "needs_evidence", "unverified")
BEHIND = "the golden path is behind the standards in force"

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,2}$")
_CONTROL_RE = re.compile(r"^[A-Z]{2}-\d{1,2}(?:\(\d+\))?$")

_COLS = ("event_id", "path_id", "version", "event", "actor", "actor_kind", "actor_role", "step_id",
         "reason", "entry_digest", "entry_json", "detail_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in _COLS if c != "row_hash")

#: The shared registers a service must be in, and the snippet each is spliced from.
SHARED = {"component_registry.yaml": "args/component_registry.yaml",
          "feature_conformance.yaml": "args/feature_conformance.yaml",
          "mcp_tools.yaml": "tools/mcp/tool_registry.py"}


class GoldenPathRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


# ── the declaration ──────────────────────────────────────────────────────────


def load_config(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else BASE_DIR / CONFIG_RELPATH
    raw = p.read_bytes()
    cfg = yaml.safe_load(raw.decode("utf-8")) or {}
    if not isinstance(cfg.get("paths"), list) or not cfg["paths"]:
        raise ValueError(f"{CONFIG_RELPATH} declares no paths")
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    return cfg


def normalise(entry: dict) -> dict:
    """A path version's fields of record, in a fixed shape: what is digested and stored."""
    sat = []
    for s in entry.get("satisfies") or []:
        if isinstance(s, str):
            sid, _, ver = s.partition("@")
            s = {"id": sid, "version": ver}
        sat.append({"id": _text(s.get("id")).upper(), "version": _text(s.get("version")),
                    "by": " ".join(_text(s.get("by")).split())})
    return {
        "id": _text(entry.get("id")).lower(),
        "title": " ".join(_text(entry.get("title")).split()),
        "version": _text(entry.get("version")),
        "owner": _text(entry.get("owner")),
        "repo": _text(entry.get("repo")),
        "template": _text(entry.get("template")),
        "vars": {str(k): v for k, v in sorted((entry.get("vars") or {}).items())},
        "satisfies": sorted(sat, key=lambda s: s["id"]),
    }


def entry_digest(entry: dict) -> str:
    return digest(normalise(entry))


def path_entry(path_id: str, cfg: Optional[dict] = None) -> dict:
    """The declared (YAML) entry of a path, normalised. KeyError when there is none."""
    cfg = cfg or load_config()
    for e in cfg["paths"]:
        if _text(e.get("id")).lower() == _text(path_id).lower():
            return normalise(e)
    raise KeyError(f"no golden path {path_id!r} in {CONFIG_RELPATH}")


def validate_entry(entry: dict, root: Optional[Path] = None) -> list[str]:
    """Shape errors for one path version. Empty when it is well formed."""
    from tools.idp import conformance, standards

    root = Path(root or BASE_DIR)
    n = normalise(entry)
    errors = []
    if not _ID_RE.match(n["id"]):
        errors.append("id: lower case letters, digits and dashes")
    if not _VERSION_RE.match(n["version"]):
        errors.append("version: digits and dots, e.g. 1.1")
    if not n["title"]:
        errors.append("title is required")
    owners = standards.load_register().get("owner_roles") or []
    if n["owner"] not in owners:
        errors.append(f"owner must be one of {', '.join(owners)}")
    try:
        conformance.repo_config(n["repo"])
    except Exception:  # noqa: BLE001
        errors.append(f"repo {n['repo']!r} is not declared in {conformance.CONFIG_RELPATH}")
    if not (root / n["template"] / "manifest.yaml").is_file():
        errors.append(f"template {n['template']!r} has no manifest.yaml")
    seen = set()
    for s in n["satisfies"]:
        if not s["id"] or not s["version"]:
            errors.append("each satisfies entry names a standard id and its version")
        if s["id"] in seen:
            errors.append(f"{s['id']} is declared twice")
        seen.add(s["id"])
    if not n["satisfies"]:
        errors.append("a path declares the standards it satisfies")
    return errors


def template_digest(entry: dict, root: Optional[Path] = None) -> str:
    """SHA-256 over every file of the path's template tree: a drift check is for THIS template."""
    root = Path(root or BASE_DIR)
    h = hashlib.sha256()
    tdir = root / normalise(entry)["template"]
    for p in sorted(x for x in tdir.rglob("*") if x.is_file()):
        h.update(p.relative_to(tdir).as_posix().encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


# ── which standards apply, and whether a path is behind ──────────────────────


def in_force(source: str = "ledger", conn=None) -> list[dict]:
    """Every standard IN FORCE: ``[{id, version, title, check}]``.

    ``ledger``: the replay of coe_standard_events (what the page and the twin judge by).
    ``register``: the reviewed seeds of args/engineering_standards.yaml that enter adopted (what CI
    judges by: a checkout has no ledger, and a standard reaches the register only through review).
    """
    from tools.idp import standards

    out = []
    if source == "register":
        for e in standards.load_register()["standards"]:
            n = standards.normalise(e)
            if _text(e.get("seed")) == "adopted":
                out.append({"id": n["id"], "version": n["version"], "title": n.get("title", ""),
                            "check": e.get("check") or {}})
        return sorted(out, key=lambda s: s["id"])
    if source != "ledger":
        raise ValueError(f"source is ledger or register, not {source!r}")
    ch = standards.chain(standards.read_events(conn))
    for sid, ordered in sorted(ch.items()):
        if sid == "_problems":
            continue
        st = standards.replay(ordered)
        if st["in_force"]:
            entry = st["versions"][st["in_force"]]["entry"]
            out.append({"id": sid, "version": st["in_force"], "title": entry.get("title", ""),
                        "check": entry.get("check") or {}})
    return out


def required(entry: dict, forced: list[dict]) -> list[dict]:
    """The standards in force that APPLY to the path: its repo's declaration (args/coe_conformance.yaml)."""
    from tools.idp import conformance

    n = normalise(entry)
    patterns = conformance.repo_config(n["repo"]).get("standards") or []
    return [s for s in forced if conformance.applies(s["id"], patterns)]


def behind(entry: dict, forced: list[dict]) -> dict:
    """``{behind, missing, stale_version, not_in_force}`` for one path version against *forced*."""
    n = normalise(entry)
    declared = {s["id"]: s["version"] for s in n["satisfies"]}
    need = required(n, forced)
    missing = [{"id": s["id"], "version": s["version"], "title": s.get("title", "")}
               for s in need if s["id"] not in declared]
    stale = [{"id": s["id"], "version": s["version"], "declared": declared[s["id"]]}
             for s in need if s["id"] in declared and declared[s["id"]] != s["version"]]
    live = {s["id"] for s in forced}
    extra = sorted(sid for sid in declared if sid not in live)
    return {"behind": bool(missing or stale), "missing": missing, "stale_version": stale,
            "not_in_force": extra, "required": [{"id": s["id"], "version": s["version"]} for s in need]}


def _in_use(conn=None) -> dict[str, dict]:
    """Each path's version IN USE in the ledger (``{path id: entry}``), as the page and the twin judge it."""
    ch = chain(read_events(conn))
    out = {}
    for pid, ordered in ch.items():
        if pid == "_problems":
            continue
        st = replay(ordered)
        if st["in_use"]:
            out[pid] = st["versions"][st["in_use"]]["entry"]
    return out


def check(source: str = "register", cfg: Optional[dict] = None, conn=None) -> dict:
    """Every declared path against the standards in force. ``ok`` is false when one is behind.

    ``register`` (CI) judges each path as ``args/golden_paths.yaml`` declares it. ``ledger`` judges the version a
    person published and is IN USE, as the page and the twin do (osx-coe-02), and the file's declaration only for a
    path the ledger has not recorded.
    """
    cfg = cfg or load_config()
    forced = in_force(source, conn)
    use = _in_use(conn) if source == "ledger" else {}
    rows = []
    for e in cfg["paths"]:
        n = normalise(e)
        judged = "declared (args/golden_paths.yaml)"
        if n["id"] in use:
            n, judged = normalise(use[n["id"]]), "in use (ledger)"
        b = behind(n, forced)
        rows.append({"id": n["id"], "version": n["version"], "judged": judged, **b,
                     "why": (f"{BEHIND}: {n['id']} {n['version']} does not declare "
                             + ", ".join([f"{m['id']} {m['version']}" for m in b["missing"]]
                                         + [f"{m['id']} {m['version']} (declares {m['declared']})"
                                            for m in b["stale_version"]])) if b["behind"] else ""})
    return {"ok": not any(r["behind"] for r in rows), "source": source, "paths": rows,
            "in_force": [{"id": s["id"], "version": s["version"]} for s in forced],
            "config": {"path": CONFIG_RELPATH, "sha256": cfg["_sha256"]}}


# ── scaffolding: artifacts and registration ──────────────────────────────────


def _load_snippet(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def generate_artifacts(out_dir: Path, variables: dict) -> dict:
    """The SBOM (and the AI-BOM when the service uses a model) and the proposed ADR record.

    From the existing generators: tools/compliance/sbom_generator.py builds the CycloneDX document
    from the service's declared requirements; tools/security/ai_bom_generator.py catalogues the AI
    components (the models the governed router may route its llm_function to); tools/idp/adrs.py
    normalises, validates the shape of and writes the ADR record, PROPOSED and recorded as the
    generator's draft. Nothing is written to a database, and the generator never accepts an ADR.
    """
    from tools.compliance import sbom_generator
    from tools.idp import adrs

    key = variables["key"]
    bom_dir = out_dir / "tools" / key / "bom"
    files = []
    components = sbom_generator._declared_python(bom_dir)
    sbom, _ = sbom_generator._build_cyclonedx_sbom(
        {"id": key, "name": variables["display_name"], "directory_path": str(out_dir / "tools" / key)},
        components, tool_version="golden-path")
    (bom_dir / "sbom.cdx.json").write_text(json.dumps(sbom, indent=2, sort_keys=True, default=str),
                                          encoding="utf-8")
    files.append(f"tools/{key}/bom/sbom.cdx.json")
    if variables.get("uses_model"):
        from tools.security.ai_bom_generator import AIBOMGenerator

        gen = AIBOMGenerator()
        # Only the models the router may send this service's llm_function to: its declared chain, or
        # the default chain when the function is not declared (args/llm_config.yaml routing).
        routing = (yaml.safe_load((BASE_DIR / "args" / "llm_config.yaml").read_text(encoding="utf-8"))
                   or {}).get("routing") or {}
        chain_ = set(((routing.get(f"{key}_draft") or routing.get("default") or {}).get("chain")) or [])
        found = gen._scan_requirements(bom_dir) + [c for c in gen._scan_llm_config(bom_dir)
                                                    if c["component_name"] in chain_]
        for c in found:
            c["component_hash"] = gen._compute_hash(c)
            c["risk_level"] = gen._assess_risk(c)
            c["source"] = os.path.relpath(c.get("source") or "", BASE_DIR) if c.get("source") else ""
        aibom = {"bomFormat": "icdev-ai-bom", "service": key, "llm_function": f"{key}_draft",
                 "generator": "tools/security/ai_bom_generator.py", "components": found}
        (bom_dir / "ai-bom.json").write_text(json.dumps(aibom, indent=2, sort_keys=True), encoding="utf-8")
        files.append(f"tools/{key}/bom/ai-bom.json")
    adr_id = f"D-{variables['key_upper']}-1"
    entry = adrs.normalise({
        "id": adr_id, "title": f"{variables['display_name']} is built on the golden path "
                               f"{variables['path_id']} {variables['path_version']}",
        "section": "Golden-path services",
        "context": (f"{variables['display_name']} was scaffolded with `icdev scaffold service {key} --path "
                    f"{variables['path_id']}`, which declares the engineering standards it satisfies in "
                    "args/golden_paths.yaml."),
        "decision": (f"Build {variables['display_name']} on golden path {variables['path_id']} "
                     f"{variables['path_version']}: proposals a person decides through "
                     f"decl-wft-{key.replace('_', '-')}-decide, a governance twin with threads, and read and "
                     "propose MCP tools."),
        "consequences": ("The service starts conformant to the standards its path declares; a later standard "
                         "is met by re-scaffolding on a newer path version or by changing the service."),
        "links": {"requirements": [f"PM-{variables['key_upper']}-01", f"PM-{variables['key_upper']}-02"],
                  "standards": [s.split("@")[0] for s in _text(variables.get("standards")).split(",") if s]},
    })
    shape = adrs.validate_entry(entry, root=out_dir, reqs={f"PM-{variables['key_upper']}-01",
                                                           f"PM-{variables['key_upper']}-02"},
                                stds=set(entry["links"]["standards"]))
    if shape:
        raise ValueError("the ADR stub does not validate: " + "; ".join(shape))
    rec_dir = out_dir / "docs" / "reference" / "adrs"
    rec_dir.mkdir(parents=True, exist_ok=True)
    adrs.write_record(rec_dir / f"{adr_id}.md", {
        **entry, "status": "proposed", "superseded_by": "",
        "recorded": {"by": "agent:icdev-scaffold", "kind": "agent", "step_id": "",
                     "note": "drafted by the golden-path generator; a person who is not its author "
                             "accepts it on /idp/adrs"}})
    files.append(f"docs/reference/adrs/{adr_id}.md")
    return {"files": files, "adr": adr_id, "sbom_components": len(components)}


def _splice_registry(root: Path, snippet: dict) -> dict:
    from tools.cli import scaffold

    path = root / SHARED["component_registry.yaml"]
    text = path.read_text(encoding="utf-8")
    if any(isinstance(c, dict) and c.get("key") == snippet["key"]
           for c in (yaml.safe_load(text) or {}).get("components") or []):
        return {"file": SHARED["component_registry.yaml"], "spliced": False, "reason": "already registered"}
    rendered = yaml.dump([snippet], default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2)
    path.write_text(scaffold._insert_into_components(text, rendered), encoding="utf-8", newline="")
    return {"file": SHARED["component_registry.yaml"], "spliced": True}


def _splice_feature(root: Path, snippet: dict) -> dict:
    path = root / SHARED["feature_conformance.yaml"]
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if any(f.get("key") == snippet["key"] for f in data.get("features") or []):
        return {"file": SHARED["feature_conformance.yaml"], "spliced": False, "reason": "already listed"}
    # `features:` is the file's last key: an entry is appended at its indentation.
    body = yaml.dump([snippet], default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2)
    block = "\n".join(("  " + ln) if ln else ln for ln in body.rstrip("\n").split("\n"))
    path.write_text(text.rstrip("\n") + "\n\n  # ── " + snippet["label"] + " (golden path) ──\n" + block + "\n",
                    encoding="utf-8")
    return {"file": SHARED["feature_conformance.yaml"], "spliced": True}


_RESOURCE_END = "\n}\n\n# ==== END GENERATED REGISTRY"
_READ_ONLY_END = "\n})"


def _splice_mcp(root: Path, tools: list[dict]) -> dict:
    path = root / SHARED["mcp_tools.yaml"]
    text = path.read_text(encoding="utf-8")
    new = [t for t in tools if f'    "{t["name"]}": {{' not in text]
    if not new:
        return {"file": SHARED["mcp_tools.yaml"], "spliced": False, "reason": "already registered"}
    entries = "".join(
        f'    "{t["name"]}": {{\n'
        f'        "category": {json.dumps(t["category"])},\n'
        f'        "module": {json.dumps(t["module"])},\n'
        f'        "handler": {json.dumps(t["handler"])},\n'
        f'        "description": {json.dumps(t["description"])},\n'
        f'        "input_schema": {json.dumps(t["input_schema"])},\n'
        "    },\n" for t in new)
    at = text.index(_RESOURCE_END)
    text = text[:at] + "\n" + entries.rstrip("\n") + text[at:]
    ro_start = text.index("READ_ONLY_DECLARATIONS = MappingProxyType({")
    ro_end = text.index(_READ_ONLY_END, ro_start)
    flags = "".join(f'\n    {json.dumps(t["name"])}: {bool(t["read_only"])},' for t in new)
    text = text[:ro_end] + flags + text[ro_end:]
    path.write_text(text, encoding="utf-8")
    return {"file": SHARED["mcp_tools.yaml"], "spliced": True, "tools": [t["name"] for t in new]}


def register(root: Path, snippets_dir: Path) -> list[dict]:
    """Splice a scaffold's snippets into *root*'s shared registers, as text (no comment is lost).

    The component registry through the same splice ``icdev scaffold canvas`` uses; the feature
    appended to args/feature_conformance.yaml; the MCP tools into tool_registry.py's
    RESOURCE_REGISTRY and READ_ONLY_DECLARATIONS. A root without a register is skipped, said.
    """
    out = []
    for name, rel in SHARED.items():
        snip = snippets_dir / name
        if not (root / rel).is_file():
            out.append({"file": rel, "spliced": False, "reason": "no such register under the output root"})
            continue
        data = _load_snippet(snip)
        out.append({"component_registry.yaml": _splice_registry, "feature_conformance.yaml": _splice_feature,
                    "mcp_tools.yaml": _splice_mcp}[name](root, data))
    return out


# ── the drift check ──────────────────────────────────────────────────────────


def _overlay(repo: Path, overlay: Path) -> None:
    """A tree whose every entry is a symlink to *repo*'s, except what is materialised later."""
    overlay.mkdir(parents=True, exist_ok=True)
    for child in repo.iterdir():
        if child.name in (".git", SNIPPETS_DIR):
            continue
        (overlay / child.name).symlink_to(child)


def _materialise(repo: Path, overlay: Path, rel_dir: Path) -> Path:
    """Make *rel_dir* a real directory in the overlay whose children link to the repo's."""
    here = overlay
    for part in rel_dir.parts:
        nxt = here / part
        src = repo / (nxt.relative_to(overlay))
        if nxt.is_symlink():
            nxt.unlink()
            nxt.mkdir()
            if src.is_dir():
                for child in src.iterdir():
                    (nxt / child.name).symlink_to(child)
        elif not nxt.exists():
            nxt.mkdir()
        here = nxt
    return here


def _place(repo: Path, overlay: Path, src: Path, rel: Path) -> None:
    _materialise(repo, overlay, rel.parent)
    dest = overlay / rel
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    shutil.copy2(src, dest)


def _clean_env(overlay: Path, db: Path, extra: Optional[dict] = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ICDEV_PG_") and k not in ("ICDEV_DATABASE_URL", "ICDEV_LLM_CONFIG")}
    env.update({"PYTHONPATH": str(overlay), "ICDEV_PROJECT_ROOT": str(overlay), "ICDEV_STORAGE_BACKEND": "sqlite",
                "ICDEV_DB_PATH": str(db), "ICDEV_NO_SERVER": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra or {})
    return env


_PROBE = r'''
import inspect, json, os, sys
import yaml
from flask import Flask
root = os.getcwd()
key, names = sys.argv[1], sys.argv[2].split(",")
reg = yaml.safe_load(open("args/component_registry.yaml", encoding="utf-8"))
comp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == key)
mod = __import__(comp["module"], fromlist=["x"])
app = Flask(__name__)
app.register_blueprint(getattr(mod, comp["blueprint_attr"])(), url_prefix=comp["url_prefix"])
rules = []
for r in app.url_map.iter_rules():
    if r.endpoint == "static":
        continue
    fn = inspect.unwrap(app.view_functions[r.endpoint])
    lines, start = inspect.getsourcelines(fn)
    path = os.path.relpath(inspect.getsourcefile(fn), root).replace(os.sep, "/")
    rules.append({"rule": r.rule, "endpoint": r.endpoint, "methods": sorted(r.methods or []),
                  "file": path, "start": start, "end": start + len(lines) - 1})
from tools.conformance.feature_audit import mcp_evidence
sys.stdout.write("@@GOLDEN@@" + json.dumps({"rules": rules, "mcp": mcp_evidence(names)}))
'''


def _probe(overlay: Path, db: Path, key: str, tools: list[str], env_flag: str, timeout: int) -> dict:
    """The scaffold's url_map and what the overlay's MCP registry declares, in a subprocess there."""
    proc = subprocess.run([sys.executable, "-c", _PROBE, key, ",".join(tools)], cwd=str(overlay),
                          env=_clean_env(overlay, db, {env_flag: "true"}), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if "@@GOLDEN@@" not in proc.stdout:
        raise RuntimeError(f"the scaffold's url_map could not be probed (rc={proc.returncode}): "
                           f"{(proc.stderr or proc.stdout)[-1200:]}")
    return json.loads(proc.stdout.split("@@GOLDEN@@", 1)[1])


def _pytest(overlay: Path, db: Path, test: str, env_flag: str, timeout: int) -> dict:
    t0 = time.monotonic()
    # A plugin that imports ``tools`` FIRST, from the overlay: modules that bootstrap sys.path from
    # their resolved location would otherwise put this checkout ahead of it before ``tools`` loads.
    (overlay / "_golden_overlay.py").write_text("import tools  # noqa: F401\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "_golden_overlay", "-p",
                           "no:cacheprovider", test],
                          cwd=str(overlay), env=_clean_env(overlay, db, {env_flag: "true"}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()][-1:] or [""]
    out = {"rc": proc.returncode, "summary": tail[0][:300], "seconds": round(time.monotonic() - t0, 1)}
    if proc.returncode:
        out["failure"] = "\n".join(((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-25:])[-3000:]
    return out


def _score_machine(overlay: Path, key: str, cards: Iterable[str]) -> dict:
    """Each scorecard, graded on the overlay's registry and tree by the platform's own rules."""
    from tools.idp import scorecard
    from tools.iqe.adapters import idp as idp_adapter

    out = {}
    with idp_adapter.rooted(overlay, overlay / SHARED["component_registry.yaml"]):
        for card in sorted(set(cards)):
            try:
                report = scorecard.evaluate(scorecard.load_scorecard(card))
                res = next((r for r in report.get("results") or [] if r.get("entity") == key), None)
                out[card] = {"outcomes": {o["identifier"]: {"status": o["status"], "message": o.get("message", "")}
                                          for o in (res or {}).get("rules") or res and res.get("outcomes") or []},
                             "found": res is not None}
            except Exception as exc:  # noqa: BLE001 — a scorecard that will not grade measured nothing
                out[card] = {"error": str(exc), "outcomes": {}, "found": False}
    return out


def _piece(name: str, ok: bool, why: str = "") -> dict:
    return {"piece": name, "ok": bool(ok), "why": "" if ok else why}


#: osx-cop-04: the twelve sections of an arc42 description, by name and in order (arc42 template 9.1, October 2026,
#: https://arc42.org/overview; CC BY-SA 4.0). Only the names are arc42's; the template's guidance is ICDEV's own.
ARC42_SECTIONS = ("Introduction and goals", "Constraints", "Context and scope", "Solution strategy",
                  "Building block view", "Runtime view", "Deployment view", "Crosscutting concepts",
                  "Architectural decisions", "Quality requirements", "Risks and technical debt", "Glossary")
#: osx-cop-04: the C4 model's levels a scaffold draws (https://c4model.com: context, container, component; code is
#: the source itself) and the supplementary views it names.
C4_VIEWS = ("Level 1: System context", "Level 2: Containers", "Level 3: Components", "Supplementary: Deployment",
            "Supplementary: Dynamic")


def architecture_record_problems(root: Path, key: str, adr_id: str) -> list[str]:
    """Why a service's architecture records do not hold (osx-cop-04): its C4 views and arc42 description are there,
    arc42's twelve sections in order, section 9 LINKS the service's ADR record, and neither document holds a decision
    of its own -- the ADR record (tools/idp/adrs.py) is the ONE place a decision is kept. Empty: they hold."""
    import re

    from tools.idp import adrs

    why: list[str] = []
    base = root / "docs" / "architecture" / key
    docs = {n: base / f"{n}.md" for n in ("c4", "arc42")}
    text = {n: p.read_text(encoding="utf-8") if p.is_file() else None for n, p in docs.items()}
    for n, t in text.items():
        if t is None:
            why.append(f"docs/architecture/{key}/{n}.md is missing")
    link = f"../../reference/adrs/{adr_id}.md"
    if text["arc42"] is not None:
        heads = re.findall(r"^## (\d+)\. (.+?)\s*$", text["arc42"], flags=re.M)
        if [(int(n), h) for n, h in heads] != list(enumerate(ARC42_SECTIONS, 1)):
            why.append(f"the arc42 description does not carry the twelve sections in order: {[h for _, h in heads]}")
        sec9 = re.search(r"^## 9\. .*?$(.*?)(?=^## 10\. |\Z)", text["arc42"], flags=re.M | re.S)
        if sec9 is None or link not in sec9.group(1):
            why.append(f"section 9 of the arc42 description does not link the ADR record {adr_id} ({link})")
    if text["c4"] is not None:
        missing = [v for v in C4_VIEWS if f"## {v}" not in text["c4"]]
        if missing:
            why.append(f"the C4 views lack {missing}")
    rec_dir = root / "docs" / "reference" / "adrs"
    stem = re.escape(adr_id.rsplit("-", 1)[0])
    mine = sorted(p.name for p in rec_dir.glob("D-*.md") if re.fullmatch(rf"{stem}-\d+\.md", p.name)) if rec_dir.is_dir() else []
    if mine != [f"{adr_id}.md"]:
        why.append(f"the service has exactly one ADR record, {adr_id}.md, written by tools/idp/adrs.py: found {mine}")
    for n, t in text.items():
        if t is not None and (t.lstrip().startswith("---\n") or re.search(r"^(id|status|supersedes):\s", t, flags=re.M)):
            why.append(f"{n}.md carries ADR front matter: a decision is recorded only as an ADR record")
    if f"{adr_id}.md" in mine:
        rec = adrs.read_record(rec_dir / f"{adr_id}.md")
        for n, t in text.items():
            if t is None:
                continue
            for field in ("decision", "context", "consequences"):
                body = str(rec.get(field) or "").strip()
                if len(body) >= 40 and body in t:
                    why.append(f"{n}.md copies the ADR record's {field}: link {adr_id}, never duplicate it")
    return why


def _pieces(overlay: Path, v: dict, feature: dict) -> list[dict]:
    """The scaffold's own parts, each checked: contract, SBOM, AI-BOM, control map, OTel, ADR, the architecture
    records (C4 and arc42, linking the ADR, never copying it; osx-cop-04), declaration."""
    from tools.idp import adrs

    key, out = v["key"], []
    try:
        api = yaml.safe_load((overlay / f"tools/{key}/contract.openapi.yaml").read_text(encoding="utf-8"))
        ops = {op.get("operationId") for p in (api.get("paths") or {}).values() for op in p.values()}
        want = {e for e in feature["endpoints"] if not e.endswith("_page")}
        out.append(_piece("contract", str(api.get("openapi", "")).startswith("3.") and want <= ops,
                          f"operationIds {sorted(want - ops)} missing from the contract"))
    except Exception as exc:  # noqa: BLE001
        out.append(_piece("contract", False, f"unreadable: {exc}"))
    try:
        sbom = json.loads((overlay / f"tools/{key}/bom/sbom.cdx.json").read_text(encoding="utf-8"))
        out.append(_piece("sbom", sbom.get("bomFormat") == "CycloneDX" and bool(sbom.get("specVersion")),
                          "not a CycloneDX document"))
    except Exception as exc:  # noqa: BLE001
        out.append(_piece("sbom", False, f"unreadable: {exc}"))
    aibom = overlay / f"tools/{key}/bom/ai-bom.json"
    if v.get("uses_model"):
        try:
            doc = json.loads(aibom.read_text(encoding="utf-8"))
            out.append(_piece("ai_bom", bool(doc.get("components")), "the AI-BOM lists no AI component"))
        except Exception as exc:  # noqa: BLE001
            out.append(_piece("ai_bom", False, f"a service that uses a model has no AI-BOM: {exc}"))
    else:
        out.append(_piece("ai_bom", not aibom.exists(), "an AI-BOM for a service that calls no model"))
    try:
        cm = yaml.safe_load((overlay / f"tools/{key}/control_map.yaml").read_text(encoding="utf-8"))
        bad = [c.get("id") for c in cm.get("controls") or [] if not _CONTROL_RE.match(_text(c.get("id")))
               or not (overlay / _text(c.get("test"))).exists()]
        out.append(_piece("control_map", bool(cm.get("controls")) and not bad,
                          f"controls without a well-formed id or an existing test: {bad}"))
    except Exception as exc:  # noqa: BLE001
        out.append(_piece("control_map", False, f"unreadable: {exc}"))
    tele = (overlay / f"tools/{key}/telemetry.py").read_text(encoding="utf-8") if (
        overlay / f"tools/{key}/telemetry.py").is_file() else ""
    bp = (overlay / f"tools/{key}/blueprint.py").read_text(encoding="utf-8")
    out.append(_piece("otel", "tools.observability" in tele and bp.count("with span(") >= 4,
                      "a route runs outside an OTel span"))
    adr_id = f"D-{v['key_upper']}-1"
    try:
        rec = adrs.read_record(overlay / f"docs/reference/adrs/{adr_id}.md")
        errs = adrs.validate_entry(rec, root=overlay, stds={s.split("@")[0] for s in v["standards"].split(",") if s})
        out.append(_piece("adr", rec.get("status") == "proposed" and not errs,
                          f"the ADR stub is {rec.get('status')!r} (only proposed: the generator never "
                          f"accepts one) {'; '.join(errs)}"))
    except Exception as exc:  # noqa: BLE001
        out.append(_piece("adr", False, f"unreadable: {exc}"))
    arch = architecture_record_problems(overlay, key, adr_id)                    # osx-cop-04
    out.append(_piece("architecture_records", not arch, "; ".join(arch)))
    try:
        decl = yaml.safe_load((overlay / f"tools/{key}/service.yaml").read_text(encoding="utf-8"))
        out.append(_piece("scorecard_declaration",
                          decl.get("golden_path") == {"id": v["path_id"], "version": v["path_version"]}
                          and decl.get("scorecard", {}).get("entity") == key,
                          "the service declaration does not name its path and scorecard entity"))
    except Exception as exc:  # noqa: BLE001
        out.append(_piece("scorecard_declaration", False, f"unreadable: {exc}"))
    return out


def drift(entry: dict, *, forced: Optional[list[dict]] = None, source: str = "register",
          cfg: Optional[dict] = None, keep: Optional[Path] = None) -> dict:
    """Scaffold the path into a temporary tree over this checkout and judge the result.

    ``result`` is ``pass`` only when every machine-checkable standard in force that applies (and every
    one the path declares) PASSES on the scaffold, a manual one shows needs_evidence, every piece of the
    scaffold is present and well formed, feature_audit finds no gap, the generated tests are red
    without the service and green with it, and the path is not behind. Anything else is ``drift``,
    with why. Writes nothing outside the temporary tree (``keep`` copies the tree out, for a reader).
    """
    from tools.cli import scaffold
    from tools.conformance import feature_audit

    cfg = cfg or load_config()
    n = normalise(entry)
    forced = forced if forced is not None else in_force(source)
    dcfg = cfg.get("drift") or {}
    key = _text(dcfg.get("sample_key")) or "golden_drift"
    timeout = int(dcfg.get("timeout_seconds") or 300)
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="icdev-golden-"))
    report: dict[str, Any] = {"path": n["id"], "version": n["version"], "entry_digest": entry_digest(n),
                              "template_digest": template_digest(n), "key": key, "source": source}
    try:
        staging, overlay, db = tmp / "staging", tmp / "root", tmp / "drift.db"
        res = scaffold.scaffold_service(key, _text(dcfg.get("sample_display_name")) or "Golden Drift Sample",
                                        staging, path_id=n["id"], register=False,
                                        config={**cfg, "paths": [n]})
        if not res.get("success"):
            raise RuntimeError("the scaffold did not render: "
                               + "; ".join((res.get("errors") or []) + (res.get("validation_failures") or [])))
        v = res["variables"]
        _overlay(BASE_DIR, overlay)
        for f in sorted(p for p in staging.rglob("*") if p.is_file()):
            rel = f.relative_to(staging)
            if rel.parts[0] != SNIPPETS_DIR:
                _place(BASE_DIR, overlay, f, rel)
        # The shared registers are spliced, so they are copies; so is the test conftest, which roots
        # sys.path at its own location, and pyproject.toml, whose directory pytest takes as rootdir and
        # puts on sys.path (resolved): both must name the overlay, not this checkout.
        for rel in (*SHARED.values(), "tests/conftest.py", "pyproject.toml"):
            _place(BASE_DIR, overlay, BASE_DIR / rel, Path(rel))
        report["registered"] = register(overlay, staging / SNIPPETS_DIR)
        feature = _load_snippet(staging / SNIPPETS_DIR / "feature_conformance.yaml")
        mcp_tools = [t["name"] for t in _load_snippet(staging / SNIPPETS_DIR / "mcp_tools.yaml")]
        report["files"] = sorted(res["rendered_files"] + res["artifacts"]["files"])

        # 1. the standards: a scorecard rule by machine on the scaffold; a manual step needs evidence
        need = {s["id"]: s for s in required(n, forced)}
        live = {s["id"]: s for s in forced}
        declared = {s["id"]: s for s in n["satisfies"]}
        judged_ids = sorted(set(need) | (set(declared) & set(live)))
        cards = [((live[i]["check"] or {}).get("scorecard_rule") or {}).get("scorecard") for i in judged_ids]
        scored = _score_machine(overlay, key, [c for c in cards if c])
        standards_out = []
        for sid in judged_ids:
            s = live[sid]
            chk = s["check"] or {}
            row = {"id": sid, "version": s["version"], "title": s["title"], "applies": sid in need,
                   "declared": declared.get(sid, {}).get("version"), "by": declared.get(sid, {}).get("by", "")}
            if "scorecard_rule" in chk:
                card, rule = chk["scorecard_rule"].get("scorecard"), chk["scorecard_rule"].get("rule")
                got = (scored.get(card) or {})
                o = (got.get("outcomes") or {}).get(rule)
                row["check"] = f"scorecard rule {card} / {rule}"
                if got.get("error") or not got.get("found") or o is None:
                    row.update(status="unverified", why=got.get("error") or f"{rule} was not graded on the scaffold")
                elif o["status"] == "pass":
                    row.update(status="pass", why=f"{rule} passes on the scaffold by machine")
                elif o["status"] == "not_applicable":
                    row.update(status="unverified", why=f"{rule} does not apply to the scaffold: nothing was measured")
                else:
                    row.update(status="fail", why=f"{rule} {o['status']} on the scaffold: {o['message']}")
            elif "manual_evidence" in chk:
                row.update(check="manual evidence: " + _text((chk["manual_evidence"] or {}).get("name")),
                           status="needs_evidence", why="a person reads the evidence; never passed by machine")
            else:
                row.update(check="none", status="unverified", why="it names no check")
            standards_out.append(row)
        report["standards"] = standards_out

        # 2. the five rules, by feature_audit, on the scaffold's own url_map and MCP registry
        probed = _probe(overlay, db, key, mcp_tools, v["env_flag"], timeout)
        fa_cfg = {**feature_audit.load_config(overlay), "features": [feature]}
        audit = feature_audit.audit(overlay, rules=probed["rules"], mcp=probed["mcp"], config=fa_cfg)
        f_out = audit["features"][0] if audit["features"] else {"cells": {}}
        hand = [h for h in audit["route_ids"]["hand_written"] if f"/{key}/" in h.split(":", 1)[0]]
        report["feature_audit"] = {
            "feature": feature["key"],
            "cells": {r: {"status": c["status"], "gaps": c.get("gaps") or []} for r, c in f_out["cells"].items()},
            "gaps": sum(len(c.get("gaps") or []) for c in f_out["cells"].values()),
            "config_errors": audit["config_errors"], "hand_written_routes": hand}

        # 3. the generated tests: red without the service, green with it
        test = f"tests/test_{key}.py"
        green = _pytest(overlay, db, test, v["env_flag"], timeout)
        hidden = overlay / "tools" / key
        hidden.rename(overlay / "tools" / f".{key}.hidden")
        try:
            red = _pytest(overlay, tmp / "red.db", test, v["env_flag"], timeout)
        finally:
            (overlay / "tools" / f".{key}.hidden").rename(hidden)
        report["tests"] = {"file": test, "green": green, "red": red,
                           "red_first": red["rc"] != 0 and green["rc"] == 0}

        # 4. the scaffold's own pieces
        report["pieces"] = _pieces(overlay, v, feature)
        b = behind(n, forced)
        report["behind"] = b
        why = []
        why += [f"{s['id']}: {s['why']}" for s in standards_out if s["status"] in ("fail", "unverified")]
        why += [f"feature_audit {r}: {g['detail']}" for r, c in report["feature_audit"]["cells"].items()
                for g in c["gaps"]]
        why += [f"feature_audit: {e}" for e in report["feature_audit"]["config_errors"]]
        why += [f"a hand-written route path: {h}" for h in hand]
        if not report["tests"]["red_first"]:
            why.append(f"{test} is not red without the service and green with it "
                       f"(green rc {green['rc']}: {green['summary']}; red rc {red['rc']})")
        why += [f"{p['piece']}: {p['why']}" for p in report["pieces"] if not p["ok"]]
        if b["behind"]:
            why.append(f"{BEHIND}: " + ", ".join(m["id"] for m in b["missing"] + b["stale_version"]))
        report["why"] = why
        report["result"] = "pass" if not why else "drift"
        counts: dict[str, int] = {}
        for s in standards_out:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        report["summary"] = {"standards": counts, "gaps": report["feature_audit"]["gaps"],
                             "pieces_failing": sum(1 for p in report["pieces"] if not p["ok"])}
        if keep:
            shutil.copytree(staging, keep, dirs_exist_ok=True)
    except Exception as exc:  # noqa: BLE001 — a check that could not run is drift, never a pass
        report.update(result="drift", why=(report.get("why") or []) + [f"the drift check could not run: {exc}"],
                      error=str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        report["seconds"] = round(time.monotonic() - t0, 1)
    return report


# ── the ledger: proposed, drift-checked, then a person publishes or retires ──


def _connection():
    """The platform database without the row-security predicate (the ledger is the platform's own
    engineering governance and has no label column, as coe_standard_events)."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def read_events(conn=None, path_id: Optional[str] = None) -> list[dict]:
    own = conn is None
    conn = conn or _connection()
    try:
        sql = f"SELECT {', '.join(_COLS)} FROM {TABLE}"  # nosec B608 — fixed table and columns
        params: tuple = ()
        if path_id:
            sql += " WHERE path_id = %s"
            params = (path_id,)
        rows = conn.execute(sql + " ORDER BY created_at, event_id", params).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Each path's rows in hash-chain order, plus ``_problems``."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["path_id"]), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for pid, items in sorted(by.items()):
        nxt = {str(r["prev_hash"]): r for r in items}
        ordered, prev = [], GENESIS
        while prev in nxt:
            r = nxt.pop(prev)
            ordered.append(r)
            prev = str(r["row_hash"])
        for r in items:
            if _row_hash(r) != r["row_hash"]:
                out["_problems"].append({"event_id": r["event_id"], "path_id": pid,
                                         "problem": "row hash does not match its content"})
            elif digest(json.loads(r["entry_json"] or "{}")) != r["entry_digest"]:
                out["_problems"].append({"event_id": r["event_id"], "path_id": pid,
                                         "problem": "the entry does not match its digest"})
        for r in nxt.values():
            out["_problems"].append({"event_id": r["event_id"], "path_id": pid,
                                     "problem": "not on the path's hash chain"})
        out[pid] = ordered
    return out


def _detail(row: dict) -> dict:
    try:
        return json.loads(row.get("detail_json") or "{}")
    except ValueError:
        return {}


def replay(ordered: list[dict]) -> dict:
    """One path's state from its rows in chain order. Pure.

    ``{versions: {v: record}, order, in_use, latest, history}``. A version's state is its last
    decision (proposed, published, refused, retired); a published version a newer publication
    replaced reads ``superseded``. ``in_use`` is the newest published version not retired.
    """
    versions: dict[str, dict] = {}
    order: list[str] = []
    in_use: Optional[str] = None
    history = []
    for r in ordered:
        v = str(r["version"])
        det = _detail(r)
        rec = versions.get(v)
        if rec is None:
            rec = versions[v] = {"version": v, "entry": json.loads(r["entry_json"]),
                                 "entry_digest": r["entry_digest"], "author": r["actor"],
                                 "author_kind": r["actor_kind"], "proposed_at": r["created_at"],
                                 "state": None, "drift": None, "event": r["event_id"]}
            order.append(v)
        ev = r["event"]
        if ev == "drift_checked":
            rec["drift"] = {"event_id": r["event_id"], "at": r["created_at"], "by": r["actor"],
                            "by_kind": r["actor_kind"], **det}
        elif ev in ("seeded", "proposed", "published", "refused", "retired"):
            state = det.get("seed") if ev == "seeded" else ev
            rec["state"] = state
            if state == "published":
                if in_use and in_use != v:
                    versions[in_use]["state"] = "superseded"
                    versions[in_use]["superseded_by"] = v
                in_use = v
                rec.update(published_at=r["created_at"], published_by=r["actor"],
                           published_kind=r["actor_kind"], published_event=r["event_id"],
                           published_step=r["step_id"])
            if ev == "retired":
                rec.update(retired_at=r["created_at"], retired_by=r["actor"], retired_event=r["event_id"])
                if in_use == v:
                    in_use = None
        history.append({k: r[k] for k in ("event_id", "version", "event", "actor", "actor_kind", "actor_role",
                                          "step_id", "reason", "entry_digest", "created_at", "row_hash",
                                          "prev_hash")} | {"detail": det})
    latest = versions[order[-1]] if order else None
    return {"versions": versions, "order": order, "in_use": in_use, "latest": latest, "history": history}


def _state(path_id: str, conn) -> tuple[dict, list[dict]]:
    ch = chain(read_events(conn, path_id))
    return replay(ch.get(path_id, [])), ch["_problems"]


def _head(st: dict) -> str:
    return st["history"][-1]["row_hash"] if st["history"] else GENESIS


def _append(conn, *, path_id: str, version: str, event: str, actor: str, actor_kind: str, entry: dict,
            prev_hash: str, actor_role: str = "", step_id: str = "", reason: str = "",
            detail: Optional[dict] = None) -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    norm = normalise(entry)
    row = {"event_id": f"coeg-{uuid.uuid4().hex[:16]}", "path_id": path_id, "version": version, "event": event,
           "actor": actor, "actor_kind": actor_kind, "actor_role": actor_role or "", "step_id": step_id or "",
           "reason": reason or "", "entry_digest": digest(norm), "entry_json": canonical(norm),
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
            raise GoldenPathRefused(409, "concurrent", f"{path_id} changed while you were deciding: reload it") from exc
        raise GoldenPathRefused(503, "store_unavailable",
                                f"the golden path ledger could not be written, so nothing changed ({exc})") from exc
    return row


def _actor_kind(actor: dict) -> str:
    if _text(actor.get("id")).startswith("bootstrap:"):
        return "bootstrap"
    if actor.get("role") == "service" or _text(actor.get("id")).startswith("agent:"):
        return "agent"
    return "person"


def builders(rec: Optional[dict]) -> dict[str, str]:
    """Who may not decide a version (``separate_from: builders``): its author."""
    who = _text((rec or {}).get("author"))
    return {who: f"authored version {(rec or {}).get('version')} of this path"} if who else {}


def seed(conn=None, cfg: Optional[dict] = None) -> dict:
    """Record each declared path the ledger has not seen, by the labelled bootstrap identity.

    ``seed: published`` enters published only when the path is not behind the register's seeds;
    otherwise it enters proposed and waits for a person like any other draft. Idempotent.
    """
    cfg = cfg or load_config()
    ident = _text(cfg.get("seed_identity")) or "bootstrap:icx-coe-04-seed"
    forced = in_force("register")
    own = conn is None
    conn = conn or _connection()
    out = {"seeded": [], "skipped": []}
    try:
        for e in cfg["paths"]:
            n = normalise(e)
            st, problems = _state(n["id"], conn)
            if st["history"] or problems:
                out["skipped"].append(n["id"])
                continue
            want = _text(e.get("seed")) or "proposed"
            if want not in SEED_STATES:
                raise ValueError(f"{n['id']}: seed is one of {', '.join(SEED_STATES)}")
            b = behind(n, forced)
            state = "published" if want == "published" and not b["behind"] else "proposed"
            _append(conn, path_id=n["id"], version=n["version"], event="seeded", actor=ident, actor_kind="bootstrap",
                    entry=n, prev_hash=GENESIS, reason="seeded from args/golden_paths.yaml",
                    detail={"seed": state, "behind": b["behind"]})
            out["seeded"].append({"id": n["id"], "version": n["version"], "state": state})
    finally:
        if own:
            conn.close()
    return out


def propose(entry: dict, actor: Optional[dict], note: str = "", conn=None) -> dict:
    """Record a new path version as a draft. Anyone signed in, or an agent: it decides nothing."""
    if not isinstance(actor, dict) or not _text(actor.get("id")):
        raise GoldenPathRefused(401, "no_actor", "a proposal needs a signed-in person or a named agent")
    n = normalise(entry)
    errors = validate_entry(n)
    if errors:
        raise GoldenPathRefused(400, "invalid", "; ".join(errors))
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems = _state(n["id"], conn)
        if problems:
            raise GoldenPathRefused(409, "chain_broken", f"{n['id']}'s history does not verify: {problems[0]['problem']}")
        if n["version"] in st["versions"]:
            raise GoldenPathRefused(409, "version_exists", f"{n['id']} {n['version']} exists: propose a new version")
        open_v = [v for v, r in st["versions"].items() if r["state"] == "proposed"]
        if open_v:
            raise GoldenPathRefused(409, "open_version", f"{n['id']} {open_v[0]} is still proposed: it is published "
                                                         "or refused before a new version is proposed")
        row = _append(conn, path_id=n["id"], version=n["version"], event="proposed", actor=_text(actor["id"]),
                      actor_kind=_actor_kind(actor), actor_role=_text(actor.get("role")), entry=n,
                      reason=_text(note), prev_hash=_head(st))
    finally:
        if own:
            conn.close()
    return {"path_id": n["id"], "version": n["version"], "state": "proposed", "event_id": row["event_id"],
            "entry_digest": row["entry_digest"], "author_kind": row["actor_kind"],
            "next": "run its drift check; a person who did not author it publishes it or refuses"}


def record_drift(path_id: str, version: Optional[str], actor: Optional[dict], conn=None,
                 cfg: Optional[dict] = None) -> dict:
    """Run a version's drift check against the standards IN FORCE (the ledger) and record the result.

    Measuring decides nothing: anyone signed in, or an agent.
    """
    if not isinstance(actor, dict) or not _text(actor.get("id")):
        raise GoldenPathRefused(401, "no_actor", "a drift check is run by a signed-in person or a named agent")
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems = _state(_text(path_id).lower(), conn)
        if not st["history"]:
            raise GoldenPathRefused(404, "unknown_path", f"no golden path {path_id} in the ledger")
        if problems:
            raise GoldenPathRefused(409, "chain_broken", f"{path_id}'s history does not verify")
        ver = _text(version) or st["order"][-1]
        rec = st["versions"].get(ver)
        if rec is None:
            raise GoldenPathRefused(404, "unknown_version", f"{path_id} has no version {ver}")
        forced = in_force("ledger", conn)
        rep = drift(rec["entry"], forced=forced, source="ledger", cfg=cfg)
        detail = {k: rep.get(k) for k in ("result", "why", "summary", "template_digest", "standards",
                                          "feature_audit", "tests", "pieces", "behind", "seconds", "key")}
        row = _append(conn, path_id=rec["entry"]["id"], version=ver, event="drift_checked", actor=_text(actor["id"]),
                      actor_kind=_actor_kind(actor), actor_role=_text(actor.get("role")), entry=rec["entry"],
                      detail=detail, prev_hash=_head(st))
    finally:
        if own:
            conn.close()
    return {"path_id": rec["entry"]["id"], "version": ver, "event_id": row["event_id"], **detail}


def _gate(rec: dict, forced: list[dict], root: Optional[Path] = None) -> list[str]:
    """What must be true before a version is published. A gate that cannot be evaluated blocks."""
    why = []
    d = rec.get("drift")
    if not d:
        why.append("no drift check has been recorded for this version: run it first")
    else:
        if d.get("result") != "pass":
            why.append("its newest drift check did not pass: " + "; ".join((d.get("why") or [])[:3]))
        try:
            now = template_digest(rec["entry"], root)
        except OSError as exc:
            now = f"unreadable ({exc})"
        if d.get("template_digest") != now:
            why.append("the template changed since its newest drift check: run it again")
    b = behind(rec["entry"], forced)
    if b["behind"]:
        why.append(f"{BEHIND}: " + ", ".join(f"{m['id']} {m['version']}" for m in b["missing"] + b["stale_version"]))
    return why


def _decide(step: str, path_id: str, version: str, want: tuple[str, ...], *, decision: str, reason: str,
            read_digest: str, actor: Optional[dict], conn=None) -> dict:
    from tools.workflow_hitl import declared

    pid, ver = _text(path_id).lower(), _text(version)
    if not isinstance(actor, dict):
        raise GoldenPathRefused(401, "no_person", f"{step} is a signed-in person's declared step on /idp/golden-paths")
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems = _state(pid, conn)
        if not st["history"]:
            raise GoldenPathRefused(404, "unknown_path", f"no golden path {pid} in the ledger")
        if problems:
            raise GoldenPathRefused(409, "chain_broken", f"{pid}'s history does not verify: {problems[0]['problem']}")
        rec = st["versions"].get(ver)
        if rec is None:
            raise GoldenPathRefused(404, "unknown_version", f"{pid} has no version {ver}")
        if rec["state"] not in want:
            raise GoldenPathRefused(409, "out_of_order", f"{pid} {ver} is {rec['state']}: {step} needs it "
                                                         + " or ".join(want))
        if _text(read_digest) != rec["entry_digest"]:
            raise GoldenPathRefused(409, "digest", f"{pid} {ver} is not what you read: its digest is "
                                                   f"{rec['entry_digest'][:12]} (reload it)")
        dec = _text(decision).lower()
        if step == "publish" and dec == "publish":
            gate = _gate(rec, in_force("ledger", conn))
            if gate:
                raise GoldenPathRefused(409, "gate", f"{pid} {ver} cannot be published: " + "; ".join(gate))
        payload = {"path_id": pid, "version": ver, "entry_digest": rec["entry_digest"],
                   "drift_event": (rec.get("drift") or {}).get("event_id")}
        try:
            taken = declared.take_step(TEMPLATES[step], surface=SURFACES[step], decision=dec,
                                       subject=f"golden_path:{pid}@{ver}", reason=_text(reason), actor=actor,
                                       payload=payload, separated_from=builders(rec))
        except declared.StepRefused as exc:
            raise GoldenPathRefused(exc.status, exc.code, exc.message) from exc
        event = {"publish": {True: "published", False: "refused"},
                 "retire": {True: "retired", False: "kept"}}[step][taken["affirmative"]]
        row = _append(conn, path_id=pid, version=ver, event=event, actor=taken["decided_by"], actor_kind="person",
                      actor_role=_text(actor.get("role")), step_id=taken["step_id"], reason=_text(reason),
                      entry=rec["entry"], prev_hash=_head(st),
                      detail={"decision": taken["decision"], "template_id": taken["template_id"],
                              "drift_event": payload["drift_event"], "replaces": st["in_use"]})
    finally:
        if own:
            conn.close()
    return {"path_id": pid, "version": ver, "event": event, "event_id": row["event_id"], "step": taken,
            "decided_by": taken["decided_by"], "author": rec["author"]}


def publish(path_id: str, version: str, *, decision: str, reason: str, read_digest: str,
            actor: Optional[dict], conn=None) -> dict:
    """A person who did not author it publishes a proposed version, or refuses it (declared step).

    Publishing only when the gate holds: its newest drift check passed on the template as it is now,
    and it is not behind the standards in force.
    """
    return _decide("publish", path_id, version, ("proposed",), decision=decision, reason=reason,
                   read_digest=read_digest, actor=actor, conn=conn)


def retire(path_id: str, version: str, *, decision: str, reason: str, read_digest: str,
           actor: Optional[dict], conn=None) -> dict:
    """A person who did not author it retires a published version, or keeps it (declared step)."""
    return _decide("retire", path_id, version, ("published", "superseded"), decision=decision, reason=reason,
                   read_digest=read_digest, actor=actor, conn=conn)


# ── reading ──────────────────────────────────────────────────────────────────


def _next(rec: dict, st: dict) -> list[str]:
    if rec.get("state") == "proposed":
        return ["drift", "publish"]
    if rec.get("state") in ("published", "superseded"):
        return ["drift", "retire"]
    return []


def _summary(pid: str, st: dict, forced: list[dict], decl: Optional[dict]) -> dict:
    use = st["versions"].get(st["in_use"]) if st["in_use"] else None
    latest = st["latest"] or {}
    entry = (use or latest).get("entry") or decl or {}
    b = behind(entry, forced) if entry else None
    drift_rec = (use or latest).get("drift") if (use or latest) else None
    return {"id": pid, "title": entry.get("title", ""), "owner": entry.get("owner", ""), "repo": entry.get("repo", ""),
            "in_use": st["in_use"], "latest_version": latest.get("version"), "latest_state": latest.get("state"),
            "published_by": (use or {}).get("published_by"), "published_kind": (use or {}).get("published_kind"),
            "behind": b, "drift": drift_rec and {k: drift_rec.get(k) for k in ("event_id", "at", "by", "result",
                                                                                "why", "summary", "template_digest")},
            "satisfies": entry.get("satisfies") or [], "events": len(st["history"]),
            "recorded": bool(st["history"])}


def paths_view(conn=None) -> dict:
    """Every path (the ledger, plus declared seeds not yet recorded), against the standards in force."""
    cfg = load_config()
    rows = read_events(conn)
    ch = chain(rows)
    try:
        forced = in_force("ledger", conn)
        forced_err = ""
    except Exception as exc:  # noqa: BLE001 — unread is said; behind is then not judged
        forced, forced_err = [], str(exc)
    decl = {normalise(e)["id"]: normalise(e) for e in cfg["paths"]}
    ids = sorted(set(decl) | {k for k in ch if k != "_problems"})
    out = [_summary(pid, replay(ch.get(pid, [])), forced, decl.get(pid)) for pid in ids]
    return {"paths": out, "in_force": [{"id": s["id"], "version": s["version"], "title": s["title"]} for s in forced],
            "in_force_error": forced_err,
            "chain": {"intact": not ch["_problems"], "problems": ch["_problems"], "rows": len(rows)},
            "config": {"path": CONFIG_RELPATH, "sha256": cfg["_sha256"]}, "templates": dict(TEMPLATES)}


def path_view(path_id: str, conn=None) -> dict:
    """One path: every version, its drift checks, the history, the step it waits for."""
    pid = _text(path_id).lower()
    own = conn is None
    conn = conn or _connection()
    try:
        st, problems = _state(pid, conn)
        forced = in_force("ledger", conn)
    finally:
        if own:
            conn.close()
    try:
        decl = path_entry(pid)
    except KeyError:
        decl = None
    if not st["history"] and decl is None:
        raise GoldenPathRefused(404, "unknown_path", f"no golden path {pid}")
    versions = []
    for v in st["order"]:
        rec = st["versions"][v]
        versions.append({**{k: rec.get(k) for k in ("version", "entry", "entry_digest", "author", "author_kind",
                                                    "state", "proposed_at", "published_by", "published_kind",
                                                    "published_event", "retired_by", "superseded_by", "drift")},
                         "behind": behind(rec["entry"], forced), "builders": builders(rec),
                         "next": _next(rec, st),
                         "gate": _gate(rec, forced) if rec.get("state") == "proposed" else []})
    return {**_summary(pid, st, forced, decl), "versions": versions, "history": st["history"],
            "declared": decl, "in_force": [{"id": s["id"], "version": s["version"], "title": s["title"]}
                                           for s in required(decl or (st["latest"] or {}).get("entry") or {}, forced)]
            if (decl or st["latest"]) else [],
            "chain": {"intact": not problems, "problems": problems}, "templates": dict(TEMPLATES)}


def entry_from_form(data: dict, base: Optional[dict] = None) -> dict:
    """A new version from the page's propose form: the base version's fields, a new version and declaration."""
    base = normalise(base or {})
    sat = data.get("satisfies")
    if isinstance(sat, str):
        sat = [s for s in re.split(r"[\s,]+", sat) if s]
    how = {s["id"]: s["by"] for s in base.get("satisfies") or []}
    items = []
    for s in sat or base.get("satisfies") or []:
        if isinstance(s, str):
            sid, _, ver = s.partition("@")
            s = {"id": sid.upper(), "version": ver, "by": how.get(sid.upper(), "")}
        items.append(s)
    return {**base, "id": data.get("id") or base.get("id"), "version": data.get("version"),
            "title": data.get("title") or base.get("title"), "owner": data.get("owner") or base.get("owner"),
            "repo": data.get("repo") or base.get("repo"), "template": data.get("template") or base.get("template"),
            "satisfies": items}


# ── the twin's evidence and judgement (canvas idp, tools/twin_core/adapters/coe_standards.py) ──


def twin_evidence() -> dict:
    """What the governance twin reads: each path's ledger state and the standards in force. Never raises."""
    ev: dict[str, Any] = {"paths": None, "errors": {}}
    try:
        cfg = load_config()
        ch = chain(read_events())
        forced = in_force("ledger")
    except Exception as exc:  # noqa: BLE001 — unreadable is unverified
        ev["errors"]["golden_paths"] = str(exc)
        return ev
    decl = {normalise(e)["id"]: normalise(e) for e in cfg["paths"]}
    items = []
    for pid in sorted(set(decl) | {k for k in ch if k != "_problems"}):
        st = replay(ch.get(pid, []))
        use = st["versions"].get(st["in_use"]) if st["in_use"] else None
        try:
            tdig = template_digest(use["entry"]) if use else None
        except OSError:
            tdig = None
        items.append({
            "id": pid, "declared": pid in decl,
            "in_use": use and {"version": use["version"], "event": use.get("published_event"),
                               "by": use.get("published_by"), "kind": use.get("published_kind"),
                               "author": use.get("author"), "satisfies": use["entry"].get("satisfies") or [],
                               "repo": use["entry"].get("repo"), "drift": use.get("drift") and {
                                   k: use["drift"].get(k) for k in ("event_id", "at", "result", "template_digest")},
                               "template_digest_now": tdig},
            "waiting": [{"version": r["version"], "event": r["event"], "at": r["proposed_at"]}
                        for r in st["versions"].values() if r["state"] == "proposed"]})
    ev["paths"] = items
    ev["problems"] = ch["_problems"]
    ev["in_force"] = [{"id": s["id"], "version": s["version"]} for s in forced]
    ev["policy"] = {k: cfg.get(k) for k in ("waiting_warn_days", "drift_stale_days")}
    return ev


def twin_judge(ev: dict, policy: dict, observed_at: str) -> tuple[list[dict], list[dict], dict]:
    """``(checks, findings, summary)`` for the golden paths. Pure: a replay judges the same."""
    from tools.twin_core.governance import age_hours, check, finding

    errors = ev.get("errors") or {}
    paths = ev.get("paths")
    checks = [check("coe.golden_paths.ledger", "Golden path ledger (coe_golden_path_events)", paths is not None,
                    errors.get("golden_paths") or "ledger not read")]
    findings: list[dict] = []
    pol = {**(ev.get("policy") or {}), **(policy or {})}
    waiting = float(pol.get("waiting_warn_days") or 14)
    stale = float(pol.get("drift_stale_days") or 30)
    forced = ev.get("in_force") or []
    for p in ev.get("problems") or []:
        findings.append(finding("coe.golden_path.chain_broken", "critical", f"{p['path_id']}: {p['problem']}",
                                thread_id=f"coe_golden_path:{p['event_id']}",
                                recommendation="The path's history cannot be trusted; the ledger is append-only, "
                                               "so find who wrote the row and treat it as an incident."))
    in_use = 0
    for p in paths or []:
        use = p.get("in_use")
        # A path no version of which is published, or never drift-checked, is unverified, never passed.
        checks.append(check(f"coe.golden_path.{p['id']}", f"A published version of golden path {p['id']}",
                            use is not None, "no version of this path is published: unverified"))
        for w in p.get("waiting") or []:
            age = age_hours(w.get("at"), observed_at)
            if age is not None and age > waiting * 24:
                findings.append(finding("coe.golden_path.awaiting_publish", "low",
                                        f"{p['id']} {w['version']} has waited {age / 24:.0f} days for a person",
                                        thread_id=f"coe_golden_path:{w['event']}",
                                        recommendation="A person who did not author it publishes it or refuses."))
        if use is None:
            continue
        in_use += 1
        thread = f"coe_golden_path:{use['event']}"
        d = use.get("drift")
        checks.append(check(f"coe.golden_path.{p['id']}.drift", f"A drift check of {p['id']} {use['version']}",
                            d is not None, "no drift check is recorded for the version in use: unverified"))
        b = behind({"repo": use.get("repo"), "satisfies": use.get("satisfies") or [], "id": p["id"]}, forced)
        if b["behind"]:
            findings.append(finding(
                "coe.golden_path.behind", "high",
                f"{p['id']} {use['version']} is behind the standards in force: "
                + ", ".join(f"{m['id']} {m['version']}" for m in b["missing"] + b["stale_version"]),
                thread_id=thread,
                recommendation="Propose a new version that declares them, run its drift check, and a person who "
                               "did not author it publishes it."))
        for sid in b["not_in_force"]:
            findings.append(finding("coe.golden_path.declares_retired_standard", "low",
                                    f"{p['id']} {use['version']} declares {sid}, which is not in force",
                                    thread_id=thread, recommendation="Propose a version without it."))
        if d is not None:
            dthread = f"coe_golden_path:{d['event_id']}"
            if d.get("result") != "pass":
                findings.append(finding("coe.golden_path.drift", "high",
                                        f"{p['id']} {use['version']}: the newest drift check found drift",
                                        thread_id=dthread, recommendation="Fix the template, then run the check."))
            if use.get("template_digest_now") and d.get("template_digest") != use["template_digest_now"]:
                findings.append(finding("coe.golden_path.drift_stale", "medium",
                                        f"{p['id']} {use['version']}: the template changed after its drift check",
                                        thread_id=dthread, recommendation="Run the drift check again."))
            age = age_hours(d.get("at"), observed_at)
            if age is not None and age > stale * 24:
                findings.append(finding("coe.golden_path.drift_old", "low",
                                        f"{p['id']} {use['version']}: the newest drift check is {age / 24:.0f} days old",
                                        thread_id=dthread, recommendation="Run the drift check again."))
        if use.get("kind") == "bootstrap":
            findings.append(finding("coe.golden_path.bootstrap_only", "low",
                                    f"{p['id']} {use['version']} is in use on a bootstrap seed: no person has "
                                    "published it", thread_id=thread,
                                    recommendation="A person publishes a version of it, or leaves it seeded."))
        if use.get("by") and use.get("by") == use.get("author") and use.get("kind") == "person":
            findings.append(finding("coe.golden_path.self_published", "critical",
                                    f"{p['id']} {use['version']} was published by its author", thread_id=thread,
                                    recommendation="Treat it as unpublished; another person publishes it."))
    return checks, findings, {"golden_paths": len(paths or []), "golden_paths_in_use": in_use}


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Golden paths: conformant by construction (icx-coe-04)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help=f"exit 1 when a path is behind ({BEHIND})")
    mode.add_argument("--drift", action="store_true", help="scaffold each path into a temp tree and judge it")
    mode.add_argument("--seed", action="store_true", help="record the declared paths the ledger has not seen")
    ap.add_argument("--source", choices=("register", "ledger"), default="register",
                    help="which standards are in force: the register's reviewed seeds (CI) or the ledger")
    ap.add_argument("--path", default=None, help="only this path (--drift)")
    ap.add_argument("--keep", default=None, help="copy the scaffold out to this directory (--drift)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)
    if args.check:
        out = check(args.source)
        print(json.dumps(out, indent=2) if args.json else "\n".join(
            [r["why"] for r in out["paths"] if r["behind"]] or
            [f"every golden path declares the {len(out['in_force'])} standards in force ({args.source})"]))
        return 0 if out["ok"] else 1
    if args.drift:
        cfg = load_config()
        reps = [drift(normalise(e), source=args.source, cfg=cfg, keep=Path(args.keep) / normalise(e)["id"]
                      if args.keep else None)
                for e in cfg["paths"] if not args.path or normalise(e)["id"] == args.path]
        if not reps:
            print(f"no golden path {args.path!r}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"ok": all(r["result"] == "pass" for r in reps), "paths": reps}, indent=2, default=str))
        else:
            for r in reps:
                print(f"{r['path']} {r['version']}: {r['result']} in {r['seconds']} s "
                      f"({json.dumps(r.get('summary', {}).get('standards', {}))})")
                for w in r.get("why") or []:
                    print(f"  ! {w}")
        return 0 if all(r["result"] == "pass" for r in reps) else 1
    if args.seed:
        print(json.dumps(seed(), indent=2))
        return 0
    print(json.dumps(paths_view(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
