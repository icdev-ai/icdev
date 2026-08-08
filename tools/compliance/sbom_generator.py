#!/usr/bin/env python3
# CUI // SP-CTI
# CANONICAL: this is the only SBOM producer in the tree. Do not deprecate it. It backs
# ~25 live call sites (MCP sbom_generate, icdev_comply, dashboard batch API, CDRL, IronBank,
# production audit/remediate, FedRAMP KSI/packager, SWFT) and a BLOCKING bdc_canvas gate.
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Generate CycloneDX Software Bill of Materials (SBOM).
Resolves the project's transitive dependency set (SBOM 2026 Coverage element),
generates CycloneDX 1.4 JSON format SBOM with CUI classification metadata,
records in sbom_records table, and logs audit event.

Coverage (sbx-cov-01): components come from `dependency_resolver.resolve_project`,
which reads each ecosystem's *resolved* lockfile and degrades to this module's
declared-manifest parsers only where offline resolution is impossible. The
resulting SBOM always carries an explicit coverage statement — `compositions`
plus `icdev:sbom:coverage*` properties — so a partial tree is never presented as
complete."""

import argparse
import hashlib
import json
import re
import sys
import uuid
from tools.compliance.component_producer import (
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
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# CycloneDX spec version (default 1.4, supports 1.4-1.7 via --spec-version)
CYCLONEDX_SPEC_VERSION = "1.4"
CYCLONEDX_SCHEMA = "http://cyclonedx.org/schema/bom-1.4.schema.json"

# Supported CycloneDX spec versions (D342)
CYCLONEDX_SUPPORTED_VERSIONS = {
    "1.4": "http://cyclonedx.org/schema/bom-1.4.schema.json",
    "1.5": "http://cyclonedx.org/schema/bom-1.5.schema.json",
    "1.6": "http://cyclonedx.org/schema/bom-1.6.schema.json",
    "1.7": "http://cyclonedx.org/schema/bom-1.7.schema.json",
}

# Coverage status -> CycloneDX `compositions[].aggregate` vocabulary (spec 1.3+,
# so this is valid across every version in CYCLONEDX_SUPPORTED_VERSIONS).
COVERAGE_AGGREGATE = {
    COVERAGE_COMPLETE: "complete",
    COVERAGE_INCOMPLETE: "incomplete",
    COVERAGE_UNKNOWN: "unknown",
}


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
                name = match.group(1).lower().replace("_", "-")
                version = match.group(3) or UNKNOWN

                purl = f"pkg:pypi/{name}"
                if version != UNKNOWN:
                    purl += f"@{version}"

                components.append(
                    {
                        "type": "library",
                        "name": name,
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
                        name = dep_match.group(1).lower().replace("_", "-")
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
            purl_name = name.replace("/", "%2F") if "/" in name else name
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
    single_requires = re.findall(r"^require\s+(\S+)\s+(\S+)", content, re.MULTILINE)
    for module, version in single_requires:
        # Skip if this is the start of a parenthesized block
        if version == "(":
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
    """Generate a unique BOM reference for a component instance."""
    key = "|".join(_component_identity(component))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


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


def _build_cyclonedx_sbom(
    project,
    components,
    serial_number=None,
    spec_version=None,
    schema=None,
    coverage=None,
    python_env=None,
    disclosure_policy=None,
):
    """Build a CycloneDX JSON SBOM document."""
    now = datetime.now(timezone.utc)
    active_spec_version = spec_version or CYCLONEDX_SPEC_VERSION
    active_schema = schema or CYCLONEDX_SUPPORTED_VERSIONS.get(
        active_spec_version, CYCLONEDX_SUPPORTED_VERSIONS[CYCLONEDX_SPEC_VERSION]
    )

    if serial_number is None:
        serial_number = f"urn:uuid:{uuid.uuid4()}"

    # Deduplicate by full metadata identity, not by purl: two instances that
    # differ in any emitted field are two components under the Coverage element.
    seen_identities = set()
    unique_components = []
    for comp in components:
        identity = _component_identity(comp)
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
        if comp.get("purl"):
            cdx_comp["purl"] = comp["purl"]
        if comp.get("scope"):
            cdx_comp["scope"] = comp["scope"]

        # Component Producer — the entity that creates, defines and identifies
        # the component. Never taken from `group`, which is a namespace. Always
        # stated: a component with no identifiable producer carries the explicit
        # unknown-provenance marker instead, because the standard says silence
        # is not acceptable.
        producer = producers.resolve(comp)
        apply_producer_to_cyclonedx(cdx_comp, producer, active_spec_version)
        cdx_comp.setdefault("properties", []).extend(producer_properties(producer))

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
        cdx_components.append(cdx_comp)

    # The target component is a component too, so the element applies to it —
    # and unlike its dependencies, the operator can simply state the answer.
    target_producer = resolve_project_producer(project, project_dir=project_dir)
    target_disclosure = disclosure_from_producer(target_producer)
    apply_document_policy(policy, into=target_disclosure)
    disclosures.append(target_disclosure)

    target_component = {
        "type": "application",
        "bom-ref": f"icdev-{project.get('id', 'unknown')}",
        "name": target_disclosure.value_for(FIELD_NAME, project.get("name", "Unknown")),
        "version": target_disclosure.value_for(FIELD_VERSION, "0.0.0"),
        "properties": producer_properties(target_producer),
    }
    apply_producer_to_cyclonedx(target_component, target_producer, active_spec_version)
    apply_to_cyclonedx(target_component, target_disclosure)

    sbom = {
        "$schema": active_schema,
        "bomFormat": "CycloneDX",
        "specVersion": active_spec_version,
        "serialNumber": serial_number,
        "version": 1,
        "metadata": {
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [
                {
                    "vendor": "ICDEV™",
                    "name": "icdev-sbom-generator",
                    "version": "1.0.0",
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
            ],
        },
        "components": cdx_components,
    }

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
):
    """Generate a Software Bill of Materials for a project.

    Args:
        project_id: The project identifier
        sbom_format: Output format (currently only 'cyclonedx' supported)
        output_path: Override output file path
        db_path: Override database path
        spec_version: CycloneDX spec version override (D342)
        python_env: Virtualenv / site-packages directory whose installed
            distributions are the Python target environment (SBOM 2026 Coverage)

    Returns:
        Path to the generated SBOM file
    """
    if sbom_format != "cyclonedx":
        raise ValueError(f"Unsupported SBOM format: {sbom_format}. Only 'cyclonedx' is supported.")

    # Apply spec version override (D342 — backward-compatible)
    active_spec_version = spec_version or CYCLONEDX_SPEC_VERSION
    if active_spec_version not in CYCLONEDX_SUPPORTED_VERSIONS:
        raise ValueError(
            f"Unsupported CycloneDX spec version: {active_spec_version}. Supported: {list(CYCLONEDX_SUPPORTED_VERSIONS.keys())}"
        )
    active_schema = CYCLONEDX_SUPPORTED_VERSIONS[active_spec_version]

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

        # Build CycloneDX SBOM
        sbom, component_count = _build_cyclonedx_sbom(
            project,
            all_components,
            spec_version=active_spec_version,
            schema=active_schema,
            coverage=coverage,
            python_env=python_env,
        )

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
            out_file = out_dir / f"sbom_{project_id}_{timestamp}.cdx.json"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(sbom, f, indent=2)

        # Determine version
        existing = conn.execute(
            """SELECT MAX(CAST(
                 CASE WHEN version GLOB '[0-9]*' THEN version ELSE '0' END
               AS REAL)) as max_ver
               FROM sbom_records WHERE project_id = %s""",
            (project_id,),
        ).fetchone()
        max_ver = existing["max_ver"] if existing and existing["max_ver"] else 0.0
        new_version = f"{max_ver + 1.0:.1f}"

        # Record in sbom_records table
        conn.execute(
            """INSERT INTO sbom_records
               (project_id, version, format, file_path,
                component_count, vulnerability_count)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                project_id,
                new_version,
                sbom_format,
                str(out_file),
                component_count,
                0,  # Vulnerability count starts at 0; updated by security scanning
            ),
        )
        conn.commit()

        # Log audit event
        _log_audit_event(
            conn,
            project_id,
            f"SBOM v{new_version} generated",
            {
                "version": new_version,
                "format": sbom_format,
                "component_count": component_count,
                "output_file": str(out_file),
                "serial_number": sbom["serialNumber"],
                "coverage": coverage["status"],
            },
            out_file,
        )

        print("\nSBOM generated successfully:")
        print(f"  File: {out_file}")
        print(f"  Format: CycloneDX {active_spec_version}")
        print(f"  Version: {new_version}")
        print(f"  Components: {component_count}")
        print(f"  Serial: {sbom['serialNumber']}")
        print(f"  Coverage: {coverage['status']}")
        if coverage["status"] != COVERAGE_COMPLETE:
            print(f"  {coverage['statement']}")

        return str(out_file)

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Generate CycloneDX Software Bill of Materials (SBOM)")
    parser.add_argument("--project-id", "--project", required=True, help="Project ID", dest="project_id")
    parser.add_argument(
        "--format",
        dest="sbom_format",
        default="cyclonedx",
        choices=["cyclonedx"],
        help="SBOM output format (default: cyclonedx)",
    )
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--db", help="Database path")
    parser.add_argument(
        "--spec-version",
        dest="spec_version",
        default=None,
        choices=["1.4", "1.5", "1.6", "1.7"],
        help="CycloneDX spec version (default: 1.4, D342)",
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
        )
        print(f"\nSBOM path: {path}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
