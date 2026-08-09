#!/usr/bin/env python3
# CUI // SP-CTI
# CANONICAL: this is the only SBOM producer in the tree. Do not deprecate it. It backs
# ~25 live call sites (MCP sbom_generate, icdev_comply, dashboard batch API, CDRL, IronBank,
# production audit/remediate, FedRAMP KSI/packager, SWFT) and a BLOCKING bdc_canvas gate.
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Generate a Software Bill of Materials (SBOM) in CycloneDX or SPDX.

Resolves the project's transitive dependency set (SBOM 2026 Coverage element),
builds a CycloneDX JSON document with CUI classification metadata, records it in
the sbom_records table, and logs an audit event.

Formats (sbx-fmt-01): the 2026 standard names **CycloneDX (ECMA-424)** and
**SPDX (ISO/IEC 5962:2021)** as the two widely used SBOM data formats and asks
for support of all of them. `--format spdx` emits the same build as SPDX 2.3 by
translating the CycloneDX document through `spdx_writer.to_spdx`, so the two
serializations carry identical elements by construction rather than by
maintenance.

Coverage (sbx-cov-01): components come from `dependency_resolver.resolve_project`,
which reads each ecosystem's *resolved* lockfile and degrades to this module's
declared-manifest parsers only where offline resolution is impossible. The
resulting SBOM always carries an explicit coverage statement — `compositions`
plus `icdev:sbom:coverage*` properties — so a partial tree is never presented as
complete.

Signature (sbx-sig-01): when an asymmetric signing key is configured, each
generated SBOM gets a detached `<sbom>.sig.json` written beside it and its
signature + algorithm persisted to `sbom_records`. Unsigned generation is the
default and says so on stdout; set `ICDEV_SBOM_REQUIRE_SIGNATURE=1` to make it a
hard failure instead. See `tools/compliance/sbom_signer.py`.

Component License (sbx-fld-04): every component and the target component carry the
element in one of four shapes — an SPDX expression validated against the vendored SPDX
License List, a URL to the full terms, a license name, or an explicit unknown/withheld
marker — plus a proprietary-conditions flag. It is never omitted. The same resolution is
written to `sbom_components.license`, a column dead since migration 209 because this
generator had never written the table at all. See `tools/compliance/component_licenser.py`.

Frequency / Accommodation of Updates (sbx-prc-02): every generation appends a row
that links back to the SBOM it replaces (`supersedes_sbom_id`) and records the
content digest, the build it came from and why it exists. Nothing is ever
rewritten — a correction is a successor row, not an edit. See
`tools/compliance/sbom_revision.py`.

SBOM Metadata (sbx-fld-01): the nine document-level elements of §1.1 — Author,
Data Format Name/Version, Generation Context, Timestamp, Tool Name/Version, SBOM
Version, and the Author Signature sbx-sig-01 attaches. The Author is the *entity*
operating this generator and is deliberately not read from the tool's vendor
field; the Tool Version is derived from the delivery rather than being a literal;
the Timestamp conforms to RFC 9557; and the document's version counter and the
`sbom_records` row are computed once and used twice, so they cannot disagree."""

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.compliance.component_licenser import (
    PROPERTY_LICENSE,
    license_entries,
    license_properties,
    project_license_from_manifests,
    record_license_disclosure,
    resolve_declared_license,
    resolve_license,
)
from tools.compliance.component_hasher import (
    PROPERTY_ALGORITHM as PROPERTY_HASH_ALGORITHM,
)
from tools.compliance.component_hasher import (
    PROPERTY_HASH,
    REASON_TARGET_NOT_BUILT,
    apply_hash_to_cyclonedx,
    record_hash_disclosure,
    resolve_hash,
    unknown_hash,
)
from tools.compliance.component_names import (
    NAME_DECLARED,
    apply_names_to_cyclonedx,
    derive_names,
)
from tools.compliance.component_producer import (
    PROPERTY_PRODUCER,
    ProducerContext,
    apply_producer_to_cyclonedx,
    producer_properties,
    resolve_project_producer,
)
from tools.compliance.dependency_resolver import (
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    COVERAGE_UNKNOWN,
    RESOLUTION_DECLARED,
    resolve_project,
)
from tools.compliance.unknown_information import (
    FIELD_NAME,
    FIELD_PRODUCER,
    FIELD_VERSION,
    REASON_DECLARED_WITHOUT_VERSION,
    REASON_NOT_PROVIDED_BY_PRODUCER,
    REASON_VERSION_MANAGED_BY_PARENT,
    UNKNOWN,
    UNKNOWN_REASONS,
    Disclosure,
    apply_component_policy,
    apply_document_policy,
    apply_to_cyclonedx,
    completeness_properties,
    disclosure_from_producer,
    enquiry_properties,
    is_legacy_sentinel,
    load_disclosure_policy,
)
from tools.compliance.sbom_identifiers import (
    apply_identifiers_to_cyclonedx,
    component_id,
    derive_identifiers,
    identifiers_to_json,
    parse_identifiers_from_cyclonedx,
)
from tools.compliance.sbom_revision import (
    SBOM_VERSION_MAJOR,
    latest_record,
    next_sbom_version,
    plan_revision,
    revision_insert_fields,
)
from tools.compliance.sbom_signer import (
    SbomSigningError,
    sign_sbom,
    signature_required,
    signing_available,
)
from tools.compliance.spdx_writer import to_spdx
from tools.db.storage import column_exists, get_connection
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# CycloneDX spec version. The default is 1.7 (sbx-fmt-01): the 2026 Minimum
# Elements warn against deprecated versions of a format and cite ECMA-424 of
# December 2025, which is CycloneDX 1.7. The previous default, 1.4, is a 2022
# spec that cannot express the Component Producer element natively -- below 1.6
# the nearest field is `supplier`, whose producer/distributor ambiguity is what
# the 2026 standard set out to remove. 1.4-1.6 stay selectable via
# --spec-version for consumers whose tooling has not caught up.
CYCLONEDX_SPEC_VERSION = "1.7"
CYCLONEDX_SCHEMA = "http://cyclonedx.org/schema/bom-1.7.schema.json"

# Supported CycloneDX spec versions (D342)
CYCLONEDX_SUPPORTED_VERSIONS = {
    "1.4": "http://cyclonedx.org/schema/bom-1.4.schema.json",
    "1.5": "http://cyclonedx.org/schema/bom-1.5.schema.json",
    "1.6": "http://cyclonedx.org/schema/bom-1.6.schema.json",
    "1.7": "http://cyclonedx.org/schema/bom-1.7.schema.json",
}

# SBOM output formats. Both are named by the 2026 standard; SWID was removed
# from the accepted list in 2026 and ICDEV has never emitted it.
FORMAT_CYCLONEDX = "cyclonedx"
FORMAT_SPDX = "spdx"
SUPPORTED_FORMATS = (FORMAT_CYCLONEDX, FORMAT_SPDX)

#: File extension per format, so a consumer can tell them apart on disk.
FORMAT_EXTENSIONS = {
    FORMAT_CYCLONEDX: "cdx.json",
    FORMAT_SPDX: "spdx.json",
}

# --- 2026 SBOM Metadata elements (§1.1) -----------------------------------------

# SBOM Data Format Name and SBOM Tool Name. The Data Format Version is
# CYCLONEDX_SPEC_VERSION above, already raised off the deprecated 1.4 by sbx-fmt-01.
SBOM_DATA_FORMAT_NAME = "CycloneDX"
SBOM_TOOL_NAME = "icdev-sbom-generator"
SBOM_TOOL_VENDOR = "ICDEV™"

# SBOM Author — the ENTITY that generated the SBOM, explicitly NOT the tool.
# metadata.tools[].vendor names the tool's vendor and does not satisfy this element;
# it stays exactly where it is and means exactly what it always meant. The standard
# asks for full names with no acronyms unless the acronym is official, and allows the
# Author and the Component Producer to be the same entity without being the same
# element. A deployment SHOULD set ICDEV_SBOM_AUTHOR to the full legal name of the
# entity operating this generator; the default names the platform in full.
SBOM_AUTHOR_ENV = "ICDEV_SBOM_AUTHOR"
DEFAULT_SBOM_AUTHOR = "Intelligent Certified Development Platform"

# Property name the SBOM Author is also emitted under. Colon-separated rather than
# hyphenated to match `spdx_writer._creators`, which reads this exact key to build the
# SPDX `Organization:` creator — the element has to survive both serializations.
PROPERTY_SBOM_AUTHOR = "icdev:sbom:author"

# SBOM Generation Context — the lifecycle phase, and therefore the data available,
# when the SBOM was generated. This generator reads declared and resolved source
# manifests and never opens a built artifact, which is "before build" in the
# standard's wording and "pre-build" in CycloneDX's metadata.lifecycles vocabulary.
# Both are emitted: the CycloneDX field where the spec version has one, the property
# always — only the property carries the standard's own term, and 1.4 has no
# lifecycles field to put it in.
SBOM_GENERATION_CONTEXT = "before build"
CYCLONEDX_LIFECYCLE_PHASE = "pre-build"
LIFECYCLES_MIN_SPEC_VERSION = (1, 5)
PROPERTY_GENERATION_CONTEXT = "icdev:sbom-generation-context"

# SBOM Version, as the standard's semver spelling. The major is pinned to 1 by
# SBOM_VERSION_MAJOR (sbom_revision) and the minor counts content revisions.
PROPERTY_SBOM_VERSION = "icdev:sbom:version"

# The sbom_records columns added by migration
# 20260808030213_sbom_2026_minimum_elements (sbx-fnd-02). Written only where they
# exist: a database that predates that migration still records its row and names on
# stderr what it could not persist, rather than raising into the caller.
SBOM_RECORD_METADATA_COLUMNS = (
    "sbom_author",
    "data_format_name",
    "data_format_version",
    "generation_context",
    "tool_name",
    "tool_version",
    "sbom_version",
    "serial_number",
)

# Coverage status -> CycloneDX `compositions[].aggregate` vocabulary (spec 1.3+,
# so this is valid across every version in CYCLONEDX_SUPPORTED_VERSIONS).
COVERAGE_AGGREGATE = {
    COVERAGE_COMPLETE: "complete",
    COVERAGE_INCOMPLETE: "incomplete",
    COVERAGE_UNKNOWN: "unknown",
}

# Mirrors the CHECK constraint on sbom_components.component_type (migration 209). A
# component whose type falls outside it is stored as 'other' rather than raising — an
# unexpected type must not cost the whole inventory write.
SBOM_COMPONENT_TYPES = frozenset(
    {
        "library",
        "framework",
        "container",
        "os",
        "firmware",
        "device",
        "application",
        "service",
        "other",
    }
)


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _get_project(conn, project_id):
    """Load project data."""
    row = conn.execute("SELECT * FROM projects WHERE id = %s", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _log_audit_event(conn, project_id, action, details, file_path=None):
    """Log an audit trail event."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                project_id,
                "sbom_generated",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)] if file_path else []),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


def _detect_project_type(project_dir):
    """Detect project type based on dependency files present.
    Returns a list of detected types."""
    project_dir = Path(project_dir)
    detected = []

    # Python indicators
    if (project_dir / "requirements.txt").exists():
        detected.append("python-requirements")
    if (project_dir / "pyproject.toml").exists():
        detected.append("python-pyproject")
    if (project_dir / "setup.py").exists():
        detected.append("python-setup")
    if (project_dir / "Pipfile").exists():
        detected.append("python-pipfile")
    if (project_dir / "Pipfile.lock").exists():
        detected.append("python-pipfile-lock")

    # JavaScript/TypeScript indicators
    if (project_dir / "package.json").exists():
        detected.append("javascript-package")
    if (project_dir / "package-lock.json").exists():
        detected.append("javascript-package-lock")
    if (project_dir / "yarn.lock").exists():
        detected.append("javascript-yarn")

    # Go
    if (project_dir / "go.mod").exists():
        detected.append("go-mod")

    # Rust
    if (project_dir / "Cargo.toml").exists():
        detected.append("rust-cargo")

    # Java
    if (project_dir / "pom.xml").exists():
        detected.append("java-maven")
    if (project_dir / "build.gradle").exists() or (project_dir / "build.gradle.kts").exists():
        detected.append("java-gradle")

    # C# / .NET
    if list(project_dir.glob("*.csproj")):
        detected.append("csharp-csproj")
    if (project_dir / "packages.config").exists():
        detected.append("csharp-packages")

    # Ruby
    if (project_dir / "Gemfile").exists():
        detected.append("ruby-gemfile")

    return detected


def _parse_requirements_txt(file_path):
    """Parse Python requirements.txt file. Returns list of component dicts."""
    components = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Skip comments, empty lines, and options
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Skip URLs and local paths
            if "://" in line or line.startswith("."):
                continue

            # Parse package specification
            # Patterns: package==1.0, package>=1.0, package~=1.0, package
            match = re.match(r"^([a-zA-Z0-9._-]+)\s*(?:([<>=!~]+)\s*([a-zA-Z0-9.*_-]+))?", line)
            if match:
                declared_name = match.group(1)
                name = declared_name.lower().replace("_", "-")
                version = match.group(3) or UNKNOWN

                purl = f"pkg:pypi/{name}"
                if version != UNKNOWN:
                    purl += f"@{version}"

                components.append(
                    {
                        "type": "library",
                        "name": name,
                        # The spelling the producer published under. `name` above is
                        # ICDEV's normalization of it, and the 2026 Component Name
                        # element is defined as the producer's name (sbx-fld-06).
                        "declared_name": declared_name,
                        "version": version,
                        "version_unknown_reason": REASON_DECLARED_WITHOUT_VERSION,
                        "purl": purl,
                        "scope": "required",
                        "group": "",
                        "source": str(file_path),
                    }
                )

    return components


def _parse_pyproject_toml(file_path):
    """Parse pyproject.toml for dependencies. Returns list of component dicts."""
    components = []
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Simple parser for [project.dependencies] section
    for line in content.split("\n"):
        stripped = line.strip()

        if stripped == "[project]":
            pass
        if "dependencies" in stripped and "=" in stripped:
            # Handle inline list: dependencies = ["pkg1>=1.0", "pkg2"]
            match = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
            if match:
                deps_str = match.group(1)
                for dep in re.findall(r'"([^"]+)"|\'([^\']+)\'', deps_str):
                    dep_str = dep[0] or dep[1]
                    dep_match = re.match(r"([a-zA-Z0-9._-]+)(?:\[.*?\])?\s*(?:([<>=!~]+)\s*(.+))?", dep_str)
                    if dep_match:
                        declared_name = dep_match.group(1)
                        name = declared_name.lower().replace("_", "-")
                        version = dep_match.group(3) or UNKNOWN
                        # Clean up version (take first version if multiple conditions)
                        version = version.split(",")[0].strip()

                        purl = f"pkg:pypi/{name}"
                        if version != UNKNOWN:
                            purl += f"@{version}"

                        components.append(
                            {
                                "type": "library",
                                "name": name,
                                "declared_name": declared_name,
                                "version": version,
                                "version_unknown_reason": REASON_DECLARED_WITHOUT_VERSION,
                                "purl": purl,
                                "scope": "required",
                                "group": "",
                                "source": str(file_path),
                            }
                        )
            break

    return components


def _npm_purl_name(name):
    """Encode an npm package name for the purl name/namespace fields.

    A scoped package ``@babel/core`` is namespace ``%40babel`` and name
    ``core``: the ``@`` is percent-encoded because purl reserves it as the
    version separator, and the ``/`` STAYS a separator because that is what
    divides namespace from name. The previous encoding did the exact opposite —
    ``pkg:npm/@babel%2Fcore`` — which ECMA-427 rejects and which the Component
    Identifiers validator flags (sbx-fld-05).
    """
    if name.startswith("@"):
        return "%40" + name[1:]
    return name


def _parse_package_json(file_path):
    """Parse package.json for dependencies. Returns list of component dicts."""
    components = []
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for dep_section, scope in [
        ("dependencies", "required"),
        ("devDependencies", "optional"),
        ("peerDependencies", "optional"),
    ]:
        deps = data.get(dep_section, {})
        for name, version_spec in deps.items():
            # Clean version spec
            version = version_spec.lstrip("^~>=<")
            if not version or version == "*":
                version = UNKNOWN

            # Handle scoped packages
            purl_name = _npm_purl_name(name)
            purl = f"pkg:npm/{purl_name}"
            if version != UNKNOWN:
                purl += f"@{version}"

            group = ""
            pkg_name = name
            if name.startswith("@"):
                parts = name.split("/", 1)
                if len(parts) == 2:
                    group = parts[0]
                    pkg_name = parts[1]

            components.append(
                {
                    "type": "library",
                    "name": pkg_name,
                    "version": version,
                    "version_unknown_reason": REASON_DECLARED_WITHOUT_VERSION,
                    "purl": purl,
                    "scope": scope,
                    "group": group,
                    "source": str(file_path),
                }
            )

    return components


# NOTE: `_parse_package_lock_json` was removed by sbx-cov-01. package-lock.json is a
# RESOLVED tree and is now read by dependency_resolver._resolve_package_lock, which keeps
# the nested node_modules instances the old parser discarded. The parsers that remain in
# this module are DECLARED-manifest parsers only, reached via DECLARED_PARSERS when an
# ecosystem cannot be resolved offline.


def _parse_go_mod(file_path):
    """Parse Go go.mod file. Returns list of component dicts."""
    components = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return components

    # Parse parenthesized require blocks: require ( ... )
    require_blocks = re.findall(r"require\s*\((.*?)\)", content, re.DOTALL)
    for block in require_blocks:
        for line in block.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            # Remove inline comments (// indirect, etc.)
            line = re.sub(r"\s*//.*$", "", line).strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                module = parts[0]
                version = parts[1]
                purl = f"pkg:golang/{module}@{version}"
                components.append(
                    {
                        "type": "library",
                        "name": module,
                        "version": version,
                        "purl": purl,
                        "scope": "required",
                        "group": "",
                        "source": str(file_path),
                    }
                )

    # Parse single-line require statements: require github.com/foo/bar v1.2.3
    # The separator is horizontal whitespace only: `\s+` matches newlines, so
    # `require (\n\tgithub.com/foo/bar v1.2.3` used to capture module="(" and
    # version="github.com/foo/bar" — a phantom component named "(" alongside
    # the real one the require-block loop above already found.
    single_requires = re.findall(r"^require[ \t]+(\S+)[ \t]+(\S+)", content, re.MULTILINE)
    for module, version in single_requires:
        # Skip if this is the start of a parenthesized block
        if version == "(" or module == "(":
            continue
        version = re.sub(r"\s*//.*$", "", version).strip()
        purl = f"pkg:golang/{module}@{version}"
        components.append(
            {
                "type": "library",
                "name": module,
                "version": version,
                "purl": purl,
                "scope": "required",
                "group": "",
                "source": str(file_path),
            }
        )

    return components


def _parse_cargo_toml(file_path):
    """Parse Rust Cargo.toml for dependencies. Returns list of component dicts."""
    components = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return components

    current_section = None
    for line in lines:
        stripped = line.strip()

        # Detect section headers
        section_match = re.match(r"^\[(.+)\]$", stripped)
        if section_match:
            current_section = section_match.group(1).strip()
            continue

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # Only parse [dependencies] and [dev-dependencies]
        if current_section not in ("dependencies", "dev-dependencies"):
            continue

        scope = "required" if current_section == "dependencies" else "optional"

        # Match: crate_name = "version"
        simple_match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"', stripped)
        if simple_match:
            name = simple_match.group(1)
            version = simple_match.group(2) or UNKNOWN
            purl = f"pkg:cargo/{name}" if version == UNKNOWN else f"pkg:cargo/{name}@{version}"
            components.append(
                {
                    "type": "library",
                    "name": name,
                    "version": version,
                    "version_unknown_reason": REASON_DECLARED_WITHOUT_VERSION,
                    "purl": purl,
                    "scope": scope,
                    "group": "",
                    "source": str(file_path),
                }
            )
            continue

        # Match: crate_name = { version = "x.y", ... }
        table_match = re.match(r"^([a-zA-Z0-9_-]+)\s*=\s*\{(.*)\}", stripped)
        if table_match:
            name = table_match.group(1)
            inner = table_match.group(2)
            version_match = re.search(r'version\s*=\s*"([^"]*)"', inner)
            version = version_match.group(1) if version_match else UNKNOWN
            purl = f"pkg:cargo/{name}" if version == UNKNOWN else f"pkg:cargo/{name}@{version}"
            components.append(
                {
                    "type": "library",
                    "name": name,
                    "version": version,
                    "version_unknown_reason": REASON_DECLARED_WITHOUT_VERSION,
                    "purl": purl,
                    "scope": scope,
                    "group": "",
                    "source": str(file_path),
                }
            )
            continue

    return components


def _parse_pom_xml(file_path):
    """Parse Maven pom.xml for dependencies. Returns list of component dicts.
    Uses regex-based parsing (no XML library required)."""
    components = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return components

    # Find all <dependency>...</dependency> blocks
    dep_blocks = re.findall(r"<dependency>(.*?)</dependency>", content, re.DOTALL)
    for block in dep_blocks:
        try:
            group_match = re.search(r"<groupId>\s*(.*?)\s*</groupId>", block)
            artifact_match = re.search(r"<artifactId>\s*(.*?)\s*</artifactId>", block)

            if not group_match or not artifact_match:
                continue

            group_id = group_match.group(1).strip()
            artifact_id = artifact_match.group(1).strip()

            # A POM that names no <version> is not silent about the version — the
            # version lives in a parent POM's dependencyManagement, which this
            # declared-only parser cannot read. That is a distinct unknown-reason,
            # not the old "managed" literal, which said neither unknown nor withheld.
            version_match = re.search(r"<version>\s*(.*?)\s*</version>", block)
            version = version_match.group(1).strip() if version_match else UNKNOWN
            version_reason = (
                REASON_DECLARED_WITHOUT_VERSION if version_match else REASON_VERSION_MANAGED_BY_PARENT
            )

            scope_match = re.search(r"<scope>\s*(.*?)\s*</scope>", block)
            maven_scope = scope_match.group(1).strip() if scope_match else "compile"

            # Map Maven scopes to CycloneDX scopes
            if maven_scope in ("test", "provided"):
                cdx_scope = "optional"
            else:
                cdx_scope = "required"

            purl = f"pkg:maven/{group_id}/{artifact_id}"
            if version != UNKNOWN:
                purl += f"@{version}"

            components.append(
                {
                    "type": "library",
                    "name": artifact_id,
                    "version": version,
                    "version_unknown_reason": version_reason,
                    "purl": purl,
                    "scope": cdx_scope,
                    "group": group_id,
                    "source": str(file_path),
                }
            )
        except Exception:
            continue

    return components


def _parse_build_gradle(file_path):
    """Parse Gradle build.gradle or build.gradle.kts for dependencies.
    Returns list of component dicts."""
    components = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return components

    # Configuration names and their CycloneDX scope mappings
    test_configs = {"testImplementation", "testCompileOnly", "testRuntimeOnly"}
    optional_configs = {"compileOnly", "testCompileOnly"}

    # Match patterns like:
    #   implementation 'group:artifact:version'
    #   implementation "group:artifact:version"
    #   testImplementation 'group:artifact:version'
    dep_pattern = re.compile(
        r"(implementation|api|compileOnly|runtimeOnly|testImplementation"
        r"|testCompileOnly|testRuntimeOnly)\s*"
        r"""[('"]([^'"]+)['")]""",
        re.MULTILINE,
    )

    for match in dep_pattern.finditer(content):
        config = match.group(1)
        dep_str = match.group(2)

        # Parse group:artifact:version
        parts = dep_str.split(":")
        if len(parts) < 3:
            continue

        group = parts[0].strip()
        artifact = parts[1].strip()
        version = parts[2].strip()

        if not group or not artifact or not version:
            continue

        # Determine scope
        if config in test_configs or config in optional_configs:
            cdx_scope = "optional"
        else:
            cdx_scope = "required"

        purl = f"pkg:maven/{group}/{artifact}@{version}"

        components.append(
            {
                "type": "library",
                "name": artifact,
                "version": version,
                "purl": purl,
                "scope": cdx_scope,
                "group": group,
                "source": str(file_path),
            }
        )

    return components


def _parse_csproj(file_path):
    """Parse .NET .csproj file for PackageReference elements.
    Returns list of component dicts."""
    components = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return components

    # Match self-closing: <PackageReference Include="Name" Version="1.0" />
    # Match expanded: <PackageReference Include="Name" Version="1.0"></PackageReference>
    # Also handle multi-line with Version on separate line
    patterns = [
        # Self-closing or single-line with both attributes
        re.compile(r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"\s*/?>'),
        # Version before Include (some projects order differently)
        re.compile(r'<PackageReference\s+Version="([^"]+)"\s+Include="([^"]+)"\s*/?>'),
    ]

    seen = set()
    for pattern in patterns:
        for match in pattern.finditer(content):
            if pattern == patterns[1]:
                # Version-first pattern: groups are swapped
                version = match.group(1)
                name = match.group(2)
            else:
                name = match.group(1)
                version = match.group(2)

            if name in seen:
                continue
            seen.add(name)

            purl = f"pkg:nuget/{name}@{version}"
            components.append(
                {
                    "type": "library",
                    "name": name,
                    "version": version,
                    "purl": purl,
                    "scope": "required",
                    "group": "",
                    "source": str(file_path),
                }
            )

    # Handle multi-line PackageReference with Version as child element
    # <PackageReference Include="Name">
    #   <Version>1.0</Version>
    # </PackageReference>
    multiline_pattern = re.compile(
        r'<PackageReference\s+Include="([^"]+)"[^/]*?>'
        r".*?<Version>([^<]+)</Version>.*?</PackageReference>",
        re.DOTALL,
    )
    for match in multiline_pattern.finditer(content):
        name = match.group(1)
        version = match.group(2).strip()

        if name in seen:
            continue
        seen.add(name)

        purl = f"pkg:nuget/{name}@{version}"
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "scope": "required",
                "group": "",
                "source": str(file_path),
            }
        )

    # A PackageReference carrying no version at all — Central Package Management
    # puts the version in Directory.Packages.props, which this declared-only
    # parser does not read. Every pattern above requires a Version, so such a
    # reference used to be dropped entirely: the component vanished from the SBOM
    # rather than appearing with its version stated as unknown. Under the 2026
    # Coverage element a component ICDEV knows about is listed, and under
    # Component Version its missing version is stated as unknown (sbx-fld-06).
    versionless_pattern = re.compile(r'<PackageReference\s+Include="([^"]+)"([^>]*)/?>')
    for match in versionless_pattern.finditer(content):
        name = match.group(1)
        if name in seen or "Version" in match.group(2):
            continue
        seen.add(name)
        components.append(
            {
                "type": "library",
                "name": name,
                "version": UNKNOWN,
                "version_unknown_reason": REASON_DECLARED_WITHOUT_VERSION,
                "purl": f"pkg:nuget/{name}",
                "scope": "required",
                "group": "",
                "source": str(file_path),
            }
        )

    return components


def _parse_packages_config(file_path):
    """Parse older .NET packages.config file.
    Returns list of component dicts."""
    components = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return components

    # Match: <package id="Name" version="1.0" ... />
    pattern = re.compile(
        r'<package\s+[^>]*id="([^"]+)"[^>]*version="([^"]+)"[^>]*/?>',
    )

    for match in pattern.finditer(content):
        name = match.group(1)
        version = match.group(2)

        purl = f"pkg:nuget/{name}@{version}"
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "scope": "required",
                "group": "",
                "source": str(file_path),
            }
        )

    return components


def _declared_python(project_dir):
    """Declared-only Python dependencies — used when no lockfile/environment exists."""
    detected = _detect_project_type(project_dir)
    components = []
    if "python-requirements" in detected:
        components.extend(_parse_requirements_txt(project_dir / "requirements.txt"))
    if "python-pyproject" in detected:
        components.extend(_parse_pyproject_toml(project_dir / "pyproject.toml"))
    return components


def _declared_npm(project_dir):
    """Declared-only npm dependencies — package.json ranges, no resolved tree."""
    if "javascript-package" not in _detect_project_type(project_dir):
        return []
    return _parse_package_json(project_dir / "package.json")


def _declared_golang(project_dir):
    if "go-mod" not in _detect_project_type(project_dir):
        return []
    return _parse_go_mod(project_dir / "go.mod")


def _declared_cargo(project_dir):
    if "rust-cargo" not in _detect_project_type(project_dir):
        return []
    return _parse_cargo_toml(project_dir / "Cargo.toml")


def _declared_maven(project_dir):
    if "java-maven" not in _detect_project_type(project_dir):
        return []
    return _parse_pom_xml(project_dir / "pom.xml")


def _declared_gradle(project_dir):
    if "java-gradle" not in _detect_project_type(project_dir):
        return []
    for build_file in ("build.gradle", "build.gradle.kts"):
        path = project_dir / build_file
        if path.exists():
            return _parse_build_gradle(path)
    return []


def _declared_nuget(project_dir):
    detected = _detect_project_type(project_dir)
    components = []
    if "csharp-csproj" in detected:
        for csproj in sorted(project_dir.glob("*.csproj")):
            components.extend(_parse_csproj(csproj))
    if "csharp-packages" in detected:
        components.extend(_parse_packages_config(project_dir / "packages.config"))
    return components


#: Passed to ``resolve_project`` and invoked only for ecosystems whose resolved
#: set could not be obtained offline. Keeping the parsers here (rather than in
#: the resolver) avoids a circular import and leaves ~25 call sites untouched.
DECLARED_PARSERS = {
    "python": _declared_python,
    "npm": _declared_npm,
    "golang": _declared_golang,
    "cargo": _declared_cargo,
    "maven": _declared_maven,
    "gradle": _declared_gradle,
    "nuget": _declared_nuget,
}


def _component_identity(component):
    """The metadata tuple that decides whether two instances are the same component.

    The 2026 Coverage element requires that "multiple instances of a component
    with differing metadata are listed separately with their dependency
    relationship". So instances collapse only when every emitted field matches;
    two npm instances that differ in version *or* scope stay separate.
    """
    return (
        component.get("type", "library"),
        component.get("group", ""),
        component.get("name", ""),
        component.get("version", ""),
        component.get("purl", ""),
        component.get("scope", ""),
    )


def _generate_bom_ref(component):
    """Generate a unique BOM reference for a component.

    Delegates to sbom_identifiers.component_id so the bom-ref, the
    sbom_components primary key and the organization-specific identifier are
    all the same value derived by one formula.
    """
    return component_id(component)


def _property_value(cdx_comp, name, default=None):
    """Read one CycloneDX property off a built component."""
    for prop in cdx_comp.get("properties") or []:
        if prop.get("name") == name:
            return prop.get("value")
    return default


def _persist_components(conn, cdx_components):
    """Write the document's components to `sbom_components`, carrying their licenses.

    The generator has never written this table, which is why `license` sat dead in the
    schema from migration 209 until now. `license` is exactly the 2026 Component License
    element, so it is populated here — an SPDX expression, a URL, a license name, or one
    of the two explicit undisclosed markers. It is never left NULL.

    `hash_value` and `hash_algorithm` (migration `20260808030213`) are populated the same
    way and under the same rule: the hexadecimal digest and its IANA Hash Function
    Textual Name where the artifact was reachable, and one of the two undisclosed
    markers where it was not. Neither is left NULL either, because an SBOM row that is
    silent about a hash is indistinguishable from one that was never asked.

    Rows are read back out of the FINISHED CycloneDX components rather than re-resolved
    from the parsed input. That is what makes the table incapable of disagreeing with the
    document: the same dedup, the same disclosure policy and the same resolution produced
    both, so a component cannot be described here in terms the recipient's copy does not
    use. It is also why the row id is the component's `bom-ref` — a row and the document
    entry it came from are the same string, and regenerating an SBOM updates the row
    rather than accumulating a duplicate.

    `identifiers_json` (Component Identifiers, sbx-fld-05) is read back off the
    finished component the same way, via `parse_identifiers_from_cyclonedx`, so the
    stored set is the set the recipient's document actually carries — including the
    identifiers that fell back to properties because the active CycloneDX spec version
    has no native field for them.

    Only the dependency components are written. The document's target component lives in
    `metadata.component`, not in `components`, and it is the subject of the inventory
    rather than an entry in it.

    A failure here is reported, not swallowed. The house rule against `except: pass`
    around an INSERT exists because a swallowed schema mismatch reports success while
    persisting nothing; the warning below names the table and the error, and
    `tests/test_sbom_component_license.py` asserts the rows really land, so a mismatch
    surfaces in CI rather than hiding. It is still not allowed to abort generation: the
    document on disk is what ~25 call sites and a blocking gate consume, and losing that
    over a supplementary inventory write would be the larger regression.
    """
    written = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        for cdx_comp in cdx_components:
            component_type = cdx_comp.get("type") or "library"
            if component_type not in SBOM_COMPONENT_TYPES:
                component_type = "other"
            # Read back what the document says, including the two undisclosed states —
            # so a withheld license persists as `withheld` and an unknown one as
            # `unknown`, never as the value that was withheld.
            unknown_json, withheld_json = Disclosure.from_cyclonedx(cdx_comp).db_values()
            conn.execute(
                """INSERT INTO sbom_components
                   (id, component_name, version, component_type, purl, license,
                    producer, hash_value, hash_algorithm, unknown_fields_json,
                    withheld_fields_json, identifiers_json, classification)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT(id) DO UPDATE SET
                       license = EXCLUDED.license,
                       identifiers_json = EXCLUDED.identifiers_json,
                       producer = EXCLUDED.producer,
                       hash_value = EXCLUDED.hash_value,
                       hash_algorithm = EXCLUDED.hash_algorithm,
                       unknown_fields_json = EXCLUDED.unknown_fields_json,
                       withheld_fields_json = EXCLUDED.withheld_fields_json,
                       purl = EXCLUDED.purl,
                       component_type = EXCLUDED.component_type,
                       updated_at = %s""",
                (
                    cdx_comp["bom-ref"],
                    cdx_comp["name"],
                    cdx_comp["version"],
                    component_type,
                    cdx_comp.get("purl"),
                    _property_value(cdx_comp, PROPERTY_LICENSE),
                    _property_value(cdx_comp, PROPERTY_PRODUCER),
                    _property_value(cdx_comp, PROPERTY_HASH),
                    _property_value(cdx_comp, PROPERTY_HASH_ALGORITHM),
                    unknown_json,
                    withheld_json,
                    identifiers_to_json(parse_identifiers_from_cyclonedx(cdx_comp)),
                    "CUI",
                    now,
                ),
            )
            written += 1
        conn.commit()
    except Exception as e:
        # Leave the connection usable: on PostgreSQL a failed statement aborts the whole
        # transaction, which would then take the audit event down with it.
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — nothing better to do than keep going
            pass
        print(f"Warning: Could not record sbom_components rows: {e}", file=sys.stderr)
        return 0
    return written


def _sign_generated_sbom(out_file):
    """Produce the SBOM Author Signature for a freshly written SBOM.

    Returns the signature block, or None when no signature was produced.

    Unsigned generation is the DEFAULT and is loud rather than silent. This
    module has ~25 live call sites and most installs have no signing key
    configured, so hard-failing on a missing key would convert "SBOMs are not
    signed yet" into "SBOM generation is broken" across the whole compliance
    pipeline. Operators who require signed SBOMs set
    ICDEV_SBOM_REQUIRE_SIGNATURE=1 and get the fail-closed behaviour instead.

    What is never allowed, in either mode, is a *non-conformant* signature:
    sbom_signer refuses to emit an HMAC tag or an empty value under the name
    "SBOM Author Signature". See tools/compliance/sbom_signer.py.
    """
    if not signing_available():
        message = (
            "no asymmetric signing key configured "
            "(set ICDEV_SBOM_SIGNING_KEY_PATH or ICDEV_AUDIT_SIGNING_KEY_PATH; "
            "generate one with: python tools/crypto/key_manager.py --generate-keys)"
        )
        if signature_required():
            raise SbomSigningError(
                f"ICDEV_SBOM_REQUIRE_SIGNATURE is set but {message}."
            )
        print(f"  Signature: NOT SIGNED — {message}")
        return None

    try:
        return sign_sbom(out_file)
    except SbomSigningError as e:
        if signature_required():
            raise
        print(f"Warning: SBOM Author Signature not produced: {e}", file=sys.stderr)
        return None


def _build_coverage_blocks(coverage, cdx_components, target_bom_ref):
    """Render a coverage report as CycloneDX ``compositions`` + ICDEV properties.

    ``compositions[].aggregate`` is the standard's own vehicle for stating that a
    component set is complete or incomplete; the properties carry the same fact
    in a form an ICDEV gate can read without a CycloneDX parser.
    """
    status = coverage.get("status", COVERAGE_UNKNOWN)

    properties = [
        {"name": "icdev:sbom:coverage", "value": status},
        {"name": "icdev:sbom:coverage:statement", "value": coverage.get("statement", "")},
    ]
    for entry in coverage.get("resolved", []):
        properties.append(
            {
                "name": f"icdev:sbom:coverage:{entry['ecosystem']}",
                "value": f"resolved: {entry['method']}",
            }
        )
    for entry in coverage.get("unresolved", []):
        properties.append(
            {
                "name": f"icdev:sbom:coverage:{entry['ecosystem']}",
                "value": f"declared-only: {entry['method']}",
            }
        )
        properties.append(
            {
                "name": f"icdev:sbom:coverage:{entry['ecosystem']}:reason",
                "value": entry.get("reason", ""),
            }
        )

    complete_refs = sorted({c["bom-ref"] for c in cdx_components if not c.get("_declared")})
    incomplete_refs = sorted({c["bom-ref"] for c in cdx_components if c.get("_declared")})

    compositions = []
    if complete_refs:
        compositions.append({"aggregate": "complete", "assemblies": complete_refs})
    if incomplete_refs:
        compositions.append({"aggregate": "incomplete", "assemblies": incomplete_refs})
    if not compositions:
        # No components at all — the aggregate still has to say what that means.
        compositions.append(
            {"aggregate": COVERAGE_AGGREGATE.get(status, "unknown"), "assemblies": [target_bom_ref]}
        )

    return compositions, properties


def _record_version_disclosure(comp, disclosure):
    """State whether a component's version is unknown, and why (sbx-prc-01).

    A version ICDEV could not establish used to be written as the bare literal
    ``"unspecified"`` (or ``"managed"`` for Maven), which told a recipient neither
    that the author had looked nor that the author was holding it back. Now the
    absence is one of the two states the 2026 standard separates — always the
    *unknown* one here, since a version nobody declared is not a version anybody
    is withholding.
    """
    raw = str(comp.get("version") or "")
    if raw.strip().lower() != UNKNOWN and not is_legacy_sentinel(raw):
        return disclosure

    reason = comp.get("version_unknown_reason")
    if reason not in UNKNOWN_REASONS:
        # A resolved lockfile that still has no version means the producer
        # published none; a declared manifest means nobody pinned one.
        reason = (
            REASON_DECLARED_WITHOUT_VERSION
            if comp.get("resolution") == RESOLUTION_DECLARED
            else REASON_NOT_PROVIDED_BY_PRODUCER
        )
    return disclosure.unknown(FIELD_VERSION, reason)


def _get_tool_version():
    """SBOM Tool Version — derived from the delivery, never a literal.

    This was hardcoded "1.0.0": a constant that could never change and therefore
    identified no particular code delivery, which is the one thing the element
    exists to do. The version is read from the package's single source of truth,
    then from installed distribution metadata, then from pyproject.toml. If every
    source fails the answer is "unknown" — what the standard requires the author to
    state when a value is unavailable, rather than a placeholder that reads like a
    real release.
    """
    try:
        from icdev._version import __version__ as package_version

        if package_version:
            return str(package_version)
    except Exception:
        pass

    try:
        from importlib.metadata import version as _distribution_version

        installed = _distribution_version("icdev")
        if installed:
            return str(installed)
    except Exception:
        pass

    try:
        text = (BASE_DIR / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass

    return "unknown"


def _get_sbom_author(sbom_author=None):
    """SBOM Author — explicit argument, then $ICDEV_SBOM_AUTHOR, then the default."""
    if sbom_author and str(sbom_author).strip():
        return str(sbom_author).strip()
    configured = os.environ.get(SBOM_AUTHOR_ENV, "")
    if configured.strip():
        return configured.strip()
    return DEFAULT_SBOM_AUTHOR


def _rfc9557_timestamp(moment):
    """SBOM Timestamp, conformant to RFC 9557.

    RFC 9557 extends RFC 3339 with optional bracketed suffixes, so an unsuffixed
    RFC 3339 date-time is already a conformant Internet-Extended-Date/Time string.
    The suffix form is deliberately not used: CycloneDX types metadata.timestamp as
    JSON Schema `format: date-time` and SPDX 2.3 pins `created` to `...Z`, both of
    which a trailing `[UTC]` would fail.

    A naive datetime raises rather than being assumed UTC. The strftime format this
    replaces stamped a literal "Z" onto whatever it was handed, so a non-UTC clock
    produced a timestamp that was wrong and well-formed at the same time.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("SBOM timestamp requires a timezone-aware datetime (RFC 9557)")
    utc = moment.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def _spec_version_tuple(spec_version):
    """Parse "1.6" into (1, 6) for ordered comparison; (0, 0) if unparseable."""
    try:
        return tuple(int(part) for part in str(spec_version).split("."))
    except (TypeError, ValueError):
        return (0, 0)


def _revision_of(version_value):
    """Map one `sbom_records.version` string to the revision number it stands for.

    Computed in Python, not in SQL. The query this replaces was
    `MAX(CAST(CASE WHEN version GLOB '[0-9]*' ... AS REAL))` — SQLite dialect on a
    PostgreSQL-primary backend, where `translate_sql` rewrites GLOB to a POSIX `~`
    whose `[0-9]*` matches every string including the empty one, and where
    `CAST('1.0.0' AS REAL)` is a hard error rather than 1.0.

    Both spellings are understood, so the counter stays monotonic across the change:
    the legacy `"<N>.0"` float this column has always held, and the semver
    `"1.<minor>.<patch>"` of the SBOM Version element, whose revision is minor + 1.
    """
    if version_value is None:
        return 0
    text = str(version_value).strip()
    if not text:
        return 0
    parts = text.split(".")
    try:
        if len(parts) >= 3 and int(parts[0]) == SBOM_VERSION_MAJOR:
            return int(parts[1]) + 1
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _plan_sbom_version(conn, project_id):
    """Settle the SBOM Version element BEFORE the document is built.

    Returns ``(revision, record_version, sbom_version)``: the integer the CycloneDX
    document's `version` carries, the legacy `sbom_records.version` spelling of that
    same integer, and the semver spelling of the 2026 SBOM Version element. All
    three come off one predecessor lookup, so the document and the row cannot
    disagree — which they previously did, the document always saying 1 while the
    column independently counted 1.0, 2.0, 3.0.
    """
    predecessor = latest_record(conn, project_id)
    prior_semver = None
    prior_record_version = None
    if predecessor:
        prior_record_version = predecessor.get("version")
        prior_semver = predecessor.get("sbom_version") or prior_record_version

    revision = _revision_of(prior_record_version) + 1
    return revision, f"{revision}.0", next_sbom_version(prior_semver)


def _build_cyclonedx_sbom(
    project,
    components,
    serial_number=None,
    spec_version=None,
    schema=None,
    coverage=None,
    python_env=None,
    disclosure_policy=None,
    sbom_author=None,
    tool_version=None,
    document_version=1,
    sbom_version=None,
):
    """Build a CycloneDX JSON SBOM document.

    ``document_version`` is the CycloneDX integer revision counter and
    ``sbom_version`` is the same revision spelled as the standard's SBOM Version
    element. A caller that passes neither gets revision 1 and its matching semver,
    so the two agree by construction rather than by coincidence.
    """
    now = datetime.now(timezone.utc)
    active_spec_version = spec_version or CYCLONEDX_SPEC_VERSION
    active_schema = schema or CYCLONEDX_SUPPORTED_VERSIONS.get(
        active_spec_version, CYCLONEDX_SUPPORTED_VERSIONS[CYCLONEDX_SPEC_VERSION]
    )
    active_author = _get_sbom_author(sbom_author)
    active_tool_version = tool_version or _get_tool_version()
    active_document_version = int(document_version)
    active_sbom_version = sbom_version or f"{SBOM_VERSION_MAJOR}.{max(active_document_version, 1) - 1}.0"

    if serial_number is None:
        # uuid4 is the random UUID of RFC 9562 §5.4, the form the standard points at
        # for serial-number style identifiers.
        serial_number = f"urn:uuid:{uuid.uuid4()}"

    # Deduplicate by full metadata identity, not by purl: two instances that
    # differ in any emitted field are two components under the Coverage element.
    seen_identities = set()
    unique_components = []
    # Component Name (2026 minimum elements) — the element exists so that a name
    # is not lost, and deduplication is exactly where a name gets lost: two
    # instances differing only in the spelling the producer used collapse into
    # one, and the loser's spelling would vanish. Collect the spellings against
    # the identity that survives and re-attach them below.
    collapsed_spellings = {}
    for comp in components:
        identity = _component_identity(comp)
        spelling = str(comp.get("declared_name") or "").strip()
        if spelling:
            seen_spellings = collapsed_spellings.setdefault(identity, [])
            if spelling not in seen_spellings:
                seen_spellings.append(spelling)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        unique_components.append(comp)

    # Component Producer (2026 minimum elements). One context for the whole
    # document so the Python environment is indexed once and each package's
    # metadata is read once, however many instances of it the tree contains.
    project_dir = project.get("directory_path") or None
    producers = ProducerContext(project_dir=project_dir, python_env=python_env)

    # Explicitly Identifying Unknown Information (2026 minimum elements). The
    # policy carries the recipient enquiry route and any field the operator
    # deliberately withholds; unknowns are discovered below, not configured.
    policy = disclosure_policy or load_disclosure_policy()
    disclosures = []

    # Build CycloneDX components array
    cdx_components = []
    for comp in unique_components:
        # Unknown first, then policy withholding: a field the operator withholds
        # is withheld even where ICDEV also failed to establish it, because
        # "we are not telling you" is the stronger and more actionable statement.
        disclosure = _record_version_disclosure(comp, Disclosure())
        apply_component_policy(comp, policy, into=disclosure)
        disclosures.append(disclosure)

        cdx_comp = {
            "type": comp.get("type", "library"),
            "bom-ref": _generate_bom_ref(comp),
            "name": disclosure.value_for(FIELD_NAME, comp["name"]),
            "version": disclosure.value_for(FIELD_VERSION, comp["version"]),
        }
        if comp.get("group"):
            cdx_comp["group"] = comp["group"]
        if comp.get("scope"):
            cdx_comp["scope"] = comp["scope"]

        # Component License — an SPDX expression validated against the SPDX License
        # List, a URL to the full terms, a license name, or one of the two explicit
        # undisclosed markers. Never omitted, and the proprietary-conditions flag is
        # always stated rather than left to inference. Resolved AFTER the policy has
        # been applied, so a license the operator withholds is not then overwritten
        # with "unknown" and is never rendered into `licenses`.
        license_result = resolve_license(comp)
        record_license_disclosure(
            license_result,
            disclosure,
            resolved=comp.get("resolution") != RESOLUTION_DECLARED,
        )
        entries = license_entries(license_result, disclosure)
        if entries:
            cdx_comp["licenses"] = entries
        cdx_comp.setdefault("properties", []).extend(
            license_properties(license_result, disclosure)
        )

        # Component Producer — the entity that creates, defines and identifies
        # the component. Never taken from `group`, which is a namespace. Always
        # stated: a component with no identifiable producer carries the explicit
        # unknown-provenance marker instead, because the standard says silence
        # is not acceptable.
        producer = producers.resolve(comp)
        apply_producer_to_cyclonedx(cdx_comp, producer, active_spec_version)
        cdx_comp.setdefault("properties", []).extend(producer_properties(producer))

        # Component Hash Value and Algorithm — a digest of the executable artifact,
        # recomputed from a local file where one exists and otherwise repeated from the
        # digest the resolved source declared. An inaccessible artifact is stated as
        # unknown with its reason rather than the fields being dropped, and a digest
        # that is not over an artifact (go.sum's `h1:`) or is under an unapproved
        # algorithm is never adopted, only carried as unadopted evidence.
        hash_result = resolve_hash(comp)
        record_hash_disclosure(hash_result, disclosure)
        apply_hash_to_cyclonedx(cdx_comp, hash_result, disclosure)

        # An unidentifiable producer is an *unknown* field like any other, so it
        # also joins the uniform convention — a validator then reads every
        # undisclosed field of every element from one pair of property prefixes,
        # without knowing that the producer element has properties of its own.
        # A producer the policy withholds stays withheld: the bridge only adds.
        if disclosure.state_of(FIELD_PRODUCER) is None:
            disclosure_from_producer(producer, into=disclosure)
        apply_to_cyclonedx(cdx_comp, disclosure)

        # Private marker, stripped before serialization — records whether this
        # instance came from a resolved set or from a declared manifest.
        cdx_comp["_declared"] = comp.get("resolution") == RESOLUTION_DECLARED

        # Component Identifiers (2026 element, sbx-fld-05): emit ALL derivable
        # identifiers, not just the purl. apply_() writes purl/cpe/swhid/
        # omniborId natively where the active spec version has the field and
        # falls back to properties, so nothing is dropped on 1.4/1.5. The list
        # is kept on the component dict for persistence into
        # sbom_components.identifiers_json.
        identifiers = derive_identifiers(comp)
        comp["identifiers"] = identifiers
        apply_identifiers_to_cyclonedx(cdx_comp, identifiers, spec_version=active_spec_version)

        # Component Name (2026 minor update, sbx-fld-06): the format must allow
        # MULTIPLE entries so a component known by more than one name is listed
        # under all of them. `name` keeps the single primary — it feeds the
        # bom-ref — and the alternates ICDEV's own normalization and name/group
        # split would otherwise destroy travel as properties. Passed the
        # disclosure so a withheld name does not publish four other spellings
        # of itself. A spelling seen only on an instance that deduplication
        # collapsed is re-attached here: losing a name is the one thing this
        # element exists to prevent, and the dedup identity keys on the
        # normalized name, so `Flask` and `FLASK` declared in two manifests are
        # one component whose loser's spelling would otherwise vanish.
        names = derive_names(comp)
        known_names = {names["primary"]} | {entry["name"] for entry in names["alternates"]}
        for spelling in collapsed_spellings.get(_component_identity(comp), ()):
            if spelling not in known_names:
                known_names.add(spelling)
                names["alternates"].append({"name": spelling, "kind": NAME_DECLARED})
        apply_names_to_cyclonedx(cdx_comp, names, disclosure)

        cdx_components.append(cdx_comp)

    # The target component is a component too, so the element applies to it —
    # and unlike its dependencies, the operator can simply state the answer.
    target_producer = resolve_project_producer(project, project_dir=project_dir)
    target_disclosure = disclosure_from_producer(target_producer)
    apply_document_policy(policy, into=target_disclosure)

    # Unlike its dependencies, the target component's license IS declared — in the
    # project's own manifest — so it is read rather than left unknown. `resolved=True`:
    # this project is the producer, so a manifest of ours that states no license is a
    # license its producer did not provide, not one an offline build could not reach.
    target_license = resolve_declared_license(project_license_from_manifests(project_dir))
    record_license_disclosure(target_license, target_disclosure, resolved=True)

    # The target component's hash is the one this generator genuinely cannot supply. An
    # SBOM produced from source before a build has no executable artifact to hash yet —
    # that is the Generation Context this document states — so the element is the
    # explicit unknown marker naming exactly that, rather than a digest of some
    # stand-in file. Recorded before `disclosures` is read, so the document's
    # completeness statement counts it.
    target_hash = unknown_hash(REASON_TARGET_NOT_BUILT)
    record_hash_disclosure(target_hash, target_disclosure)
    disclosures.append(target_disclosure)

    target_component = {
        "type": "application",
        "bom-ref": f"icdev-{project.get('id', 'unknown')}",
        "name": target_disclosure.value_for(FIELD_NAME, project.get("name", "Unknown")),
        "version": target_disclosure.value_for(FIELD_VERSION, "0.0.0"),
        "properties": producer_properties(target_producer)
        + license_properties(target_license, target_disclosure),
    }
    target_entries = license_entries(target_license, target_disclosure)
    if target_entries:
        target_component["licenses"] = target_entries
    apply_producer_to_cyclonedx(target_component, target_producer, active_spec_version)
    apply_hash_to_cyclonedx(target_component, target_hash, target_disclosure)
    apply_to_cyclonedx(target_component, target_disclosure)

    sbom = {
        "$schema": active_schema,
        "bomFormat": "CycloneDX",
        "specVersion": active_spec_version,
        "serialNumber": serial_number,
        "version": active_document_version,
        "metadata": {
            "timestamp": _rfc9557_timestamp(now),
            # SBOM Author, in CycloneDX's own metadata.authors slot — present in
            # every spec version this module emits, unlike metadata.manufacturer
            # (1.6+). The tools[] entry below is the Tool elements and keeps naming
            # the tool's vendor; the two are deliberately separate statements.
            "authors": [{"name": active_author}],
            "tools": [
                {
                    "vendor": SBOM_TOOL_VENDOR,
                    "name": SBOM_TOOL_NAME,
                    "version": active_tool_version,
                }
            ],
            "component": target_component,
            "properties": [
                {
                    "name": "icdev:classification",
                    "value": "CUI // SP-CTI",
                },
                {
                    "name": "icdev:project-id",
                    "value": project.get("id", ""),
                },
                {
                    "name": "icdev:cui-category",
                    "value": "CTI",
                },
                {
                    "name": "icdev:distribution",
                    "value": "Distribution D -- Authorized DoD Personnel Only",
                },
                # SBOM Author again, under the key spdx_writer reads to build the
                # SPDX `Organization:` creator. metadata.authors has no SPDX 2.3
                # counterpart, so without this the element would reach one
                # serialization and not the other.
                {
                    "name": PROPERTY_SBOM_AUTHOR,
                    "value": active_author,
                },
                {
                    "name": PROPERTY_GENERATION_CONTEXT,
                    "value": SBOM_GENERATION_CONTEXT,
                },
                {
                    "name": PROPERTY_SBOM_VERSION,
                    "value": active_sbom_version,
                },
            ],
        },
        "components": cdx_components,
    }

    # SBOM Generation Context in CycloneDX's own vocabulary. metadata.lifecycles
    # arrived in 1.5, so a 1.4 document carries the context in the property above
    # only, rather than in a field its schema would reject.
    if _spec_version_tuple(active_spec_version) >= LIFECYCLES_MIN_SPEC_VERSION:
        sbom["metadata"]["lifecycles"] = [{"phase": CYCLONEDX_LIFECYCLE_PHASE}]

    # Explicitly Identifying Unknown Information — the recipient enquiry route
    # goes immediately after the classification and distribution markings, because
    # those markings *are* the withholding posture the standard's process element
    # is about: they tell a recipient what they may not have, and this tells them
    # how to ask. Emitted on every document, withholding or not.
    sbom["metadata"]["properties"].extend(enquiry_properties(policy))
    sbom["metadata"]["properties"].extend(completeness_properties(disclosures))

    # Coverage (2026 Minimum Elements) — always emitted, including when the
    # answer is "incomplete" or "unknown".
    target_bom_ref = sbom["metadata"]["component"]["bom-ref"]
    compositions, coverage_properties = _build_coverage_blocks(
        coverage or {"status": COVERAGE_UNKNOWN, "statement": "", "resolved": [], "unresolved": []},
        cdx_components,
        target_bom_ref,
    )
    sbom["metadata"]["properties"].extend(coverage_properties)
    sbom["compositions"] = compositions

    for cdx_comp in cdx_components:
        cdx_comp.pop("_declared", None)

    return sbom, len(unique_components)


def generate_sbom(
    project_id,
    sbom_format="cyclonedx",
    output_path=None,
    db_path=None,
    spec_version=None,
    python_env=None,
    build_id=None,
    sbom_author=None,
):
    """Generate a Software Bill of Materials for a project.

    Args:
        project_id: The project identifier
        sbom_format: Output format -- 'cyclonedx' or 'spdx'. The 2026 Minimum
            Elements name both; SPDX is translated from the CycloneDX document
            of the same build so the two carry identical elements.
        output_path: Override output file path
        db_path: Override database path
        spec_version: CycloneDX spec version override (D342). Applies to the
            CycloneDX document, including the one the SPDX document is
            translated from -- the producer field it selects travels through.
        python_env: Virtualenv / site-packages directory whose installed
            distributions are the Python target environment (SBOM 2026 Coverage)
        build_id: Identifier of the build this SBOM describes (SBOM 2026
            Frequency). Falls back to `$ICDEV_BUILD_ID`, then to the project
            directory's git commit, then to unknown.
        sbom_author: SBOM Author — the full name of the entity generating this
            SBOM, which is not the tool and not its vendor. Falls back to
            `$ICDEV_SBOM_AUTHOR`, then to DEFAULT_SBOM_AUTHOR.

    Returns:
        Path to the generated SBOM file
    """
    if sbom_format not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported SBOM format: {sbom_format}. Supported: {list(SUPPORTED_FORMATS)}")

    # Apply spec version override (D342 — backward-compatible)
    active_spec_version = spec_version or CYCLONEDX_SPEC_VERSION
    if active_spec_version not in CYCLONEDX_SUPPORTED_VERSIONS:
        raise ValueError(
            f"Unsupported CycloneDX spec version: {active_spec_version}. Supported: {list(CYCLONEDX_SUPPORTED_VERSIONS.keys())}"
        )
    active_schema = CYCLONEDX_SUPPORTED_VERSIONS[active_spec_version]
    active_author = _get_sbom_author(sbom_author)
    active_tool_version = _get_tool_version()

    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        project_dir = project.get("directory_path", "")

        if not project_dir or not Path(project_dir).is_dir():
            print(f"Warning: Project directory not found or not accessible: {project_dir}")
            print("Generating empty SBOM with project metadata only.")
            project_dir_path = None
        else:
            project_dir_path = Path(project_dir)

        # Resolve the transitive dependency set per ecosystem (SBOM 2026 Coverage).
        # Ecosystems that cannot be resolved offline fall back to DECLARED_PARSERS
        # and are reported as declared-only rather than silently passed off as
        # complete.
        resolution = resolve_project(
            project_dir_path,
            declared_parsers=DECLARED_PARSERS,
            python_env=python_env,
        )
        all_components = resolution["components"]
        coverage = resolution["coverage"]

        for eco in resolution["ecosystems"]:
            flag = "resolved" if eco["complete"] else "DECLARED ONLY"
            print(f"  [{flag}] {eco['ecosystem']}: {eco['component_count']} via {eco['method']}")
            if eco["reason"]:
                print(f"      {eco['reason']}")

        # SBOM Version. Settled BEFORE the document is built, so the integer the
        # document carries and the row that records it are one number written twice
        # rather than two counters that happened to be near each other.
        revision_number, new_version, new_sbom_version = _plan_sbom_version(conn, project_id)

        # Build CycloneDX SBOM. This is the document every format is derived
        # from, so an element added here reaches both serializations.
        sbom, component_count = _build_cyclonedx_sbom(
            project,
            all_components,
            spec_version=active_spec_version,
            schema=active_schema,
            coverage=coverage,
            python_env=python_env,
            sbom_author=active_author,
            tool_version=active_tool_version,
            document_version=revision_number,
            sbom_version=new_sbom_version,
        )

        document = sbom if sbom_format == FORMAT_CYCLONEDX else to_spdx(sbom)

        # Determine output path
        if output_path:
            out_file = Path(output_path)
        else:
            if project_dir_path:
                out_dir = project_dir_path / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            extension = FORMAT_EXTENSIONS[sbom_format]
            out_file = out_dir / f"sbom_{project_id}_{timestamp}.{extension}"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(document, f, indent=2)

        # SBOM Author Signature (2026 Minimum Elements, sbx-sig-01). Signed
        # after the bytes are on disk and before the row is written, so the
        # persisted signature always describes the artifact that shipped.
        signature = _sign_generated_sbom(out_file)

        # Frequency + Accommodation of Updates (2026 Minimum Elements, sbx-prc-02).
        # Resolved before the INSERT so the new row carries its link to the SBOM it
        # replaces from the moment it exists, rather than being patched afterwards —
        # and so nothing ever has to write to the predecessor.
        revision = plan_revision(
            conn,
            project_id,
            sbom,
            project_dir=project_dir_path,
            build_id=build_id,
        )

        # Record in sbom_records table. author_signature holds the base64
        # signature value; the full block — fingerprint, public key, artifact
        # hash — lives in the detached <sbom>.sig.json beside the artifact,
        # which is what a downstream consumer actually receives.
        record_columns = [
            "project_id",
            "version",
            "format",
            "file_path",
            "component_count",
            "vulnerability_count",
            "author_signature",
            "signature_algorithm",
        ]
        record_values = [
            project_id,
            new_version,
            sbom_format,
            str(out_file),
            component_count,
            0,  # Vulnerability count starts at 0; updated by security scanning
            signature["value"] if signature else None,
            signature["algorithm"] if signature else None,
        ]

        # The 2026 SBOM Metadata elements (sbx-fld-01), appended only where the
        # sbx-fnd-02 migration has landed. A database that predates it still records
        # its row and says on stderr exactly which elements are in the document but
        # not in the table — the alternative is an INSERT that names a column the
        # live schema lacks, which raises into a caller that has already written the
        # artifact to disk.
        metadata_elements = {
            "sbom_author": active_author,
            "data_format_name": SBOM_DATA_FORMAT_NAME,
            "data_format_version": active_spec_version,
            "generation_context": SBOM_GENERATION_CONTEXT,
            "tool_name": SBOM_TOOL_NAME,
            "tool_version": active_tool_version,
            "sbom_version": new_sbom_version,
            "serial_number": sbom["serialNumber"],
        }
        unpersisted = []
        for column in SBOM_RECORD_METADATA_COLUMNS:
            if column_exists(conn, "sbom_records", column):
                record_columns.append(column)
                record_values.append(metadata_elements[column])
            else:
                unpersisted.append(column)
        if unpersisted:
            print(
                f"Warning: sbom_records is missing {', '.join(unpersisted)} — those 2026 SBOM "
                "metadata elements are in the document but were not persisted. "
                "Run: python tools/db/migrate.py",
                file=sys.stderr,
            )

        revision_columns, revision_values, revision_unpersisted = revision_insert_fields(conn, revision)
        record_columns.extend(revision_columns)
        record_values.extend(revision_values)
        if revision_unpersisted:
            print(
                f"Warning: sbom_records is missing {', '.join(revision_unpersisted)} — this SBOM was "
                "recorded but its link to the one it supersedes was not, so the revision "
                "chain has a break in it. Run: python tools/db/migrate.py",
                file=sys.stderr,
            )

        # Every interpolated name is a module-level literal — the fixed base list,
        # SBOM_RECORD_METADATA_COLUMNS, or SBOM_RECORD_REVISION_COLUMNS — filtered
        # through column_exists. No caller input reaches the string; every value is
        # bound.
        cursor = conn.execute(
            f"INSERT INTO sbom_records ({', '.join(record_columns)}) "  # nosec B608
            f"VALUES ({', '.join(['%s'] * len(record_columns))})",
            tuple(record_values),
        )
        conn.commit()
        record_id = getattr(cursor, "lastrowid", None)

        # Component inventory (sbx-fld-04). Written from the FINISHED document, so the
        # table and the artifact describe the same components with the same licenses,
        # and written after the record commits so rows only ever exist for an SBOM that
        # was actually recorded. Format-independent: the SPDX serialization is a
        # translation of this same document, so the rows describe it equally.
        persisted = _persist_components(conn, sbom["components"])

        # Log audit event
        _log_audit_event(
            conn,
            project_id,
            f"SBOM v{new_version} generated",
            {
                "version": new_version,
                "sbom_version": new_sbom_version,
                "document_version": revision_number,
                "sbom_author": active_author,
                "tool_name": SBOM_TOOL_NAME,
                "tool_version": active_tool_version,
                "generation_context": SBOM_GENERATION_CONTEXT,
                "format": sbom_format,
                "component_count": component_count,
                "output_file": str(out_file),
                "serial_number": sbom["serialNumber"],
                "coverage": coverage["status"],
                "signed": bool(signature),
                "signature_algorithm": signature["algorithm"] if signature else None,
                "public_key_fp": signature["public_key_fp"] if signature else None,
                "sbom_record_id": record_id,
                "components_persisted": persisted,
                "supersedes_sbom_id": revision["supersedes_sbom_id"],
                "revision_reason": revision["revision_reason"],
                "content_digest": revision["content_digest"],
                "content_changed": revision["content_changed"],
                "source_revision": revision["source_revision"],
            },
            out_file,
        )

        format_label = (
            f"CycloneDX {active_spec_version}"
            if sbom_format == FORMAT_CYCLONEDX
            else f"{document['spdxVersion']} (translated from CycloneDX {active_spec_version})"
        )

        print("\nSBOM generated successfully:")
        print(f"  File: {out_file}")
        print(f"  Format: {format_label}")
        print(f"  Version: {new_sbom_version} (document revision {revision_number})")
        print(f"  Author: {active_author}")
        print(f"  Tool: {SBOM_TOOL_NAME} {active_tool_version}")
        print(f"  Context: {SBOM_GENERATION_CONTEXT}")
        print(f"  Components: {component_count} ({persisted} recorded in sbom_components)")
        print(f"  Serial: {sbom['serialNumber']}")
        print(f"  Coverage: {coverage['status']}")
        print(f"  Revision: {revision['revision_reason']}")
        if revision["supersedes_sbom_id"]:
            changed = "content changed" if revision["content_changed"] else "same content, re-issued"
            print(f"    Supersedes SBOM record {revision['supersedes_sbom_id']} ({changed})")
        print(f"    Build: {revision['source_revision'] or 'unknown'}")
        if coverage["status"] != COVERAGE_COMPLETE:
            print(f"  {coverage['statement']}")
        if signature:
            print(f"  Signature: {signature['signature_path']}")
            print(f"    Algorithm: {signature['algorithm']}")
            print(f"    Key fp:    {signature['public_key_fp']}")

        return str(out_file)

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Generate a Software Bill of Materials (CycloneDX or SPDX)")
    parser.add_argument("--project-id", "--project", required=True, help="Project ID", dest="project_id")
    parser.add_argument(
        "--format",
        dest="sbom_format",
        default=FORMAT_CYCLONEDX,
        choices=list(SUPPORTED_FORMATS),
        help="SBOM output format -- both are named by the 2026 Minimum Elements (default: cyclonedx)",
    )
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--db", help="Database path")
    parser.add_argument(
        "--spec-version",
        dest="spec_version",
        default=None,
        choices=list(CYCLONEDX_SUPPORTED_VERSIONS.keys()),
        help=(
            f"CycloneDX spec version (default: {CYCLONEDX_SPEC_VERSION}, D342). "
            "Older versions remain selectable for consumers whose tooling requires them."
        ),
    )
    parser.add_argument(
        "--python-env",
        dest="python_env",
        default=None,
        help=(
            "Virtualenv (or site-packages) directory to read installed Python distributions "
            "from, instead of resolving from a lockfile (SBOM 2026 Coverage)"
        ),
    )
    parser.add_argument(
        "--build-id",
        dest="build_id",
        default=None,
        help=(
            "Identifier of the build this SBOM describes (SBOM 2026 Frequency). "
            "Defaults to $ICDEV_BUILD_ID, then the project directory's git commit"
        ),
    )
    parser.add_argument(
        "--author",
        dest="sbom_author",
        default=None,
        help=(
            "SBOM Author (SBOM 2026 §1.1) -- the full name of the entity generating this "
            "SBOM, which is not the tool and not the tool's vendor. "
            f"Defaults to ${SBOM_AUTHOR_ENV}, then to '{DEFAULT_SBOM_AUTHOR}'."
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        path = generate_sbom(
            project_id=args.project_id,
            sbom_format=args.sbom_format,
            output_path=args.output,
            db_path=Path(args.db) if args.db else None,
            spec_version=args.spec_version,
            python_env=args.python_env,
            build_id=args.build_id,
            sbom_author=args.sbom_author,
        )
        print(f"\nSBOM path: {path}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
