#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Component Producer resolution — 2026 SBOM Minimum Elements (sbx-fld-02).

The 2026 standard **replaces Supplier Name with Component Producer** because
"supplier" proved ambiguous around distributors: a registry that serves a
tarball supplies it but does not produce it. Component Producer is defined as
*the entity that creates, defines and identifies the component*, **exactly one
organization per component**, written out in full with no acronyms unless the
acronym is the official name.

ICDEV emitted no supplier or producer field at all. It does emit CycloneDX
``group``, and the temptation is to reuse it — but ``group`` is a Maven/npm
*namespace*. ``org.apache.commons`` is a coordinate, not an organization's name,
and ``@types`` is a scope owned by no one in particular. Populating the producer
from ``group`` would satisfy a schema and misstate a fact, so this module never
reads it as a candidate and :func:`_reject_namespace_echo` drops any candidate
that turns out to echo it.

RESOLUTION ORDER
================

Per ecosystem, first-party package metadata first, namespace registry second,
explicit unknown-provenance marker last:

===========  ==========================================================
Ecosystem    Evidence, in precedence order
===========  ==========================================================
python       ``*.dist-info``/``*.egg-info`` ``METADATA``: ``Author`` →
             ``Maintainer`` → the domain of ``Author-email`` /
             ``Maintainer-email`` via the registry
npm          the installed package's own ``package.json``: ``author`` →
             ``maintainers[0]`` → ``contributors[0]``, then the author
             e-mail domain via the registry
maven        the artifact's POM in the local repository:
             ``<organization><name>`` → ``<developers>`` organization →
             groupId read as reverse-DNS via the registry
gradle       identical to maven (Gradle coordinates are Maven coordinates)
golang       the module host path via the registry; a bare forge host
             (``github.com`` and friends) is explicitly **not** a producer
cargo        the crate's ``Cargo.toml`` ``[package] authors`` from a
             vendor directory or the Cargo registry source cache
nuget        the package's ``.nuspec`` ``<authors>`` from the project's
             ``packages/`` directory or the global packages folder
===========  ==========================================================

Everything above is read from disk with :mod:`json`, :mod:`tomllib` and anchored
regexes. Nothing shells out to a package manager and nothing goes to the
network, so resolution behaves identically inside an air-gapped enclave — the
same offline-first property ``dependency_resolver`` (sbx-cov-01) was built on.
The cost is stated rather than hidden: where the evidence is not on disk, the
component is marked of unknown provenance instead of being guessed at.

WHY "crates.io owner" AND "PyPI maintainer" BECOME LOCAL READS
--------------------------------------------------------------

Both of those are registry-side facts, and querying a registry is exactly what
an air-gapped build cannot do. The offline stand-in is the metadata the registry
itself displays and the package itself carries: crates.io renders the crate's
``authors``, and PyPI renders the distribution's ``Author``/``Maintainer``. Same
fact, obtained from the artifact rather than from the index.

UNKNOWN PROVENANCE
==================

The standard is explicit that silence is not acceptable: where no producer can
be identified the author *must* mark the component as being of unknown
provenance. Every component therefore leaves this module with one of two
outcomes, never with an absent field:

* a producer name plus the source it came from, or
* ``producer == "unknown"``, a machine-readable ``reason``, and the
  ``icdev:component-provenance = unknown`` marker.

The general unknown-versus-**withheld** convention is sbx-prc-01's to define.
This module only ever produces the *unknown* state — ICDEV withholds nothing
about a producer — so it will slot into that convention without re-stating it.

CYCLONEDX MAPPING
=================

CycloneDX only grew a field with the producer's exact meaning in 1.6:
``component.manufacturer``, "the organization that created the component". On
1.4 and 1.5 the nearest expressible field is ``component.supplier``, which is
the very field whose ambiguity the 2026 standard set out to remove. So the
native field is chosen by spec version, and the unambiguous statement always
travels in the ``icdev:component-producer*`` properties, which every supported
version can carry.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# `_site_packages_dirs` is the Coverage element's own answer to "which
# site-packages is the target environment" (sbx-cov-01). Re-deriving it here
# would let the producer read a different environment than the one the
# component list came from, which is the one thing that must not happen.
from tools.compliance.dependency_resolver import _site_packages_dirs

BASE_DIR = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = BASE_DIR / "args" / "sbom_producer_registry.yaml"

#: The explicit unknown-provenance marker. The standard forbids omitting the field.
UNKNOWN = "unknown"

#: Value of the provenance marker property when a producer *was* identified.
KNOWN = "known"

# --- Machine-readable unknown reasons ------------------------------------------------
REASON_NO_PROJECT_DIR = "no-project-directory-to-read-metadata-from"
REASON_UNSUPPORTED_ECOSYSTEM = "ecosystem-has-no-producer-resolver"
REASON_METADATA_NOT_FOUND = "package-metadata-not-found-on-disk"
REASON_METADATA_SILENT = "package-metadata-names-no-producer"
REASON_PLACEHOLDER = "package-metadata-producer-is-a-placeholder"
REASON_FORGE_HOST = "forge-host-is-not-a-producer"
REASON_NAMESPACE_UNREGISTERED = "namespace-maps-to-no-known-organization"
REASON_NAMESPACE_ECHO = "candidate-was-the-component-namespace"

# --- Where a producer came from ------------------------------------------------------
SOURCE_PYTHON_DIST = "python-dist-metadata"
SOURCE_NPM_PACKAGE_JSON = "npm-package-json"
SOURCE_MAVEN_POM_ORGANIZATION = "maven-pom-organization"
SOURCE_MAVEN_POM_DEVELOPER = "maven-pom-developer-organization"
SOURCE_CARGO_MANIFEST = "cargo-manifest-authors"
SOURCE_NUGET_NUSPEC = "nuget-nuspec-authors"
SOURCE_NAMESPACE_REGISTRY = "namespace-registry"
SOURCE_EMAIL_DOMAIN_REGISTRY = "author-email-domain-registry"
SOURCE_PROJECT_MANIFEST = "project-manifest"
SOURCE_OPERATOR = "operator-declared"

# --- CycloneDX property names --------------------------------------------------------
PROPERTY_PRODUCER = "icdev:component-producer"
PROPERTY_PRODUCER_SOURCE = "icdev:component-producer-source"
PROPERTY_PRODUCER_EVIDENCE = "icdev:component-producer-evidence"
PROPERTY_PRODUCER_UNKNOWN_REASON = "icdev:component-producer-unknown-reason"
PROPERTY_PROVENANCE = "icdev:component-provenance"

#: Environment override for the *target* component's producer — the organization
#: that produces the software the SBOM describes. Distinct from the SBOM Author
#: (``ICDEV_SBOM_AUTHOR``, sbx-fld-01), which is whoever generated the document.
ENV_PROJECT_PRODUCER = "ICDEV_SBOM_PRODUCER"

#: ``ecosystem`` values, and purl types, that share one resolver. Gradle
#: coordinates are Maven coordinates and carry ``pkg:maven`` purls.
_ECOSYSTEM_ALIASES = {
    "gradle": "maven",
    "pypi": "python",
    "cargo": "cargo",
    "crates": "cargo",
    "go": "golang",
}

#: purl type -> ecosystem, for components that reached us without an ``ecosystem``
#: key (a hand-built dict in a test, or a caller predating sbx-cov-01).
_PURL_TYPE_TO_ECOSYSTEM = {
    "pypi": "python",
    "npm": "npm",
    "golang": "golang",
    "cargo": "cargo",
    "maven": "maven",
    "nuget": "nuget",
}

# A public suffix on its own identifies nobody, so a registry lookup never
# descends to a single label. Two-label suffixes such as `co.uk` are not
# enumerated: the registry is keyed on full domains, so `foo.co.uk` simply
# fails to match rather than matching something wrong.
_MIN_DOMAIN_LABELS = 2

_EMAIL_IN_ANGLE_BRACKETS = re.compile(r"<[^<>]*>")
_URL_IN_PARENTHESES = re.compile(r"\((?:https?://|www\.)[^()]*\)")
_BARE_EMAIL = re.compile(r"\b[^\s<>@,;]+@[^\s<>@,;]+\.[A-Za-z]{2,}\b")
_WHITESPACE = re.compile(r"\s+")

# POM and nuspec are read with anchored regexes rather than an XML parser on
# purpose: the input is a third party's packaging metadata, and every stdlib XML
# parser is exposed to entity-expansion and external-entity attacks that a
# regex simply cannot be. `_parse_pom_xml` in sbom_generator.py made the same
# call for the same reason.
_POM_ORGANIZATION = re.compile(
    r"<organization>\s*(?:<!--.*?-->\s*)?.*?<name>\s*(.*?)\s*</name>.*?</organization>",
    re.DOTALL | re.IGNORECASE,
)
_POM_DEVELOPER_BLOCK = re.compile(r"<developer>(.*?)</developer>", re.DOTALL | re.IGNORECASE)
_POM_DEVELOPER_ORG = re.compile(r"<organization>\s*(.*?)\s*</organization>", re.DOTALL | re.IGNORECASE)
_NUSPEC_AUTHORS = re.compile(r"<authors>\s*(.*?)\s*</authors>", re.DOTALL | re.IGNORECASE)
_CARGO_AUTHORS = re.compile(r"^\s*authors\s*=\s*\[(.*?)\]", re.DOTALL | re.MULTILINE)
_CARGO_AUTHOR_ENTRY = re.compile(r"""["']([^"']+)["']""")


# =====================================================================================
# Registry
# =====================================================================================


def _empty_registry():
    return {"domains": {}, "forges": set(), "namespaces": {}, "placeholders": set()}


def load_producer_registry(path=None):
    """Load ``args/sbom_producer_registry.yaml``.

    A missing or unreadable registry degrades to an empty one: every component
    that would have matched it is then marked of unknown provenance, which is
    the standard's own answer to "no clear indication" and is never worse than
    inventing a name.
    """
    registry_path = Path(path) if path else REGISTRY_PATH
    try:
        import yaml

        with open(registry_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except Exception:
        return _empty_registry()

    if not isinstance(raw, dict):
        return _empty_registry()

    domains = {}
    for key, value in (raw.get("domains") or {}).items():
        name = str(value or "").strip()
        if name:
            domains[str(key).strip().lower()] = name

    namespaces = {}
    for key, value in (raw.get("namespaces") or {}).items():
        name = str(value or "").strip()
        if name:
            namespaces[str(key).strip().lower()] = name

    return {
        "domains": domains,
        "forges": {str(f).strip().lower() for f in (raw.get("forges") or []) if str(f).strip()},
        "namespaces": namespaces,
        "placeholders": {str(p).strip().lower() for p in (raw.get("placeholders") or []) if str(p).strip()},
    }


# =====================================================================================
# Results
# =====================================================================================


def unknown_producer(reason, detail=None):
    """Build the explicit unknown-provenance marker. Never an omitted value."""
    return {
        "producer": UNKNOWN,
        "provenance": UNKNOWN,
        "source": None,
        "evidence": str(detail) if detail else None,
        "reason": reason,
    }


def _known_producer(name, source, evidence=None):
    return {
        "producer": name,
        "provenance": KNOWN,
        "source": source,
        "evidence": str(evidence) if evidence else None,
        "reason": None,
    }


def is_unknown(result):
    """True when ``result`` marks the component as being of unknown provenance."""
    return not result or result.get("producer", UNKNOWN) == UNKNOWN


def producer_db_value(result):
    """Value for ``sbom_components.producer``. Never ``None``.

    A NULL would be indistinguishable from "this row predates the element", and
    the whole point of the 2026 wording is that an unidentified producer is a
    stated fact rather than an absence.
    """
    if is_unknown(result):
        return UNKNOWN
    return result["producer"]


# =====================================================================================
# Candidate normalization
# =====================================================================================


def normalize_producer_name(raw, placeholders=frozenset()):
    """Reduce a metadata value to a single organization name, or ``None``.

    Strips the ``Name <email> (url)`` decoration that npm and PyPI author
    strings carry, collapses whitespace, and takes the **first** entry of a
    delimited list: the standard allows exactly one organization per component,
    so a multi-author field has to be resolved deterministically rather than
    joined into something no organization is called.
    """
    if raw is None:
        return None
    text = str(raw)
    text = _EMAIL_IN_ANGLE_BRACKETS.sub(" ", text)
    text = _URL_IN_PARENTHESES.sub(" ", text)
    text = _BARE_EMAIL.sub(" ", text)

    # First entry of a delimited list. Semicolons and commas both appear;
    # `Inc.`-style names contain commas too, which is why the split is only
    # applied when the tail looks like another entity rather than a suffix.
    for entry in _split_entities(text):
        candidate = _WHITESPACE.sub(" ", entry).strip().strip("\"'").strip(" ,;-").strip()
        if not candidate:
            continue
        if candidate.lower() in placeholders:
            continue
        return candidate
    return None


#: Comma-separated tails that are part of a company's legal name rather than a
#: second author. Without this, "Acme Widgets, Inc." resolves to "Acme Widgets".
_LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "inc.",
        "llc",
        "llc.",
        "ltd",
        "ltd.",
        "limited",
        "co",
        "co.",
        "corp",
        "corp.",
        "gmbh",
        "ag",
        "sa",
        "s.a.",
        "sas",
        "sarl",
        "s.r.o.",
        "srl",
        "s.p.a.",
        "bv",
        "b.v.",
        "nv",
        "n.v.",
        "plc",
        "pty",
        "pty.",
        "ab",
        "oy",
        "aisbl",
        "asbl",
        "ev",
        "e.v.",
        "llp",
        "lp",
        "pbc",
    }
)


def _split_entities(text):
    """Split an author field into entities, keeping legal-name commas attached."""
    parts = [p for p in re.split(r"[;\n]", text) if p.strip()]
    entities = []
    for part in parts:
        pieces = part.split(",")
        current = pieces[0]
        for piece in pieces[1:]:
            if piece.strip().lower().rstrip(".") in {s.rstrip(".") for s in _LEGAL_SUFFIXES}:
                current = f"{current},{piece}"
            else:
                entities.append(current)
                current = piece
        entities.append(current)
    return entities


def _reject_namespace_echo(candidate, component):
    """Drop a candidate that is really the component's own namespace.

    "Producer is never populated from ``group``" is an acceptance criterion, and
    this is its enforcement. No resolver in this module reads ``group`` as an
    input, so reaching this is a sign that a package's metadata literally
    repeats its coordinate — ``authors = ["org.apache.commons"]`` — which
    identifies no organization and must fall through to unknown provenance.
    """
    if not candidate:
        return None
    folded = candidate.strip().lower()
    namespaces = {
        str(component.get("group", "") or "").strip().lower(),
        str(component.get("name", "") or "").strip().lower(),
    }
    namespaces.discard("")
    if folded in namespaces:
        return None
    # `@scope` and `@scope/name` echoes of an npm scope.
    group = str(component.get("group", "") or "").strip().lower()
    if group and folded in {group.lstrip("@"), f"{group}/{component.get('name', '')}".lower()}:
        return None
    return candidate


# =====================================================================================
# Registry lookups
# =====================================================================================


def _domain_lookup(domain, registry):
    """Longest-suffix match of ``domain`` against the registry's domain map."""
    if not domain:
        return None
    labels = [label for label in str(domain).strip().lower().strip(".").split(".") if label]
    for start in range(len(labels) - _MIN_DOMAIN_LABELS + 1):
        candidate = ".".join(labels[start:])
        name = registry["domains"].get(candidate)
        if name:
            return name, candidate
    return None


def _email_domain(value):
    if not value:
        return None
    match = _BARE_EMAIL.search(str(value))
    if not match:
        return None
    return match.group(0).rsplit("@", 1)[-1].strip().lower()


def _producer_from_email(value, registry, component):
    domain = _email_domain(value)
    hit = _domain_lookup(domain, registry)
    if not hit:
        return None
    name = _reject_namespace_echo(hit[0], component)
    if not name:
        return None
    return _known_producer(name, SOURCE_EMAIL_DOMAIN_REGISTRY, f"{domain} -> {hit[1]}")


# =====================================================================================
# File readers
# =====================================================================================


def _read_text(path):
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


# =====================================================================================
# The resolution context
# =====================================================================================


class ProducerContext:
    """Resolves Component Producer for every component of one project.

    Stateful on purpose. A resolved SBOM for a real npm tree is tens of
    thousands of instances over a few thousand distinct packages; re-globbing
    ``site-packages`` or re-reading the same ``package.json`` per instance would
    dominate generation time. The Python index is built once, and every lookup
    is memoized on the component's identity.
    """

    def __init__(self, project_dir=None, python_env=None, registry=None, env=None):
        self.project_dir = Path(project_dir) if project_dir else None
        self.python_env = python_env
        self.registry = registry if registry is not None else load_producer_registry()
        self.env = env if env is not None else os.environ
        self._cache = {}
        self._python_index = None

    # -- public ----------------------------------------------------------------------

    def resolve(self, component):
        """Resolve one component. Always returns a result, never ``None``."""
        key = (
            _ecosystem_of(component),
            str(component.get("group", "") or ""),
            str(component.get("name", "") or ""),
            str(component.get("version", "") or ""),
            str(component.get("key", "") or ""),
        )
        if key not in self._cache:
            self._cache[key] = self._resolve_uncached(component)
        return self._cache[key]

    # -- dispatch --------------------------------------------------------------------

    def _resolve_uncached(self, component):
        ecosystem = _ecosystem_of(component)
        resolver = {
            "python": self._resolve_python,
            "npm": self._resolve_npm,
            "maven": self._resolve_maven,
            "golang": self._resolve_golang,
            "cargo": self._resolve_cargo,
            "nuget": self._resolve_nuget,
        }.get(ecosystem)

        if resolver is None:
            return unknown_producer(REASON_UNSUPPORTED_ECOSYSTEM, ecosystem or component.get("purl"))

        try:
            result = resolver(component)
        except Exception as exc:
            # A malformed third-party manifest must not abort the SBOM; it makes
            # the component's provenance unknown, which is a true statement.
            return unknown_producer(REASON_METADATA_NOT_FOUND, f"{type(exc).__name__}: {exc}")

        if result is None or is_unknown(result):
            return result or unknown_producer(REASON_METADATA_NOT_FOUND, component.get("purl"))

        checked = _reject_namespace_echo(result["producer"], component)
        if not checked:
            return unknown_producer(REASON_NAMESPACE_ECHO, result["producer"])
        return result

    # -- python ----------------------------------------------------------------------

    def _build_python_index(self):
        """Map normalized distribution name -> its installed metadata.

        Reads ``*.dist-info/METADATA`` as text through ``PathDistribution``.
        Nothing from the target environment is imported or executed.
        """
        index = {}
        if not self.project_dir and not self.python_env:
            return index
        try:
            from importlib.metadata import PathDistribution
        except Exception:
            return index

        for site_packages in _site_packages_dirs(self.project_dir, self.python_env):
            infos = sorted(site_packages.glob("*.dist-info")) + sorted(site_packages.glob("*.egg-info"))
            for info in infos:
                try:
                    dist = PathDistribution(info)
                    raw_name = dist.metadata["Name"]
                except Exception:
                    continue
                if not raw_name:
                    continue
                index.setdefault(_normalize_pypi_name(raw_name), (dist, info))
        return index

    def _resolve_python(self, component):
        if self._python_index is None:
            self._python_index = self._build_python_index()
        if not self._python_index:
            return unknown_producer(
                REASON_NO_PROJECT_DIR if not self.project_dir else REASON_METADATA_NOT_FOUND,
                "no installed Python distribution metadata was found",
            )

        entry = self._python_index.get(_normalize_pypi_name(component.get("name", "")))
        if entry is None:
            return unknown_producer(REASON_METADATA_NOT_FOUND, component.get("name"))

        dist, info = entry
        placeholders = self.registry["placeholders"]
        for field in ("Author", "Maintainer"):
            candidate = normalize_producer_name(_metadata_get(dist, field), placeholders)
            if candidate:
                return _known_producer(candidate, SOURCE_PYTHON_DIST, info)

        for field in ("Author-email", "Maintainer-email"):
            raw = _metadata_get(dist, field)
            # `Author-email: Firstname Lastname <a@b.c>` is the modern spelling;
            # the display name in front of the address is the producer's own.
            candidate = normalize_producer_name(raw, placeholders)
            if candidate and "@" not in candidate:
                return _known_producer(candidate, SOURCE_PYTHON_DIST, info)
            from_email = _producer_from_email(raw, self.registry, component)
            if from_email:
                return from_email

        if _metadata_get(dist, "Author") or _metadata_get(dist, "Maintainer"):
            return unknown_producer(REASON_PLACEHOLDER, info)
        return unknown_producer(REASON_METADATA_SILENT, info)

    # -- npm -------------------------------------------------------------------------

    def _npm_manifest_paths(self, component):
        """Candidate ``package.json`` paths for this instance, most specific first."""
        if not self.project_dir:
            return []
        paths = []
        # `dependency_resolver` keys an npm instance as `npm|<install path>`, so
        # a nested duplicate resolves to its own copy rather than to whichever
        # version happens to sit at the top level.
        key = str(component.get("key", "") or "")
        if key.startswith("npm|"):
            install_path = key[len("npm|") :]
            if install_path and ".." not in install_path.split("/"):
                paths.append(self.project_dir / install_path / "package.json")

        group = str(component.get("group", "") or "")
        name = str(component.get("name", "") or "")
        full = f"{group}/{name}" if group else name
        if full:
            paths.append(self.project_dir / "node_modules" / Path(full) / "package.json")
        return paths

    def _resolve_npm(self, component):
        placeholders = self.registry["placeholders"]
        for path in self._npm_manifest_paths(component):
            data = _read_json(path)
            if not isinstance(data, dict):
                continue

            for value in (data.get("author"), _first(data.get("maintainers")), _first(data.get("contributors"))):
                candidate = normalize_producer_name(_person_name(value), placeholders)
                if candidate:
                    return _known_producer(candidate, SOURCE_NPM_PACKAGE_JSON, path)

            for value in (data.get("author"), _first(data.get("maintainers"))):
                from_email = _producer_from_email(_person_email(value), self.registry, component)
                if from_email:
                    return from_email

            return unknown_producer(REASON_METADATA_SILENT, path)

        if not self.project_dir:
            return unknown_producer(REASON_NO_PROJECT_DIR, component.get("purl"))
        return unknown_producer(REASON_METADATA_NOT_FOUND, component.get("purl"))

    # -- maven / gradle ---------------------------------------------------------------

    def _maven_repositories(self):
        roots = []
        for env_key in ("MAVEN_REPO_LOCAL", "M2_REPO"):
            value = self.env.get(env_key)
            if value:
                roots.append(Path(value))
        if self.project_dir:
            roots.append(self.project_dir / ".m2" / "repository")
        home = self.env.get("USERPROFILE") or self.env.get("HOME")
        if home:
            roots.append(Path(home) / ".m2" / "repository")
        return [r for r in roots if r.is_dir()]

    def _maven_pom_paths(self, group_id, artifact_id, version):
        if not group_id or not artifact_id or not version:
            return []
        relative = Path(*group_id.split(".")) / artifact_id / version / f"{artifact_id}-{version}.pom"
        return [root / relative for root in self._maven_repositories()]

    def _resolve_maven(self, component):
        group_id, artifact_id = _maven_coordinates(component)
        version = str(component.get("version", "") or "")
        placeholders = self.registry["placeholders"]

        for path in self._maven_pom_paths(group_id, artifact_id, version):
            text = _read_text(path)
            if text is None:
                continue

            match = _POM_ORGANIZATION.search(text)
            if match:
                candidate = normalize_producer_name(match.group(1), placeholders)
                if candidate:
                    return _known_producer(candidate, SOURCE_MAVEN_POM_ORGANIZATION, path)

            for block in _POM_DEVELOPER_BLOCK.finditer(text):
                org = _POM_DEVELOPER_ORG.search(block.group(1))
                if not org:
                    continue
                candidate = normalize_producer_name(org.group(1), placeholders)
                if candidate:
                    return _known_producer(candidate, SOURCE_MAVEN_POM_DEVELOPER, path)
            break

        # No POM, or a POM that names nobody. A Maven groupId is reverse-DNS, so
        # reversing it yields the domain the organization registered — which the
        # registry, and only the registry, turns into that organization's name.
        # The groupId itself is never the answer.
        if group_id:
            reversed_domain = ".".join(reversed(group_id.split(".")))
            hit = _domain_lookup(reversed_domain, self.registry)
            if hit:
                return _known_producer(hit[0], SOURCE_NAMESPACE_REGISTRY, f"{group_id} -> {hit[1]}")
            return unknown_producer(REASON_NAMESPACE_UNREGISTERED, group_id)

        return unknown_producer(REASON_METADATA_NOT_FOUND, component.get("purl"))

    # -- golang ------------------------------------------------------------------------

    def _resolve_golang(self, component):
        module_path = str(component.get("name", "") or "").strip().lower()
        if not module_path:
            return unknown_producer(REASON_METADATA_NOT_FOUND, component.get("purl"))

        segments = [s for s in module_path.split("/") if s]
        host = segments[0]

        if host in self.registry["forges"]:
            # A forge hosts; it does not produce. Only a registered account
            # under it identifies an organization.
            for depth in range(len(segments), 1, -1):
                prefix = "/".join(segments[:depth])
                name = self.registry["namespaces"].get(prefix)
                if name:
                    return _known_producer(name, SOURCE_NAMESPACE_REGISTRY, prefix)
            return unknown_producer(REASON_FORGE_HOST, host)

        hit = _domain_lookup(host, self.registry)
        if hit:
            return _known_producer(hit[0], SOURCE_NAMESPACE_REGISTRY, f"{host} -> {hit[1]}")
        return unknown_producer(REASON_NAMESPACE_UNREGISTERED, host)

    # -- cargo -------------------------------------------------------------------------

    def _cargo_manifest_paths(self, name, version):
        paths = []
        if self.project_dir and name:
            paths.append(self.project_dir / "vendor" / name / "Cargo.toml")
        cargo_home = self.env.get("CARGO_HOME")
        if not cargo_home:
            home = self.env.get("USERPROFILE") or self.env.get("HOME")
            cargo_home = str(Path(home) / ".cargo") if home else None
        if cargo_home and name and version:
            src = Path(cargo_home) / "registry" / "src"
            if src.is_dir():
                for registry_dir in sorted(src.iterdir()):
                    paths.append(registry_dir / f"{name}-{version}" / "Cargo.toml")
        return paths

    def _resolve_cargo(self, component):
        name = str(component.get("name", "") or "")
        version = str(component.get("version", "") or "")
        placeholders = self.registry["placeholders"]

        for path in self._cargo_manifest_paths(name, version):
            text = _read_text(path)
            if text is None:
                continue
            for raw in _cargo_authors(text):
                candidate = normalize_producer_name(raw, placeholders)
                if candidate:
                    return _known_producer(candidate, SOURCE_CARGO_MANIFEST, path)
                from_email = _producer_from_email(raw, self.registry, component)
                if from_email:
                    return from_email
            return unknown_producer(REASON_METADATA_SILENT, path)

        return unknown_producer(REASON_METADATA_NOT_FOUND, component.get("purl"))

    # -- nuget -------------------------------------------------------------------------

    def _nuspec_paths(self, package_id, version):
        paths = []
        if not package_id:
            return paths
        if self.project_dir and version:
            packages = self.project_dir / "packages" / f"{package_id}.{version}"
            paths.append(packages / f"{package_id}.nuspec")
        root = self.env.get("NUGET_PACKAGES")
        if not root:
            home = self.env.get("USERPROFILE") or self.env.get("HOME")
            root = str(Path(home) / ".nuget" / "packages") if home else None
        if root and version:
            # The global packages folder lower-cases both the id and the version.
            base = Path(root) / package_id.lower() / version.lower()
            paths.append(base / f"{package_id.lower()}.nuspec")
            paths.append(base / f"{package_id}.nuspec")
        return paths

    def _resolve_nuget(self, component):
        package_id = str(component.get("name", "") or "")
        version = str(component.get("version", "") or "")
        placeholders = self.registry["placeholders"]

        for path in self._nuspec_paths(package_id, version):
            text = _read_text(path)
            if text is None:
                continue
            match = _NUSPEC_AUTHORS.search(text)
            if match:
                candidate = normalize_producer_name(match.group(1), placeholders)
                if candidate:
                    return _known_producer(candidate, SOURCE_NUGET_NUSPEC, path)
            return unknown_producer(REASON_METADATA_SILENT, path)

        return unknown_producer(REASON_METADATA_NOT_FOUND, component.get("purl"))


# =====================================================================================
# Helpers used by the context
# =====================================================================================


def _ecosystem_of(component):
    ecosystem = str(component.get("ecosystem", "") or "").strip().lower()
    if not ecosystem:
        purl = str(component.get("purl", "") or "")
        if purl.startswith("pkg:"):
            purl_type = purl[4:].split("/", 1)[0].strip().lower()
            ecosystem = _PURL_TYPE_TO_ECOSYSTEM.get(purl_type, purl_type)
    return _ECOSYSTEM_ALIASES.get(ecosystem, ecosystem)


def _normalize_pypi_name(name):
    return re.sub(r"[-_.]+", "-", str(name or "")).strip().lower()


def _metadata_get(dist, field):
    try:
        return dist.metadata.get(field)
    except Exception:
        return None


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _person_name(value):
    """npm ``author``/``maintainers`` entries are a string or ``{name, email, url}``."""
    if isinstance(value, dict):
        return value.get("name")
    return value


def _person_email(value):
    if isinstance(value, dict):
        return value.get("email")
    return value


def _cargo_authors(text):
    """``[package] authors`` entries, in declaration order."""
    try:
        import tomllib

        data = tomllib.loads(text)
        authors = (data.get("package") or {}).get("authors")
        if isinstance(authors, list):
            return [str(a) for a in authors]
        if isinstance(authors, str):
            return [authors]
        return []
    except Exception:
        # No tomllib (Python < 3.11) or a manifest this crate's own toolchain
        # would also reject — fall back to reading the array literally.
        match = _CARGO_AUTHORS.search(text)
        if not match:
            return []
        return _CARGO_AUTHOR_ENTRY.findall(match.group(1))


def _maven_coordinates(component):
    """``(groupId, artifactId)`` for a Maven component.

    Read from the **purl**, not from ``group``: the purl namespace is the
    groupId by ECMA-427 definition, whereas ``group`` is a display field a
    caller may have filled in with anything. Falls back to ``group`` only to
    locate the POM file on disk — never as a producer candidate.
    """
    purl = str(component.get("purl", "") or "")
    if purl.startswith("pkg:maven/"):
        remainder = purl[len("pkg:maven/") :].split("?", 1)[0].split("#", 1)[0]
        coordinate = remainder.split("@", 1)[0]
        if "/" in coordinate:
            group_id, artifact_id = coordinate.rsplit("/", 1)
            return group_id.strip(), artifact_id.strip()
    return (
        str(component.get("group", "") or "").strip(),
        str(component.get("name", "") or "").strip(),
    )


# =====================================================================================
# Public API used by the generator
# =====================================================================================


def resolve_producer(component, project_dir=None, python_env=None, registry=None, env=None):
    """Resolve one component's producer without a reusable context.

    Convenience for tests and one-off callers. Generation goes through
    :class:`ProducerContext` so the Python index and the per-package reads are
    paid for once.
    """
    context = ProducerContext(
        project_dir=project_dir,
        python_env=python_env,
        registry=registry,
        env=env,
    )
    return context.resolve(component)


def resolve_project_producer(project, project_dir=None, registry=None, env=None):
    """Resolve the producer of the software the SBOM *describes*.

    The document's target component is a component, so the Component Producer
    element applies to it too — and unlike its dependencies, the operator knows
    the answer. ``ICDEV_SBOM_PRODUCER`` therefore outranks the manifest: an
    organization's legal name is rarely what ``pyproject.toml`` says.
    """
    registry = registry if registry is not None else load_producer_registry()
    env = env if env is not None else os.environ
    placeholders = registry["placeholders"]

    declared = normalize_producer_name(env.get(ENV_PROJECT_PRODUCER), placeholders)
    if declared:
        return _known_producer(declared, SOURCE_OPERATOR, ENV_PROJECT_PRODUCER)

    directory = Path(project_dir) if project_dir else None
    if directory and directory.is_dir():
        found = _project_producer_from_manifests(directory, placeholders)
        if found:
            return found

    return unknown_producer(
        REASON_METADATA_SILENT,
        f"set {ENV_PROJECT_PRODUCER} to name the organization that produces {project.get('name', 'this software')}",
    )


def _project_producer_from_manifests(directory, placeholders):
    """Read the project's own manifest for the organization that produces it."""
    package_json = _read_json(directory / "package.json")
    if isinstance(package_json, dict):
        candidate = normalize_producer_name(_person_name(package_json.get("author")), placeholders)
        if candidate:
            return _known_producer(candidate, SOURCE_PROJECT_MANIFEST, directory / "package.json")

    pyproject = directory / "pyproject.toml"
    text = _read_text(pyproject)
    if text is not None:
        try:
            import tomllib

            data = tomllib.loads(text)
            for entry in (data.get("project") or {}).get("authors") or []:
                candidate = normalize_producer_name(
                    entry.get("name") if isinstance(entry, dict) else entry, placeholders
                )
                if candidate:
                    return _known_producer(candidate, SOURCE_PROJECT_MANIFEST, pyproject)
        except Exception:
            pass

    pom = directory / "pom.xml"
    text = _read_text(pom)
    if text is not None:
        match = _POM_ORGANIZATION.search(text)
        if match:
            candidate = normalize_producer_name(match.group(1), placeholders)
            if candidate:
                return _known_producer(candidate, SOURCE_PROJECT_MANIFEST, pom)

    cargo = directory / "Cargo.toml"
    text = _read_text(cargo)
    if text is not None:
        for raw in _cargo_authors(text):
            candidate = normalize_producer_name(raw, placeholders)
            if candidate:
                return _known_producer(candidate, SOURCE_PROJECT_MANIFEST, cargo)

    return None


def producer_entity(result):
    """The CycloneDX ``OrganizationalEntity`` for a resolved producer.

    ``None`` when the provenance is unknown: CycloneDX has no way to say
    "unknown" inside an entity, and an entity named ``"unknown"`` would read to
    every downstream tool as an organization actually called that. The
    unknown-provenance marker travels in the properties instead.
    """
    if is_unknown(result):
        return None
    return {"name": result["producer"]}


def _spec_at_least(spec_version, target):
    def parse(value):
        try:
            return tuple(int(part) for part in str(value).split("."))
        except (TypeError, ValueError):
            return (0,)

    return parse(spec_version) >= parse(target)


def apply_producer_to_cyclonedx(cdx_component, result, spec_version="1.4"):
    """Write the producer into a CycloneDX component, honouring the spec version.

    ``manufacturer`` ("the organization that created the component") is the
    field whose definition matches Component Producer, and it exists from 1.6.
    Below that the only organizational field is ``supplier`` — whose ambiguity
    between producer and distributor is precisely what the 2026 standard set out
    to fix, so it is used as the carrier of last resort and the properties, not
    the native field, remain the authoritative statement.
    """
    entity = producer_entity(result)
    if entity is None:
        return cdx_component
    if _spec_at_least(spec_version, "1.6"):
        cdx_component["manufacturer"] = entity
    else:
        cdx_component["supplier"] = entity
    return cdx_component


def producer_properties(result):
    """Render a resolution result as CycloneDX ``properties`` entries.

    Always non-empty. A component either states its producer or states that its
    provenance is unknown and why; there is no third outcome in which the
    element is simply missing.
    """
    properties = [
        {"name": PROPERTY_PRODUCER, "value": producer_db_value(result)},
        {"name": PROPERTY_PROVENANCE, "value": UNKNOWN if is_unknown(result) else KNOWN},
    ]
    if is_unknown(result):
        properties.append(
            {
                "name": PROPERTY_PRODUCER_UNKNOWN_REASON,
                "value": (result or {}).get("reason") or REASON_METADATA_NOT_FOUND,
            }
        )
    else:
        properties.append({"name": PROPERTY_PRODUCER_SOURCE, "value": result["source"]})
    if (result or {}).get("evidence"):
        properties.append({"name": PROPERTY_PRODUCER_EVIDENCE, "value": result["evidence"]})
    return properties


def read_producer_from_cyclonedx(cdx_component):
    """Read back what :func:`producer_properties` wrote. Round-trip for validators."""
    values = {
        prop.get("name"): prop.get("value")
        for prop in (cdx_component.get("properties") or [])
        if isinstance(prop, dict)
    }
    producer = values.get(PROPERTY_PRODUCER)
    if producer is None or producer == UNKNOWN:
        return unknown_producer(values.get(PROPERTY_PRODUCER_UNKNOWN_REASON) or REASON_METADATA_NOT_FOUND)
    return _known_producer(
        producer,
        values.get(PROPERTY_PRODUCER_SOURCE),
        values.get(PROPERTY_PRODUCER_EVIDENCE),
    )


def validate_sbom_producers(sbom):
    """Check that every component states a producer or its unknown provenance.

    Returns ``(errors, summary)``. This is the acceptance criterion in
    executable form, and sbx-sig-02's conformance validator can call it as-is.
    """
    errors = []
    known = 0
    unknown = 0

    components = list(sbom.get("components") or [])
    target = (sbom.get("metadata") or {}).get("component")
    if isinstance(target, dict):
        components.append(target)

    for index, cdx_component in enumerate(components):
        label = cdx_component.get("bom-ref") or cdx_component.get("name") or f"component[{index}]"
        values = {
            prop.get("name"): prop.get("value")
            for prop in (cdx_component.get("properties") or [])
            if isinstance(prop, dict)
        }
        producer = values.get(PROPERTY_PRODUCER)
        provenance = values.get(PROPERTY_PROVENANCE)

        if producer is None:
            errors.append(f"{label}: no {PROPERTY_PRODUCER} property")
            continue
        if provenance not in (KNOWN, UNKNOWN):
            errors.append(f"{label}: {PROPERTY_PROVENANCE} is {provenance!r}, expected 'known' or 'unknown'")
            continue

        if producer == UNKNOWN:
            unknown += 1
            if provenance != UNKNOWN:
                errors.append(f"{label}: producer is unknown but provenance is not marked unknown")
            if not values.get(PROPERTY_PRODUCER_UNKNOWN_REASON):
                errors.append(f"{label}: unknown provenance with no {PROPERTY_PRODUCER_UNKNOWN_REASON}")
            continue

        known += 1
        if provenance != KNOWN:
            errors.append(f"{label}: producer is stated but provenance is marked {provenance!r}")
        group = cdx_component.get("group")
        if group and producer.strip().lower() == str(group).strip().lower():
            errors.append(f"{label}: producer was populated from the component's group ({group!r})")

    return errors, {
        "component_count": len(components),
        "producers_known": known,
        "producers_unknown": unknown,
    }


# =====================================================================================
# CLI
# =====================================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Resolve the 2026 SBOM Component Producer element for a component or an SBOM"
    )
    parser.add_argument("--purl", help="Package URL of a single component to resolve, e.g. pkg:pypi/flask@3.0.0")
    parser.add_argument("--name", help="Component name (when --purl is not given)")
    parser.add_argument("--version", dest="version", help="Component version (when --purl is not given)")
    parser.add_argument("--ecosystem", help="python, npm, maven, gradle, golang, cargo or nuget")
    parser.add_argument("--project-dir", help="Project directory whose installed packages hold the metadata")
    parser.add_argument("--python-env", help="Virtualenv or site-packages directory for the Python ecosystem")
    parser.add_argument("--validate", help="Path to a CycloneDX JSON SBOM to check for producer conformance")
    parser.add_argument("--registry", action="store_true", help="Print the loaded namespace registry and exit")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.registry:
        registry = load_producer_registry()
        payload = {
            "path": str(REGISTRY_PATH),
            "domains": registry["domains"],
            "forges": sorted(registry["forges"]),
            "namespaces": registry["namespaces"],
            "placeholders": sorted(registry["placeholders"]),
        }
        print(json.dumps(payload, indent=2) if args.json_output else payload)
        return 0

    if args.validate:
        sbom = _read_json(args.validate)
        if sbom is None:
            print(f"ERROR: could not read SBOM: {args.validate}", file=sys.stderr)
            return 1
        errors, summary = validate_sbom_producers(sbom)
        payload = {"valid": not errors, "errors": errors, **summary}
        if args.json_output:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Components: {summary['component_count']}")
            print(f"  producer stated:      {summary['producers_known']}")
            print(f"  unknown provenance:   {summary['producers_unknown']}")
            for error in errors:
                print(f"  ERROR {error}")
        return 1 if errors else 0

    if not args.purl and not args.name:
        parser.error("one of --purl, --name, --validate or --registry is required")

    component = {"name": args.name or "", "version": args.version or "", "purl": args.purl or ""}
    if args.ecosystem:
        component["ecosystem"] = args.ecosystem
    if args.purl and not args.name:
        component["name"], component["version"] = _component_from_purl(args.purl)

    result = resolve_producer(component, project_dir=args.project_dir, python_env=args.python_env)
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Producer:   {result['producer']}")
        print(f"Provenance: {result['provenance']}")
        print(f"Source:     {result['source'] or '-'}")
        if result.get("reason"):
            print(f"Reason:     {result['reason']}")
        if result.get("evidence"):
            print(f"Evidence:   {result['evidence']}")
    return 0


def _component_from_purl(purl):
    """``(name, version)`` from a purl, for the CLI's single-component mode."""
    remainder = str(purl)[4:] if str(purl).startswith("pkg:") else str(purl)
    remainder = remainder.split("?", 1)[0].split("#", 1)[0]
    _, _, path = remainder.partition("/")
    coordinate, _, version = path.partition("@")
    purl_type = remainder.split("/", 1)[0].lower()
    if purl_type == "maven":
        name = coordinate.rsplit("/", 1)[-1]
    elif purl_type == "golang":
        name = coordinate
    else:
        name = coordinate.rsplit("/", 1)[-1].replace("%2F", "/")
    return name, version


if __name__ == "__main__":
    sys.exit(main())
