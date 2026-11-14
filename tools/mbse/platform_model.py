#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV platform model: ICDEV's own architecture, traced to code and tests (icx-cnf-04).

``args/platform_model/*.yaml`` is the reviewed source. Each file declares, for one
component, its elements (blocks), its interfaces (interface blocks) and its
requirements. A requirement names the features it serves (keys from
``args/feature_conformance.yaml``), the elements that realise it, the NIST
controls it maps to, and the tests that verify it.

This tool does three things with that source:

``--check``  validates it against the repository with no database: every
             element type is a digital-thread artifact type, every code entry
             exists (a file, or a ``path::func`` / ``path::Class.method`` symbol
             in it), every test is a pytest node whose file AND function exist,
             no element is an orphan, every feature key is a real one. A test
             that does not exist is an ERROR; a requirement with ``tests: []`` is
             a GAP, reported by name and never counted as traced.
``--seed``   writes the MBSE digital thread for project ``icdev-platform`` (the
             project row, ``doors_requirements``, ``sysml_elements`` and
             ``digital_thread_links``): requirement -traces_to-> element
             -implements-> code -verifies-> test, test -verifies-> requirement,
             code -maps_to-> control. A re-seed replaces this project's rows, so
             a removed requirement leaves nothing behind.
``--trace``  reads the seeded thread back per feature, with
             ``digital_thread.find_orphans`` and ``find_gaps`` for the project.
             The MCP tools ``trace_forward`` / ``trace_backward`` resolve the
             same rows (requirement ids such as ``PM-CTX-01``).

A code entry with a symbol is its own ``code_module`` in the thread, so a test is
never linked to code it does not exercise: an untested route inside a well-tested
file still shows as code without a test.

    python tools/mbse/platform_model.py --check [--json]
    python tools/mbse/platform_model.py --seed [--db-path PATH] [--json]
    python tools/mbse/platform_model.py --trace [--feature KEY] [--db-path PATH] [--json]

Run ``--seed`` against a scratch database copy (``ICDEV_DB_PATH``), never
``data/icdev.db`` on a shared host, unless that is the database you mean to seed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

import yaml  # noqa: E402

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

MODEL_DIR = "args/platform_model"
FEATURES_FILE = "args/feature_conformance.yaml"
PROJECT_ID = "icdev-platform"
CREATED_BY = "platform-model"

# sysml_elements.element_type for each declared kind.
_KINDS = {"block": "block", "interface_block": "interface_block"}
_REQ_TYPES = ("functional", "non_functional", "interface", "design", "security",
              "performance", "constraint")
_PRIORITIES = ("critical", "high", "medium", "low")


class ModelError(RuntimeError):
    """The platform model cannot be read."""


# ── loading ──────────────────────────────────────────────────────────────────


def load_model(root: Path = BASE_DIR, rel_dir: str = MODEL_DIR) -> Dict[str, Any]:
    """Every element, interface and requirement under ``rel_dir``, in file order.

    Interfaces are elements with ``interface: True``; the feature audit reads
    both lists the same way.
    """
    files: List[str] = []
    elements: Dict[str, Dict[str, Any]] = {}
    requirements: List[Dict[str, Any]] = []
    duplicates: List[str] = []
    digest = hashlib.sha256()
    for path in sorted((root / rel_dir).glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        digest.update(rel.encode() + b"\0" + raw)
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            raise ModelError(f"{rel}: {exc}") from exc
        files.append(rel)
        component = str(data.get("component") or path.stem)
        for key, is_iface in (("elements", False), ("interfaces", True)):
            for el in data.get(key) or []:
                if not isinstance(el, dict) or not el.get("id"):
                    continue
                el_id = str(el["id"])
                if el_id in elements:
                    duplicates.append(el_id)
                elements[el_id] = dict(el, interface=is_iface, component=component, file=rel)
        for req in data.get("requirements") or []:
            if isinstance(req, dict) and req.get("id"):
                requirements.append(dict(req, component=component, file=rel))
    seen: set = set()
    for req in requirements:
        if req["id"] in seen:
            duplicates.append(str(req["id"]))
        seen.add(req["id"])
    return {"files": files, "elements": elements, "requirements": requirements,
            "duplicates": sorted(set(duplicates)), "digest": digest.hexdigest()}


def _feature_keys(root: Path) -> List[str]:
    try:
        cfg = yaml.safe_load((root / FEATURES_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return [str(f["key"]) for f in cfg.get("features") or [] if isinstance(f, dict) and f.get("key")]


def _thread_types(root: Path) -> set:
    """``VALID_TYPES`` from tools/mbse/digital_thread.py, read without importing it."""
    try:
        tree = ast.parse((root / "tools/mbse/digital_thread.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "VALID_TYPES" for t in node.targets):
            return set(ast.literal_eval(node.value))
    return set()


# ── resolving code and tests against the repository ─────────────────────────


class _Symbols:
    """Top-level functions and classes (with their methods) per Python file, cached."""

    def __init__(self, root: Path):
        self.root = root
        self._cache: Dict[str, Optional[Dict[str, set]]] = {}

    def of(self, rel: str) -> Optional[Dict[str, set]]:
        if rel not in self._cache:
            try:
                tree = ast.parse((self.root / rel).read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                self._cache[rel] = None
                return None
            found: Dict[str, set] = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.setdefault(node.name, set())
                elif isinstance(node, ast.ClassDef):
                    found[node.name] = {
                        n.name for n in node.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            self._cache[rel] = found
        return self._cache[rel]


def resolve_code(root: Path, ref: str, symbols: Optional[_Symbols] = None) -> Optional[str]:
    """None when ``ref`` (a file, or ``path::func`` / ``path::Class.method``) exists, else why not."""
    path, _, symbol = str(ref).partition("::")
    if not path or not (root / path).is_file():
        return f"no file {path or ref!r}"
    if not symbol:
        return None
    found = (symbols or _Symbols(root)).of(path)
    if found is None:
        return f"{path} is not parseable Python"
    owner, _, method = symbol.partition(".")
    if owner not in found:
        return f"{path} defines no top-level {owner!r}"
    if method and method not in found[owner]:
        return f"{path}: {owner} has no method {method!r}"
    return None


def resolve_test(root: Path, ref: str, symbols: Optional[_Symbols] = None) -> Optional[str]:
    """None when the pytest node ``ref`` exists (file, and function if named), else why not.

    ``path``, ``path::test_x`` or ``path::TestClass::test_x``. A parametrized
    test is named without its parameter id. The name is checked in the file's
    AST, so a cited test is one pytest would collect, never one made up.
    """
    parts = str(ref).split("::")
    path = parts[0]
    if not path.startswith("tests/") or not path.endswith(".py"):
        return f"{ref!r} is not a test under tests/"
    if not (root / path).is_file():
        return f"no test file {path}"
    if len(parts) == 1:
        return None
    found = (symbols or _Symbols(root)).of(path)
    if found is None:
        return f"{path} is not parseable Python"
    if len(parts) == 2:
        if not parts[1].startswith("test") or parts[1] not in found or found[parts[1]]:
            return f"{path} has no test function {parts[1]!r}"
        return None
    if len(parts) == 3:
        cls, name = parts[1], parts[2]
        if cls not in found or name not in found[cls] or not name.startswith("test"):
            return f"{path} has no test {cls}::{name}"
        return None
    return f"{ref!r} is not a pytest node id"


# ── validation (no database) ─────────────────────────────────────────────────


def validate(root: Path = BASE_DIR, model: Optional[Dict[str, Any]] = None,
             feature_keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Structural errors, untested requirements (gaps) and a per-feature verdict.

    ``errors`` are defects in the model itself and make ``--check`` fail. A
    ``gap`` is a requirement nothing tests yet; it is listed, never passed.
    """
    model = model if model is not None else load_model(root)
    keys = list(feature_keys) if feature_keys is not None else _feature_keys(root)
    types = _thread_types(root)
    symbols = _Symbols(root)
    errors: List[str] = [f"duplicate id {d}" for d in model["duplicates"]]
    elements = model["elements"]

    for el_id, el in sorted(elements.items()):
        if types and str(el.get("type")) not in types:
            errors.append(f"element {el_id}: type {el.get('type')!r} is not a digital-thread type")
        if str(el.get("kind", "block")) not in _KINDS:
            errors.append(f"element {el_id}: kind {el.get('kind')!r} is not one of {sorted(_KINDS)}")
        code = el.get("code") or []
        if not code:
            errors.append(f"element {el_id}: links to no code")
        for ref in code:
            why = resolve_code(root, ref, symbols)
            if why:
                errors.append(f"element {el_id}: code {ref}: {why}")
        for key in el.get("features") or []:
            if keys and key not in keys:
                errors.append(f"element {el_id}: unknown feature {key!r}")

    used: Dict[str, set] = {}
    gaps: List[Dict[str, str]] = []
    per_feature: Dict[str, Dict[str, Any]] = {
        k: {"requirements": [], "traced": [], "untested": [], "elements": []} for k in keys}
    for req in model["requirements"]:
        rid = str(req["id"])
        feats = [str(f) for f in req.get("features") or []]
        if not feats:
            errors.append(f"requirement {rid}: names no feature")
        if str(req.get("type", "functional")) not in _REQ_TYPES:
            errors.append(f"requirement {rid}: type {req.get('type')!r} is not a DOORS type")
        if str(req.get("priority", "medium")) not in _PRIORITIES:
            errors.append(f"requirement {rid}: priority {req.get('priority')!r} is not valid")
        els = [str(e) for e in req.get("elements") or []]
        if not els:
            errors.append(f"requirement {rid}: names no element")
        for el_id in els:
            if el_id not in elements:
                errors.append(f"requirement {rid}: undeclared element {el_id}")
                continue
            for key in feats:
                used.setdefault(key, set()).add(el_id)
        tests = [str(t) for t in req.get("tests") or []]
        for ref in tests:
            why = resolve_test(root, ref, symbols)
            if why:
                errors.append(f"requirement {rid}: test {ref}: {why}")
        if not req.get("controls"):
            errors.append(f"requirement {rid}: maps to no NIST control (find_gaps would "
                          "report its tests as test_without_control)")
        if not tests:
            gaps.append({"requirement": rid, "features": ", ".join(feats),
                         "detail": f"{rid} traces to no test"})
        for key in feats:
            if keys and key not in keys:
                errors.append(f"requirement {rid}: unknown feature {key!r}")
                continue
            row = per_feature.setdefault(
                key, {"requirements": [], "traced": [], "untested": [], "elements": []})
            row["requirements"].append(rid)
            (row["traced"] if tests else row["untested"]).append(rid)

    for el_id, el in sorted(elements.items()):
        for key in el.get("features") or []:
            if el_id not in used.get(key, set()):
                errors.append(f"element {el_id}: orphan, no requirement of {key} names it")
            if key in per_feature:
                per_feature[key]["elements"].append(el_id)

    for key, row in per_feature.items():
        if not row["requirements"]:
            row["status"] = "no_requirement"
        elif row["untested"]:
            row["status"] = "gap"
        else:
            row["status"] = "traced"
    return {
        "ok": not errors,
        "errors": errors,
        "gaps": gaps,
        "features": per_feature,
        "summary": {
            "files": len(model["files"]),
            "elements": sum(1 for e in elements.values() if not e["interface"]),
            "interfaces": sum(1 for e in elements.values() if e["interface"]),
            "requirements": len(model["requirements"]),
            "untested_requirements": len(gaps),
            "features_traced": sum(1 for r in per_feature.values() if r["status"] == "traced"),
            "features": len(per_feature),
            "model_digest": model["digest"],
        },
    }


# ── the digital thread ───────────────────────────────────────────────────────


def _db_path(db_path: Optional[str]) -> str:
    """The SQLite file the digital-thread functions will read (PostgreSQL ignores it)."""
    if db_path:
        return str(db_path)
    return os.environ.get("ICDEV_DB_PATH") or str(BASE_DIR / "data" / "icdev.db")


def thread_links(model: Dict[str, Any]) -> List[tuple]:
    """Every (source_type, source_id, target_type, target_id, link_type) the model implies."""
    elements = model["elements"]
    links: set = set()
    for req in model["requirements"]:
        rid = str(req["id"])
        tests = [str(t) for t in req.get("tests") or []]
        controls = [str(c) for c in req.get("controls") or []]
        for test in tests:
            links.add(("test_file", test, "doors_requirement", rid, "verifies"))
        for el_id in req.get("elements") or []:
            el = elements.get(str(el_id))
            if el is None:
                continue
            links.add(("doors_requirement", rid, "sysml_element", str(el_id), "traces_to"))
            for code in el.get("code") or []:
                links.add(("sysml_element", str(el_id), "code_module", str(code), "implements"))
                for test in tests:
                    links.add(("code_module", str(code), "test_file", test, "verifies"))
                for control in controls:
                    links.add(("code_module", str(code), "nist_control", control, "maps_to"))
    return sorted(links)


def seed(db_path: Optional[str] = None, root: Path = BASE_DIR,
         project_id: str = PROJECT_ID) -> Dict[str, Any]:
    """Replace this project's digital thread with the one the model declares.

    Refuses a model with structural errors: a thread seeded from a model that
    cites a missing test would put an invented test into the RTM.
    """
    from tools.db.storage import get_connection

    model = load_model(root)
    report = validate(root, model)
    if not report["ok"]:
        return {"seeded": False, "project_id": project_id, "errors": report["errors"]}
    untested = {g["requirement"] for g in report["gaps"]}
    links = thread_links(model)
    conn = get_connection(db_path=_db_path(db_path))
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
        if not c.fetchone():
            c.execute(
                """INSERT INTO projects (id, name, description, type, classification,
                       status, directory_path, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (project_id, "ICDEV platform model",
                 "ICDEV's own architecture: Cortex, ACE, Co-Workers and the conformance "
                 "audit, seeded from args/platform_model (icx-cnf-04).",
                 "webapp", "CUI", "active", ".", CREATED_BY),
            )
        c.execute("DELETE FROM digital_thread_links WHERE project_id = %s", (project_id,))
        c.execute("DELETE FROM doors_requirements WHERE project_id = %s", (project_id,))
        c.execute("DELETE FROM sysml_elements WHERE project_id = %s", (project_id,))
        for el_id, el in sorted(model["elements"].items()):
            c.execute(
                """INSERT INTO sysml_elements (id, project_id, xmi_id, element_type, name,
                       qualified_name, description, properties, source_file, source_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (el_id, project_id, el_id, _KINDS[str(el.get("kind", "block"))],
                 str(el.get("name") or el_id), f"{el['component']}::{el_id}",
                 str(el.get("description") or ""),
                 json.dumps({"features": el.get("features") or [], "code": el.get("code") or []}),
                 el["file"], model["digest"]),
            )
        for req in model["requirements"]:
            rid = str(req["id"])
            c.execute(
                """INSERT INTO doors_requirements (id, project_id, doors_id, module_name,
                       requirement_type, title, description, priority, status,
                       source_file, source_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (rid, project_id, rid, req["component"], str(req.get("type", "functional")),
                 str(req.get("title") or rid), " ".join(str(req.get("text") or "").split()),
                 str(req.get("priority", "medium")),
                 "active" if rid in untested else "verified",
                 req["file"], model["digest"]),
            )
        for s_type, s_id, t_type, t_id, link_type in links:
            c.execute(
                """INSERT INTO digital_thread_links (project_id, source_type, source_id,
                       target_type, target_id, link_type, confidence, evidence, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (project_id, s_type, s_id, t_type, t_id, link_type, 1.0,
                 "args/platform_model", CREATED_BY),
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "seeded": True,
        "project_id": project_id,
        "elements": len(model["elements"]),
        "requirements": len(model["requirements"]),
        "links": len(links),
        "untested_requirements": sorted(untested),
        "model_digest": model["digest"],
    }


def trace(db_path: Optional[str] = None, root: Path = BASE_DIR, feature: Optional[str] = None,
          project_id: str = PROJECT_ID) -> Dict[str, Any]:
    """Each feature's requirement -> element -> code -> test chain, read from the thread.

    A requirement counts as traced only when a ``test_file -verifies->`` link
    reaches it in the database. ``find_orphans`` and ``find_gaps`` run for the
    project and each item is attributed to the features whose code it names.
    An unseeded or out-of-date thread says so; it is never read as clean.
    """
    from tools.db.storage import get_connection
    from tools.mbse.digital_thread import find_gaps, find_orphans

    model = load_model(root)
    path = _db_path(db_path)
    conn = get_connection(db_path=path)
    try:
        c = conn.cursor()
        try:
            c.execute(
                "SELECT source_type, source_id, target_type, target_id, link_type "
                "FROM digital_thread_links WHERE project_id = %s", (project_id,))
            rows = [(r[0], r[1], r[2], r[3], r[4]) for r in c.fetchall()]
            c.execute("SELECT source_hash FROM doors_requirements WHERE project_id = %s",
                      (project_id,))
            hashes = {r[0] for r in c.fetchall()}
        except Exception as exc:  # noqa: BLE001 — a missing table is "not seeded", said so
            return {"seeded": False, "project_id": project_id,
                    "reason": f"digital thread unreadable: {exc}", "features": {}}
    finally:
        conn.close()
    if not rows:
        return {"seeded": False, "project_id": project_id,
                "reason": "no digital thread for this project; run "
                          "`python tools/mbse/platform_model.py --seed`",
                "features": {}}

    verified: Dict[str, List[str]] = {}
    for s_type, s_id, t_type, t_id, link_type in rows:
        if s_type == "test_file" and t_type == "doors_requirement" and link_type == "verifies":
            verified.setdefault(t_id, []).append(s_id)
    orphans = find_orphans(project_id, db_path=path)
    gaps = find_gaps(project_id, db_path=path)
    orphan_code = {o["id"] for o in orphans["code_without_tests"]["items"]}
    gap_code = {g.get("code_id") for g in gaps["gaps"] if g.get("code_id")}

    features: Dict[str, Dict[str, Any]] = {}
    for req in model["requirements"]:
        for key in req.get("features") or []:
            if feature and key != feature:
                continue
            row = features.setdefault(key, {"requirements": [], "orphans": [], "gaps": []})
            chain = []
            for el_id in req.get("elements") or []:
                el = model["elements"].get(str(el_id)) or {}
                code = [str(x) for x in el.get("code") or []]
                chain.append({"element": str(el_id), "name": el.get("name", ""),
                              "interface": bool(el.get("interface")), "code": code})
                for ref in code:
                    if ref in orphan_code and ref not in row["orphans"]:
                        row["orphans"].append(ref)
                    if ref in gap_code and ref not in row["gaps"]:
                        row["gaps"].append(ref)
            tests = sorted(verified.get(str(req["id"]), []))
            row["requirements"].append({
                "id": str(req["id"]), "title": str(req.get("title") or ""),
                "elements": chain, "tests": tests,
                "controls": [str(x) for x in req.get("controls") or []],
                "status": "traced" if tests else "gap",
            })
    for row in features.values():
        row["orphans"].sort()
        row["gaps"].sort()
        untested = [r["id"] for r in row["requirements"] if r["status"] != "traced"]
        row["untested"] = untested
        row["status"] = "traced" if not untested and not row["orphans"] and not row["gaps"] else "gap"
    return {
        "seeded": True,
        "project_id": project_id,
        "current": hashes == {model["digest"]},
        "model_digest": model["digest"],
        "features": dict(sorted(features.items())),
        "orphans": {k: v["count"] for k, v in orphans.items()},
        "gaps_total": gaps["total_gaps"],
        "gap_types": sorted({g["gap_type"] for g in gaps["gaps"]}),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _print_check(report: Dict[str, Any]) -> None:
    s = report["summary"]
    print(f"platform model: {s['files']} file(s), {s['elements']} element(s), "
          f"{s['interfaces']} interface(s), {s['requirements']} requirement(s)")
    print(f"features traced: {s['features_traced']} of {s['features']}; "
          f"untested requirements: {s['untested_requirements']}")
    for key, row in sorted(report["features"].items()):
        extra = f" (untested: {', '.join(row['untested'])})" if row["untested"] else ""
        print(f"  {row['status']:<15} {key}{extra}")
    for err in report["errors"]:
        print(f"ERROR {err}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate the model, no database")
    mode.add_argument("--seed", action="store_true", help="write the digital thread")
    mode.add_argument("--trace", action="store_true", help="read the seeded thread per feature")
    parser.add_argument("--db-path", default=None, help="SQLite path (default: ICDEV_DB_PATH)")
    parser.add_argument("--feature", default=None, help="limit --trace to one feature key")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        report = validate()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_check(report)
        return 0 if report["ok"] else 1
    if args.seed:
        result = seed(db_path=args.db_path)
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else
              (f"seeded {result['project_id']}: {result['requirements']} requirement(s), "
               f"{result['links']} link(s)" if result["seeded"] else
               "refused: " + "; ".join(result["errors"][:5])))
        return 0 if result["seeded"] else 1
    result = trace(db_path=args.db_path, feature=args.feature)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif not result["seeded"]:
        print(result["reason"])
    else:
        for key, row in result["features"].items():
            print(f"{row['status']:<7} {key}: {len(row['requirements'])} requirement(s)"
                  + (f", untested {', '.join(row['untested'])}" if row["untested"] else ""))
    return 0 if result["seeded"] else 1


if __name__ == "__main__":
    sys.exit(main())
