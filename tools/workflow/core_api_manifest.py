# CUI // SP-CTI
"""Generate / verify the committed public-API manifest for the shared core (xcore-api-01).

WHY THIS EXISTS, AND WHY IT IS A COMMITTED SNAPSHOT RATHER THAN A COMPARISON.
``icdev-core`` is a SEPARATE repository. A check that compared this parent's
``icdev/core/`` against the published package would need that package checked
out, and the ICDEV runner does not check it out -- so every run would SKIP, the
finding list would stay empty, and the gate would report clean however far the
two drifted. That is not hypothetical: ``check_vendor_parity`` (cxo-doc-03) was
built exactly that way, could never block, and ``args/vendor_api_manifest.json``
(ctx-enf-01) exists because of it. This is the same remedy for the same
topology, and it reuses that remedy's ``_public_api`` rather than deriving a
second opinion about what "the public API" is.

TWO INDEPENDENT QUESTIONS, AND THEY FAIL FOR DIFFERENT REASONS.

  1. Is the manifest STALE? Changing a core module's public surface without
     regenerating fails on a runner with no external checkout, which makes
     re-publishing the package a deliberate step instead of something you
     remember. That is what this module answers.

  2. Does this parent CALL a symbol the pinned core does not export? That is
     ``coherence_checker.check_core_api``, which reads the manifest this module
     writes. Kept apart because the repairs differ: (1) is regenerate-and-
     republish, (2) is stop calling it or ship it in the package.

THE MANIFEST IS NOT A DIRECTORY LISTING. ``args/core_api.yaml`` declares which
modules the distribution ships, and a module found in the installed package that
the declaration does not name FAILS rather than being published silently.

RESOLVED THROUGH ``importlib``, NOT FROM A SOURCE TREE (xcore-cut-02). This
parent no longer ships ``icdev/core/`` -- ``icdev.core.*`` comes from the
``icdev-core`` distribution -- so the manifest is generated from what
``import icdev.core`` actually resolves. That is the stronger claim and the one
that matters: a wrong answer here is an ImportError at the start of the
dashboard, the genesis daemon, ``migrate.py`` and ``kanban/cli.py``.

    python tools/workflow/core_api_manifest.py              # verify, exit 1 on drift
    python tools/workflow/core_api_manifest.py --write      # regenerate
    python tools/workflow/core_api_manifest.py --json
    python tools/workflow/core_api_manifest.py --verify-upstream   # network; compares
                                                                   # against the published repo
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

# sys.path BOOTSTRAP -- resolves the IMPORT root, identical before and after a
# package move, and so deliberately not a self-root site (xit-decl-03).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from tools.workflow.coherence_checker import PROJECT_ROOT, _public_api  # noqa: E402

CORE_API_DECLARATION = "args/core_api.yaml"
CORE_API_MANIFEST = "args/core_api_manifest.json"


class CoreApiError(RuntimeError):
    """The declaration or the tree cannot support a manifest."""


def declaration_path() -> Path:
    return PROJECT_ROOT / CORE_API_DECLARATION


def manifest_path() -> Path:
    return PROJECT_ROOT / CORE_API_MANIFEST


def load_declaration() -> dict:
    """Read args/core_api.yaml. Raises rather than defaulting.

    A missing or malformed declaration must never degrade to "no exports", which
    would render every core import undeclared and fail the parent gate on a
    condition the committer did not cause.
    """
    path = declaration_path()
    if not path.exists():
        raise CoreApiError(f"{CORE_API_DECLARATION} is absent")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not data.get("exports"):
        raise CoreApiError(f"{CORE_API_DECLARATION} declares no exports")
    pinned = str(data.get("pinned_version") or "").strip()
    if not pinned:
        raise CoreApiError(f"{CORE_API_DECLARATION} declares no pinned_version")
    resolution = str(data.get("resolution") or "installed").strip()
    if resolution != "installed":
        raise CoreApiError(
            f"{CORE_API_DECLARATION} declares resolution={resolution!r}; this parent no "
            "longer ships a core source tree, so 'installed' is the only supported value"
        )
    if pinned in {"main", "master", "HEAD"}:
        # The card's last line: no floating `main` dependency anywhere. A branch
        # pin makes the manifest a description of whatever was installed.
        raise CoreApiError(
            f"{CORE_API_DECLARATION} pins the core to '{pinned}' -- a branch, not a version"
        )
    return data


def module_source(module: str) -> pathlib.Path:
    """Where the INSTALLED distribution provides *module*.

    ``find_spec`` rather than ``import_module``: locating a module must not execute it. The
    core is import-safe today, but a manifest generator that runs the code it is describing
    would make "the surface changed" and "the surface raises on import" the same failure.
    """
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError) as exc:
        raise CoreApiError(f"{module} could not be located ({exc}) -- is icdev-core installed?")
    if spec is None or not spec.origin or spec.origin == "namespace":
        raise CoreApiError(
            f"{module} could not be located -- is icdev-core installed? "
            "(`pip install -r requirements.txt`)"
        )
    return pathlib.Path(spec.origin)


def package_root(package: str = "icdev.core") -> pathlib.Path:
    """The installed package's directory, for data files and undeclared-module scanning."""
    return module_source(package).parent


def _text_hash(path: pathlib.Path) -> str:
    """Content hash of a committed TEXT file, independent of line endings.

    NOT ``sha256(read_bytes())``. The same `schema/tables.yaml` checks out CRLF on Windows
    under ``core.autocrlf`` and LF on a Linux runner, so a byte hash makes the committed
    manifest platform-dependent: it was generated on Windows and every Linux CI job reported
    ``schema/tables.yaml: content changed`` -- eight test failures whose real cause was the
    hash, not the file. ``_public_api`` already avoids exactly this for source (it compares an
    AST, and its docstring calls a byte comparison "pure noise"); a data file needs the same
    care, taken one layer lower.

    A trailing-newline difference is likewise not a content change. Real edits still move it.
    """
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.rstrip("\n").encode("utf-8")).hexdigest()[:16]


def _hash(parts: List[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _module_all(source: str) -> Optional[List[str]]:
    """The module's own ``__all__``, or None when it declares none.

    None and [] are different answers and are kept apart: a module that declares
    nothing has not declared an empty surface.
    """
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return None
                if isinstance(value, (list, tuple)):
                    return sorted(str(v) for v in value)
    return None


def _module_constants(source: str) -> List[str]:
    """Public module-level names bound by assignment.

    ``_public_api`` reports functions and classes only, which is right for what
    it was built for -- a vendored copy's CALLABLE surface. It is not the whole
    importable surface: ``from icdev.core.domain import BUILTIN_DEFAULT`` binds a
    name that no function or class declares, and a gate matching against
    callables alone would refuse that import as a symbol the core does not
    export. Seven such names exist across the exported modules today.

    Computed HERE rather than by widening ``_public_api``, which is shared with
    ``check_vendor_parity`` -- widening it would silently change what every
    vendored-copy comparison considers drift.
    """
    names: List[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            names += [
                t.id for t in node.targets
                if isinstance(t, ast.Name) and not t.id.startswith("_")
            ]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                names.append(target.id)
    return sorted(set(names))


def collect_surface(declaration: Optional[dict] = None) -> dict:
    """The current public surface of every DECLARED export, from this tree."""
    decl = declaration or load_declaration()
    root = package_root()
    modules: Dict[str, dict] = {}
    for module in sorted(decl["exports"]):
        path = module_source(module)
        # Recorded relative to the installed package so the manifest does not carry an
        # absolute site-packages path, which differs on every machine and would make the
        # committed file churn on every regeneration.
        try:
            rel = path.relative_to(root.parent).as_posix()
        except ValueError:
            rel = path.name
        source = path.read_text(encoding="utf-8")
        symbols = sorted(_public_api(source))
        constants = _module_constants(source)
        modules[module] = {
            "path": rel,
            "symbols": symbols,
            "constants": constants,
            "dunder_all": _module_all(source),
            # The hash covers BOTH -- a removed constant is as breaking to an
            # importer as a removed function.
            "signature_hash": _hash(symbols + [f"={c}" for c in constants]),
        }

    data_files: Dict[str, dict] = {}
    for rel in sorted(decl.get("data_files") or []):
        # Relative to the icdev.core package, NOT to this repo. The parent's own
        # icdev/core/schema/tables.yaml is a different file with a different lifecycle
        # (written by schema_ownership.py --regenerate, read by sensitivity from the repo
        # root); this manifest describes only what the distribution ships.
        path = root / rel
        if not path.exists():
            raise CoreApiError(
                f"declared data_file {rel} is not present in the installed icdev.core package"
            )
        data_files[rel] = {"content_hash": _text_hash(path)}

    return {
        "package": decl.get("package", "icdev-core"),
        "repo": decl.get("repo"),
        "pinned_version": decl["pinned_version"],
        "resolution": "installed",
        "modules": modules,
        "data_files": data_files,
        "parent_local": sorted((decl.get("parent_local") or {}).keys()),
        "surface_hash": _hash(
            [f"{m}:{d['signature_hash']}" for m, d in modules.items()]
            + [f"{p}:{d['content_hash']}" for p, d in data_files.items()]
        ),
    }


def render_manifest(declaration: Optional[dict] = None) -> str:
    """The manifest exactly as it is committed (LF, trailing newline)."""
    return json.dumps(collect_surface(declaration), indent=2, sort_keys=True) + "\n"


def load_manifest() -> Optional[dict]:
    path = manifest_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def manifest_drift() -> List[str]:
    """Human-readable differences between the committed manifest and this tree."""
    committed = load_manifest()
    if committed is None:
        return [f"{CORE_API_MANIFEST} is absent or unparseable"]
    current = collect_surface()

    findings: List[str] = []
    if committed.get("pinned_version") != current["pinned_version"]:
        findings.append(
            f"pinned_version: manifest {committed.get('pinned_version')!r} "
            f"vs declaration {current['pinned_version']!r}"
        )

    old_mods, new_mods = committed.get("modules", {}), current["modules"]
    for module in sorted(set(old_mods) | set(new_mods)):
        if module not in new_mods:
            findings.append(f"{module}: in the manifest, no longer a declared export")
            continue
        if module not in old_mods:
            findings.append(f"{module}: newly declared, absent from the manifest")
            continue
        was = set(old_mods[module].get("symbols", []))
        now = set(new_mods[module]["symbols"])
        for gone in sorted(was - now):
            findings.append(f"{module}: REMOVED {gone}")
        for added in sorted(now - was):
            findings.append(f"{module}: added {added}")
        was_const = set(old_mods[module].get("constants", []))
        now_const = set(new_mods[module]["constants"])
        for gone in sorted(was_const - now_const):
            findings.append(f"{module}: REMOVED constant {gone}")
        for added in sorted(now_const - was_const):
            findings.append(f"{module}: added constant {added}")

    old_data, new_data = committed.get("data_files", {}), current["data_files"]
    for rel in sorted(set(old_data) | set(new_data)):
        if rel not in new_data:
            findings.append(f"{rel}: in the manifest, no longer a declared data_file")
        elif rel not in old_data:
            findings.append(f"{rel}: newly declared data_file, absent from the manifest")
        elif old_data[rel].get("content_hash") != new_data[rel]["content_hash"]:
            findings.append(f"{rel}: content changed")
    return findings


def undeclared_core_modules() -> List[str]:
    """Modules the INSTALLED package provides that the declaration names nowhere.

    The other direction of the declaration check. Without it a module added to the
    distribution is simply never described here, and nothing says so.
    """
    decl = load_declaration()
    declared = set(decl["exports"]) | set((decl.get("parent_local") or {}).keys())
    base = package_root()
    found: List[str] = []
    for path in sorted(base.rglob("*.py")):
        tail = path.relative_to(base).as_posix()[: -len(".py")]
        module = "icdev.core" if tail == "__init__" else f"icdev.core.{tail.replace('/', '.')}"
        if module not in declared:
            found.append(module)
    return found


def write_manifest() -> bool:
    """Regenerate. Returns True when the file changed on disk."""
    path = manifest_path()
    rendered = render_manifest()
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" -- committed file, LF repo. Path.write_text emits CRLF on
    # Windows otherwise and every regeneration becomes a whole-file diff.
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def verify() -> dict:
    try:
        drift = manifest_drift()
        undeclared = undeclared_core_modules()
        error = None
    except CoreApiError as exc:
        drift, undeclared, error = [], [], str(exc)
    return {
        "declaration": CORE_API_DECLARATION,
        "manifest": CORE_API_MANIFEST,
        "manifest_present": manifest_path().exists(),
        "drift": drift,
        "undeclared_core_modules": undeclared,
        "error": error,
        "in_sync": error is None and not drift and not undeclared,
    }


def _fetch_upstream(repo: str, rel: str, ref: str) -> Optional[str]:
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/contents/{rel}?ref={ref}", "--jq", ".content"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return base64.b64decode(proc.stdout.strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def verify_upstream(ref: Optional[str] = None) -> dict:
    """Compare the committed manifest against the PUBLISHED package. Network.

    Deliberately NOT part of ``verify()`` and never wired into a gate: it needs
    the forge, and a check that goes red because a network call failed is one
    people learn to bypass. Run it when publishing.
    """
    decl = load_declaration()
    repo = decl.get("repo")
    if not repo:
        return {"state": "unmeasurable", "reason": "no repo declared", "findings": []}
    ref = ref or f"v{decl['pinned_version']}"

    probe = _fetch_upstream(repo, "icdev/core/__init__.py", ref)
    if probe is None:
        # An unreleased pin is a real state and is REPORTED as one, never
        # silently retried as `main` and reported as agreement.
        if ref not in {"main", "master"} and _fetch_upstream(
            repo, "icdev/core/__init__.py", "main"
        ) is not None:
            return {
                "state": "unreleased",
                "ref": ref,
                "repo": repo,
                "reason": f"tag {ref} does not exist in {repo}; the pin names no published release",
                "findings": [],
            }
        return {
            "state": "unmeasurable",
            "ref": ref,
            "repo": repo,
            "reason": f"{repo} unreachable",
            "findings": [],
        }

    findings: List[str] = []
    for module, entry in sorted(collect_surface(decl)["modules"].items()):
        remote = _fetch_upstream(repo, entry["path"], ref)
        if remote is None:
            findings.append(f"{module}: absent from {repo}@{ref}")
            continue
        remote_symbols = sorted(_public_api(remote))
        remote_constants = _module_constants(remote)
        remote_hash = _hash(remote_symbols + [f"={c}" for c in remote_constants])
        if remote_hash != entry["signature_hash"]:
            for gone in sorted(set(entry["symbols"]) - set(remote_symbols)):
                findings.append(f"{module}: local has {gone}, {ref} does not")
            for extra in sorted(set(remote_symbols) - set(entry["symbols"])):
                findings.append(f"{module}: {ref} has {extra}, local does not")
            for gone in sorted(set(entry.get("constants", [])) - set(remote_constants)):
                findings.append(f"{module}: local has constant {gone}, {ref} does not")
            for extra in sorted(set(remote_constants) - set(entry.get("constants", []))):
                findings.append(f"{module}: {ref} has constant {extra}, local does not")
    return {
        "state": "agrees" if not findings else "disagrees",
        "ref": ref,
        "repo": repo,
        "findings": findings,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate from the current tree")
    parser.add_argument("--check", action="store_true", help="verify only (the default)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--verify-upstream",
        action="store_true",
        help="compare against the published package (network; never gated)",
    )
    parser.add_argument("--ref", help="git ref to compare upstream against (default: v<pinned>)")
    args = parser.parse_args(argv)

    if args.verify_upstream:
        result = verify_upstream(args.ref)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            reason = f" ({result['reason']})" if result.get("reason") else ""
            print(f"upstream: {result['state']}{reason}")
            for line in result.get("findings", []):
                print(f"  {line}")
        # Never a gate: an unreachable forge is not a finding about the code.
        return 0

    if args.write:
        try:
            changed = write_manifest()
        except CoreApiError as exc:
            print(f"ERROR: {exc}")
            return 2
        result = verify()
        result["written"] = True
        result["changed"] = changed
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{CORE_API_MANIFEST} {'updated' if changed else 'already up to date'}")
            for line in result["undeclared_core_modules"]:
                print(
                    f"UNDECLARED: {line} is under source_root but in neither "
                    "exports nor parent_local"
                )
        return 0 if not result["undeclared_core_modules"] else 1

    result = verify()
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["error"]:
        print(f"ERROR: {result['error']}")
    elif result["in_sync"]:
        manifest = load_manifest() or {}
        print(
            f"{CORE_API_MANIFEST} matches {len(manifest.get('modules', {}))} declared "
            f"core module(s) at {manifest.get('pinned_version')}"
        )
    else:
        for line in result["drift"]:
            print(f"DRIFT: {line}")
        for line in result["undeclared_core_modules"]:
            print(
                f"UNDECLARED: {line} is under source_root but in neither "
                "exports nor parent_local"
            )
        print(
            "Regenerate with `python tools/workflow/core_api_manifest.py --write`, "
            "then publish the package and bump pinned_version."
        )
    # exit 2 = could not be produced, which is never the same as clean.
    return 2 if result["error"] else (0 if result["in_sync"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
