#!/usr/bin/env python3
# CUI // SP-CTI
"""Dependency Scanner — inventories all project dependencies with version staleness.

For each detected language:
- Parse dependency files (requirements.txt, pom.xml, go.mod, Cargo.toml, package.json, *.csproj)
- Query package registries for latest versions (PyPI, npm, Maven Central, crates.io, pkg.go.dev, NuGet)
- Calculate days_stale per dependency
- Store in dependency_inventory table

CLI: python tools/maintenance/dependency_scanner.py --project-id <id> [--language <lang>] [--offline] [--json]
"""

import argparse
import json
import re
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
MAINTENANCE_CONFIG_PATH = BASE_DIR / "args" / "maintenance_config.yaml"

# HTTP request timeout in seconds
HTTP_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Standard ICDEV helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection with Row factory."""
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
    """Load project from projects table."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _log_audit_event(conn, project_id, action, details):
    """Append-only audit trail entry, event_type='dependency_scanned'."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "dependency_scanned",
                "icdev-maintenance-engine",
                action,
                json.dumps(details) if isinstance(details, dict) else str(details),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: Could not log audit event: {exc}", file=sys.stderr)


def _load_maintenance_config():
    """Load args/maintenance_config.yaml using a simple line-based parser (no pyyaml)."""
    defaults = {
        "staleness": {
            "warning_days": 90,
            "critical_days": 180,
            "max_acceptable_days": 365,
        },
        "registries": {
            "pypi": "https://pypi.org/pypi/{package}/json",
            "npm": "https://registry.npmjs.org/{package}",
            "maven": "https://search.maven.org/solrsearch/select?q=g:{group}+AND+a:{artifact}&rows=1&wt=json",
            "crates": "https://crates.io/api/v1/crates/{crate}",
            "go": "https://proxy.golang.org/{module}/@latest",
            "nuget": "https://api.nuget.org/v3-flatcontainer/{package}/index.json",
        },
    }
    if not MAINTENANCE_CONFIG_PATH.exists():
        return defaults

    try:
        content = MAINTENANCE_CONFIG_PATH.read_text(encoding="utf-8")
        config = {}
        current_section = None
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Detect top-level section (no leading whitespace, ends with colon, no value)
            if not line.startswith(" ") and not line.startswith("\t") and stripped.endswith(":"):
                current_section = stripped[:-1].strip()
                config[current_section] = {}
                continue
            if current_section and ":" in stripped:
                parts = stripped.split(":", 1)
                key = parts[0].strip()
                val = parts[1].strip().strip('"').strip("'")
                # Try int, then float, then keep as string
                try:
                    config[current_section][key] = int(val)
                except ValueError:
                    try:
                        config[current_section][key] = float(val)
                    except ValueError:
                        config[current_section][key] = val
        # Merge with defaults
        for section_key in defaults:
            if section_key in config:
                merged = dict(defaults[section_key])
                merged.update(config[section_key])
                defaults[section_key] = merged
        return defaults
    except Exception:
        return defaults


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------

def _detect_project_languages(project_dir):
    """Detect languages using language_support module if available, else basic detection."""
    try:
        import importlib.util
        ls_path = BASE_DIR / "tools" / "builder" / "language_support.py"
        if ls_path.exists():
            spec = importlib.util.spec_from_file_location("language_support", ls_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.detect_languages(str(project_dir))
    except Exception:
        pass
    # Fallback: basic file-based detection
    languages = []
    if any((project_dir / f).exists() for f in ["requirements.txt", "pyproject.toml", "setup.py"]):
        languages.append("python")
    if (project_dir / "package.json").exists():
        languages.append("javascript")
    if (project_dir / "go.mod").exists():
        languages.append("go")
    if (project_dir / "Cargo.toml").exists():
        languages.append("rust")
    if (project_dir / "pom.xml").exists() or list(project_dir.glob("build.gradle*")):
        languages.append("java")
    if list(project_dir.glob("*.csproj")):
        languages.append("csharp")
    return languages


# ---------------------------------------------------------------------------
# Dependency Parsers (one per language)
# Each returns List[Dict] with: package_name, current_version, dependency_file, scope, direct
# ---------------------------------------------------------------------------

def _parse_python_deps(project_dir):
    """Parse requirements.txt and pyproject.toml [project.dependencies]."""
    deps = []
    # --- requirements.txt ---
    req_file = project_dir / "requirements.txt"
    if req_file.exists():
        try:
            content = req_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # Handle inline comments
                if "#" in line:
                    line = line[:line.index("#")].strip()
                # Match patterns: pkg==1.0, pkg>=1.0, pkg~=1.0, pkg!=1.0, pkg<=1.0, pkg<1.0, pkg>1.0
                match = re.match(r'^([A-Za-z0-9_][A-Za-z0-9._-]*)\s*([><=!~]+)\s*([^\s,;]+)', line)
                if match:
                    deps.append({
                        "package_name": match.group(1).strip(),
                        "current_version": match.group(3).strip(),
                        "dependency_file": str(req_file),
                        "scope": "required",
                        "direct": True,
                    })
                else:
                    # Package without version specifier
                    name_match = re.match(r'^([A-Za-z0-9_][A-Za-z0-9._-]*)\s*$', line)
                    if name_match:
                        deps.append({
                            "package_name": name_match.group(1).strip(),
                            "current_version": "unknown",
                            "dependency_file": str(req_file),
                            "scope": "required",
                            "direct": True,
                        })
        except Exception:
            pass

    # --- pyproject.toml [project.dependencies] ---
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped == "dependencies = [" or stripped.startswith("dependencies = ["):
                    in_deps = True
                    # Handle single-line list
                    if "]" in stripped:
                        inner = stripped.split("[", 1)[1].rsplit("]", 1)[0]
                        for item in inner.split(","):
                            item = item.strip().strip('"').strip("'")
                            if item:
                                m = re.match(r'^([A-Za-z0-9_][A-Za-z0-9._-]*)\s*([><=!~]+)\s*([^\s,;"\']+)', item)
                                if m:
                                    deps.append({
                                        "package_name": m.group(1),
                                        "current_version": m.group(3),
                                        "dependency_file": str(pyproject),
                                        "scope": "required",
                                        "direct": True,
                                    })
                        in_deps = False
                    continue
                if in_deps:
                    if "]" in stripped:
                        in_deps = False
                    item = stripped.strip('",').strip("',").strip()
                    if item:
                        m = re.match(r'^([A-Za-z0-9_][A-Za-z0-9._-]*)\s*([><=!~]+)\s*([^\s,;"\']+)', item)
                        if m:
                            # Avoid duplicates from requirements.txt
                            if not any(d["package_name"] == m.group(1) for d in deps):
                                deps.append({
                                    "package_name": m.group(1),
                                    "current_version": m.group(3),
                                    "dependency_file": str(pyproject),
                                    "scope": "required",
                                    "direct": True,
                                })
        except Exception:
            pass

    return deps


def _parse_javascript_deps(project_dir):
    """Parse package.json dependencies + devDependencies."""
    deps = []
    pkg_file = project_dir / "package.json"
    if not pkg_file.exists():
        return deps
    try:
        content = json.loads(pkg_file.read_text(encoding="utf-8"))
        for section, scope in [("dependencies", "required"), ("devDependencies", "dev")]:
            section_data = content.get(section, {})
            for name, version_spec in section_data.items():
                # Strip leading ^, ~, >=, etc. to get the base version
                clean_version = re.sub(r'^[^0-9]*', '', version_spec)
                deps.append({
                    "package_name": name,
                    "current_version": clean_version if clean_version else version_spec,
                    "dependency_file": str(pkg_file),
                    "scope": scope,
                    "direct": True,
                })
    except Exception:
        pass
    return deps


def _parse_java_deps(project_dir):
    """Parse pom.xml <dependency> blocks or build.gradle."""
    deps = []
    # --- pom.xml ---
    pom = project_dir / "pom.xml"
    if pom.exists():
        try:
            content = pom.read_text(encoding="utf-8")
            # Simple regex-based XML parsing (no lxml dependency)
            dep_blocks = re.findall(
                r'<dependency>\s*(.*?)\s*</dependency>',
                content, re.DOTALL
            )
            for block in dep_blocks:
                group_match = re.search(r'<groupId>\s*([^<]+)\s*</groupId>', block)
                artifact_match = re.search(r'<artifactId>\s*([^<]+)\s*</artifactId>', block)
                version_match = re.search(r'<version>\s*([^<]+)\s*</version>', block)
                scope_match = re.search(r'<scope>\s*([^<]+)\s*</scope>', block)
                if group_match and artifact_match:
                    pkg_name = f"{group_match.group(1).strip()}:{artifact_match.group(1).strip()}"
                    version = version_match.group(1).strip() if version_match else "unknown"
                    # Skip property references like ${project.version}
                    if version.startswith("${"):
                        version = "unknown"
                    scope = scope_match.group(1).strip() if scope_match else "compile"
                    deps.append({
                        "package_name": pkg_name,
                        "current_version": version,
                        "dependency_file": str(pom),
                        "scope": scope,
                        "direct": True,
                    })
        except Exception:
            pass

    # --- build.gradle ---
    for gradle_name in ["build.gradle", "build.gradle.kts"]:
        gradle_file = project_dir / gradle_name
        if gradle_file.exists():
            try:
                content = gradle_file.read_text(encoding="utf-8")
                # Match: implementation 'group:artifact:version' or implementation("group:artifact:version")
                pattern = r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*[\('\"]([^'\"]+):([^'\"]+):([^'\"]+)[\'\")]"
                for match in re.finditer(pattern, content):
                    pkg_name = f"{match.group(1)}:{match.group(2)}"
                    version = match.group(3)
                    deps.append({
                        "package_name": pkg_name,
                        "current_version": version,
                        "dependency_file": str(gradle_file),
                        "scope": "compile",
                        "direct": True,
                    })
            except Exception:
                pass
    return deps


def _parse_go_deps(project_dir):
    """Parse go.mod require blocks."""
    deps = []
    gomod = project_dir / "go.mod"
    if not gomod.exists():
        return deps
    try:
        content = gomod.read_text(encoding="utf-8")
        in_require = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if in_require and stripped == ")":
                in_require = False
                continue
            # Single-line require: require github.com/foo/bar v1.2.3
            if stripped.startswith("require ") and "(" not in stripped:
                parts = stripped[len("require "):].strip().split()
                if len(parts) >= 2:
                    deps.append({
                        "package_name": parts[0],
                        "current_version": parts[1],
                        "dependency_file": str(gomod),
                        "scope": "required",
                        "direct": True,
                    })
                continue
            if in_require and stripped and not stripped.startswith("//"):
                parts = stripped.split()
                if len(parts) >= 2:
                    indirect = "// indirect" in stripped
                    deps.append({
                        "package_name": parts[0],
                        "current_version": parts[1],
                        "dependency_file": str(gomod),
                        "scope": "required",
                        "direct": not indirect,
                    })
    except Exception:
        pass
    return deps


def _parse_rust_deps(project_dir):
    """Parse Cargo.toml [dependencies] + [dev-dependencies]."""
    deps = []
    cargo = project_dir / "Cargo.toml"
    if not cargo.exists():
        return deps
    try:
        content = cargo.read_text(encoding="utf-8")
        current_section = None
        for line in content.splitlines():
            stripped = line.strip()
            # Detect section headers
            section_match = re.match(r'^\[([^\]]+)\]', stripped)
            if section_match:
                current_section = section_match.group(1).strip()
                continue
            if current_section in ("dependencies", "dev-dependencies"):
                scope = "required" if current_section == "dependencies" else "dev"
                # Simple form: name = "version"
                simple_match = re.match(r'^([A-Za-z0-9_-]+)\s*=\s*"([^"]+)"', stripped)
                if simple_match:
                    deps.append({
                        "package_name": simple_match.group(1),
                        "current_version": simple_match.group(2),
                        "dependency_file": str(cargo),
                        "scope": scope,
                        "direct": True,
                    })
                    continue
                # Table form: name = { version = "1.0", ... }
                table_match = re.match(r'^([A-Za-z0-9_-]+)\s*=\s*\{(.*)\}', stripped)
                if table_match:
                    pkg_name = table_match.group(1)
                    inner = table_match.group(2)
                    ver_match = re.search(r'version\s*=\s*"([^"]+)"', inner)
                    version = ver_match.group(1) if ver_match else "unknown"
                    deps.append({
                        "package_name": pkg_name,
                        "current_version": version,
                        "dependency_file": str(cargo),
                        "scope": scope,
                        "direct": True,
                    })
    except Exception:
        pass
    return deps


def _parse_csharp_deps(project_dir):
    """Parse *.csproj PackageReference elements."""
    deps = []
    for csproj in project_dir.glob("*.csproj"):
        try:
            content = csproj.read_text(encoding="utf-8")
            # Match <PackageReference Include="Name" Version="1.0" />
            for match in re.finditer(
                r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"',
                content,
            ):
                deps.append({
                    "package_name": match.group(1),
                    "current_version": match.group(2),
                    "dependency_file": str(csproj),
                    "scope": "required",
                    "direct": True,
                })
        except Exception:
            pass
    # Also check nested directories (common in solution structures)
    for csproj in project_dir.glob("**/*.csproj"):
        # Skip if already processed at root level
        if csproj.parent == project_dir:
            continue
        try:
            content = csproj.read_text(encoding="utf-8")
            for match in re.finditer(
                r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"',
                content,
            ):
                pkg_name = match.group(1)
                # Avoid duplicates
                if not any(d["package_name"] == pkg_name and d["dependency_file"] == str(csproj) for d in deps):
                    deps.append({
                        "package_name": pkg_name,
                        "current_version": match.group(2),
                        "dependency_file": str(csproj),
                        "scope": "required",
                        "direct": True,
                    })
        except Exception:
            pass
    return deps


# Map language names to their parser functions
_LANGUAGE_PARSERS = {
    "python": _parse_python_deps,
    "javascript": _parse_javascript_deps,
    "typescript": _parse_javascript_deps,
    "java": _parse_java_deps,
    "go": _parse_go_deps,
    "rust": _parse_rust_deps,
    "csharp": _parse_csharp_deps,
}


# ---------------------------------------------------------------------------
# Registry Checkers (one per language)
# Each returns (latest_version: str | None, release_date: str | None)
# ---------------------------------------------------------------------------

def _http_get_json(url, headers=None):
    """Perform an HTTP GET and return parsed JSON, or None on failure."""
    try:
        req = urllib.request.Request(url)
        if headers:
            for key, val in headers.items():
                req.add_header(key, val)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            OSError, ValueError, TimeoutError):
        return None


def _check_pypi_latest(package_name):
    """GET https://pypi.org/pypi/{package}/json -> .info.version"""
    url = f"https://pypi.org/pypi/{package_name}/json"
    data = _http_get_json(url)
    if data and "info" in data:
        version = data["info"].get("version")
        # Try to extract release date for the latest version
        release_date = None
        releases = data.get("releases", {})
        if version and version in releases and releases[version]:
            upload_time = releases[version][-1].get("upload_time")
            if upload_time:
                release_date = upload_time[:10]  # YYYY-MM-DD
        return (version, release_date)
    return (None, None)


def _check_npm_latest(package_name):
    """GET https://registry.npmjs.org/{package} -> ["dist-tags"]["latest"]"""
    url = f"https://registry.npmjs.org/{package_name}"
    data = _http_get_json(url)
    if data and "dist-tags" in data:
        version = data["dist-tags"].get("latest")
        release_date = None
        time_data = data.get("time", {})
        if version and version in time_data:
            release_date = time_data[version][:10]
        return (version, release_date)
    return (None, None)


def _check_maven_latest(group_id, artifact_id):
    """GET Maven Central search API -> parse response.docs[0].latestVersion"""
    url = (
        f"https://search.maven.org/solrsearch/select?"
        f"q=g:{group_id}+AND+a:{artifact_id}&rows=1&wt=json"
    )
    data = _http_get_json(url)
    if data and "response" in data:
        docs = data["response"].get("docs", [])
        if docs:
            version = docs[0].get("latestVersion")
            timestamp = docs[0].get("timestamp")
            release_date = None
            if timestamp:
                try:
                    release_date = datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d")
                except (OSError, ValueError, OverflowError):
                    pass
            return (version, release_date)
    return (None, None)


def _check_crates_latest(crate_name):
    """GET https://crates.io/api/v1/crates/{crate} -> .crate.max_version"""
    url = f"https://crates.io/api/v1/crates/{crate_name}"
    # crates.io requires a User-Agent header
    data = _http_get_json(url, headers={"User-Agent": "icdev-dependency-scanner/1.0"})
    if data and "crate" in data:
        version = data["crate"].get("max_version") or data["crate"].get("newest_version")
        updated = data["crate"].get("updated_at")
        release_date = updated[:10] if updated else None
        return (version, release_date)
    return (None, None)


def _check_go_latest(module_path):
    """GET https://proxy.golang.org/{module}/@latest -> .Version"""
    # URL-encode the module path (capital letters need special encoding for Go proxy)
    encoded = module_path.replace("/", "/")
    url = f"https://proxy.golang.org/{encoded}/@latest"
    data = _http_get_json(url)
    if data:
        version = data.get("Version")
        time_str = data.get("Time")
        release_date = time_str[:10] if time_str else None
        return (version, release_date)
    return (None, None)


def _check_nuget_latest(package_name):
    """GET https://api.nuget.org/v3-flatcontainer/{package}/index.json -> last version"""
    lower_name = package_name.lower()
    url = f"https://api.nuget.org/v3-flatcontainer/{lower_name}/index.json"
    data = _http_get_json(url)
    if data and "versions" in data:
        versions = data["versions"]
        if versions:
            return (versions[-1], None)
    return (None, None)


def _check_latest_version(language, package_name):
    """Dispatch to the correct registry checker based on language.

    Returns:
        (latest_version: str | None, release_date: str | None)
    """
    lang = language.lower()
    try:
        if lang == "python":
            return _check_pypi_latest(package_name)
        elif lang in ("javascript", "typescript"):
            return _check_npm_latest(package_name)
        elif lang == "java":
            # Java packages are group:artifact
            parts = package_name.split(":", 1)
            if len(parts) == 2:
                return _check_maven_latest(parts[0], parts[1])
            return (None, None)
        elif lang == "rust":
            return _check_crates_latest(package_name)
        elif lang == "go":
            return _check_go_latest(package_name)
        elif lang == "csharp":
            return _check_nuget_latest(package_name)
    except Exception:
        pass
    return (None, None)


# ---------------------------------------------------------------------------
# Staleness Calculator
# ---------------------------------------------------------------------------

def _parse_version_tuple(version_str):
    """Parse a version string into a tuple of integers for comparison.

    Returns a list of integer parts, e.g. "1.2.3" -> [1, 2, 3].
    Non-numeric parts are ignored.
    """
    if not version_str:
        return []
    parts = []
    for segment in re.split(r'[.\-+]', version_str):
        # Take only the leading numeric portion
        num_match = re.match(r'^(\d+)', segment)
        if num_match:
            parts.append(int(num_match.group(1)))
    return parts


def _calculate_staleness(current_version, latest_version):
    """Simple version comparison. Returns estimated days_stale (0 if current, -1 if unknown).

    Uses a heuristic: each major version difference = ~180 days, minor = ~30 days, patch = ~7 days.
    This is a rough estimate; actual staleness requires release date comparison.
    """
    if not latest_version:
        return -1
    if not current_version or current_version == "unknown":
        return -1
    if current_version == latest_version:
        return 0

    current_parts = _parse_version_tuple(current_version)
    latest_parts = _parse_version_tuple(latest_version)

    if not current_parts or not latest_parts:
        return -1

    # Pad to same length
    max_len = max(len(current_parts), len(latest_parts))
    while len(current_parts) < max_len:
        current_parts.append(0)
    while len(latest_parts) < max_len:
        latest_parts.append(0)

    # If current is >= latest, not stale
    if current_parts >= latest_parts:
        return 0

    # Estimate staleness based on version distance
    days = 0
    if len(current_parts) >= 1 and len(latest_parts) >= 1:
        major_diff = latest_parts[0] - current_parts[0]
        if major_diff > 0:
            days += major_diff * 180
    if len(current_parts) >= 2 and len(latest_parts) >= 2:
        minor_diff = latest_parts[1] - current_parts[1]
        if minor_diff > 0:
            days += minor_diff * 30
    if len(current_parts) >= 3 and len(latest_parts) >= 3:
        patch_diff = latest_parts[2] - current_parts[2]
        if patch_diff > 0:
            days += patch_diff * 7

    return max(days, 1)  # At least 1 day stale if versions differ


# ---------------------------------------------------------------------------
# PURL Generation
# ---------------------------------------------------------------------------

def _generate_purl(language, package_name, version):
    """Generate a Package URL (PURL) for a dependency.

    Specification: https://github.com/package-url/purl-spec
    """
    lang = language.lower()
    ver = version if version and version != "unknown" else ""
    ver_suffix = f"@{ver}" if ver else ""

    if lang == "python":
        return f"pkg:pypi/{package_name}{ver_suffix}"
    elif lang in ("javascript", "typescript"):
        # Handle scoped packages: @scope/name -> pkg:npm/%40scope/name
        if package_name.startswith("@"):
            encoded = package_name.replace("@", "%40", 1)
            return f"pkg:npm/{encoded}{ver_suffix}"
        return f"pkg:npm/{package_name}{ver_suffix}"
    elif lang == "java":
        # group:artifact -> pkg:maven/group/artifact
        parts = package_name.split(":", 1)
        if len(parts) == 2:
            return f"pkg:maven/{parts[0]}/{parts[1]}{ver_suffix}"
        return f"pkg:maven/{package_name}{ver_suffix}"
    elif lang == "rust":
        return f"pkg:cargo/{package_name}{ver_suffix}"
    elif lang == "go":
        return f"pkg:golang/{package_name}{ver_suffix}"
    elif lang == "csharp":
        return f"pkg:nuget/{package_name}{ver_suffix}"
    else:
        return f"pkg:generic/{package_name}{ver_suffix}"


# ---------------------------------------------------------------------------
# Database Storage
# ---------------------------------------------------------------------------

def _store_dependency(conn, project_id, language, dep, latest_version, days_stale, purl):
    """INSERT OR REPLACE a dependency into the dependency_inventory table."""
    try:
        conn.execute(
            """INSERT OR REPLACE INTO dependency_inventory
               (project_id, language, package_name, current_version,
                latest_version, latest_check_date, days_stale, purl,
                scope, dependency_file, direct, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                project_id,
                language,
                dep["package_name"],
                dep["current_version"],
                latest_version,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                days_stale,
                purl,
                dep.get("scope", "required"),
                dep.get("dependency_file", ""),
                1 if dep.get("direct", True) else 0,
            ),
        )
    except Exception as exc:
        print(
            f"Warning: Could not store dependency {dep['package_name']}: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Main Scanner
# ---------------------------------------------------------------------------

def scan_dependencies(project_id, language=None, offline=False, project_dir=None, db_path=None):
    """Scan all dependencies for a project, check latest versions, store in DB.

    Args:
        project_id: Project ID in the database.
        language: Specific language to scan (None = all detected).
        offline: If True, skip registry checks (for air-gapped environments).
        project_dir: Override project directory (default: from DB).
        db_path: Override database path.

    Returns:
        dict with: project_id, languages_scanned, total_dependencies, outdated_count,
                    dependencies (list), scan_date
    """
    conn = _get_connection(Path(db_path) if db_path else None)
    try:
        # 1. Load project
        project = _get_project(conn, project_id)

        # 2. Resolve project directory
        if project_dir:
            proj_dir = Path(project_dir)
        else:
            dir_path = project.get("directory_path", "")
            if not dir_path:
                raise ValueError(
                    f"Project '{project_id}' has no directory_path set and "
                    "--project-dir was not provided."
                )
            proj_dir = Path(dir_path)

        if not proj_dir.is_dir():
            raise FileNotFoundError(f"Project directory not found: {proj_dir}")

        # 3. Detect languages
        if language:
            languages = [language.lower()]
        else:
            languages = _detect_project_languages(proj_dir)

        if not languages:
            return {
                "project_id": project_id,
                "languages_scanned": [],
                "total_dependencies": 0,
                "outdated_count": 0,
                "dependencies": [],
                "scan_date": datetime.now(timezone.utc).isoformat(),
                "message": "No supported languages detected in project directory.",
            }

        # Load config for staleness thresholds
        config = _load_maintenance_config()
        staleness_config = config.get("staleness", {})

        all_deps = []
        outdated_count = 0
        scanned_languages = []

        # 4. For each language, parse dependency files
        for lang in languages:
            parser = _LANGUAGE_PARSERS.get(lang)
            if not parser:
                continue
            scanned_languages.append(lang)
            parsed = parser(proj_dir)

            # 5. Check each dep against registry for latest version
            for dep in parsed:
                latest_version = None
                release_date = None
                days_stale = -1

                if not offline:
                    try:
                        latest_version, release_date = _check_latest_version(
                            lang, dep["package_name"]
                        )
                    except Exception:
                        latest_version = None
                        release_date = None

                # 6. Calculate staleness
                if latest_version:
                    days_stale = _calculate_staleness(dep["current_version"], latest_version)
                else:
                    days_stale = -1  # Unknown

                # 8. Generate PURL
                purl = _generate_purl(lang, dep["package_name"], dep["current_version"])

                # Determine staleness category
                warning_days = staleness_config.get("warning_days", 90)
                critical_days = staleness_config.get("critical_days", 180)
                if days_stale > 0:
                    outdated_count += 1

                staleness_category = "current"
                if days_stale > critical_days:
                    staleness_category = "critical"
                elif days_stale > warning_days:
                    staleness_category = "warning"
                elif days_stale > 0:
                    staleness_category = "outdated"
                elif days_stale == -1:
                    staleness_category = "unknown"

                dep_record = {
                    "language": lang,
                    "package_name": dep["package_name"],
                    "current_version": dep["current_version"],
                    "latest_version": latest_version,
                    "release_date": release_date,
                    "days_stale": days_stale,
                    "staleness_category": staleness_category,
                    "purl": purl,
                    "scope": dep.get("scope", "required"),
                    "dependency_file": dep.get("dependency_file", ""),
                    "direct": dep.get("direct", True),
                }
                all_deps.append(dep_record)

                # 7. Store in DB
                _store_dependency(
                    conn, project_id, lang, dep,
                    latest_version, days_stale, purl,
                )

        conn.commit()

        # 9. Log audit event
        summary_details = {
            "languages_scanned": scanned_languages,
            "total_dependencies": len(all_deps),
            "outdated_count": outdated_count,
            "offline_mode": offline,
        }
        _log_audit_event(
            conn, project_id,
            f"Dependency scan completed: {len(all_deps)} deps across {len(scanned_languages)} languages",
            summary_details,
        )

        # 10. Return summary dict
        return {
            "project_id": project_id,
            "languages_scanned": scanned_languages,
            "total_dependencies": len(all_deps),
            "outdated_count": outdated_count,
            "dependencies": all_deps,
            "scan_date": datetime.now(timezone.utc).isoformat(),
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scan project dependencies and check for updates"
    )
    parser.add_argument("--project-id", required=True, help="Project ID in the database")
    parser.add_argument(
        "--language",
        choices=["python", "java", "javascript", "typescript", "go", "rust", "csharp"],
        help="Scan only this language (default: auto-detect all)",
    )
    parser.add_argument(
        "--project-dir",
        help="Override project directory (default: from database)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip registry checks (air-gapped mode)",
    )
    parser.add_argument(
        "--db-path",
        help="Override database path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args()

    try:
        result = scan_dependencies(
            project_id=args.project_id,
            language=args.language,
            offline=args.offline,
            project_dir=args.project_dir,
            db_path=args.db_path,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Dependency Scan: project {result['project_id']}")
        print(f"  Scan date:    {result['scan_date']}")
        print(f"  Languages:    {', '.join(result['languages_scanned']) or 'none detected'}")
        print(f"  Total deps:   {result['total_dependencies']}")
        print(f"  Outdated:     {result['outdated_count']}")
        print()

        if not result["dependencies"]:
            print("  No dependencies found.")
        else:
            # Group by language
            by_lang = {}
            for dep in result["dependencies"]:
                by_lang.setdefault(dep["language"], []).append(dep)

            for lang in sorted(by_lang.keys()):
                deps = by_lang[lang]
                print(f"  [{lang}] ({len(deps)} dependencies)")
                for dep in deps:
                    status_marker = " "
                    if dep["staleness_category"] == "critical":
                        status_marker = "!"
                    elif dep["staleness_category"] == "warning":
                        status_marker = "~"
                    elif dep["staleness_category"] == "outdated":
                        status_marker = "*"
                    elif dep["staleness_category"] == "unknown":
                        status_marker = "?"

                    latest_str = dep["latest_version"] or "?"
                    stale_str = f"{dep['days_stale']}d" if dep["days_stale"] >= 0 else "?"
                    print(
                        f"   {status_marker} {dep['package_name']:40s} "
                        f"{dep['current_version']:>15s} -> {latest_str:>15s}  "
                        f"({stale_str} stale)"
                    )
                print()

        # Summary legend
        print("  Legend: ! = critical, ~ = warning, * = outdated, ? = unknown")


if __name__ == "__main__":
    main()
