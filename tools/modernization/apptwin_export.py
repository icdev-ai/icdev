# CUI // SP-CTI
"""Export one legacy application as an ICDEV[OS] ``icos-apptwin/v1`` bundle (icx-bun-01).

ICDEV[OS] twins every legacy application it is asked to consolidate from one bundle per application; it carries
what is in the bundle and recomputes nothing. This module only ASSEMBLES what ICDEV[IT]'s own tools already
produced, so every section is traceable to the tool that made it:

    inventory     components, apis, data, dependencies   the legacy_* tables (tools/modernization/legacy_analyzer.py)
    --features    features (basis observed)              tools/aiify/observed_inventory.py output (its ``features``)
    --static-features  features (provenance static)      tools/modernization/static_features.py (entry points)
    --extract     features AND processes, no model       tools/modernization/bundle_features.py (icx-mdz-03): static
                                                         and REA-observed features with file:line / route evidence;
                                                         static and observed processes, each step normalised by the
                                                         reviewed rule set; a process a person rejected is left out
    --process     processes (basis document | observed)  python -m tools.workflow_canvas.processify --json
    --aiify       aiify                                  python -m tools.aiify.engine scan --json
    --integrity   integrity                              python tools/integrity/engine.py --json (SIPA)
    --seven-r     seven_r                                python tools/modernization/seven_r_assessor.py --json
    (database)    migration                              the latest migration_plans row and its migration_tasks
    --waves       migration.waves                        tools/modernization/app_wave_planner.py (the application's own
                                                         waves, osx-fth-13), or the migration canvas's (JSON list)
    --strangler   migration.strangler                    tools/modernization/strangler_fig_manager.py status (JSON)
    --refactor    refactor                               tools/modernization/refactor_jobs.py (CAM's jobs), or
                                                         cam_refactor_engine.py --list-jobs rows (JSON list)
    --cloud-native cloud_native                          tools/modernization/cloud_native_jobs.py (CAM's cloud-native
                                                         jobs, osx-fth-14): every generated file by SHA-256, the
                                                         twelve factors, config by location, events; AI use cases
                                                         DRAFTED from --aiify's opportunities
    --ivv/--sbd/--rtm/--coherence/--readiness            already in the bundle's section shape
    (database)    ivv (when --ivv is not given)          tools/modernization/survivor_ivv.py (icx-mdz-06): ICDEV's
                                                         IV&V of the application's modernisation outputs, carried
                                                         only when an independent person signed it off at its
                                                         digest; else sections["ivv"] says why

A verification result (``--ivv``/``--sbd``/``--rtm``/``--coherence``) is passed through as it was made, with the
``subject`` its tool recorded (``tools/compliance/portable_result.py --bundle``, ``tools/workflow/app_coherence.py
--bundle``): this module never stamps a subject on one, because only the run that assessed the code knows what it saw.

A section with no input stays ``null``: ICDEV[OS] reads null as UNVERIFIED, never as a pass, so nothing here
fills a gap with a default. ``sections`` (icx-mdz-03) says, for every section and for the parts ICDEV[OS] plans on --
the waves, the CAM refactor jobs and the cloud-native twelve factors, container, configuration and events -- whether
the bundle carries it (``present``, with a count) or not (``missing``, with why), so an absent part is explicit, never
silently empty; ``--sections-out`` writes it. Nothing is invented either: a static feature is only what static_features grouped
from the entry points the analyser found, labelled ``static`` and never ``observed``, and ``consumers`` (which
needs every other application) is left to ICDEV[OS]. The bundle is validated against the vendored contract
(``context/icos/apptwin.schema.json``, pinned by SHA-256) and the rules ICDEV[OS]'s importer adds to it
(``agreement``) before it is written.

icx-mdz-04 (contract 1.189.0): what used to go beside the bundle (``<app>.features.json``) is IN it -- each feature's
provenance (``both``: read from source and seen running) and capability hints, each process's source, step signature
and review, each step's verb, object, evidence and APIs; ``sections`` is the bundle's own map; a strategy carries its
confidence, unmeasured criteria and whether it is assessable; waves derived before any plan are ``derived_waves``; a
planned wave carries the sum of its tasks' estimates. ``application.environment`` (security domain, impact level, ATO
boundary, hosting) comes only from the declared environments file (``args/modernisation_environments.yaml``, each
entry naming its source); an application not declared there has none, and the run's report says so.

    python tools/modernization/apptwin_export.py --app-id <legacy app id> [--aiify scan.json] [--process p.json ...]
        [--features inv.json] [--integrity sipa.json] [--seven-r 7r.json] [--at 2026-11-07T00:00:00Z] --out app.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- bootstrap the import root first.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

_BASE = repo_root(__file__)

SCHEMA = "icos-apptwin/v1"
SCHEMA_PATH = _BASE / "context" / "icos" / "apptwin.schema.json"
#: The digest of the vendored ICDEV[OS] contract (1.189.0, icx-mdz-04: a feature's ``provenance: both`` and capability
#: hints, a process's source, step signature and review, a step's verb and object, the ``sections`` map, ``derived_waves``,
#: a strategy's confidence and unmeasured criteria, a wave's ``estimated_hours``; ICDEV[OS] osx-mdz-01..04). A changed
#: file is refused, never silently used.
SCHEMA_SHA256 = "8f49b5285e9b6180275c89897ae77c205c6770f4a5a78618db1bae1c76f79b2f"
#: The contract version that digest is (context/icos/README.md).
CONTRACT_VERSION = "1.189.0"
PRODUCER = "icdev-it/apptwin_export"
PASSTHROUGH = ("ivv", "sbd", "rtm", "coherence", "readiness")
#: Every section a bundle may report (ICDEV[OS] icdev_os/twin/apptwin.py SECTIONS), and the parts of a section its
#: ``sections`` map names beside them (its PARTS). A ``sections`` key that is neither is refused there.
SECTIONS = ("components", "apis", "data", "dependencies", "consumers", "features", "processes",
            "integrity", "aiify", "readiness", "seven_r", "ivv", "sbd", "rtm", "coherence",
            "migration", "refactor", "cloud_native")
PARTS = ("migration.waves", "refactor.jobs", "cloud_native.twelve_factor", "cloud_native.containerisation",
         "cloud_native.config", "cloud_native.events")
#: The declared environment of each legacy application (icx-mdz-04): its security domain, impact level, ATO boundary
#: and hosting AS ITS DEPLOYMENT DECLARES THEM, each entry naming its source. Nothing here guesses one.
ENVIRONMENTS_REL = "args/modernisation_environments.yaml"
ENVIRONMENT_FIELDS = ("security_domain", "impact_level", "ato_boundary", "hosting")
IMPACT_LEVELS = ("il2", "il4", "il5", "il6")


#: The inventory sections ICDEV[OS]'s ``inventory_digest`` covers (its icdev_os/twin/apptwin.py INVENTORY), in order.
INVENTORY = ("components", "apis", "data", "dependencies", "consumers", "features", "processes")


class BundleError(ValueError):
    pass


def _pruned(x):
    """Null, [] and {} dropped at every depth: a field left out and a field given empty are the same."""
    if isinstance(x, dict):
        out = {k: _pruned(v) for k, v in x.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {})}
    if isinstance(x, list):
        return [_pruned(v) for v in x]
    return x


def inventory_digest(bundle: dict) -> str:
    """ICDEV[OS]'s inventory digest of ``bundle`` (icos-apptwin/v1, contract 1.120.0): SHA-256 over the canonical JSON of
    ``{section: [item, ...] | null}`` for each INVENTORY section, each item pruned (``_pruned``), each list of items
    sorted by its canonical JSON, keys sorted, no whitespace. ICDEV[OS] recomputes it from the bundle it imports; a
    verification result whose ``subject`` names another digest was taken before a significant change, and counts for
    nothing there."""
    def canon(x) -> str:
        return json.dumps(x, sort_keys=True, separators=(",", ":"))
    doc = {s: None if bundle.get(s) is None else sorted((_pruned(i) for i in bundle[s]), key=canon) for s in INVENTORY}
    return hashlib.sha256(canon(doc).encode()).hexdigest()


def subject(bundle: dict) -> dict:
    """What a verification result run on the code ``bundle`` describes assessed: its version (if any) and digest."""
    v = (bundle.get("application") or {}).get("version")
    return {**({"version": v} if v else {}), "inventory_digest": inventory_digest(bundle)}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def _iso(ts) -> str | None:
    if ts in (None, ""):
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(ts).replace(" ", "T")
    s = re.sub(r"([+-]\d\d:?\d\d)Z$", r"\1", s)  # "+00:00Z" (an offset AND a Z) is not a timestamp
    if not re.search(r"(Z|[+-]\d\d:?\d\d)$", s):
        s += "Z"
    return s


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("icdev")
    except Exception:  # noqa: BLE001 -- an unpackaged checkout still says what it is
        return "unpackaged"


def schema() -> dict:
    raw = SCHEMA_PATH.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != SCHEMA_SHA256:
        raise BundleError(f"{SCHEMA_PATH} is not the pinned ICDEV[OS] contract (sha256 {got}, pinned {SCHEMA_SHA256})")
    return json.loads(raw)


def validate(bundle: dict) -> list[str]:
    """The bundle's errors against the pinned contract (its JSON Schema), then, when it meets that, against the rules
    ICDEV[OS]'s importer adds to it (``agreement``). Empty: ICDEV[OS] would take it."""
    import jsonschema
    v = jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())
    errors = sorted(f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in v.iter_errors(bundle))
    return errors or agreement(bundle)


def _relpath_ok(v: str) -> bool:
    """ICDEV[OS]'s ``_relpath``: a location is relative to the source, never where the analysis ran."""
    head = v.split("/")[0]
    return not (v.startswith(("/", "\\", "~")) or (len(head) > 1 and head[1] == ":") or ".." in v.split("/"))


def step_digest(steps: list[str]) -> str:
    """A process's step-signature digest: SHA-256 of its steps' ``verb:object``, in order, one per line (ICDEV[OS]
    ``apptwin.step_digest``; ``bundle_features.signature`` computes the same)."""
    return hashlib.sha256("\n".join(steps).encode()).hexdigest()


def _part(bundle: dict, key: str):
    """What a ``sections`` key names in the bundle (ICDEV[OS] ``apptwin._part``)."""
    sec, _, part = key.partition(".")
    v = bundle.get(sec)
    if not part or v is None:
        return v
    if sec == "cloud_native":
        kinds = dict(CLOUD_PARTS)[part]
        return [a for a in v.get("artefacts") or [] if a.get("kind") in kinds]
    return v.get(part)


def agreement(bundle: dict) -> list[str]:
    """What ICDEV[OS]'s importer refuses beyond the JSON Schema (its ``icdev_os/twin/apptwin.py`` validators, 1.189.0):
    a feature's provenance against its basis; a process's source against its basis, its signature against its steps,
    its review against its signature; a step's signature against its verb and object, its evidence a relative
    location; every API, table, step, feature and component a feature, process, consumer, requirement or wave names
    declared; ids unique; and the ``sections`` map agreeing with what the bundle carries. Each is said, never fixed."""
    app = (bundle.get("application") or {}).get("id")
    out: list[str] = []
    apis = {f"{str(a['method']).upper()} {a['path']}" for a in bundle.get("apis") or []}
    tables = {t["name"] for t in bundle.get("data") or []}
    procs = bundle.get("processes") or []
    steps = {f"{p['id']}.{s['id']}" for p in procs for s in p["steps"]}
    feats = [f["id"] for f in bundle.get("features") or []]
    for f in bundle.get("features") or []:
        pv, basis = f.get("provenance"), f.get("basis")
        if pv in ("static", "both") and basis not in ("source", "decompiled"):
            out.append(f"feature {f['id']}: a {pv} feature is read from source or a binary, not {basis}")
        if pv == "observed" and basis != "observed":
            out.append(f"feature {f['id']}: an observed feature has basis observed, not {basis}")
    for p in procs:
        src, basis, sig, rv = p.get("source"), p.get("basis"), p.get("signature"), p.get("review")
        if src == "static" and basis not in ("source", "decompiled"):
            out.append(f"process {p['id']}: a static process is read from source or a binary, not {basis}")
        if src == "observed" and basis != "observed":
            out.append(f"process {p['id']}: an observed process has basis observed, not {basis}")
        for st in p["steps"]:
            if st.get("signature") is not None and st.get("verb") is not None:
                want = f"{st['verb']}:{st['object']}" if st.get("object") else st["verb"]
                if st["signature"] != want:
                    out.append(f"process {p['id']} step {st['id']}: signature {st['signature']!r} is not {want!r}")
            if st.get("evidence") is not None and not _relpath_ok(str(st["evidence"])):
                out.append(f"process {p['id']} step {st['id']}: evidence {st['evidence']!r} is a local path")
        if sig is not None:
            if sig["digest"] != step_digest(sig["steps"]):
                out.append(f"process {p['id']}: its signature's digest is not the digest of its steps")
            if len(sig["steps"]) != len(p["steps"]):
                out.append(f"process {p['id']}: its signature has {len(sig['steps'])} steps, the process {len(p['steps'])}")
            elif bad := [s["id"] for s, x in zip(p["steps"], sig["steps"]) if s.get("signature") not in (None, x)]:
                out.append(f"process {p['id']}: steps {bad} are not signed as its signature says")
        if rv is not None and rv.get("state") != "unreviewed":
            if not (rv.get("step_id") and rv.get("by") and rv.get("signature_digest")):
                out.append(f"process {p['id']}: a {rv.get('state')} review names its step, who took it and its digest")
            elif sig is None:
                out.append(f"process {p['id']}: a {rv['state']} review needs the signature it was taken at")
            elif (rv["signature_digest"] == sig["digest"]) != (rv["state"] != "stale"):
                out.append(f"process {p['id']}: a review at another digest is stale, and one at this digest is not")
    bad = [a for f in bundle.get("features") or [] for a in (f.get("signatures") or {}).get("apis") or [] if a not in apis]
    bad += [t for f in bundle.get("features") or [] for t in (f.get("signatures") or {}).get("tables") or [] if t not in tables]
    bad += [s for f in bundle.get("features") or [] for s in (f.get("signatures") or {}).get("steps") or [] if s not in steps]
    bad += [f for r in ((bundle.get("rtm") or {}).get("requirements") or []) for f in r.get("features") or [] if f not in feats]
    bad += [c["api"] for c in bundle.get("consumers") or [] if c["api"] not in apis]
    bad += [a for p in procs for s in p["steps"] for a in s.get("apis") or [] if a not in apis]
    bad += [p["feature"] for p in procs if p.get("feature") is not None and p["feature"] not in feats]
    if bundle.get("components") is not None:
        comps = {c["id"] for c in bundle["components"]}
        for w in ((bundle.get("migration") or {}).get("waves") or []) + ((bundle.get("derived_waves") or {}).get("waves") or []):
            bad += [c for c in w.get("components") or [] if c not in comps]
        bad += [s["component"] for s in (bundle.get("migration") or {}).get("strangler") or [] if s["component"] not in comps]
    if bad:
        out.append(f"apptwin {app}: undeclared {sorted(set(bad))}")
    if len(set(feats)) != len(feats):
        out.append(f"apptwin {app}: a feature id is repeated")
    if len({p["id"] for p in procs}) != len(procs):
        out.append(f"apptwin {app}: a process id is repeated")
    for key, st in (bundle.get("sections") or {}).items():
        if key not in SECTIONS and key not in PARTS:
            out.append(f"sections names {key!r}, not a section or a part")
            continue
        there = _part(bundle, key) not in (None, [], {})
        if st["state"] == "present" and not there:
            out.append(f"sections says {key} is present, but it is not carried")
        if st["state"] == "missing" and there:
            out.append(f"sections says {key} is missing, but it is carried")
        if st["state"] == "missing" and not st.get("reason"):
            out.append(f"sections says {key} is missing and not why")
        if st["state"] == "present" and st.get("derived") is not None:
            out.append(f"sections: only a missing part has waves derived but not carried ({key})")
    return out


# --- inventory: the legacy_* tables ---------------------------------------------------------------

def _source_root(app_id: str, conn) -> str | None:
    row = conn.execute("SELECT source_path FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
    return row["source_path"] if row else None


def inventory(app_id: str, conn) -> dict:
    """The application row and its components, APIs, tables and dependencies, in a stable order."""
    app = conn.execute("SELECT id, name, primary_language, framework FROM legacy_applications WHERE id = %s",
                       (app_id,)).fetchone()
    if app is None:
        raise BundleError(f"no legacy application {app_id!r} (register it with legacy_analyzer first)")
    comps = conn.execute("SELECT id, name, qualified_name, component_type, file_path FROM legacy_components "
                         "WHERE legacy_app_id = %s", (app_id,)).fetchall()
    name = {c["id"]: c["qualified_name"] or c["name"] for c in comps}
    apis = conn.execute("SELECT method, path, handler_function FROM legacy_apis WHERE legacy_app_id = %s",
                        (app_id,)).fetchall()
    cols = conn.execute("SELECT table_name, column_name FROM legacy_db_schemas WHERE legacy_app_id = %s",
                        (app_id,)).fetchall()
    deps = conn.execute("SELECT source_component_id, target_component_id, dependency_type FROM legacy_dependencies "
                        "WHERE legacy_app_id = %s", (app_id,)).fetchall()
    tables: dict[str, list[str]] = {}
    for r in cols:
        tables.setdefault(r["table_name"], []).append(r["column_name"])
    return {
        "application": {"id": app["id"], "title": app["name"],
                        "languages": sorted({x for x in (app["primary_language"],) if x})},
        "components": sorted(({"id": name[c["id"]], "kind": c["component_type"], "path": c["file_path"]} for c in comps),
                             key=lambda x: (x["id"], x["kind"])),
        "apis": sorted(({"method": a["method"] or "ALL", "path": a["path"],
                         **({"handler": a["handler_function"]} if a["handler_function"] else {})} for a in apis),
                       key=lambda x: (x["path"], x["method"])),
        "data": [{"name": t, "columns": sorted(set(c))} for t, c in sorted(tables.items())],
        "dependencies": sorted(({"source": name.get(d["source_component_id"], d["source_component_id"]),
                                 "target": name.get(d["target_component_id"], d["target_component_id"]),
                                 "kind": d["dependency_type"]} for d in deps if d["target_component_id"]),
                               key=lambda x: (x["source"], x["target"], x["kind"])),
    }


# --- sections from each tool's own JSON ------------------------------------------------------------

def features_from_inventory(inv: dict) -> list[dict] | None:
    """Observed features (observed_inventory.build_inventory output). ``features: null`` there is null here."""
    feats = inv.get("features")
    if feats is None:
        return None
    out = {}
    for f in feats:
        label = f.get("label") or f.get("title")
        if not label:
            continue
        fid = _slug(label)                         # one feature per label: a repeat adds its evidence
        f0 = out.setdefault(fid, {"id": fid, "title": label, "basis": "observed", "evidence": []})
        f0["evidence"] = sorted(set(f0["evidence"]) | {str(e) for e in f.get("evidence_ids") or []})
    return [out[k] for k in sorted(out)]


def features_from_static(doc: dict) -> list[dict] | None:
    """Static features (static_features.report output), carried as that tool grouped them: basis ``source``,
    provenance ``static``, its confidence. ``features: null`` there (no entry point found) is null here."""
    feats = doc.get("features")
    if feats is None:
        return None
    out = []
    for f in feats:
        if f.get("provenance") != "static" or f.get("basis") not in ("source", "decompiled"):
            raise BundleError(f"static feature {f.get('id')!r} is not labelled static from source")
        out.append({k: f[k] for k in ("id", "title", "basis", "provenance", "confidence", "signatures", "evidence")
                    if f.get(k) not in (None, [], {})})
    return sorted(out, key=lambda f: f["id"])


def features_from_extract(rep: dict) -> list[dict] | None:
    """bundle_features.report's features in the contract's shape (icx-mdz-03; icx-mdz-04: 1.189.0). ``static`` is read
    from source and never run (basis ``source``); ``both`` is read from source AND seen running -- an observed route
    reached a static entry point -- so its basis stays ``source`` and its provenance is ``both``; ``observed`` was only
    seen running (basis ``observed``). Each carries its capability HINTS as the rule set found them (never a mapping).
    A feature's ``signatures.steps`` names the steps of its processes a person did not reject (``<process>.<step>``)."""
    feats = rep.get("features")
    if feats is None:
        return None
    kept = {p["id"]: p for p in rep.get("processes") or [] if (p.get("review") or {}).get("state") != "rejected"}
    out = []
    for f in feats:
        if f.get("source") not in ("static", "observed", "both"):
            raise BundleError(f"feature {f.get('id')!r} says it is {f.get('source')!r}, not static, observed or both")
        steps = [f"{pid}.{st['id']}" for pid in f.get("processes") or [] if pid in kept for st in kept[pid]["steps"]]
        sig = {k: v for k, v in (("apis", sorted(f.get("apis") or [])), ("routes", sorted(f.get("routes") or [])),
                                 ("steps", steps)) if v}
        hints = [{"capability": str(h["capability"]), "title": str(h.get("title") or h["capability"]),
                  "terms": [str(t) for t in h["terms"]], **({"from": [str(x) for x in h["from"]]} if h.get("from") else {})}
                 for h in f.get("capability_hints") or [] if h.get("capability") and h.get("terms")]
        out.append({"id": f["id"], "title": f["name"], "basis": "observed" if f["source"] == "observed" else "source",
                    "provenance": f["source"],
                    **({"confidence": float(f["confidence"])} if f.get("confidence") is not None else {}),
                    **({"signatures": sig} if sig else {}), "evidence": list(f.get("evidence") or []),
                    **({"capability_hints": hints} if hints else {})})
    return sorted(out, key=lambda f: f["id"])


_STEP_KEYS = ("verb", "object", "signature", "evidence")
_REVIEW_KEYS = ("step_id", "decision", "by", "role", "reason", "signature_digest", "error")


def _review(rv: dict | None) -> dict:
    """A process's review as ICDEV recorded it (``bundle_features.review_states``): its state, and for a decided one
    the step, who took it and the digest it was taken at. Never ``reviewed`` without them: no review read is
    ``unreviewed``."""
    rv = rv or {}
    state = rv.get("state") if rv.get("state") in ("reviewed", "rejected", "stale", "unreviewed") else "unreviewed"
    out = {"state": state, **{k: str(rv[k]) for k in _REVIEW_KEYS if rv.get(k) not in (None, "")}}
    if rv.get("at") not in (None, ""):
        out["at"] = _iso(rv["at"])
    return out


def processes_from_extract(rep: dict) -> list[dict]:
    """bundle_features.report's processes in the contract's shape (icx-mdz-03; icx-mdz-04: 1.189.0 carries them whole):
    how each was found (``source``), the feature it belongs to, what its order means, what it was derived from, whether
    it was cut at the rule set's maximum, its step signature (steps, digest, rules digest), the review a person took at
    that digest, and each step's verb, object, signature, evidence and the APIs it serves. One a person REJECTED is
    not exported."""
    out = []
    for p in rep.get("processes") or []:
        if (p.get("review") or {}).get("state") == "rejected":
            continue
        sig = p.get("signature")
        out.append({"id": p["id"], "title": p["title"], "basis": p["basis"],
                    **{k: p[k] for k in ("source", "feature", "order", "derived_from") if p.get(k) not in (None, "")},
                    **({"truncated": True} if p.get("truncated") else {}),
                    **({"signature": {"steps": list(sig["steps"]), "digest": sig["digest"],
                                      "rules_digest": sig["rules_digest"]}} if sig else {}),
                    "review": _review(p.get("review")),
                    "steps": [{"id": st["id"], "title": st["title"],
                               **{k: str(st[k]) for k in _STEP_KEYS if st.get(k) not in (None, "")},
                               **({"apis": sorted(st["apis"])} if st.get("apis") else {})} for st in p["steps"]]})
    return out


def process_from_processify(doc: dict) -> dict | None:
    """A Process-Ify draft (CLI output or its ``workflow``). A draft with no measured steps is no process."""
    wf = doc.get("workflow", doc)
    steps = wf.get("steps")
    if not steps:
        return None
    basis = wf.get("basis") if wf.get("basis") in ("document", "observed") else "document"
    return {"id": _slug(wf.get("workflow_name") or wf.get("source_name") or "process"),
            "title": wf.get("workflow_name") or wf.get("source_name") or "process", "basis": basis,
            "steps": [{"id": str(s.get("id") or f"step-{i + 1}"), "title": s.get("title") or f"step {i + 1}",
                       **({"role": s["assignee_role"]} if s.get("assignee_role") else {})} for i, s in enumerate(steps)]}


def _rel(path: str, root: str | None) -> str:
    """A module path relative to the application's source root: a bundle never carries where it was analysed."""
    if root:
        try:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return path


def _disqualifiers(o: dict) -> list[str]:
    d = o.get("disqualifiers")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = [d] if d.strip() else []
    return [str(x) for x in d or []]


VERDICTS = ("ai_ready", "partial", "not_suitable")


def _verdict(scan: dict) -> str:
    """AI-Ify's verdict as the contract's enum. A live scan puts the enum in ``overall_ai_readiness`` and a sentence
    ("Partially AI-ready") in ``overall_verdict``; older outputs had the enum in ``overall_verdict``."""
    for k in ("overall_ai_readiness", "overall_verdict"):
        if scan.get(k) in VERDICTS:
            return scan[k]
    raise BundleError(f"AI-Ify reported no verdict in {VERDICTS}: {scan.get('overall_verdict')!r}")


def aiify_from_scan(scan: dict, *, at: str, root: str | None = None) -> dict:
    """``python -m tools.aiify.engine scan --json`` output. AI-Ify's posture grade is database-wide, not this
    application's, so it is never carried (null). Module paths are relative to ``root`` (the application's source
    path), and AI-Ify's disqualifiers are carried: ICDEV[OS] refuses to enable a disqualified opportunity."""
    pillars = {k: {"passed": int(v["passed"]), "total": int(v["total"]),
                   "percentage": round(float(v["percentage"]) * (100 if float(v["percentage"]) <= 1 else 1), 2)}
               for k, v in (scan.get("pillar_scores") or {}).items()} or None
    def sub(o, k):
        v = (o.get("score") or {}).get(k)
        return None if v is None else float(v)
    opps = [{"pattern_type": o["pattern_type"], "module_path": _rel(o["module_path"], root),
             **({"disqualifiers": dq} if (dq := _disqualifiers(o)) else {}),
             "ai_readiness": (o.get("score") or {}).get("ai_readiness"),
             "composite_score": (o.get("score") or {}).get("composite_score"),
             **({"id": str(o["opportunity_id"])} if o.get("opportunity_id") is not None else {}),
             **{k: v for k, v in (("value", sub(o, "value_score")), ("feasibility", sub(o, "feasibility_score")),
                                  ("risk", sub(o, "risk_score"))) if v is not None}}
            for o in scan.get("opportunities") or []]
    opps = [o for o in opps if o["ai_readiness"] is not None and o["composite_score"] is not None]
    rs = scan.get("overall_readiness_score")
    return {"tool": "icdev-it/aiify", "assessed_at": _iso(scan.get("completed_at")) or at,
            "overall_verdict": _verdict(scan), "posture_grade": None,
            "readiness_score": None if rs is None else float(rs), "pillars": pillars,
            "opportunities": sorted(opps, key=lambda o: (o["module_path"], o["pattern_type"])),
            # ICDEV[OS] Phase 247: the roadmap and the decisions people made here, carried
            "roadmap": [{"phase": str(ph.get("phase") or ph.get("id")), "title": str(ph.get("title") or ph.get("name") or ""),
                         "opportunities": [str(x) for x in ph.get("opportunities") or []],
                         **({"effort_days": float(ph["effort_days"])} if ph.get("effort_days") is not None else {})}
                        for ph in ((scan.get("roadmap") or {}).get("phases") or [])],
            "decisions": [{"opportunity": str(d["source_id"]), "decision": {"accept": "accepted", "reject": "rejected"}.get(d["decision"], d["decision"]),
                           "by": str(d.get("actor") or "user"), "at": _iso(d.get("decided_at")) or at}
                          for d in scan.get("hitl_decisions") or [] if d.get("decision") in ("accept", "reject", "accepted", "rejected", "deferred")]}


def integrity_from_sipa(r: dict, *, at: str) -> dict:
    caps = r.get("capabilities") or []
    return {"tool": "icdev-it/sipa", "assessed_at": _iso(r.get("assessed_at")) or at,
            "verdict": str(r["verdict"]).upper(),
            "risk_score": None if r.get("risk_score") is None else float(r["risk_score"]),
            "capabilities": sorted({c if isinstance(c, str) else str(c.get("capability_type") or c.get("type"))
                                    for c in caps})}


def _json(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return default if v is None else v


def seven_r_from_assessment(a: dict, *, at: str) -> dict | None:
    """The 7R assessment, carried whole (ICDEV[OS] Phase 247): the ranking, the recommendation and its own estimates.

    icx-mdz-02: only an ASSESSABLE strategy is ranked (the contract's score is a number); a not-assessable one, and
    each ranked strategy's confidence and unmeasured criteria, are said in ``evidence``. With no strategy assessable
    there is no ranking, and the section is None (unverified), never a ranking of made-up scores.
    """
    ev = _json(a.get("evidence"), {})
    ranking = (ev.get("ranking") if isinstance(ev, dict) else None) or a.get("ranking") or []
    per = (ev.get("strategies") if isinstance(ev, dict) else None) or {}
    per = per if isinstance(per, dict) else {}

    def measured(sid: str) -> dict:
        """icx-mdz-04 (1.189.0): what the score rests on, as fields -- its confidence (the share of its criterion
        weight measured), the criteria left unmeasured, and whether it is assessable -- as the assessor said them."""
        v = per.get(sid) or {}
        c = v.get("confidence")
        return {**({"confidence": round(max(0.0, min(1.0, float(c))), 4)} if c is not None else {}),
                **({"unmeasured": [str(x) for x in v["unmeasured"]]} if v.get("unmeasured") else {}),
                **({"assessable": bool(v["assessable"])} if v.get("assessable") is not None else {})}
    ranked = []
    for r in ranking:
        if r.get("score") is not None:
            sid = r.get("strategy_id") or r.get("strategy")
            ranked.append({"strategy": sid, "score": float(r["score"]), **measured(sid)})
    if not ranked:
        return None
    out = {"tool": "icdev-it/seven_r_assessor", "assessed_at": _iso(a.get("assessed_at") or a.get("created_at")) or at,
           "ranked": ranked}
    rec = (ev.get("recommendation") if isinstance(ev, dict) else None) or {}
    if rec.get("status") == "recommended" and rec.get("confidence") is not None:
        out["confidence"] = round(max(0.0, min(1.0, float(rec["confidence"]))), 4)
    for k, src in (("recommended", "recommended_strategy"), ("ato_impact", "ato_impact")):
        if a.get(src):
            out[k] = str(a[src])
    for k in ("cost_estimate_hours", "risk_score", "timeline_weeks", "tech_debt_reduction"):
        if a.get(k) is not None:
            out[k] = float(a[k])
    w = _json(a.get("scoring_weights"), {})
    if isinstance(w, dict) and w:
        out["weights"] = {str(k): float(v) for k, v in w.items() if isinstance(v, int | float)}
    notes = [str(x) for x in (ev.get("rationale") or ev.get("notes") or [])] if isinstance(ev, dict) else []
    strategies = ev.get("strategies") if isinstance(ev, dict) else None
    if isinstance(strategies, dict):           # icx-mdz-02: what each score rests on
        rec = ev.get("recommendation") or {}
        if rec.get("reason"):
            notes.append(f"recommendation: {rec['reason']}")
        for sid, v in strategies.items():
            un = ", ".join(v.get("unmeasured") or []) or "none"
            notes.append(f"{sid}: not assessable (nothing measured)" if not v.get("assessable") else
                         f"{sid}: confidence {float(v.get('confidence') or 0):.2f}; unmeasured: {un}")
    if notes:
        out["evidence"] = notes[:20]
    return out


_WAVE = {"planned": "planned", "in_progress": "in_progress", "complete": "closed", "closed": "closed",
         "backed_out": "backed_out", "blocked": "in_progress"}


def _wave_rows(waves) -> list:
    if isinstance(waves, dict):                       # app_wave_planner's report: its waves
        waves = waves.get("waves")
    return list(waves or [])


def waves_out(waves, *, estimates: dict | None = None) -> list[dict]:
    """Waves in the contract's shape: id, title, order, status, components, backout, and -- only where the planner or
    the plan has them -- planned dates as given and the wave's ``estimated_hours`` (``estimates``: the sum of its
    tasks' estimates, ``wave_estimates``; else the planner's own). Absent when unknown: never 0, never a date made
    up here. icx-mdz-05: ``app_wave_planner`` gives ``starts``/``ends`` only from a reviewed team capacity
    (``wave_schedule``), and they are carried as given."""
    out = []
    for i, w in enumerate(_wave_rows(waves)):
        wid = str(w.get("id") or f"w{w.get('wave_number')}")
        starts = w.get("starts") or w.get("start_date")
        ends = w.get("ends") or w.get("end_date") or w.get("cutover_date")
        est = (estimates or {}).get(wid)
        est = w.get("estimated_hours") if est is None else est     # the planner's own, where it gave one
        out.append({"id": wid, "title": str(w.get("name") or w.get("title")),
                    "order": int(w.get("wave_number") or w.get("order") or i + 1),
                    "status": _WAVE.get(str(w.get("status") or "planned"), "planned"),
                    **({"components": [str(c) for c in w["components"]]} if w.get("components") else {}),
                    **({"backout": str(w["backout"])} if w.get("backout") else {}),
                    **({"starts": str(starts)} if starts else {}), **({"ends": str(ends)} if ends else {}),
                    **({"estimated_hours": round(float(est), 2)} if est is not None else {})})
    return out


def wave_estimates(waves, tasks: list[dict], names: dict[str, str]) -> dict[str, float | None]:
    """Each wave's estimate: the sum of the ``estimated_hours`` of the plan's tasks on its components (a task names its
    component by row id; ``names`` maps that to the bundle's component id). None -- unknown -- for a wave no task is
    on, or with a task whose estimate is unrecorded: a partial sum would understate it. A task on no component (plan
    level) belongs to no wave."""
    out: dict[str, float | None] = {}
    for w in waves_out(waves):
        comps = set(w.get("components") or [])
        hours = [t.get("estimated_hours") for t in tasks if names.get(str(t.get("legacy_component_id") or "")) in comps]
        out[w["id"]] = None if not hours or any(h is None for h in hours) else float(sum(float(h) for h in hours))
    return out


def derived_waves(waves, *, at: str) -> dict | None:
    """Waves the planner derived BEFORE any plan exists (contract 1.189.0, ``derived_waves``): shown in ICDEV[OS], never
    approved (its migration-plan gate reads ``migration.waves`` only). No task exists yet, so no estimate; no date."""
    if not _wave_rows(waves):
        return None
    rep = waves if isinstance(waves, dict) else {}
    basis = str(rep.get("basis") or "the wave planner's order over the application's own dependencies")
    if schedule_reason(waves):          # icx-mdz-05: why they are not dated, said in the contract's own field
        basis += f"; {schedule_reason(waves)}"
    return {"tool": str(rep.get("tool") or "icdev-it/app_wave_planner"), "assessed_at": at, "basis": basis,
            "waves": waves_out(waves)}


def schedule_reason(waves) -> str | None:
    """The wave planner's word on its dates (icx-mdz-05, ``wave_schedule``): dated from which declared capacity (its
    digest), or why not. ICDEV[OS] 1.189.0 has no field for it, so it rides in ``sections["migration.waves"].reason``
    and ``derived_waves.basis``; None for a report from before it (or the migration canvas's own list)."""
    sch = waves.get("schedule") if isinstance(waves, dict) else None
    return str(sch["reason"]) if isinstance(sch, dict) and sch.get("reason") else None


def migration_from_plan(app_id: str, conn, *, at: str, waves: list | None = None, strangler: list | None = None) -> dict | None:
    """The application's latest migration plan (ICDEV[OS] Phase 247): strategy, approach, task counts by type, and the
    waves and strangler status the planners produced, each wave with its estimate where its tasks have one
    (icx-mdz-04). No plan recorded: null (unverified in ICDEV[OS])."""
    try:
        row = conn.execute("SELECT * FROM migration_plans WHERE legacy_app_id = %s ORDER BY created_at DESC LIMIT 1", (app_id,)).fetchone()
        if row is None:
            return None
        p = dict(row)
        tasks = [dict(t) for t in conn.execute("SELECT task_type, status, legacy_component_id, estimated_hours "
                                               "FROM migration_tasks WHERE plan_id = %s", (p["id"],))]
        names = {str(c["id"]): str(c["qualified_name"] or c["name"]) for c in conn.execute(
            "SELECT id, name, qualified_name FROM legacy_components WHERE legacy_app_id = %s", (app_id,)).fetchall()}
    except Exception as exc:  # no migration tables in this database: nothing was planned here
        if "no such table" in str(exc) or "does not exist" in str(exc):
            return None
        raise
    by: dict[str, int] = {}
    for t in tasks:
        by[t["task_type"]] = by.get(t["task_type"], 0) + 1
    out = {"tool": "icdev-it/monolith_decomposer", "assessed_at": _iso(p.get("created_at")) or at, "plan_id": p["id"],
           "strategy": p["strategy"], "approach": p.get("migration_approach") or "strangler_fig", "status": p.get("status") or "draft",
           "tasks_total": len(tasks), "tasks_done": sum(t["status"] == "completed" for t in tasks), "tasks_by_type": by}
    if p.get("target_architecture"):
        out["target_architecture"] = p["target_architecture"]
    out["waves"] = waves_out(waves, estimates=wave_estimates(waves, tasks, names))
    out["strangler"] = [{"component": _slug(str(s["component"])), "status": s["status"],
                         **({"routed_pct": float(s["routed_pct"])} if s.get("routed_pct") is not None else {})}
                        for s in strangler or [] if s.get("status") in ("legacy", "routing", "migrated")]
    return out


def _manifest_digest(arts: list[dict]) -> str:
    """ICDEV[OS]'s binding of a job's artefacts (its apptwin.manifest_digest): SHA-256 over ``"<sha256>  <path>\\n"`` per
    file, sorted by path -- what ``sha256sum`` prints for them."""
    return hashlib.sha256("".join(f"{a['sha256']}  {a['path']}\n" for a in sorted(arts, key=lambda a: a["path"])).encode()).hexdigest()


def _job_artefacts(j: dict) -> list[dict] | None:
    """Each file a CAM job wrote, by its path under the job's output and the SHA-256 of the bytes on disk now. A file
    that is gone is not listed (never a guessed digest); a job that lists files none of which is here gives None."""
    arts = _json(j.get("artifacts_json"), []) if "artifacts_json" in j and not j.get("artifacts") else j.get("artifacts") or []
    if not arts or not all(isinstance(a, dict) for a in arts):
        return None
    out = {}
    root = Path(j["output_path"]) if j.get("output_path") else None
    for a in arts:
        f = Path(str(a.get("path") or ""))
        if not f.is_file():
            continue
        rel = a.get("rel") or (f.relative_to(root).as_posix() if root is not None and f.is_relative_to(root) else f.name)
        out[rel] = {"path": rel, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    return [out[k] for k in sorted(out)] or None


def refactor_from_jobs(jobs, *, at: str) -> dict:
    """CAM's refactor jobs (ICDEV[OS] Phase 247; osx-fth-13). A job's artefacts are each file it wrote, by its SHA-256,
    and ``artefact_digest`` binds them all (``_manifest_digest``). What a migrator writes is generated code that nobody
    here reviewed, so a job that wrote files says ``review: unreviewed``. The older form (``artifacts_json`` a list of
    paths under the repository) is digested by its first file, as before; never a guessed digest."""
    if isinstance(jobs, dict):                        # refactor_jobs.py's report: its jobs
        jobs = jobs.get("jobs") or []
    out = []
    for j in jobs:
        files = _job_artefacts(j)
        if files:
            extra = {"artefact_digest": _manifest_digest(files), "artefacts": files, "review": "unreviewed"}
        else:
            arts = _json(j.get("artifacts_json"), []) if "artifacts_json" in j else j.get("artifacts") or []
            first = _BASE / str(arts[0]) if arts and isinstance(arts[0], str) else None
            dig = hashlib.sha256(first.read_bytes()).hexdigest() if first is not None and first.is_file() else None
            extra = {"artefact_digest": dig} if dig else {}
        why = j.get("result_summary") if j.get("status") != "failed" else j.get("error_message") or j.get("result_summary")
        out.append({"id": str(j["id"]), "kind": str(j.get("refactor_type") or j.get("kind")),
                    **({"component": str(j["component_name"])} if j.get("component_name") else {}),
                    "status": str(j.get("status") or "queued"), **extra,
                    **({"summary": re.sub(r"(?:[A-Za-z]:)?[/\\][^\s'\"]+", lambda m: Path(m.group()).name, str(why))[:300]}
                       if why else {})})   # a summary never says where the job ran
    return {"tool": "icdev-it/cam_refactor_engine", "assessed_at": at, "jobs": out}


#: each file a cloud-native job writes, and what it is in the contract (ICDEV[OS] apptwin.GENERATED)
CLOUD_FILES = {"Containerfile": "container_definition", "deployment.yaml": "deployment_manifest",
               "config-findings.json": "config_mapping", "env.mapping.json": "config_mapping",
               "config.env.example": "config_mapping", "twelve-factor.json": "twelve_factor_report",
               "asyncapi.yaml": "event_contract", "event-contract.json": "event_contract"}


def ai_use_case_drafts(aiify: dict | None, app: str) -> list[dict]:
    """AI-Ify's opportunities as DRAFT AI use cases, one per pattern: those AI-Ify neither ruled not suitable nor
    disqualified. A draft only -- ICDEV[OS] registers an AI use case through its own reviewed workflow."""
    by: dict[str, list[dict]] = {}
    for o in (aiify or {}).get("opportunities") or []:
        if o.get("ai_readiness") == "not_suitable" or o.get("disqualifiers"):
            continue
        by.setdefault(o["pattern_type"], []).append(o)
    out = []
    for pat in sorted(by):
        os_ = sorted(by[pat], key=lambda o: (-float(o["composite_score"]), o["module_path"]))
        out.append({"id": f"{_slug(app)}-{_slug(pat)}", "title": f"{pat.replace('_', ' ')} in {app}",
                    "pattern_type": pat, "opportunities": [str(o.get("id") or o["module_path"]) for o in os_],
                    "modules": sorted({o["module_path"] for o in os_})[:20],
                    "best_score": float(os_[0]["composite_score"]), "status": "draft"})
    return out


def cloud_native_from_jobs(rep: dict, *, at: str, app: str, aiify: dict | None = None,
                           refactor: dict | None = None) -> dict | None:
    """CAM's cloud-native jobs (osx-fth-14). Every file they wrote is bound by SHA-256 (path ``<job>/<file>``) and the
    section by the digest of that list; each is generated and unreviewed. The twelve factors, the configuration found
    (where, never a value), the environment mapping and the event channels are read from the job files themselves,
    so the section says what the bound files say. No twelve-factor report, no section: unverified, never filled in."""
    jobs = {j["scaffold"]: j for j in rep.get("jobs") or [] if j.get("status") == "completed"}
    tf = jobs.get("twelve_factor_report")
    if tf is None:
        return None
    arts, docs = [], {}
    for scaffold, j in sorted(jobs.items()):
        files = _job_artefacts(j) or []
        root = Path(j["output_path"])
        for a in files:
            kind = CLOUD_FILES.get(Path(a["path"]).name)
            if kind is None:
                continue
            arts.append({"kind": kind, "job": str(j["id"]), "path": f"{scaffold}/{a['path']}", "sha256": a["sha256"],
                         "review": "unreviewed"})
            if a["path"].endswith(".json"):
                docs[a["path"]] = json.loads((root / a["path"]).read_text(encoding="utf-8"))
    tfd = docs.get("twelve-factor.json") or {}
    from tools.modernization import cloud_native
    generated = {a["kind"] for a in arts} | {j["kind"] for j in (refactor or {}).get("jobs") or []
                                             if j.get("artefact_digest")}
    stack = dict(tfd.get("stack") or {})
    stack["ports"] = [{k: v for k, v in p.items() if v is not None} for p in stack.get("ports") or []]
    return {"tool": cloud_native.TOOL,
            "assessed_at": at,
            "stack": {k: v for k, v in stack.items() if v not in (None, [])} | {"ports": stack["ports"]},
            "factors": tfd.get("factors") or [],
            "config": [{k: v for k, v in f.items() if v is not None} for f in (docs.get("config-findings.json") or {}).get("findings") or []],
            "env": (docs.get("env.mapping.json") or {}).get("env") or [],
            "events": (docs.get("event-contract.json") or {}).get("channels") or [],
            "artefacts": sorted(arts, key=lambda a: a["path"]),
            **({"artefact_digest": _manifest_digest(arts)} if arts else {}),
            "ai_use_cases": ai_use_case_drafts(aiify, app),
            "targets": cloud_native.targets(rep.get("targets") or {}, generated, stack.get("container_files") or [])}


# --- the application's environment: declared, never guessed (icx-mdz-04) -------------------------------

def declared_environments(path: str | Path | None = None) -> dict[str, dict]:
    """``{application name or legacy id: {"fields": {...}, "source": str}}`` from the declared environments file
    (``args/modernisation_environments.yaml``, or ``path``). Each entry says the application's security domain, impact
    level, ATO boundary and hosting AS ITS DEPLOYMENT DECLARES THEM and names that source (an SSP section, a CMDB
    record, an ATO letter); one that names no source, names a field the contract has not, or gives an impact level off
    ``il2``/``il4``/``il5``/``il6`` is refused -- nothing is guessed or normalised into one. No file: nothing declared."""
    import yaml

    p = Path(path) if path else _BASE / ENVIRONMENTS_REL
    if not p.is_file():
        if path:
            raise BundleError(f"no environments file {p}")
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    apps = doc.get("applications") or {}
    if not isinstance(apps, dict):
        raise BundleError(f"{p.name}: 'applications' must be a mapping of application name (or id) to its environment")
    out = {}
    for key, e in apps.items():
        if not isinstance(e, dict):
            raise BundleError(f"{p.name}: {key!r} must be a mapping")
        odd = sorted(set(e) - set(ENVIRONMENT_FIELDS) - {"source"})
        if odd:
            raise BundleError(f"{p.name}: {key!r} names {odd}, not one of {list(ENVIRONMENT_FIELDS)} or source")
        fields = {k: e[k] for k in ENVIRONMENT_FIELDS if e.get(k) not in (None, "")}
        if bad := [k for k, v in fields.items() if not isinstance(v, str)]:
            raise BundleError(f"{p.name}: {key!r} {bad} must be words (quote them): YAML read {[fields[k] for k in bad]}")
        if "impact_level" in fields and fields["impact_level"] not in IMPACT_LEVELS:
            raise BundleError(f"{p.name}: {key!r} impact_level {fields['impact_level']!r} is not one of {list(IMPACT_LEVELS)}")
        if fields and not str(e.get("source") or "").strip():
            raise BundleError(f"{p.name}: {key!r} declares an environment and names no source: say where it is declared")
        out[str(key)] = {"fields": fields, "source": str(e.get("source") or "").strip() or None}
    return out


def environment(app_id: str, name: str | None, *, path: str | Path | None = None) -> tuple[dict | None, dict]:
    """The application's declared environment (by its legacy id, else its name) for ``application.environment``, and
    what the run reports about it: declared with its source and the fields left unreported, or not declared, with why.
    Absent stays absent: ICDEV[OS] then classes any overlap of it ``unverified`` (a security domain is unreported)."""
    where = str(path) if path else ENVIRONMENTS_REL
    envs = declared_environments(path)
    hit = envs.get(app_id) or (envs.get(name) if name else None)
    if not hit or not hit["fields"]:
        return None, {"declared": False, "file": Path(where).name if path else where,
                      "reason": f"no environment is declared for {name or app_id} in {Path(where).name if path else where}: "
                                "its security domain, impact level, ATO boundary and hosting are unreported (never guessed "
                                "from the source)"}
    return dict(hit["fields"]), {"declared": True, "file": Path(where).name if path else where, "source": hit["source"],
                                 "fields": sorted(hit["fields"]),
                                 "unreported": [k for k in ENVIRONMENT_FIELDS if k not in hit["fields"]]}


def build(app_id: str, conn, *, at: str | None = None, features: dict | None = None, processes: list[dict] = (),
          aiify: dict | None = None, integrity: dict | None = None, seven_r: dict | None = None,
          sections: dict | None = None, waves: list | dict | None = None, strangler: list | None = None,
          refactor: list | dict | None = None, static_features: dict | None = None,
          cloud_native: dict | None = None, extract: dict | None = None, environment: dict | None = None,
          reasons: dict | None = None, check: bool = True, survivor_ivv: dict | None = None) -> dict:
    """The bundle (contract 1.189.0), validated before it is returned (``check``: the preview validates it itself).

    icx-mdz-04: the extras ICDEV used to write beside it are inline -- each feature's provenance (``both`` when seen
    running too) and capability hints; each process's source, step signature and review; each strategy's confidence
    and unmeasured criteria; ``derived_waves`` when no plan carries the waves; each planned wave's estimate; the
    application's declared ``environment`` (``environment``: the fields from ``environment()``; absent stays absent);
    and ``sections``, every section and part present or missing with why (``reasons``: the run's own).

    icx-mdz-06: ``survivor_ivv`` is ``survivor_ivv.for_bundle``'s ``{section, reason}``: the ``ivv`` section only when an
    independent person signed the current IV&V result at its digest, and ``sections["ivv"]`` says why either way. An
    ``ivv`` passed in ``sections`` (``--ivv``) is carried as made instead."""
    at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if sum(x is not None for x in (features, static_features, extract)) > 1:
        raise BundleError("give one features report (observed, static or the extract), not both: each is one tool's report")
    inv = inventory(app_id, conn)
    procs = [p for p in (process_from_processify(d) for d in processes) if p is not None]
    if extract is not None:
        if extract.get("app_id") not in (None, app_id):
            raise BundleError(f"the extract is of {extract.get('app_id')!r}, not {app_id!r}")
        taken = {p["id"] for p in procs}
        procs += [p for p in processes_from_extract(extract) if p["id"] not in taken]
    bundle = {"schema": SCHEMA, "producer": {"tool": PRODUCER, "version": _version()}, "produced_at": at, **inv,
              "features": features_from_inventory(features) if features is not None else
                          features_from_static(static_features) if static_features is not None else
                          features_from_extract(extract) if extract is not None else None,
              "processes": sorted(procs, key=lambda p: p["id"]) or None,   # no measured steps: unverified, never "none"
              "consumers": None,
              "aiify": aiify_from_scan(aiify, at=at, root=_source_root(app_id, conn)) if aiify is not None else None,
              "integrity": integrity_from_sipa(integrity, at=at) if integrity is not None else None,
              "seven_r": seven_r_from_assessment(seven_r, at=at) if seven_r is not None else None,
              "migration": migration_from_plan(app_id, conn, at=at, waves=waves, strangler=strangler),
              "refactor": refactor_from_jobs(refactor, at=at) if refactor is not None else None}
    bundle["cloud_native"] = (cloud_native_from_jobs(cloud_native, at=at, app=inv["application"]["title"],
                                                     aiify=bundle["aiify"], refactor=bundle["refactor"])
                              if cloud_native is not None else None)
    for k in PASSTHROUGH:
        bundle[k] = (sections or {}).get(k)
    ivv_why = None
    if bundle["ivv"] is None and survivor_ivv:      # icx-mdz-06: signed at the current digest, or why not
        bundle["ivv"], ivv_why = survivor_ivv.get("section"), survivor_ivv.get("reason")
        if bundle["ivv"] is None and ivv_why:
            reasons = {**(reasons or {}), "ivv": (reasons or {}).get("ivv") or ivv_why}
    if environment:
        bundle["application"]["environment"] = {k: environment[k] for k in ENVIRONMENT_FIELDS if environment.get(k)}
    dw = derived_waves(waves, at=at) if bundle["migration"] is None else None
    if dw is not None:                   # left out, not null, when a plan carries the waves or none was derived
        bundle["derived_waves"] = dw
    bundle["sections"] = section_states(bundle, waves=waves, reasons=reasons)
    if bundle["ivv"] is not None and ivv_why:
        bundle["sections"]["ivv"]["reason"] = ivv_why
    if check:
        errors = validate(bundle)
        if errors:
            raise BundleError("the bundle does not meet the icos-apptwin/v1 contract: " + "; ".join(errors[:10]))
    return bundle


#: The parts of the bundle ICDEV[OS] plans a migration on, beside its sections (icx-mdz-03).
CLOUD_PARTS = (("twelve_factor", ("twelve_factor_report",)), ("containerisation", ("container_definition",
               "deployment_manifest")), ("config", ("config_mapping",)), ("events", ("event_contract",)))


def section_states(bundle: dict, *, waves: list | dict | None = None, reasons: dict | None = None) -> dict:
    """Every section, and the waves, refactor jobs and cloud-native parts, as ``present`` (with a count) or ``missing``
    (with why): what the bundle carries, said explicitly. ``reasons`` are the producing run's own (``gaps.json``);
    ``waves`` is the wave planner's report, so waves derived but not carried (no plan to carry them) are said."""
    reasons = dict(reasons or {})

    def why(k, default):
        return reasons.get(k) or default

    def count(v):
        return len(v) if isinstance(v, list) else None

    out: dict[str, dict] = {}
    for k in SECTIONS:              # the contract's sections only (icx-mdz-04: ICDEV[OS] refuses any other key)
        v = bundle.get(k)
        out[k] = ({"state": "present", **({"count": count(v)} if count(v) is not None else {})}
                  if v not in (None, [], {}) else {"state": "missing", "reason": why(k, "no step produced it")})
    carried = ((bundle.get("migration") or {}).get("waves")) or []
    derived = "carried in derived_waves, " if bundle.get("derived_waves") else ""
    dates = schedule_reason(waves)        # icx-mdz-05: dated from a reviewed capacity, or why not
    waves = _wave_rows(waves)
    out["migration.waves"] = ({"state": "present", "count": len(carried), **({"reason": dates} if dates else {})}
                              if carried else
                              {"state": "missing", **({"derived": len(waves)} if waves else {}),
                               "reason": why("migration.waves", f"derived before any plan ({derived}never approved): no "
                                             "migration plan carries them, so ICDEV[OS]'s migration-plan gate will block"
                                             if waves else "no wave was planned")})
    jobs = ((bundle.get("refactor") or {}).get("jobs")) or []
    out["refactor.jobs"] = ({"state": "present", "count": len(jobs)} if jobs else
                            {"state": "missing", "reason": why("refactor", "no CAM refactor job ran")})
    cn = bundle.get("cloud_native")
    kinds: dict[str, int] = {}
    for a in (cn or {}).get("artefacts") or []:
        kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
    for part, want in CLOUD_PARTS:
        key = f"cloud_native.{part}"
        n = sum(kinds.get(w, 0) for w in want)
        if cn is None:
            out[key] = {"state": "missing", "reason": why("cloud_native", "no cloud-native job ran")}
        elif n:
            extra = {"twelve_factor": ("factors", cn.get("factors")), "config": ("findings", cn.get("config")),
                     "events": ("channels", cn.get("events"))}.get(part)
            out[key] = {"state": "present", "count": n, **({extra[0]: len(extra[1] or [])} if extra else {})}
        else:
            out[key] = {"state": "missing", "reason": why(key, f"no {' or '.join(want)} job output in the run")}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export a legacy application as an ICDEV[OS] icos-apptwin/v1 bundle")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--features", type=Path, help="observed_inventory output (JSON)")
    ap.add_argument("--static-features", type=Path, help="static_features.py output (JSON)")
    ap.add_argument("--extract", type=Path, help="bundle_features.py output (JSON): features and processes, no model")
    ap.add_argument("--reasons", type=Path, help="why each missing section is missing (JSON {section: reason})")
    ap.add_argument("--sections-out", type=Path, help="write every section's present/missing state here (JSON)")
    ap.add_argument("--process", type=Path, action="append", default=[], help="a Process-Ify draft (JSON); repeatable")
    ap.add_argument("--aiify", type=Path, help="python -m tools.aiify.engine scan --json output")
    ap.add_argument("--integrity", type=Path, help="SIPA assessment (JSON)")
    ap.add_argument("--seven-r", type=Path, help="7R assessment (JSON)")
    ap.add_argument("--waves", type=Path, help="the migration canvas's waves (JSON list)")
    ap.add_argument("--strangler", type=Path, help="strangler-fig component status (JSON list)")
    ap.add_argument("--refactor", type=Path, help="CAM refactor jobs (JSON list)")
    ap.add_argument("--cloud-native", type=Path, help="cloud_native_jobs.py output (JSON)")
    ap.add_argument("--environments", type=Path,
                    help=f"the declared environments file (default {ENVIRONMENTS_REL}): each application's security "
                         "domain, impact level, ATO boundary and hosting as its deployment declares them, with the source")
    for k in PASSTHROUGH:
        ap.add_argument(f"--{k}", type=Path, help=f"the bundle's {k} section (JSON)")
    ap.add_argument("--at", help="produced_at (ISO 8601; default now), so a bundle can be re-made byte for byte")
    ap.add_argument("--out", type=Path, help="write the bundle here (default: stdout)")
    a = ap.parse_args(argv)

    def load(p):
        return None if p is None else json.loads(p.read_text(encoding="utf-8"))
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        name = (conn.execute("SELECT name FROM legacy_applications WHERE id = %s", (a.app_id,)).fetchone() or {"name": None})["name"]
        env, env_report = environment(a.app_id, name, path=a.environments)
        survivor = None
        if a.ivv is None:                       # icx-mdz-06: the survivor IV&V, carried only when signed
            from tools.modernization import survivor_ivv
            try:
                survivor = survivor_ivv.for_bundle(a.app_id)
            except Exception as exc:  # noqa: BLE001 -- unreadable is said, and the section stays null (unverified)
                survivor = {"section": None, "reason": f"the survivor IV&V could not be read: {exc}"}
        b = build(a.app_id, conn, at=_iso(a.at) if a.at else None, features=load(a.features),
                  processes=[load(p) for p in a.process], aiify=load(a.aiify), integrity=load(a.integrity),
                  seven_r=load(a.seven_r), sections={k: load(getattr(a, k)) for k in PASSTHROUGH},
                  waves=load(a.waves), strangler=load(a.strangler), refactor=load(a.refactor),
                  static_features=load(a.static_features), cloud_native=load(a.cloud_native),
                  extract=load(a.extract), environment=env, reasons=load(a.reasons), survivor_ivv=survivor)
    except (BundleError, OSError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    finally:
        conn.close()
    states = b["sections"]
    if a.sections_out:
        a.sections_out.write_text(json.dumps(states, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text = json.dumps(b, indent=2, sort_keys=True) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "out": str(a.out), "application": b["application"]["id"],
                          "contract": CONTRACT_VERSION, "environment": env_report,
                          "unverified": sorted(k for k in SECTIONS if b.get(k) is None),
                          "missing": sorted(k for k, v in states.items() if v["state"] == "missing")}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
