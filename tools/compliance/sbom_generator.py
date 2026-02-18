#!/usr/bin/env python3
"""Generate CycloneDX Software Bill of Materials (SBOM).
Detects project type, parses dependency files, generates CycloneDX 1.4 JSON
format SBOM with CUI classification metadata, records in sbom_records table,
and logs audit event."""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# CycloneDX spec version
CYCLONEDX_SPEC_VERSION = "1.4"
CYCLONEDX_SCHEMA = "http://cyclonedx.org/schema/bom-1.4.schema.json"


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project data."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
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
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
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
            match = re.match(
                r'^([a-zA-Z0-9._-]+)\s*(?:([<>=!~]+)\s*([a-zA-Z0-9.*_-]+))?',
                line
            )
            if match:
                name = match.group(1).lower().replace("_", "-")
                version = match.group(3) or "unspecified"

                purl = f"pkg:pypi/{name}"
                if version != "unspecified":
                    purl += f"@{version}"

                components.append({
                    "type": "library",
                    "name": name,
                    "version": version,
                    "purl": purl,
                    "scope": "required",
                    "group": "",
                    "source": str(file_path),
                })

    return components


def _parse_pyproject_toml(file_path):
    """Parse pyproject.toml for dependencies. Returns list of component dicts."""
    components = []
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Simple parser for [project.dependencies] section
    in_deps = False
    for line in content.split("\n"):
        stripped = line.strip()

        if stripped == "[project]":
            in_deps = False
        if "dependencies" in stripped and "=" in stripped:
            # Handle inline list: dependencies = ["pkg1>=1.0", "pkg2"]
            match = re.search(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
            if match:
                deps_str = match.group(1)
                for dep in re.findall(r'"([^"]+)"|\'([^\']+)\'', deps_str):
                    dep_str = dep[0] or dep[1]
                    dep_match = re.match(r'([a-zA-Z0-9._-]+)(?:\[.*?\])?\s*(?:([<>=!~]+)\s*(.+))?', dep_str)
                    if dep_match:
                        name = dep_match.group(1).lower().replace("_", "-")
                        version = dep_match.group(3) or "unspecified"
                        # Clean up version (take first version if multiple conditions)
                        version = version.split(",")[0].strip()

                        purl = f"pkg:pypi/{name}"
                        if version != "unspecified":
                            purl += f"@{version}"

                        components.append({
                            "type": "library",
                            "name": name,
                            "version": version,
                            "purl": purl,
                            "scope": "required",
                            "group": "",
                            "source": str(file_path),
                        })
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
                version = "unspecified"

            # Handle scoped packages
            purl_name = name.replace("/", "%2F") if "/" in name else name
            purl = f"pkg:npm/{purl_name}"
            if version != "unspecified":
                purl += f"@{version}"

            group = ""
            pkg_name = name
            if name.startswith("@"):
                parts = name.split("/", 1)
                if len(parts) == 2:
                    group = parts[0]
                    pkg_name = parts[1]

            components.append({
                "type": "library",
                "name": pkg_name,
                "version": version,
                "purl": purl,
                "scope": scope,
                "group": group,
                "source": str(file_path),
            })

    return components


def _parse_package_lock_json(file_path):
    """Parse package-lock.json for exact dependency versions."""
    components = []
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # package-lock.json v2/v3 format
    packages = data.get("packages", {})
    if packages:
        for pkg_path, pkg_info in packages.items():
            if not pkg_path or pkg_path == "":
                continue  # Skip root
            name = pkg_path.replace("node_modules/", "")
            # Skip nested node_modules
            if "node_modules/" in name[1:]:
                continue
            version = pkg_info.get("version", "unspecified")

            purl_name = name.replace("/", "%2F") if "/" in name else name
            purl = f"pkg:npm/{purl_name}@{version}"

            group = ""
            pkg_name = name
            if name.startswith("@"):
                parts = name.split("/", 1)
                if len(parts) == 2:
                    group = parts[0]
                    pkg_name = parts[1]

            components.append({
                "type": "library",
                "name": pkg_name,
                "version": version,
                "purl": purl,
                "scope": "required" if not pkg_info.get("dev") else "optional",
                "group": group,
                "source": str(file_path),
            })
    else:
        # Fallback: package-lock.json v1 format
        deps = data.get("dependencies", {})
        for name, dep_info in deps.items():
            version = dep_info.get("version", "unspecified")
            purl_name = name.replace("/", "%2F") if "/" in name else name
            purl = f"pkg:npm/{purl_name}@{version}"

            group = ""
            pkg_name = name
            if name.startswith("@"):
                parts = name.split("/", 1)
                if len(parts) == 2:
                    group = parts[0]
                    pkg_name = parts[1]

            components.append({
                "type": "library",
                "name": pkg_name,
                "version": version,
                "purl": purl,
                "scope": "required" if not dep_info.get("dev") else "optional",
                "group": group,
                "source": str(file_path),
            })

    return components


def _parse_go_mod(file_path):
    """Parse Go go.mod file. Returns list of component dicts."""
    components = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return components

    # Parse parenthesized require blocks: require ( ... )
    require_blocks = re.findall(r'require\s*\((.*?)\)', content, re.DOTALL)
    for block in require_blocks:
        for line in block.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            # Remove inline comments (// indirect, etc.)
            line = re.sub(r'\s*//.*$', '', line).strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                module = parts[0]
                version = parts[1]
                purl = f"pkg:golang/{module}@{version}"
                components.append({
                    "type": "library",
                    "name": module,
                    "version": version,
                    "purl": purl,
                    "scope": "required",
                    "group": "",
                    "source": str(file_path),
                })

    # Parse single-line require statements: require github.com/foo/bar v1.2.3
    single_requires = re.findall(
        r'^require\s+(\S+)\s+(\S+)', content, re.MULTILINE
    )
    for module, version in single_requires:
        # Skip if this is the start of a parenthesized block
        if version == "(":
            continue
        version = re.sub(r'\s*//.*$', '', version).strip()
        purl = f"pkg:golang/{module}@{version}"
        components.append({
            "type": "library",
            "name": module,
            "version": version,
            "purl": purl,
            "scope": "required",
            "group": "",
            "source": str(file_path),
        })

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
        section_match = re.match(r'^\[(.+)\]$', stripped)
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
            version = simple_match.group(2) or "unspecified"
            purl = f"pkg:cargo/{name}@{version}"
            components.append({
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "scope": scope,
                "group": "",
                "source": str(file_path),
            })
            continue

        # Match: crate_name = { version = "x.y", ... }
        table_match = re.match(
            r'^([a-zA-Z0-9_-]+)\s*=\s*\{(.*)\}', stripped
        )
        if table_match:
            name = table_match.group(1)
            inner = table_match.group(2)
            version_match = re.search(r'version\s*=\s*"([^"]*)"', inner)
            version = version_match.group(1) if version_match else "unspecified"
            purl = f"pkg:cargo/{name}@{version}"
            components.append({
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "scope": scope,
                "group": "",
                "source": str(file_path),
            })
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
    dep_blocks = re.findall(
        r'<dependency>(.*?)</dependency>', content, re.DOTALL
    )
    for block in dep_blocks:
        try:
            group_match = re.search(r'<groupId>\s*(.*?)\s*</groupId>', block)
            artifact_match = re.search(r'<artifactId>\s*(.*?)\s*</artifactId>', block)

            if not group_match or not artifact_match:
                continue

            group_id = group_match.group(1).strip()
            artifact_id = artifact_match.group(1).strip()

            version_match = re.search(r'<version>\s*(.*?)\s*</version>', block)
            version = version_match.group(1).strip() if version_match else "managed"

            scope_match = re.search(r'<scope>\s*(.*?)\s*</scope>', block)
            maven_scope = scope_match.group(1).strip() if scope_match else "compile"

            # Map Maven scopes to CycloneDX scopes
            if maven_scope in ("test", "provided"):
                cdx_scope = "optional"
            else:
                cdx_scope = "required"

            purl = f"pkg:maven/{group_id}/{artifact_id}@{version}"

            components.append({
                "type": "library",
                "name": artifact_id,
                "version": version,
                "purl": purl,
                "scope": cdx_scope,
                "group": group_id,
                "source": str(file_path),
            })
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
        r'(implementation|api|compileOnly|runtimeOnly|testImplementation'
        r'|testCompileOnly|testRuntimeOnly)\s*'
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

        components.append({
            "type": "library",
            "name": artifact,
            "version": version,
            "purl": purl,
            "scope": cdx_scope,
            "group": group,
            "source": str(file_path),
        })

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
        re.compile(
            r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"\s*/?>'
        ),
        # Version before Include (some projects order differently)
        re.compile(
            r'<PackageReference\s+Version="([^"]+)"\s+Include="([^"]+)"\s*/?>'
        ),
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
            components.append({
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "scope": "required",
                "group": "",
                "source": str(file_path),
            })

    # Handle multi-line PackageReference with Version as child element
    # <PackageReference Include="Name">
    #   <Version>1.0</Version>
    # </PackageReference>
    multiline_pattern = re.compile(
        r'<PackageReference\s+Include="([^"]+)"[^/]*?>'
        r'.*?<Version>([^<]+)</Version>.*?</PackageReference>',
        re.DOTALL,
    )
    for match in multiline_pattern.finditer(content):
        name = match.group(1)
        version = match.group(2).strip()

        if name in seen:
            continue
        seen.add(name)

        purl = f"pkg:nuget/{name}@{version}"
        components.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "scope": "required",
            "group": "",
            "source": str(file_path),
        })

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
        components.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "scope": "required",
            "group": "",
            "source": str(file_path),
        })

    return components


def _generate_bom_ref(component):
    """Generate a unique BOM reference for a component."""
    key = f"{component.get('group', '')}/{component['name']}@{component['version']}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _build_cyclonedx_sbom(project, components, serial_number=None):
    """Build a CycloneDX 1.4 JSON SBOM document."""
    now = datetime.utcnow()

    if serial_number is None:
        serial_number = f"urn:uuid:{uuid.uuid4()}"

    # Deduplicate components by purl
    seen_purls = set()
    unique_components = []
    for comp in components:
        purl = comp.get("purl", "")
        if purl and purl in seen_purls:
            continue
        seen_purls.add(purl)
        unique_components.append(comp)

    # Build CycloneDX components array
    cdx_components = []
    for comp in unique_components:
        cdx_comp = {
            "type": comp.get("type", "library"),
            "bom-ref": _generate_bom_ref(comp),
            "name": comp["name"],
            "version": comp["version"],
        }
        if comp.get("group"):
            cdx_comp["group"] = comp["group"]
        if comp.get("purl"):
            cdx_comp["purl"] = comp["purl"]
        if comp.get("scope"):
            cdx_comp["scope"] = comp["scope"]
        cdx_components.append(cdx_comp)

    sbom = {
        "$schema": CYCLONEDX_SCHEMA,
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": serial_number,
        "version": 1,
        "metadata": {
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [
                {
                    "vendor": "ICDEV",
                    "name": "icdev-sbom-generator",
                    "version": "1.0.0",
                }
            ],
            "component": {
                "type": "application",
                "bom-ref": f"icdev-{project.get('id', 'unknown')}",
                "name": project.get("name", "Unknown"),
                "version": "0.0.0",
            },
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

    return sbom, len(unique_components)


def generate_sbom(
    project_id,
    sbom_format="cyclonedx",
    output_path=None,
    db_path=None,
):
    """Generate a Software Bill of Materials for a project.

    Args:
        project_id: The project identifier
        sbom_format: Output format (currently only 'cyclonedx' supported)
        output_path: Override output file path
        db_path: Override database path

    Returns:
        Path to the generated SBOM file
    """
    if sbom_format != "cyclonedx":
        raise ValueError(f"Unsupported SBOM format: {sbom_format}. Only 'cyclonedx' is supported.")

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

        # Detect project type and parse dependencies
        all_components = []

        if project_dir_path:
            detected_types = _detect_project_type(project_dir_path)
            print(f"Detected project types: {detected_types or ['none']}")

            # Parse each detected dependency file
            for ptype in detected_types:
                try:
                    if ptype == "python-requirements":
                        comps = _parse_requirements_txt(
                            project_dir_path / "requirements.txt"
                        )
                        all_components.extend(comps)
                        print(f"  Parsed requirements.txt: {len(comps)} dependencies")

                    elif ptype == "python-pyproject":
                        comps = _parse_pyproject_toml(
                            project_dir_path / "pyproject.toml"
                        )
                        all_components.extend(comps)
                        print(f"  Parsed pyproject.toml: {len(comps)} dependencies")

                    elif ptype == "javascript-package":
                        comps = _parse_package_json(
                            project_dir_path / "package.json"
                        )
                        all_components.extend(comps)
                        print(f"  Parsed package.json: {len(comps)} dependencies")

                    elif ptype == "javascript-package-lock":
                        comps = _parse_package_lock_json(
                            project_dir_path / "package-lock.json"
                        )
                        all_components.extend(comps)
                        print(f"  Parsed package-lock.json: {len(comps)} dependencies")

                    elif ptype == "go-mod":
                        dep_file = project_dir_path / "go.mod"
                        if dep_file.exists():
                            comps = _parse_go_mod(dep_file)
                            all_components.extend(comps)
                            print(f"  Parsed go.mod: {len(comps)} dependencies")

                    elif ptype == "rust-cargo":
                        dep_file = project_dir_path / "Cargo.toml"
                        if dep_file.exists():
                            comps = _parse_cargo_toml(dep_file)
                            all_components.extend(comps)
                            print(f"  Parsed Cargo.toml: {len(comps)} dependencies")

                    elif ptype == "java-maven":
                        dep_file = project_dir_path / "pom.xml"
                        if dep_file.exists():
                            comps = _parse_pom_xml(dep_file)
                            all_components.extend(comps)
                            print(f"  Parsed pom.xml: {len(comps)} dependencies")

                    elif ptype == "java-gradle":
                        for gf in ["build.gradle", "build.gradle.kts"]:
                            dep_file = project_dir_path / gf
                            if dep_file.exists():
                                comps = _parse_build_gradle(dep_file)
                                all_components.extend(comps)
                                print(f"  Parsed {gf}: {len(comps)} dependencies")
                                break

                    elif ptype == "csharp-csproj":
                        for csproj in project_dir_path.glob("*.csproj"):
                            comps = _parse_csproj(csproj)
                            all_components.extend(comps)
                            print(f"  Parsed {csproj.name}: {len(comps)} dependencies")

                    elif ptype == "csharp-packages":
                        dep_file = project_dir_path / "packages.config"
                        if dep_file.exists():
                            comps = _parse_packages_config(dep_file)
                            all_components.extend(comps)
                            print(f"  Parsed packages.config: {len(comps)} dependencies")

                except Exception as e:
                    print(f"  Warning: Failed to parse {ptype}: {e}")

        # Build CycloneDX SBOM
        sbom, component_count = _build_cyclonedx_sbom(project, all_components)

        # Determine output path
        if output_path:
            out_file = Path(output_path)
        else:
            if project_dir_path:
                out_dir = project_dir_path / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            out_file = out_dir / f"sbom_{project_id}_{timestamp}.cdx.json"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(sbom, f, indent=2)

        # Determine version
        existing = conn.execute(
            """SELECT MAX(CAST(
                 CASE WHEN version GLOB '[0-9]*' THEN version ELSE '0' END
               AS REAL)) as max_ver
               FROM sbom_records WHERE project_id = ?""",
            (project_id,),
        ).fetchone()
        max_ver = existing["max_ver"] if existing and existing["max_ver"] else 0.0
        new_version = f"{max_ver + 1.0:.1f}"

        # Record in sbom_records table
        conn.execute(
            """INSERT INTO sbom_records
               (project_id, version, format, file_path,
                component_count, vulnerability_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
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
        _log_audit_event(conn, project_id, f"SBOM v{new_version} generated", {
            "version": new_version,
            "format": sbom_format,
            "component_count": component_count,
            "output_file": str(out_file),
            "serial_number": sbom["serialNumber"],
        }, out_file)

        print(f"\nSBOM generated successfully:")
        print(f"  File: {out_file}")
        print(f"  Format: CycloneDX {CYCLONEDX_SPEC_VERSION}")
        print(f"  Version: {new_version}")
        print(f"  Components: {component_count}")
        print(f"  Serial: {sbom['serialNumber']}")

        return str(out_file)

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generate CycloneDX Software Bill of Materials (SBOM)"
    )
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument(
        "--format", dest="sbom_format", default="cyclonedx",
        choices=["cyclonedx"],
        help="SBOM output format (default: cyclonedx)"
    )
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--db", help="Database path")
    args = parser.parse_args()

    try:
        path = generate_sbom(
            project_id=args.project,
            sbom_format=args.sbom_format,
            output_path=args.output,
            db_path=Path(args.db) if args.db else None,
        )
        print(f"\nSBOM path: {path}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
