#!/usr/bin/env python3
# CUI // SP-CTI
# CANONICAL: the resolved-dependency-set backend for tools/compliance/sbom_generator.py.
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Resolved (transitive) dependency sets — the SBOM 2026 **Coverage** element.

The 2026 Minimum Elements replaced *Depth* with **Coverage**: an SBOM must list
"all components that make up the target software, **including transitive
dependencies**", with **no minimum depth**. ICDEV historically parsed *declared*
dependency manifests, which yields direct dependencies only for every ecosystem
except npm.

This module moves each ecosystem to its **resolved** set, and — where resolution
is genuinely impossible offline — degrades to the declared set while saying so
explicitly. That honesty is the whole point of the element: a recipient must be
able to conclude that a vulnerability does not affect them *from a component's
absence*, and a silently-partial tree destroys that inference.

Design constraints (see docs/compliance/sbom-2026-minimum-elements-gap-analysis.md
§1.3, §3.3):

* **Offline-first.** Lockfiles are parsed with pure Python — ``json``,
  ``tomllib``, ``yaml.safe_load``, ``importlib.metadata`` and ``re``. Nothing
  shells out to a package manager, so the resolver behaves identically in an
  air-gapped enclave and on a developer laptop.
* **Never overstate.** Every ecosystem result carries ``complete`` (did we get
  the transitive set?) and, when False, a ``reason`` naming exactly what was
  missing. ``resolve_project()`` aggregates those into a single coverage
  statement that the SBOM carries verbatim.
* **Instances, not names.** Where multiple instances of a component differ in
  metadata — the npm nested ``node_modules`` case above all — each instance gets
  its own ``key`` and its own edge list, so they are listed separately rather
  than collapsed.

Resolution sources, in precedence order per ecosystem:

===========  ============================================================
python       uv.lock -> poetry.lock -> pdm.lock -> Pipfile.lock ->
             installed environment (``importlib.metadata`` over a venv's
             ``site-packages``) -> declared requirements.txt/pyproject.toml
npm          package-lock.json -> yarn.lock (v1 text or Berry YAML) ->
             declared package.json
golang       go.mod with ``go >= 1.17`` (pruned graph lists all indirect
             modules) -> go.sum -> declared go.mod
cargo        Cargo.lock -> declared Cargo.toml
maven        ``mvn dependency:list`` output file -> declared pom.xml
gradle       gradle.lockfile / gradle/dependency-locks/*.lockfile ->
             declared build.gradle
nuget        obj/project.assets.json -> packages.lock.json -> declared
             .csproj / packages.config
===========  ============================================================

CLI::

    python tools/compliance/dependency_resolver.py --project-dir . --json
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    try:
        import tomli as _toml
    except ModuleNotFoundError:
        _toml = None

try:
    import yaml as _yaml
except ModuleNotFoundError:  # pragma: no cover - pyyaml is a declared dependency
    _yaml = None

# --- vocabulary -------------------------------------------------------------

#: This component came from a resolved (transitive-complete) source.
RESOLUTION_RESOLVED = "resolved"
#: This component came from a declared manifest; its own dependencies are unknown.
RESOLUTION_DECLARED = "declared"

#: Every detected ecosystem yielded its full transitive set.
COVERAGE_COMPLETE = "complete"
#: At least one detected ecosystem could only be read at declared depth.
COVERAGE_INCOMPLETE = "incomplete"
#: No dependency manifest was found at all — nothing to be complete *about*.
COVERAGE_UNKNOWN = "unknown"

ECOSYSTEMS = ("python", "npm", "golang", "cargo", "maven", "gradle", "nuget")

_NO_TOML = "no TOML parser is available (Python < 3.11 without `tomli` installed)"
_NO_YAML = "no YAML parser is available (PyYAML is not installed)"

#: The sentence appended whenever coverage is incomplete. The 2026 standard's
#: stated purpose for Coverage is that absence is evidence; when the tree is
#: partial, absence is *not* evidence, and the SBOM has to say so.
_ABSENCE_DISCLAIMER = (
    "A component's absence from this SBOM therefore does NOT establish that the "
    "target software is unaffected by a vulnerability in that component."
)


# --- helpers ----------------------------------------------------------------


def _read_text(path):
    """Read a text file, returning None rather than raising on any I/O error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _read_json(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _read_toml(path):
    """Parse a TOML file. Returns None if unreadable *or* if no parser exists."""
    if _toml is None:
        return None
    try:
        with open(path, "rb") as handle:
            return _toml.load(handle)
    except Exception:
        return None


def _component(
    ecosystem,
    name,
    version,
    purl,
    key,
    group="",
    scope="required",
    source="",
    dependencies=None,
    resolution=RESOLUTION_RESOLVED,
    direct=True,
    ctype="library",
):
    """Build one component instance.

    ``key`` is the *instance* identity, not the package identity: two npm
    instances of the same name and version at different ``node_modules`` paths
    are two keys, so they survive deduplication and can each carry their own
    dependency relationship.
    """
    return {
        "type": ctype,
        "name": name,
        "version": version,
        "purl": purl,
        "group": group,
        "scope": scope,
        "source": str(source),
        "ecosystem": ecosystem,
        "key": key,
        "dependencies": list(dependencies or []),
        "resolution": resolution,
        "direct": bool(direct),
    }


def _result(ecosystem, method, complete, components, reason="", source=""):
    return {
        "ecosystem": ecosystem,
        "method": method,
        "complete": bool(complete),
        "components": components,
        "component_count": len(components),
        "reason": reason,
        "source": str(source),
    }


def _pypi_purl(name, version):
    purl = f"pkg:pypi/{name}"
    if version:
        purl += f"@{version}"
    return purl


def _npm_purl(name, version):
    purl_name = name.replace("/", "%2F") if "/" in name else name
    purl = f"pkg:npm/{purl_name}"
    if version:
        purl += f"@{version}"
    return purl


def _normalize_pypi_name(name):
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _requirement_name(requirement):
    """Extract the distribution name from a PEP 508 requirement string."""
    match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement or "")
    return _normalize_pypi_name(match.group(1)) if match else ""


# --- python -----------------------------------------------------------------


def _python_lock_packages(data):
    """Normalize the ``[[package]]`` array shared by uv.lock, poetry.lock, pdm.lock."""
    packages = data.get("package") if isinstance(data, dict) else None
    return packages if isinstance(packages, list) else []


def _python_lock_edges(entry):
    """Pull dependency names out of a lock entry, across the three lock dialects.

    poetry writes ``[package.dependencies]`` (a name -> constraint table), uv
    writes ``[[package.dependencies]]`` (a list of ``{name = ...}`` tables), and
    pdm writes a list of PEP 508 strings.
    """
    raw = entry.get("dependencies")
    names = []
    if isinstance(raw, dict):
        names = list(raw.keys())
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                names.append(item.get("name", ""))
            elif isinstance(item, str):
                names.append(_requirement_name(item))
    return [_normalize_pypi_name(n) for n in names if n]


def _resolve_python_lock(path, ecosystem_method):
    data = _read_toml(path)
    if data is None:
        return None
    entries = _python_lock_packages(data)
    if not entries:
        return None

    components = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = _normalize_pypi_name(entry.get("name", ""))
        if not name:
            continue
        version = str(entry.get("version", "") or "")
        components.append(
            _component(
                "python",
                name,
                version,
                _pypi_purl(name, version),
                key=f"python|{name}@{version}",
                source=path,
                dependencies=[f"python|{d}" for d in _python_lock_edges(entry)],
            )
        )
    return _result("python", ecosystem_method, True, components, source=path)


def _resolve_pipfile_lock(path):
    data = _read_json(path)
    if not isinstance(data, dict):
        return None

    components = []
    for section, scope in (("default", "required"), ("develop", "optional")):
        for raw_name, info in (data.get(section) or {}).items():
            if not isinstance(info, dict):
                continue
            name = _normalize_pypi_name(raw_name)
            version = str(info.get("version", "") or "").lstrip("=")
            components.append(
                _component(
                    "python",
                    name,
                    version,
                    _pypi_purl(name, version),
                    key=f"python|{name}@{version}",
                    scope=scope,
                    source=path,
                )
            )
    if not components:
        return None
    # Pipfile.lock pins every transitive package but records no edges between
    # them, so the component set is complete while the graph is not.
    return _result(
        "python",
        "Pipfile.lock",
        True,
        components,
        reason="Pipfile.lock pins the full transitive set but records no inter-package edges.",
        source=path,
    )


def _site_packages_dirs(project_dir, python_env=None):
    """Locate ``site-packages`` directories to read installed metadata from."""
    candidates = []
    if python_env:
        env = Path(python_env)
        if env.name == "site-packages":
            candidates.append(env)
        else:
            candidates.extend(sorted(env.glob("Lib/site-packages")))
            candidates.extend(sorted(env.glob("lib/*/site-packages")))
    else:
        for venv in (".venv", "venv", "env"):
            root = project_dir / venv
            if not root.is_dir():
                continue
            candidates.extend(sorted(root.glob("Lib/site-packages")))
            candidates.extend(sorted(root.glob("lib/*/site-packages")))
    return [c for c in candidates if c.is_dir()]


def _resolve_python_environment(project_dir, python_env=None):
    """Read the *installed* distributions of a target environment.

    This is ``importlib.metadata`` pointed at a specific ``site-packages`` rather
    than at the running interpreter: ``PathDistribution`` parses each
    ``*.dist-info/METADATA`` file as text. Nothing is imported and no code from
    the target environment executes.
    """
    from importlib.metadata import PathDistribution

    dirs = _site_packages_dirs(project_dir, python_env)
    if not dirs:
        return None

    components = []
    seen = set()
    for site_packages in dirs:
        infos = sorted(site_packages.glob("*.dist-info")) + sorted(site_packages.glob("*.egg-info"))
        for info in infos:
            try:
                dist = PathDistribution(info)
                raw_name = dist.metadata["Name"]
                if not raw_name:
                    continue
                name = _normalize_pypi_name(raw_name)
                version = str(dist.version or "")
                edges = sorted({_requirement_name(r) for r in (dist.requires or [])} - {""})
            except Exception:
                continue

            key = f"python|{name}@{version}"
            if key in seen:
                continue
            seen.add(key)
            components.append(
                _component(
                    "python",
                    name,
                    version,
                    _pypi_purl(name, version),
                    key=key,
                    source=info,
                    dependencies=[f"python|{e}" for e in edges],
                )
            )

    if not components:
        return None
    return _result(
        "python",
        "importlib.metadata (installed environment)",
        True,
        components,
        source=dirs[0],
    )


def _resolve_python(project_dir, python_env=None):
    for filename, method in (
        ("uv.lock", "uv.lock"),
        ("poetry.lock", "poetry.lock"),
        ("pdm.lock", "pdm.lock"),
    ):
        path = project_dir / filename
        if not path.exists():
            continue
        resolved = _resolve_python_lock(path, method)
        if resolved:
            return resolved
        return _result(
            "python",
            filename,
            False,
            [],
            reason=f"{filename} is present but could not be parsed — {_NO_TOML}."
            if _toml is None
            else f"{filename} is present but could not be parsed.",
            source=path,
        )

    pipfile_lock = project_dir / "Pipfile.lock"
    if pipfile_lock.exists():
        resolved = _resolve_pipfile_lock(pipfile_lock)
        if resolved:
            return resolved

    env = _resolve_python_environment(project_dir, python_env)
    if env:
        return env

    declared_files = [f for f in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile") if (project_dir / f).exists()]
    if not declared_files:
        return None
    return _result(
        "python",
        f"declared ({', '.join(declared_files)})",
        False,
        [],
        reason=(
            "no Python lockfile (uv.lock, poetry.lock, pdm.lock, Pipfile.lock) and no "
            "installed environment were found, so only directly declared requirements "
            "could be read. Transitive dependencies are absent."
        ),
        source=project_dir / declared_files[0],
    )


# --- npm --------------------------------------------------------------------


def _npm_name_from_path(pkg_path):
    marker = "node_modules/"
    index = pkg_path.rfind(marker)
    return pkg_path[index + len(marker):] if index >= 0 else pkg_path


def _npm_prefix_chain(pkg_path):
    """npm resolution order: nearest ``node_modules`` first, then walk up to root."""
    chain = [pkg_path]
    current = pkg_path
    while True:
        index = current.rfind("/node_modules/")
        if index == -1:
            break
        current = current[:index]
        chain.append(current)
    if chain[-1] != "":
        chain.append("")
    return chain


def _npm_resolve_edge(packages, from_path, dep_name):
    for prefix in _npm_prefix_chain(from_path):
        candidate = f"{prefix}/node_modules/{dep_name}" if prefix else f"node_modules/{dep_name}"
        if candidate in packages:
            return candidate
    return None


def _resolve_package_lock(path):
    data = _read_json(path)
    if not isinstance(data, dict):
        return None

    packages = data.get("packages")
    if isinstance(packages, dict) and packages:
        return _resolve_package_lock_v2(path, packages)

    dependencies = data.get("dependencies")
    if isinstance(dependencies, dict) and dependencies:
        return _resolve_package_lock_v1(path, dependencies)
    return None


def _resolve_package_lock_v2(path, packages):
    """lockfileVersion 2/3 — a flat ``packages`` map keyed by install path.

    Every ``node_modules/**`` key is a real installed instance, including the
    nested duplicates npm creates when two dependents need incompatible ranges.
    Those nested entries are exactly the "multiple instances differing in
    metadata" case the standard requires be listed separately.
    """
    components = []
    for pkg_path, info in packages.items():
        if not pkg_path or "node_modules/" not in pkg_path or not isinstance(info, dict):
            continue
        if info.get("link"):
            continue  # a workspace symlink, not an installed instance
        name = _npm_name_from_path(pkg_path)
        version = str(info.get("version", "") or "")

        edges = []
        for section in ("dependencies", "optionalDependencies", "peerDependencies"):
            for dep_name in (info.get(section) or {}):
                target = _npm_resolve_edge(packages, pkg_path, dep_name)
                if target:
                    edges.append(f"npm|{target}")

        group = ""
        pkg_name = name
        if name.startswith("@") and "/" in name:
            group, pkg_name = name.split("/", 1)

        components.append(
            _component(
                "npm",
                pkg_name,
                version,
                _npm_purl(name, version),
                key=f"npm|{pkg_path}",
                group=group,
                scope="optional" if info.get("dev") else "required",
                source=path,
                dependencies=sorted(set(edges)),
                direct=pkg_path.count("node_modules/") == 1,
            )
        )
    if not components:
        return None
    return _result("npm", "package-lock.json (lockfileVersion 2/3)", True, components, source=path)


def _resolve_package_lock_v1(path, dependencies):
    """lockfileVersion 1 — a recursive ``dependencies`` tree."""
    packages = {}

    def walk(tree, prefix):
        for name, info in (tree or {}).items():
            if not isinstance(info, dict):
                continue
            pkg_path = f"{prefix}/node_modules/{name}" if prefix else f"node_modules/{name}"
            packages[pkg_path] = info
            walk(info.get("dependencies"), pkg_path)

    walk(dependencies, "")
    if not packages:
        return None

    components = []
    for pkg_path, info in packages.items():
        name = _npm_name_from_path(pkg_path)
        version = str(info.get("version", "") or "")

        edges = []
        for dep_name in (info.get("requires") or {}):
            target = _npm_resolve_edge(packages, pkg_path, dep_name)
            if target:
                edges.append(f"npm|{target}")

        group = ""
        pkg_name = name
        if name.startswith("@") and "/" in name:
            group, pkg_name = name.split("/", 1)

        components.append(
            _component(
                "npm",
                pkg_name,
                version,
                _npm_purl(name, version),
                key=f"npm|{pkg_path}",
                group=group,
                scope="optional" if info.get("dev") else "required",
                source=path,
                dependencies=sorted(set(edges)),
                direct=pkg_path.count("node_modules/") == 1,
            )
        )
    return _result("npm", "package-lock.json (lockfileVersion 1)", True, components, source=path)


def _yarn_descriptor_name(descriptor):
    """Split ``lodash@^4.0.0`` / ``@babel/core@npm:^7.0.0`` into (name, range)."""
    descriptor = descriptor.strip().strip('"')
    if descriptor.startswith("@"):
        head, _, tail = descriptor[1:].partition("@")
        return "@" + head, tail
    head, _, tail = descriptor.partition("@")
    return head, tail


def _yarn_components(entries, path, method):
    """Turn parsed yarn entries into components, wiring edges via descriptors."""
    by_descriptor = {}
    for index, entry in enumerate(entries):
        for descriptor in entry["descriptors"]:
            by_descriptor[descriptor.strip().strip('"')] = index

    components = []
    for index, entry in enumerate(entries):
        name, _ = _yarn_descriptor_name(entry["descriptors"][0])
        version = entry["version"]

        edges = []
        for dep_name, dep_range in entry["deps"].items():
            target = by_descriptor.get(f"{dep_name}@{dep_range}")
            if target is None:
                target = by_descriptor.get(f"{dep_name}@npm:{dep_range}")
            if target is not None:
                edges.append(f"npm|yarn:{target}")

        group = ""
        pkg_name = name
        if name.startswith("@") and "/" in name:
            group, pkg_name = name.split("/", 1)

        components.append(
            _component(
                "npm",
                pkg_name,
                version,
                _npm_purl(name, version),
                key=f"npm|yarn:{index}",
                group=group,
                source=path,
                dependencies=sorted(set(edges)),
            )
        )
    if not components:
        return None
    return _result("npm", method, True, components, source=path)


def _parse_yarn_lock_v1(text):
    entries = []
    current = None
    in_deps = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()

        if indent == 0:
            if current:
                entries.append(current)
            current = None
            in_deps = False
            if line.endswith(":"):
                current = {
                    "descriptors": [d.strip().strip('"') for d in line[:-1].split(",")],
                    "version": "",
                    "deps": {},
                }
            continue

        if current is None:
            continue

        if indent <= 2:
            in_deps = line in ("dependencies:", "optionalDependencies:")
            version_match = re.match(r'^version\s+"?([^"\s]+)"?$', line)
            if version_match:
                current["version"] = version_match.group(1)
            continue

        if in_deps:
            dep_match = re.match(r'^"?(@?[^"\s]+?)"?\s+"?([^"]*?)"?$', line)
            if dep_match:
                current["deps"][dep_match.group(1)] = dep_match.group(2)

    if current:
        entries.append(current)
    return [e for e in entries if e["version"]]


def _parse_yarn_lock_berry(text):
    if _yaml is None:
        return []
    try:
        data = _yaml.safe_load(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    entries = []
    for key, info in data.items():
        if key == "__metadata" or not isinstance(info, dict):
            continue
        deps = info.get("dependencies")
        entries.append(
            {
                "descriptors": [d.strip().strip('"') for d in str(key).split(",")],
                "version": str(info.get("version", "") or ""),
                "deps": {str(k): str(v) for k, v in (deps or {}).items()} if isinstance(deps, dict) else {},
            }
        )
    return [e for e in entries if e["version"]]


def _resolve_yarn_lock(path):
    text = _read_text(path)
    if not text:
        return None
    if "__metadata:" in text:  # Yarn Berry (v2+) writes YAML
        entries = _parse_yarn_lock_berry(text)
        if not entries:
            return _result(
                "npm",
                "yarn.lock (Berry)",
                False,
                [],
                reason=f"yarn.lock is in Yarn Berry YAML format and {_NO_YAML}."
                if _yaml is None
                else "yarn.lock is in Yarn Berry YAML format but could not be parsed.",
                source=path,
            )
        return _yarn_components(entries, path, "yarn.lock (Berry YAML)")

    entries = _parse_yarn_lock_v1(text)
    if not entries:
        return None
    return _yarn_components(entries, path, "yarn.lock (v1)")


def _resolve_npm(project_dir):
    lock = project_dir / "package-lock.json"
    if lock.exists():
        resolved = _resolve_package_lock(lock)
        if resolved:
            return resolved

    yarn_lock = project_dir / "yarn.lock"
    if yarn_lock.exists():
        resolved = _resolve_yarn_lock(yarn_lock)
        if resolved:
            return resolved

    if not (project_dir / "package.json").exists():
        return None
    return _result(
        "npm",
        "declared (package.json)",
        False,
        [],
        reason=(
            "no npm lockfile (package-lock.json, yarn.lock) was found, so only the ranges "
            "declared in package.json could be read. Transitive dependencies are absent "
            "and the recorded versions are ranges, not resolved versions."
        ),
        source=project_dir / "package.json",
    )


# --- golang -----------------------------------------------------------------


_GO_REQUIRE_LINE = re.compile(r"^\s*(?P<module>[^\s()]+)\s+(?P<version>v\S+)\s*(?P<comment>//.*)?$")


def _parse_go_mod_requires(text):
    """Yield (module, version, indirect) for every require directive."""
    requires = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if in_block:
            if line.startswith(")"):
                in_block = False
                continue
            match = _GO_REQUIRE_LINE.match(line)
            if match:
                requires.append(
                    (match.group("module"), match.group("version"), "indirect" in (match.group("comment") or ""))
                )
            continue
        if line.startswith("require ("):
            in_block = True
            continue
        if line.startswith("require "):
            match = _GO_REQUIRE_LINE.match(line[len("require "):].strip())
            if match:
                requires.append(
                    (match.group("module"), match.group("version"), "indirect" in (match.group("comment") or ""))
                )
    return requires


def _go_directive_at_least_117(text):
    match = re.search(r"^\s*go\s+(\d+)\.(\d+)", text, re.MULTILINE)
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return (major, minor) >= (1, 17)


def _go_components(pairs, path, source_label):
    components = []
    for module, version, indirect in pairs:
        components.append(
            _component(
                "golang",
                module,
                version,
                f"pkg:golang/{module}@{version}",
                key=f"golang|{module}@{version}",
                source=path,
                direct=not indirect,
            )
        )
    return components


def _resolve_golang(project_dir):
    go_mod = project_dir / "go.mod"
    go_sum = project_dir / "go.sum"
    if not go_mod.exists() and not go_sum.exists():
        return None

    text = _read_text(go_mod) if go_mod.exists() else None
    if text and _go_directive_at_least_117(text):
        requires = _parse_go_mod_requires(text)
        if requires:
            # Since Go 1.17 the main module's go.mod records every module in the
            # pruned module graph, indirect ones explicitly — that is the resolved
            # build list, not just the direct requirements.
            return _result(
                "golang",
                "go.mod (Go >= 1.17 pruned module graph)",
                True,
                _go_components(requires, go_mod, "go.mod"),
                reason="go.mod records the module set but no inter-module edges.",
                source=go_mod,
            )

    if go_sum.exists():
        sum_text = _read_text(go_sum) or ""
        seen = {}
        for line in sum_text.splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[1].endswith("/go.mod"):
                continue
            seen.setdefault((parts[0], parts[1]), True)
        if seen:
            # go.sum is a conservative superset: it can retain modules that the
            # final build no longer selects. Over-listing is safe for Coverage;
            # under-listing is not.
            return _result(
                "golang",
                "go.sum",
                True,
                _go_components([(m, v, False) for (m, v) in seen], go_sum, "go.sum"),
                reason="go.sum may be a superset of the selected build list and records no edges.",
                source=go_sum,
            )

    return _result(
        "golang",
        "declared (go.mod)",
        False,
        [],
        reason=(
            "go.mod declares `go < 1.17` (unpruned module graph) and no go.sum was found, so "
            "only directly required modules could be read. Transitive modules are absent."
        ),
        source=go_mod if go_mod.exists() else go_sum,
    )


# --- cargo ------------------------------------------------------------------


def _resolve_cargo(project_dir):
    lock = project_dir / "Cargo.lock"
    manifest = project_dir / "Cargo.toml"
    if not lock.exists() and not manifest.exists():
        return None

    if lock.exists():
        data = _read_toml(lock)
        packages = data.get("package") if isinstance(data, dict) else None
        if isinstance(packages, list) and packages:
            components = []
            for entry in packages:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "") or "")
                if not name:
                    continue
                version = str(entry.get("version", "") or "")
                # Cargo.lock edges are "name", "name version", or
                # "name version (source)" — the leading token is the crate name.
                edges = []
                for dep in entry.get("dependencies") or []:
                    dep_name = str(dep).split(" ", 1)[0]
                    if dep_name:
                        edges.append(f"cargo|{dep_name}")
                components.append(
                    _component(
                        "cargo",
                        name,
                        version,
                        f"pkg:cargo/{name}@{version}",
                        key=f"cargo|{name}@{version}",
                        source=lock,
                        dependencies=sorted(set(edges)),
                    )
                )
            if components:
                return _result("cargo", "Cargo.lock", True, components, source=lock)

        return _result(
            "cargo",
            "Cargo.lock",
            False,
            [],
            reason=f"Cargo.lock is present but could not be parsed — {_NO_TOML}."
            if _toml is None
            else "Cargo.lock is present but could not be parsed.",
            source=lock,
        )

    return _result(
        "cargo",
        "declared (Cargo.toml)",
        False,
        [],
        reason=(
            "no Cargo.lock was found, so only the crates declared in Cargo.toml could be "
            "read. Transitive crates are absent and versions are ranges, not resolved versions."
        ),
        source=manifest,
    )


# --- maven ------------------------------------------------------------------


#: Where ``mvn dependency:list -DoutputFile=...`` output is conventionally written.
MAVEN_DEPENDENCY_LIST_PATHS = (
    "target/dependency-list.txt",
    "target/dependencies.txt",
    "dependency-list.txt",
)


def _resolve_maven(project_dir):
    pom = project_dir / "pom.xml"
    if not pom.exists():
        return None

    for relative in MAVEN_DEPENDENCY_LIST_PATHS:
        listing = project_dir / relative
        if not listing.exists():
            continue
        text = _read_text(listing) or ""
        components = []
        for raw in text.splitlines():
            line = raw.strip()
            # group:artifact:type[:classifier]:version:scope
            parts = line.split(":")
            if len(parts) < 5 or " " in parts[0]:
                continue
            group_id, artifact_id = parts[0], parts[1]
            scope = parts[-1].split(" ")[0]
            version = parts[-2]
            if not group_id or not artifact_id or not version:
                continue
            components.append(
                _component(
                    "maven",
                    artifact_id,
                    version,
                    f"pkg:maven/{group_id}/{artifact_id}@{version}",
                    key=f"maven|{group_id}:{artifact_id}@{version}",
                    group=group_id,
                    scope="optional" if scope in ("test", "provided") else "required",
                    source=listing,
                )
            )
        if components:
            return _result(
                "maven",
                f"mvn dependency:list output ({relative})",
                True,
                components,
                reason="dependency:list is a flattened set and records no inter-artifact edges.",
                source=listing,
            )

    return _result(
        "maven",
        "declared (pom.xml)",
        False,
        [],
        reason=(
            "Maven transitive resolution requires `mvn dependency:list`, which needs the "
            "Maven toolchain and a populated local repository; no resolved output was found "
            f"at any of {', '.join(MAVEN_DEPENDENCY_LIST_PATHS)}. Only the dependencies "
            "declared in pom.xml could be read, so transitive artifacts are absent."
        ),
        source=pom,
    )


# --- gradle -----------------------------------------------------------------


def _gradle_lockfiles(project_dir):
    paths = []
    root_lock = project_dir / "gradle.lockfile"
    if root_lock.exists():
        paths.append(root_lock)
    paths.extend(sorted((project_dir / "gradle" / "dependency-locks").glob("*.lockfile")))
    return paths


def _resolve_gradle(project_dir):
    build_files = [f for f in ("build.gradle", "build.gradle.kts") if (project_dir / f).exists()]
    lockfiles = _gradle_lockfiles(project_dir)
    if not build_files and not lockfiles:
        return None

    components = []
    for lockfile in lockfiles:
        for raw in (_read_text(lockfile) or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("empty="):
                continue
            coordinate = line.split("=", 1)[0]
            parts = coordinate.split(":")
            if len(parts) != 3:
                continue
            group_id, artifact_id, version = (p.strip() for p in parts)
            if not group_id or not artifact_id or not version:
                continue
            components.append(
                _component(
                    "gradle",
                    artifact_id,
                    version,
                    f"pkg:maven/{group_id}/{artifact_id}@{version}",
                    key=f"gradle|{group_id}:{artifact_id}@{version}",
                    group=group_id,
                    source=lockfile,
                )
            )
    if components:
        return _result(
            "gradle",
            "gradle.lockfile (dependency locking)",
            True,
            components,
            reason="Gradle lockfiles are flattened sets and record no inter-artifact edges.",
            source=lockfiles[0],
        )

    return _result(
        "gradle",
        f"declared ({', '.join(build_files) or 'build.gradle'})",
        False,
        [],
        reason=(
            "Gradle transitive resolution requires the `dependencies` task, which needs the "
            "Gradle toolchain and network access; no `gradle.lockfile` or "
            "`gradle/dependency-locks/*.lockfile` was found. Only the dependencies declared "
            "in the build script could be read, so transitive artifacts are absent."
        ),
        source=project_dir / (build_files[0] if build_files else "build.gradle"),
    )


# --- nuget ------------------------------------------------------------------


def _resolve_project_assets(path):
    """``obj/project.assets.json`` — NuGet's resolved restore graph, with edges."""
    data = _read_json(path)
    targets = data.get("targets") if isinstance(data, dict) else None
    if not isinstance(targets, dict):
        return None

    components = []
    seen = set()
    for framework, entries in targets.items():
        if not isinstance(entries, dict):
            continue
        for coordinate, info in entries.items():
            if not isinstance(info, dict) or info.get("type") != "package":
                continue
            name, _, version = str(coordinate).partition("/")
            if not name or not version:
                continue
            key = f"nuget|{name}@{version}"
            if key in seen:
                continue
            seen.add(key)
            edges = [f"nuget|{d}" for d in (info.get("dependencies") or {})]
            components.append(
                _component(
                    "nuget",
                    name,
                    version,
                    f"pkg:nuget/{name}@{version}",
                    key=key,
                    source=path,
                    dependencies=sorted(set(edges)),
                )
            )
    if not components:
        return None
    return _result("nuget", "obj/project.assets.json", True, components, source=path)


def _resolve_packages_lock(path):
    """``packages.lock.json`` — NuGet's repeatable-restore lockfile, with edges."""
    data = _read_json(path)
    frameworks = data.get("dependencies") if isinstance(data, dict) else None
    if not isinstance(frameworks, dict):
        return None

    components = []
    seen = set()
    for framework, entries in frameworks.items():
        if not isinstance(entries, dict):
            continue
        for name, info in entries.items():
            if not isinstance(info, dict):
                continue
            version = str(info.get("resolved", "") or "")
            if not version:
                continue
            key = f"nuget|{name}@{version}"
            if key in seen:
                continue
            seen.add(key)
            edges = [f"nuget|{d}" for d in (info.get("dependencies") or {})]
            components.append(
                _component(
                    "nuget",
                    name,
                    version,
                    f"pkg:nuget/{name}@{version}",
                    key=key,
                    source=path,
                    dependencies=sorted(set(edges)),
                    direct=str(info.get("type", "")) == "Direct",
                )
            )
    if not components:
        return None
    return _result("nuget", "packages.lock.json", True, components, source=path)


def _resolve_nuget(project_dir):
    assets = project_dir / "obj" / "project.assets.json"
    if assets.exists():
        resolved = _resolve_project_assets(assets)
        if resolved:
            return resolved

    packages_lock = project_dir / "packages.lock.json"
    if packages_lock.exists():
        resolved = _resolve_packages_lock(packages_lock)
        if resolved:
            return resolved

    declared_files = [p.name for p in sorted(project_dir.glob("*.csproj"))]
    if (project_dir / "packages.config").exists():
        declared_files.append("packages.config")
    if not declared_files:
        return None
    return _result(
        "nuget",
        f"declared ({', '.join(declared_files)})",
        False,
        [],
        reason=(
            "no NuGet restore output (obj/project.assets.json) and no packages.lock.json were "
            "found, so only the PackageReference entries declared in the project file could be "
            "read. Transitive packages are absent."
        ),
        source=project_dir / declared_files[0],
    )


# --- aggregation ------------------------------------------------------------


_RESOLVERS = {
    "python": _resolve_python,
    "npm": _resolve_npm,
    "golang": _resolve_golang,
    "cargo": _resolve_cargo,
    "maven": _resolve_maven,
    "gradle": _resolve_gradle,
    "nuget": _resolve_nuget,
}


def _declared_key(ecosystem, component, index):
    """Stable instance key for a component that came from a declared manifest."""
    return "{}|declared|{}|{}@{}|{}".format(
        ecosystem,
        component.get("group", ""),
        component.get("name", ""),
        component.get("version", ""),
        index,
    )


def _adopt_declared(ecosystem, components, source):
    """Normalize a declared-manifest parser's output into resolver component shape."""
    adopted = []
    for index, component in enumerate(components or []):
        adopted.append(
            _component(
                ecosystem,
                component.get("name", ""),
                component.get("version", ""),
                component.get("purl", ""),
                key=_declared_key(ecosystem, component, index),
                group=component.get("group", ""),
                scope=component.get("scope", "required"),
                source=component.get("source", source),
                dependencies=[],
                resolution=RESOLUTION_DECLARED,
                direct=True,
                ctype=component.get("type", "library"),
            )
        )
    return adopted


def _build_statement(status, resolved, unresolved):
    if status == COVERAGE_UNKNOWN:
        return (
            "UNKNOWN COVERAGE: no dependency manifest or lockfile was found for the target "
            "software, so no component set could be established. " + _ABSENCE_DISCLAIMER
        )

    resolved_text = (
        "; ".join(f"{r['ecosystem']} via {r['method']}" for r in resolved) if resolved else "none"
    )
    if status == COVERAGE_COMPLETE:
        return (
            "COMPLETE COVERAGE: every detected ecosystem was read from a resolved dependency "
            f"set, so all transitive components are listed with no minimum depth. Resolved: {resolved_text}."
        )

    unresolved_text = "; ".join(
        f"{u['ecosystem']} via {u['method']} — {u['reason']}" for u in unresolved
    )
    return (
        "INCOMPLETE COVERAGE: this SBOM does not list all transitive dependencies. "
        f"Resolved (transitive-complete): {resolved_text}. "
        f"Unresolved (declared/direct dependencies only): {unresolved_text} "
        + _ABSENCE_DISCLAIMER
    )


def resolve_project(project_dir, declared_parsers=None, python_env=None):
    """Resolve the dependency set of ``project_dir``, per ecosystem.

    Args:
        project_dir: Directory to inspect. ``None`` or a non-directory yields an
            ``unknown`` coverage report rather than raising.
        declared_parsers: Optional ``{ecosystem: callable(project_dir) -> [component]}``
            fallback used only for ecosystems that could not be resolved. This is
            how ``sbom_generator`` reuses its existing manifest parsers without a
            circular import.
        python_env: Optional path to a virtualenv (or a ``site-packages``
            directory) whose installed distributions are the target environment.

    Returns:
        A dict with ``components`` (every instance, resolved or declared),
        ``ecosystems`` (per-ecosystem detail) and ``coverage`` (``status``,
        ``statement``, ``resolved``, ``unresolved``).
    """
    declared_parsers = declared_parsers or {}
    path = Path(project_dir) if project_dir else None

    if path is None or not path.is_dir():
        return {
            "project_dir": str(project_dir or ""),
            "components": [],
            "component_count": 0,
            "ecosystems": [],
            "coverage": {
                "status": COVERAGE_UNKNOWN,
                "statement": _build_statement(COVERAGE_UNKNOWN, [], []),
                "resolved": [],
                "unresolved": [],
            },
        }

    ecosystems = []
    for ecosystem in ECOSYSTEMS:
        try:
            result = _RESOLVERS[ecosystem](path, python_env) if ecosystem == "python" else _RESOLVERS[ecosystem](path)
        except Exception as exc:  # a malformed lockfile must not abort the SBOM
            result = _result(
                ecosystem,
                "error",
                False,
                [],
                reason=f"resolution raised {type(exc).__name__}: {exc}",
                source=path,
            )
        if result is None:
            continue

        if not result["complete"] and not result["components"]:
            parser = declared_parsers.get(ecosystem)
            if parser:
                try:
                    result["components"] = _adopt_declared(ecosystem, parser(path), result["source"])
                except Exception as exc:
                    result["reason"] += f" (declared fallback also failed: {type(exc).__name__}: {exc})"
                result["component_count"] = len(result["components"])
        ecosystems.append(result)

    components = [c for result in ecosystems for c in result["components"]]
    resolved = [
        {"ecosystem": r["ecosystem"], "method": r["method"], "component_count": r["component_count"]}
        for r in ecosystems
        if r["complete"]
    ]
    unresolved = [
        {
            "ecosystem": r["ecosystem"],
            "method": r["method"],
            "reason": r["reason"],
            "component_count": r["component_count"],
        }
        for r in ecosystems
        if not r["complete"]
    ]

    if not ecosystems:
        status = COVERAGE_UNKNOWN
    elif unresolved:
        status = COVERAGE_INCOMPLETE
    else:
        status = COVERAGE_COMPLETE

    return {
        "project_dir": str(path),
        "components": components,
        "component_count": len(components),
        "ecosystems": ecosystems,
        "coverage": {
            "status": status,
            "statement": _build_statement(status, resolved, unresolved),
            "resolved": resolved,
            "unresolved": unresolved,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Resolve a project's transitive dependency set (SBOM 2026 Coverage element)"
    )
    parser.add_argument("--project-dir", required=True, help="Directory to resolve")
    parser.add_argument(
        "--python-env",
        default=None,
        help="Virtualenv (or site-packages) directory to read installed Python distributions from",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    # Imported lazily: sbom_generator imports this module at module level, so a
    # top-level import here would be circular. The CLI wants the same
    # declared-only fallback the generator uses, hence the round trip.
    from tools.compliance.sbom_generator import DECLARED_PARSERS

    resolution = resolve_project(
        args.project_dir, declared_parsers=DECLARED_PARSERS, python_env=args.python_env
    )

    if args.json_output:
        print(json.dumps(resolution, indent=2))
        return

    coverage = resolution["coverage"]
    print(f"Project:  {resolution['project_dir']}")
    print(f"Coverage: {coverage['status']}")
    print(f"Components: {resolution['component_count']}")
    for result in resolution["ecosystems"]:
        flag = "resolved" if result["complete"] else "DECLARED ONLY"
        print(f"  [{flag}] {result['ecosystem']}: {result['component_count']} via {result['method']}")
        if result["reason"]:
            print(f"      {result['reason']}")
    print(f"\n{coverage['statement']}")

    if coverage["status"] == COVERAGE_UNKNOWN:
        sys.exit(0)


if __name__ == "__main__":
    main()
