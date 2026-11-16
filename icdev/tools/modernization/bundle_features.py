# CUI // SP-CTI
"""An application's features and processes for its ICDEV[OS] bundle, WITHOUT A MODEL (icx-mdz-03).

The bundles ICDEV exported for real applications carried no features (ICDEV[OS]'s overlap engine then found nothing)
and no processes (the only Process-Ify path read a document with a language model, so ``--no-model`` skipped it).
This module derives both deterministically, from what the analyser and REA already recorded, and normalises them
with a REVIEWED rule set (``args/modernisation_signatures.yaml``), never a model:

* **features** -- the entry points ``legacy_analyzer`` read from source, grouped by ``static_features`` (``static``),
  merged with the routes an REA observed inventory names (``tools/aiify/observed_inventory.build_inventory`` output):
  a static feature an observed route reaches is ``both``; an observed route no static feature declares is an
  ``observed`` feature of its first path segment. Each has its evidence -- ``<file>:<line>`` of each handler, located
  in the source, ``route:<METHOD /path>`` and ``rea:<evidence id>`` for what was seen -- and its capability HINTS: each
  capability of the rule set whose terms the feature's words reach (its name, paths, handlers, dispatch values and the
  classes its controller calls), with the words matched. A hint is for a person, never a mapping.
* **processes** -- ``static``: the actions ONE controller offers, in source order (its handler methods, or the values a
  dispatcher servlet branches on, ``action.equals("buy")``), when it offers at least ``process.min_steps``. That is the
  order they are declared in, never an observed sequence, and each process says so. ``observed``: an REA scenario
  capture turned into a draft by ``tools/workflow_canvas/observed_processify.build_draft``. Each process carries its
  STEP SIGNATURE: every step normalised to ``verb:object`` by the rule set, in order, the SHA-256 of that list
  (``digest``) and the digest of the rules (``rules_digest``), so ICDEV[OS] can compare processes across applications
  deterministically -- and only under the same rules.

A process is unreviewed until a person reviews it at its signature digest, through the declared step
``decl-wft-mdz-process-review`` on the Modernisation page; a review at another digest (the code or the rules changed)
is stale. The exporter marks a process reviewed only on such a step, and leaves out one a person rejected.

Nothing is invented: an application with no entry point has no features (``None``, unverified in ICDEV[OS]), a
controller with one action has no process, and a handler whose declaration is not found in the source has no line.

    python tools/modernization/bundle_features.py --app-id <legacy app id> [--observed inv.json]
        [--observed-process draft.json ...] [--out extract.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- bootstrap the import root first.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root

_BASE = repo_root(__file__)

TOOL = "icdev-it/bundle_features"
RULES_REL = "args/modernisation_signatures.yaml"
METHOD = ("features from the analyser's entry points and REA's observed routes; processes from one controller's "
          "actions in source order or an REA capture; every step normalised by the reviewed rule set (no model)")
STATIC_ORDER = ("the actions this controller offers, in the order its source declares them: not an observed sequence; "
                "a person reviews it before ICDEV[OS] counts its steps")
SOURCES = ("static", "observed", "both")
REVIEW_STATES = ("reviewed", "rejected", "stale", "unreviewed")

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_GENERIC_HANDLERS = {"service", "doget", "dopost", "doput", "dodelete", "handle", "execute", "process", "perform"}


# --- the rule set -----------------------------------------------------------------------------------------------

def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


def rules(path: Path | None = None) -> dict:
    """The reviewed rule set, with ``digest``: the SHA-256 of its canonical JSON (comments and layout do not count)."""
    import yaml

    p = Path(path) if path else _BASE / RULES_REL
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for k in ("verbs", "objects", "capabilities"):
        if not isinstance(doc.get(k), dict) or not doc[k]:
            raise ValueError(f"{p}: '{k}' must be a non-empty mapping")
    listed = [w for k in ("verbs", "objects") for ws in doc[k].values() for w in ws] + list(doc.get("stopwords") or []) \
        + [t for c in doc["capabilities"].values() for t in c.get("terms") or []] + list(doc.get("phrases") or {})
    odd = [w for w in listed if not isinstance(w, str)]
    if odd:   # YAML reads a bare no / on / yes as a boolean: a rule must say the word it means
        raise ValueError(f"{p}: not a word: {odd[:5]} (quote it)")
    out = dict(doc)
    out["digest"] = hashlib.sha256(_canon(doc).encode()).hexdigest()
    out["path"] = RULES_REL if path is None else str(path)
    out["_verb"] = {w.lower(): v for v, ws in doc["verbs"].items() for w in ws}
    out["_object"] = {w.lower(): o for o, ws in doc["objects"].items() for w in ws}
    out["_stop"] = {str(w).lower() for w in doc.get("stopwords") or []}
    out["_phrases"] = sorted(((str(k).lower(), str(v).lower()) for k, v in (doc.get("phrases") or {}).items()),
                             key=lambda kv: (-len(kv[0]), kv[0]))
    out["_dispatch"] = [re.compile(rx) for rx in doc.get("dispatch") or []]
    return out


def _fold(w: str) -> str:
    """ICDEV[OS]'s plural fold (its twin/signatures._fold), so both sides fold a word the same way."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def words(text: str, r: dict) -> list[str]:
    """``addItemToCart`` -> ``["add", "item", "to", "cart"]``: split on case and punctuation, lowercased, the rule
    set's phrases joined (``check out`` -> ``checkout``)."""
    raw = " ".join(w.lower() for w in _SPLIT.split(_CAMEL.sub(" ", str(text or ""))) if w)
    raw = f" {raw} "
    for k, v in r["_phrases"]:
        raw = raw.replace(f" {k} ", f" {v} ")
    return [w for w in raw.split() if w and not w.isdigit()]


def _objects(ws: list[str], r: dict) -> list[str]:
    out = set()
    for w in ws:
        if w in r["_stop"]:
            continue
        o = r["_object"].get(w) or r["_object"].get(_fold(w)) or _fold(w)
        if o not in r["_stop"]:
            out.add(o)
    return sorted(out)


def normalise(text: str, r: dict, *, fallback_object: list[str] | None = None) -> dict:
    """One step's ``verb``, ``object`` and ``signature`` (``verb:object``; ``verb`` alone when it names no object and
    there is no fallback). The first word the rules call a verb is the verb, else ``default_verb``."""
    ws = words(text, r)
    verb, rest = None, []
    for w in ws:
        if verb is None and (w in r["_verb"] or _fold(w) in r["_verb"]):
            verb = r["_verb"].get(w) or r["_verb"][_fold(w)]
            continue
        rest.append(w)
    verb = verb or str(r.get("default_verb") or "view")
    obj = _objects(rest, r) or sorted(fallback_object or [])
    o = "+".join(obj)
    return {"verb": verb, "object": o, "signature": f"{verb}:{o}" if o else verb}


def signature(steps: list[dict], r: dict) -> dict:
    """A process's step signature: its steps' ``verb:object``, in order, their digest and the rules' digest."""
    sig = [s["signature"] for s in steps]
    return {"steps": sig, "digest": hashlib.sha256("\n".join(sig).encode()).hexdigest(), "rules_digest": r["digest"]}


def capability_hints(terms: dict[str, set[str]], r: dict) -> list[dict]:
    """Each capability whose terms the feature's words reach (``min_terms``), with the words matched and where from."""
    have: dict[str, set[str]] = {}
    for where, ws in terms.items():
        for w in ws:
            for v in {w, _fold(w), r["_object"].get(w) or "", r["_object"].get(_fold(w)) or ""} - {""}:
                have.setdefault(v, set()).add(where)
    least = int(r.get("min_terms") or 1)
    out = []
    for cid, c in sorted(r["capabilities"].items()):
        want = {_fold(str(t).lower()) for t in c.get("terms") or []} | {str(t).lower() for t in c.get("terms") or []}
        hit = sorted(want & set(have))
        if len(hit) >= least:
            out.append({"capability": cid, "title": c.get("title") or cid, "terms": hit,
                        "from": sorted(set().union(*(have[h] for h in hit)))})
    return out


# --- reading the source (never built or run) --------------------------------------------------------------------

def _read(root: str | None, rel: str | None) -> list[str] | None:
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        if not root:
            return None
        p = Path(root) / rel
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _class_line(lines: list[str], cls: str | None) -> int | None:
    if not cls:
        return None
    rx = re.compile(rf"\b(?:class|interface|object|def)\s+{re.escape(cls)}\b")
    return next((i + 1 for i, ln in enumerate(lines) if rx.search(ln)), None)


_DECL = r"^\s*(?:(?:public|protected|private|internal|static|final|async|override|virtual|synchronized|def)\s+)"


def _method_line(lines: list[str], method: str, after: int = 0) -> int | None:
    """The line declaring ``method`` (Java, C#, Python): a modifier, then the name and its opening parenthesis."""
    rx = re.compile(_DECL + rf"(?:[\w<>\[\],.?\s]*?\s)?{re.escape(method)}\s*\(")
    return next((i + 1 for i, ln in enumerate(lines) if i + 1 > after and rx.search(ln)), None)


def _calls(lines: list[str], own: set[str]) -> set[str]:
    """The application's own classes a source file calls into statically (``CategoryManager.GetMainCategories(``) or
    creates (``new SiteLayoutModel(``): their NAMES, never a library's, never a method's."""
    text = "\n".join(lines)
    found = {m.group(1) for m in re.finditer(r"\b([A-Z]\w+)\.[A-Z_a-z]\w*\s*\(", text)}
    found |= {m.group(1) for m in re.finditer(r"\bnew\s+([A-Z]\w+)\s*[(<]", text)}
    return found & own


def _dispatch(lines: list[str], r: dict) -> list[tuple[str, int, int]]:
    """The values a dispatcher branches on, each where it first appears (line, column), in source order."""
    found = sorted((i + 1, m.start(), m.group(1)) for i, ln in enumerate(lines) for rx in r["_dispatch"]
                   for m in rx.finditer(ln))
    seen, out = set(), []
    for line, col, v in found:
        if v not in seen:
            seen.add(v)
            out.append((v, line, col))
    return out


# --- features ---------------------------------------------------------------------------------------------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def _norm_route(ref: str) -> tuple[str, str]:
    method, _, path = str(ref).strip().partition(" ")
    if not path:
        method, path = "ALL", method
    parts = [p for p in path.split("?")[0].split("/") if p]
    parts = ["{}" if re.fullmatch(r"\{.*\}|:.+|<.*>|\d+", p) else p.lower() for p in parts]
    return method.upper(), "/" + "/".join(parts)


def _matches(api: str, route: str) -> bool:
    am, ap = _norm_route(api)
    rm, rp = _norm_route(route)
    return ap == rp and (am == "ALL" or rm in ("ALL", am))


def _observed_routes(observed: dict | None) -> list[dict]:
    """The routes an REA observed inventory names (``http_endpoint`` / ``route`` features), with their evidence."""
    out = []
    for f in (observed or {}).get("features") or []:
        if f.get("kind") in ("http_endpoint", "route") and f.get("label"):
            out.append({"label": str(f["label"]), "evidence": sorted({str(e) for e in f.get("evidence_ids") or []})})
    return sorted(out, key=lambda x: x["label"])


def _app(app_id: str, conn) -> dict:
    row = conn.execute("SELECT id, name, source_path, primary_language, framework FROM legacy_applications "
                       "WHERE id = %s", (app_id,)).fetchone()
    if row is None:
        raise ValueError(f"no legacy application {app_id!r} (register it with legacy_analyzer first)")
    return dict(row)


def _evidence_key(e: str):
    """``file:12`` before ``file:100``: by file, then line as a number."""
    head, _, tail = e.rpartition(":")
    return (head, int(tail), "") if head and tail.isdigit() else (e, -1, e)


def _members(eps: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for e in eps:
        by.setdefault(e["qualified"] or e["ref"], []).append(e)
    return by


def features(app_id: str, conn, r: dict, *, observed: dict | None = None) -> tuple[list[dict] | None, dict]:
    """The application's features (static, observed or both) and, per class, what its source says (for processes)."""
    from tools.modernization import static_features

    app = _app(app_id, conn)
    root = app.get("source_path")
    eps = static_features.entry_points(app_id, conn)
    grouped = static_features.derive(app_id, conn) or []
    own = {str(x["name"]) for x in conn.execute("SELECT name FROM legacy_components WHERE legacy_app_id = %s",
                                            (app_id,)).fetchall()}
    classes: dict[str, dict] = {}
    for q, ms in _members(eps).items():
        lines = _read(root, ms[0]["file"])
        cls = ms[0]["class"]
        cl = _class_line(lines, cls) if lines else None
        for m in ms:
            meth = (m["handler"] or "").rsplit(".", 1)[-1] if m["handler"] and "." in m["handler"] else None
            m["method"] = meth
            m["line"] = (_method_line(lines, meth, cl or 0) if lines and meth else None) or cl
        classes[q] = {"class": cls, "file": ms[0]["file"], "line": cl, "members": ms, "lines": lines,
                      "calls": (_calls(lines, own) - {cls}) if lines else set()}
    routes = _observed_routes(observed)
    used: set[str] = set()
    out = []
    for g in grouped:
        apis = set((g.get("signatures") or {}).get("apis") or [])
        ms = [e for e in eps if e["ref"] in apis]
        qs = sorted({e["qualified"] or e["ref"] for e in ms})
        seen = sorted({rt["label"] for rt in routes if any(_matches(a, rt["label"]) for a in apis)})
        used |= set(seen)
        ev = {f"{e['file']}:{e['line']}" if e.get("line") else f"{e['file']}" if e["file"] else f"api:{e['ref']}"
              for e in ms}
        ev |= {f"route:{lb}" for lb in seen}
        ev |= {f"rea:{x}" for rt in routes if rt["label"] in seen for x in rt["evidence"]}
        terms = {"name": set(words(g["title"], r)),
                 "apis": {w for a in apis for w in words(a.split(" ", 1)[-1], r)},
                 "handlers": {w for e in ms for w in words(e.get("method") or "", r)},
                 "calls": {w for q in qs for c in classes.get(q, {}).get("calls", ()) for w in words(c, r)}}
        disp = {w for q in qs for v, _, _ in (_dispatch(classes[q]["lines"], r) if classes.get(q, {}).get("lines") else [])
                for w in words(v, r)}
        if disp:
            terms["dispatch"] = disp
        if seen:
            terms["routes"] = {w for lb in seen for w in words(lb.split(" ", 1)[-1], r)}
        out.append({"id": g["id"], "name": g["title"], "source": "both" if seen else "static", "basis": "source",
                    "confidence": g.get("confidence"), "evidence": sorted(ev, key=_evidence_key), "apis": sorted(apis),
                    "routes": seen,
                    "classes": qs, "capability_hints": capability_hints(terms, r), "processes": []})
    rest: dict[str, list[dict]] = {}                       # observed routes no static feature declares
    for rt in routes:
        if rt["label"] in used:
            continue
        _, path = _norm_route(rt["label"])
        seg = next((p for p in path.split("/") if p and p != "{}"), "root")
        rest.setdefault(seg, []).append(rt)
    have = {f["id"] for f in out}
    for seg, rts in sorted(rest.items()):
        name = " ".join(words(seg, r)).capitalize() or seg
        fid = _slug(name) if _slug(name) not in have else f"{_slug(name)}-observed"
        terms = {"name": set(words(seg, r)), "routes": {w for rt in rts for w in words(rt["label"].split(" ", 1)[-1], r)}}
        out.append({"id": fid, "name": name, "source": "observed", "basis": "observed", "confidence": None,
                    "evidence": sorted({f"route:{rt['label']}" for rt in rts} |
                                       {f"rea:{x}" for rt in rts for x in rt["evidence"]}),
                    "apis": [], "routes": sorted(rt["label"] for rt in rts), "classes": [],
                    "capability_hints": capability_hints(terms, r), "processes": []})
    return (sorted(out, key=lambda f: f["id"]) or None), classes


# --- processes --------------------------------------------------------------------------------------------------

def _title(text: str) -> str:
    w = " ".join(re.sub(r"[_\-.]+", " ", _CAMEL.sub(" ", str(text))).split())
    return (w[:1].upper() + w[1:].lower()) if w else str(text)


def _static_process(feat: dict, q: str, info: dict, r: dict, *, many: bool) -> dict | None:
    lim = (r.get("process") or {})
    least, most = int(lim.get("min_steps") or 2), int(lim.get("max_steps") or 40)
    acts: dict[str, dict] = {}
    for m in info["members"]:
        meth = m.get("method")
        if not meth or meth.lower() in _GENERIC_HANDLERS or meth == info["class"]:
            continue
        a = acts.setdefault(meth, {"text": meth, "line": m.get("line"), "col": 0, "apis": set()})
        a["apis"].add(m["ref"])
    how = "handler methods"
    if len(acts) < least and info.get("lines"):
        acts = {v: {"text": v, "line": ln, "col": col, "apis": set()} for v, ln, col in _dispatch(info["lines"], r)}
        how = "the values its dispatcher branches on"
    if len(acts) < least:
        return None
    fallback = _objects([w for w in words(feat["name"], r) if w not in r["_verb"] and _fold(w) not in r["_verb"]], r)
    ordered = sorted(acts.values(), key=lambda a: (a["line"] is None, a["line"] or 0, a["col"], a["text"]))
    cut = len(ordered) > most
    steps = []
    for i, a in enumerate(ordered[:most]):
        n = normalise(a["text"], r, fallback_object=fallback if how == "handler methods" else None)
        steps.append({"id": f"s{i + 1:02d}", "title": _title(a["text"]), **n,
                      "evidence": f"{info['file']}:{a['line']}" if a["line"] else info["file"],
                      **({"apis": sorted(a["apis"])} if a["apis"] else {})})
    pid = f"{feat['id']}-{_slug(info['class'] or q)}" if many else f"{feat['id']}-actions"
    return {"id": pid, "title": f"{feat['name']}: {len(steps)} actions of {info['class'] or q}",
            "feature": feat["id"], "source": "static", "basis": "source", "order": STATIC_ORDER,
            "derived_from": how, "steps": steps, "signature": signature(steps, r),
            **({"truncated": True} if cut else {})}


def _observed_process(doc: dict, r: dict) -> dict | None:
    """An observed draft (``observed_processify.build_draft``, or its saved form): REA's steps, normalised."""
    wf = doc.get("workflow", doc)
    if wf.get("basis") != "observed" or not wf.get("steps"):
        return None
    steps = []
    for i, s in enumerate(wf["steps"]):
        n = normalise(s.get("title") or "", r)
        steps.append({"id": str(s.get("id") or f"obs-{i + 1:03d}"), "title": str(s.get("title") or f"step {i + 1}"),
                      **n, "evidence": f"rea:{s['evidence_id']}" if s.get("evidence_id") else "observed"})
    name = wf.get("workflow_name") or "observed process"
    return {"id": _slug(name), "title": name, "feature": None, "source": "observed", "basis": "observed",
            "order": "the order REA saw the program take", "derived_from": "an REA scenario capture",
            "steps": steps, "signature": signature(steps, r)}


def processes(feats: list[dict] | None, classes: dict, r: dict, *, observed: list[dict] = ()) -> list[dict] | None:
    out = []
    for f in feats or []:
        qs = [q for q in f["classes"] if q in classes]
        for q in qs:
            p = _static_process(f, q, classes[q], r, many=len(qs) > 1)
            if p:
                out.append(p)
                f["processes"].append(p["id"])
    for d in observed:
        p = _observed_process(d, r)
        if p:
            out.append(p)
    ids: dict[str, int] = {}
    for p in out:                                    # ids are unique within the application
        ids[p["id"]] = ids.get(p["id"], 0) + 1
        if ids[p["id"]] > 1:
            p["id"] = f"{p['id']}-{ids[p['id']]}"
    return sorted(out, key=lambda p: p["id"]) or None


# --- review ----------------------------------------------------------------------------------------------------

def subject(app_id: str, process_id: str) -> str:
    return f"{app_id}:{process_id}"


def review_states(app_id: str, procs: list[dict] | None, conn=None) -> None:
    """Set each process's ``review`` from the decided ``decl-wft-mdz-process-review`` steps. ``reviewed`` or
    ``rejected`` only at the process's signature digest now; a step at another digest is ``stale``. Raises when the
    ledger cannot be read: a caller that says "unreviewed" must know it looked."""
    from tools.modernization import decisions

    if not procs:
        return
    rows = decisions.decided(decisions.TEMPLATE_PROCESS, [subject(app_id, p["id"]) for p in procs], conn=conn)
    for p in procs:
        row = rows.get(subject(app_id, p["id"]))
        if row is None:
            p["review"] = {"state": "unreviewed"}
            continue
        at = (row.get("payload") or {}).get("signature_digest")
        state = ("reviewed" if row["affirmative"] else "rejected") if at == p["signature"]["digest"] else "stale"
        p["review"] = {"state": state, "step_id": row["step_id"], "decision": row["decision"], "by": row["actor"],
                       "role": row["actor_role"], "reason": row["reason"], "at": row["created_at"],
                       "signature_digest": at}


# --- the report -------------------------------------------------------------------------------------------------

def report(app_id: str, conn, *, observed: dict | None = None, observed_processes: list[dict] = (),
           rules_path: Path | None = None, reviews: bool = True) -> dict:
    """What the exporter carries (``apptwin_export.py --extract``) and the Modernisation page shows."""
    r = rules(rules_path)
    feats, classes = features(app_id, conn, r, observed=observed)
    procs = processes(feats, classes, r, observed=list(observed_processes))
    review_error = None
    if reviews:
        try:
            review_states(app_id, procs, conn=conn)
        except Exception as exc:  # noqa: BLE001 -- unreadable is said; nothing is marked reviewed on it
            review_error = str(exc)
            for p in procs or []:
                p["review"] = {"state": "unreviewed", "error": review_error}
    routes = _observed_routes(observed)
    by = {s: sum(1 for f in feats or [] if f["source"] == s) for s in SOURCES}
    return {"tool": TOOL, "method": METHOD, "app_id": app_id,
            "rules": {"path": r["path"], "digest": r["digest"], "version": r.get("version")},
            "features": feats, "processes": procs,
            "counts": {"features": len(feats or []), **{f"features_{k}": v for k, v in by.items()},
                       "processes": len(procs or []),
                       **{f"processes_{s}": sum(1 for p in procs or [] if (p.get("review") or {}).get("state") == s)
                          for s in REVIEW_STATES}},
            "observed": {"given": observed is not None, "routes": len(routes),
                         "basis": (observed or {}).get("features_basis") if observed is not None else
                         "no REA observed inventory given (the source is never run here)",
                         "processes_given": len(observed_processes)},
            **({"review_error": review_error} if review_error else {})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="An application's features and processes, without a model (JSON out)")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--observed", type=Path, help="an REA observed inventory (observed_inventory.build_inventory JSON)")
    ap.add_argument("--observed-process", type=Path, action="append", default=[],
                    help="an observed Process-Ify draft (observed_processify.build_draft JSON); repeatable")
    ap.add_argument("--out", type=Path, help="write the report here (default: stdout)")
    a = ap.parse_args(argv)

    def load(p):
        return json.loads(p.read_text(encoding="utf-8"))
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rep = report(a.app_id, conn, observed=load(a.observed) if a.observed else None,
                     observed_processes=[load(p) for p in a.observed_process])
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    finally:
        conn.close()
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "out": str(a.out), **rep["counts"]}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
