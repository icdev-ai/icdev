# CUI // SP-CTI
"""A coherence profile for ANY application's directory (icx-coh-01).

``coherence_checker.py`` is ICDEV checking itself: its root is fixed and most of its checks know ICDEV's own
layout. ICDEV[OS]'s application twin wants the GENERIC ones run against a legacy application being refactored, as
the ``coherence`` section of its ``icos-apptwin/v1`` bundle. This profile re-points those at a target directory:

    swallowed_persistence  ``except Exception: pass`` around a write (Python; the detector the ICDEV gate uses)
    config_code            config keys (YAML/JSON under config dirs) that no source file mentions
    append_only            an audit/history table declared in SQL with nothing that stops UPDATE or DELETE on it
    openapi_parity         paths in an OpenAPI/Swagger document vs the routes the code declares (Flask, FastAPI,
                           Spring, Express, ASP.NET attribute routes)
    signature_call         Python tests that call an imported function with arguments it cannot take

Each says ``pass``, ``fail``, ``warn`` or ``skip`` WITH its evidence; ``skip`` always says why (no Python, no
OpenAPI document, no audit table), so a check that could not run is never read as one that passed.

    python tools/workflow/app_coherence.py --target /src/app --json            # the full report
    python tools/workflow/app_coherence.py --target /src/app --bundle-section  # the bundle's coherence section
    python tools/workflow/coherence_checker.py --target /src/app --profile app --json   # the same, via the checker
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- bootstrap the import root first.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

_BASE = repo_root(__file__)

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target", ".tox", "vendor"}
SOURCE_EXT = {".py", ".java", ".cs", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb", ".php", ".kt", ".scala", ".sql",
              ".html", ".jinja", ".jinja2", ".vb", ".cbl", ".cob"}
CONFIG_DIRS = ("args", "config", "conf", "configs", "settings")
MAX_FILE = 2_000_000


@dataclass
class Result:
    id: str
    status: str                      # pass | fail | warn | skip
    summary: str
    evidence: list[str] = field(default_factory=list)


def _files(root: Path, exts: set[str] | None = None) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]) or not p.is_file():
            continue
        if exts is None or p.suffix.lower() in exts:
            try:
                if p.stat().st_size <= MAX_FILE:
                    out.append(p)
            except OSError:
                continue
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


# --- the checks -----------------------------------------------------------------------------------

def check_swallowed_persistence(root: Path) -> Result:
    py = _files(root, {".py"})
    if not py:
        return Result("swallowed_persistence", "skip", "no Python source (the detector reads Python only)")
    from tools.refactor.swallowed_persistence import find_sites
    sites = find_sites([root], root)
    if sites:
        return Result("swallowed_persistence", "fail", f"{len(sites)} write(s) whose failure is swallowed silently",
                      [f"{s.rel}:{s.handler_lineno} ({s.func_name or '<module>'} -> {s.table})" for s in sites[:50]])
    return Result("swallowed_persistence", "pass", f"no swallowed write in {len(py)} Python file(s)")


def _config_keys(p: Path) -> list[str]:
    text = _read(p)
    try:
        if p.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            import yaml
            data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 -- an unreadable config file is reported, not fatal
        return []
    return [str(k) for k in data] if isinstance(data, dict) else []


def check_config_code(root: Path) -> Result:
    cfgs = [p for d in CONFIG_DIRS if (root / d).is_dir() for p in _files(root / d, {".yaml", ".yml", ".json"})]
    if not cfgs:
        return Result("config_code", "skip", f"no config directory ({', '.join(CONFIG_DIRS)}) with YAML/JSON in it")
    corpus = "\n".join(_read(p) for p in _files(root, SOURCE_EXT))
    unused = []
    for c in cfgs:
        stem_used = c.stem in corpus or c.name in corpus
        for k in _config_keys(c):
            if len(k) > 2 and k not in corpus and not (stem_used and k in ("version", "schema")):
                unused.append(f"{_rel(root, c)}: {k}")
    if unused:
        return Result("config_code", "warn", f"{len(unused)} config key(s) no source file mentions", unused[:50])
    return Result("config_code", "pass", f"every top-level key of {len(cfgs)} config file(s) is mentioned in source")


_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(\w*(?:audit|history|ledger|journal)\w*)",
                    re.IGNORECASE)


def check_append_only(root: Path) -> Result:
    sources = [p for p in _files(root, SOURCE_EXT | {".ddl"})]
    tables: dict[str, str] = {}
    blob = []
    for p in sources:
        t = _read(p)
        for m in _TABLE.finditer(t):
            tables.setdefault(m.group(1).lower(), _rel(root, p))
        blob.append(t)
    if not tables:
        return Result("append_only", "skip", "no audit, history, ledger or journal table declared in SQL")
    text = "\n".join(blob)
    open_, guarded = [], []
    for t, where in sorted(tables.items()):
        trig = re.search(rf"CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER[\s\S]{{0,400}}?(?:UPDATE|DELETE)[\s\S]{{0,200}}?ON\s+[\"`\[]?{t}\b",
                         text, re.IGNORECASE)
        revoke = re.search(rf"REVOKE\s+[\w,\s]*(?:UPDATE|DELETE)[\w,\s]*ON\s+(?:TABLE\s+)?[\"`\[]?{t}\b", text, re.IGNORECASE)
        (guarded if trig or revoke else open_).append(f"{t} ({where})")
    if open_:
        return Result("append_only", "fail", f"{len(open_)} audit-like table(s) with nothing that stops UPDATE/DELETE",
                      open_)
    return Result("append_only", "pass", f"{len(guarded)} audit-like table(s), each guarded", guarded)


_ROUTE = [
    re.compile(r"@\w+\.(?:route|get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]"),                # Flask/FastAPI
    re.compile(r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?['\"]([^'\"]+)['\"]"),
    re.compile(r"\b(?:app|router)\.(?:get|post|put|delete|patch|all)\(\s*['\"]([^'\"]+)['\"]"),       # Express
    re.compile(r"\[(?:Http(?:Get|Post|Put|Delete|Patch)|Route)\(\s*\"([^\"]+)\""),                     # ASP.NET
]


def _norm(path: str) -> str:
    p = re.sub(r"\{[^}]+\}|<[^>]+>|:\w+", "{}", path.strip())
    return "/" + p.strip("/")


def check_openapi_parity(root: Path) -> Result:
    specs = [p for p in _files(root, {".json", ".yaml", ".yml"})
             if re.search(r"(openapi|swagger)", p.name, re.IGNORECASE)]
    if not specs:
        return Result("openapi_parity", "skip", "no OpenAPI/Swagger document in the tree")
    documented = set()
    for s in specs:
        text = _read(s)
        try:
            import yaml
            doc = json.loads(text) if s.suffix == ".json" else yaml.safe_load(text)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict) and isinstance(doc.get("paths"), dict):
            documented |= {_norm(k) for k in doc["paths"]}
    if not documented:
        return Result("openapi_parity", "skip", "OpenAPI/Swagger file(s) found but none declares paths",
                      [_rel(root, s) for s in specs])
    declared = set()
    for p in _files(root, SOURCE_EXT - {".sql", ".html", ".jinja", ".jinja2"}):
        for rx in _ROUTE:
            declared |= {_norm(m) for m in rx.findall(_read(p))}
    if not declared:
        return Result("openapi_parity", "skip", "the document declares paths but no route declaration was recognised "
                      "in source (frameworks recognised: Flask, FastAPI, Spring, Express, ASP.NET)")
    undoc, unimpl = sorted(declared - documented), sorted(documented - declared)
    if undoc or unimpl:
        return Result("openapi_parity", "fail", f"{len(undoc)} route(s) undocumented, {len(unimpl)} documented path(s) "
                      "with no route", [f"undocumented: {x}" for x in undoc[:25]] + [f"no route: {x}" for x in unimpl[:25]])
    return Result("openapi_parity", "pass", f"{len(declared)} route(s), each documented")


def _arity(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int | None, set[str], bool]:
    a = fn.args
    pos = a.posonlyargs + a.args
    required = len(pos) - len(a.defaults)
    names = {x.arg for x in a.args + a.kwonlyargs}
    return required, (None if a.vararg else len(pos)), names, a.kwarg is not None


def check_signature_call(root: Path) -> Result:
    tests = [p for p in _files(root, {".py"}) if p.name.startswith("test_") or "/tests/" in "/" + _rel(root, p)]
    if not tests:
        return Result("signature_call", "skip", "no Python tests")
    defs: dict[str, dict[str, tuple]] = {}

    def module_defs(mod: str) -> dict[str, tuple]:
        if mod not in defs:
            defs[mod] = {}
            f = next((b.joinpath(*mod.split(".")).with_suffix(".py") for b in (root, root / "src")
                      if b.joinpath(*mod.split(".")).with_suffix(".py").is_file()), None)
            if f is not None:
                try:
                    tree = ast.parse(_read(f))
                except SyntaxError:
                    return defs[mod]
                for n in tree.body:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defs[mod][n.name] = _arity(n)
        return defs[mod]
    bad, calls = [], 0
    for t in tests:
        try:
            tree = ast.parse(_read(t))
        except SyntaxError:
            continue
        imported = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for al in n.names:
                    sig = module_defs(n.module).get(al.name)
                    if sig:
                        imported[al.asname or al.name] = (n.module, al.name, sig)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in imported:
                if any(isinstance(x, ast.Starred) for x in n.args) or any(k.arg is None for k in n.keywords):
                    continue
                calls += 1
                mod, name, (req, most, names, kw) = imported[n.func.id]
                given = len(n.args)
                unknown = [k.arg for k in n.keywords if k.arg not in names and not kw]
                short = given + sum(1 for k in n.keywords if k.arg in names) < req
                if (most is not None and given > most) or unknown or short:
                    bad.append(f"{_rel(root, t)}:{n.lineno} {mod}.{name}"
                               + (f" takes at most {most} positional" if most is not None and given > most else "")
                               + (f" has no parameter {unknown}" if unknown else "") + (" misses required arguments" if short else ""))
    if not calls:
        return Result("signature_call", "skip", "no test calls a function imported from the application's own modules")
    if bad:
        return Result("signature_call", "fail", f"{len(bad)} of {calls} test call(s) do not fit the signature", bad[:50])
    return Result("signature_call", "pass", f"{calls} test call(s) fit their functions' signatures")


CHECKS = (check_swallowed_persistence, check_config_code, check_append_only, check_openapi_parity, check_signature_call)


def run(target: str | Path) -> dict:
    root = Path(target).resolve()
    if not root.is_dir():
        raise ValueError(f"{target} is not a directory")
    results = [c(root) for c in CHECKS]
    return {"profile": "app", "target": str(root), "assessed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checks": [asdict(r) for r in results],
            "counts": {s: sum(1 for r in results if r.status == s) for s in ("pass", "fail", "warn", "skip")}}


def bundle_section(report: dict) -> dict:
    """The ``coherence`` section of an icos-apptwin/v1 bundle (id + status per check; evidence stays in the report)."""
    return {"tool": "icdev-it/app_coherence", "assessed_at": report["assessed_at"],
            "checks": [{"id": c["id"], "status": c["status"]} for c in report["checks"]]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The generic coherence checks, against an application's directory")
    ap.add_argument("--target", required=True)
    ap.add_argument("--json", action="store_true", help="JSON out (the default)")
    ap.add_argument("--bundle-section", action="store_true", help="print the bundle's coherence section only")
    ap.add_argument("--gate", action="store_true", help="exit 1 if any check fails")
    a = ap.parse_args(argv)
    try:
        rep = run(a.target)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    print(json.dumps(bundle_section(rep) if a.bundle_section else rep, indent=2))
    return 1 if a.gate and rep["counts"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
