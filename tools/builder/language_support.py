#!/usr/bin/env python3
"""Language Support Module — unified language detection and registry for ICDEV.

Provides:
- load_registry()        -> Load language profiles from language_registry.json
- detect_languages()     -> Unified language detection (replaces per-tool detection)
- get_language_profile()  -> Get full profile for a language
- get_cui_header()       -> CUI header in correct comment style
- get_dependency_files() -> Find dependency manifest files
- detect_package_manager() -> Detect which package manager is in use

CLI: python tools/builder/language_support.py --detect <path> | --list | --profile <lang>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Module-level cache for the language registry
_REGISTRY_CACHE: Optional[Dict] = None

# Directories to exclude when globbing for source files
_EXCLUDE_DIRS = {"venv", "node_modules", "target", ".git", "vendor", "bin", "obj"}

# CUI marking lines shared across comment styles
_CUI_LINES = [
    "CUI // SP-CTI",
    "Controlled by: Department of Defense",
    "CUI Category: CTI",
    "Distribution: D",
    "POC: ICDEV System Administrator",
]


def load_registry() -> Dict:
    """Load the language registry from context/languages/language_registry.json.

    Returns the full registry dict. Caches after first load so subsequent
    calls are free.

    Returns:
        Dict containing all language profiles keyed by language name.

    Raises:
        FileNotFoundError: If the registry JSON file does not exist.
    """
    global _REGISTRY_CACHE

    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE

    registry_path = BASE_DIR / "context" / "languages" / "language_registry.json"

    if not registry_path.exists():
        raise FileNotFoundError(
            f"Language registry not found at {registry_path}. "
            f"Expected file: context/languages/language_registry.json relative to "
            f"project root ({BASE_DIR}). Run /initialize or create the registry file."
        )

    with open(registry_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Support both flat format {"python": {...}, ...} and wrapped {"languages": {...}}
    if "languages" in raw and isinstance(raw["languages"], dict):
        _REGISTRY_CACHE = raw["languages"]
    else:
        _REGISTRY_CACHE = raw

    return _REGISTRY_CACHE


def _is_excluded(path: Path) -> bool:
    """Check whether a path falls inside any excluded directory.

    Args:
        path: The file path to check.

    Returns:
        True if the path contains an excluded directory component.
    """
    parts = set(path.parts)
    return bool(parts & _EXCLUDE_DIRS)


def detect_languages(project_path: str) -> List[str]:
    """Detect which programming languages are present in a project.

    For each language defined in the registry:
      1. Check ``config_indicators`` — if any file exists at the project root,
         the language is detected.
      2. If no config file matched, glob for ``file_extensions`` (excluding
         venv/, node_modules/, target/, .git/, vendor/, bin/, obj/).
      3. If matching source files are found, the language is detected.

    Special case: if both "typescript" and "javascript" are detected, both
    are kept (TypeScript projects commonly contain plain JS files too).

    Args:
        project_path: Root path of the project to scan.

    Returns:
        Sorted list of detected language keys (e.g. ["javascript", "python"]).
    """
    registry = load_registry()
    root = Path(project_path)
    detected: List[str] = []

    for lang_key, profile in registry.items():
        found = False

        # Step 1: check config indicator files at project root
        config_indicators = profile.get("config_indicators", [])
        for indicator in config_indicators:
            if (root / indicator).exists():
                found = True
                break

        # Step 2: if no config file found, glob for source file extensions
        if not found:
            file_extensions = profile.get("file_extensions", [])
            for ext in file_extensions:
                # Glob recursively for this extension
                pattern = f"**/*{ext}"
                for match in root.glob(pattern):
                    if not _is_excluded(match):
                        found = True
                        break
                if found:
                    break

        if found:
            detected.append(lang_key)

    return sorted(detected)


def get_language_profile(language: str) -> Dict:
    """Return the full profile dict for a language key.

    Args:
        language: Language key (e.g. "python", "java", "rust").

    Returns:
        Dict with all profile fields for the requested language.

    Raises:
        ValueError: If the language is not in the registry.
    """
    registry = load_registry()

    if language not in registry:
        supported = sorted(registry.keys())
        raise ValueError(
            f"Language '{language}' not found in registry. "
            f"Supported languages: {', '.join(supported)}"
        )

    return registry[language]


def get_cui_header(language: str) -> str:
    """Return a CUI header block in the correct comment style for a language.

    Uses the ``cui_comment_style`` field from the language profile:
      - "hash"      -> ``# ...``
      - "c-style"   -> ``// ...``
      - "xml-style" -> ``<!-- ... -->``

    Args:
        language: Language key.

    Returns:
        Multi-line string containing the CUI header with proper comment markers.
    """
    profile = get_language_profile(language)
    style = profile.get("cui_comment_style", "hash")

    lines: List[str] = []

    if style == "hash":
        for line in _CUI_LINES:
            lines.append(f"# {line}")
    elif style == "c-style":
        for line in _CUI_LINES:
            lines.append(f"// {line}")
    elif style == "xml-style":
        for line in _CUI_LINES:
            lines.append(f"<!-- {line} -->")
    else:
        # Fallback to hash style for unknown styles
        for line in _CUI_LINES:
            lines.append(f"# {line}")

    return "\n".join(lines) + "\n"


def get_dependency_files(project_path: str, language: str) -> List[Path]:
    """Find all dependency manifest files for a language in a project.

    Checks each entry in the ``dependency_files`` list from the language
    profile. Also globs for common patterns (e.g. ``**/requirements*.txt``
    for Python).

    Args:
        project_path: Root path of the project.
        language: Language key.

    Returns:
        List of Path objects for dependency files that exist.
    """
    profile = get_language_profile(language)
    root = Path(project_path)
    found: List[Path] = []
    seen: set = set()

    # Check explicit dependency_files from the profile
    dep_files = profile.get("dependency_files", [])
    for dep_file in dep_files:
        candidate = root / dep_file
        if candidate.exists() and candidate.resolve() not in seen:
            found.append(candidate)
            seen.add(candidate.resolve())

    # Glob for common patterns based on language
    glob_patterns: List[str] = []
    lang_lower = language.lower()

    if lang_lower == "python":
        glob_patterns = ["**/requirements*.txt", "**/Pipfile", "**/poetry.lock"]
    elif lang_lower == "java":
        glob_patterns = ["**/pom.xml", "**/build.gradle", "**/build.gradle.kts"]
    elif lang_lower in ("javascript", "typescript"):
        glob_patterns = ["**/package.json", "**/yarn.lock", "**/pnpm-lock.yaml"]
    elif lang_lower == "go":
        glob_patterns = ["**/go.mod", "**/go.sum"]
    elif lang_lower == "rust":
        glob_patterns = ["**/Cargo.toml", "**/Cargo.lock"]
    elif lang_lower in ("csharp", "c#"):
        glob_patterns = ["**/*.csproj", "**/*.sln", "**/packages.config"]

    for pattern in glob_patterns:
        for match in root.glob(pattern):
            if not _is_excluded(match) and match.resolve() not in seen:
                found.append(match)
                seen.add(match.resolve())

    return found


def detect_package_manager(project_path: str, language: str) -> str:
    """Determine which package manager variant is in use for a language.

    Detection logic per language:
      - Python: poetry.lock -> "poetry", Pipfile -> "pipenv",
        pyproject.toml with [tool.poetry] -> "poetry", else -> "pip"
      - Java: pom.xml -> "maven", build.gradle -> "gradle"
      - JavaScript/TypeScript: yarn.lock -> "yarn",
        pnpm-lock.yaml -> "pnpm", else -> "npm"
      - Go: always "go"
      - Rust: always "cargo"
      - C#: always "dotnet"

    Args:
        project_path: Root path of the project.
        language: Language key.

    Returns:
        String identifying the package manager (e.g. "pip", "maven", "npm").
    """
    root = Path(project_path)
    lang_lower = language.lower()

    if lang_lower == "python":
        if (root / "poetry.lock").exists():
            return "poetry"
        if (root / "Pipfile").exists():
            return "pipenv"
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(encoding="utf-8")
                if "[tool.poetry]" in content:
                    return "poetry"
            except (OSError, UnicodeDecodeError):
                pass
        return "pip"

    elif lang_lower == "java":
        if (root / "pom.xml").exists():
            return "maven"
        if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
            return "gradle"
        # Default for Java if neither is found
        return "maven"

    elif lang_lower in ("javascript", "typescript"):
        if (root / "yarn.lock").exists():
            return "yarn"
        if (root / "pnpm-lock.yaml").exists():
            return "pnpm"
        return "npm"

    elif lang_lower == "go":
        return "go"

    elif lang_lower == "rust":
        return "cargo"

    elif lang_lower in ("csharp", "c#"):
        return "dotnet"

    else:
        return "unknown"


def get_supported_languages() -> List[str]:
    """Return a sorted list of all language keys defined in the registry.

    Returns:
        Sorted list of language key strings.
    """
    registry = load_registry()
    return sorted(registry.keys())


def main():
    """CLI entry point for language support operations."""
    parser = argparse.ArgumentParser(
        description="Language detection and registry for ICDEV projects"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--detect",
        metavar="PROJECT_PATH",
        help="Detect languages in a project directory",
    )
    group.add_argument(
        "--list",
        action="store_true",
        help="List all supported languages in the registry",
    )
    group.add_argument(
        "--profile",
        metavar="LANGUAGE",
        help="Show the full profile for a language",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    try:
        if args.detect:
            project_path = args.detect
            root = Path(project_path)
            if not root.is_dir():
                print(f"Error: '{project_path}' is not a directory", file=sys.stderr)
                sys.exit(1)

            languages = detect_languages(project_path)

            if args.json:
                result = {
                    "project_path": str(root.resolve()),
                    "languages_detected": languages,
                    "details": {},
                }
                for lang in languages:
                    result["details"][lang] = {
                        "package_manager": detect_package_manager(project_path, lang),
                        "dependency_files": [
                            str(p) for p in get_dependency_files(project_path, lang)
                        ],
                    }
                print(json.dumps(result, indent=2))
            else:
                if languages:
                    print(f"Detected languages: {', '.join(languages)}")
                    for lang in languages:
                        pm = detect_package_manager(project_path, lang)
                        deps = get_dependency_files(project_path, lang)
                        print(f"\n  {lang}:")
                        print(f"    Package manager: {pm}")
                        if deps:
                            print(f"    Dependency files:")
                            for dep in deps:
                                print(f"      - {dep}")
                        else:
                            print(f"    Dependency files: none found")
                else:
                    print("No supported languages detected.")

        elif args.list:
            languages = get_supported_languages()
            if args.json:
                print(json.dumps({"supported_languages": languages}, indent=2))
            else:
                print(f"Supported languages ({len(languages)}):")
                for lang in languages:
                    print(f"  - {lang}")

        elif args.profile:
            language = args.profile
            profile = get_language_profile(language)
            if args.json:
                print(json.dumps({language: profile}, indent=2))
            else:
                print(f"Profile: {language}")
                print(f"{'=' * (len(language) + 9)}")
                for key, value in profile.items():
                    if isinstance(value, list):
                        print(f"  {key}:")
                        for item in value:
                            print(f"    - {item}")
                    elif isinstance(value, dict):
                        print(f"  {key}:")
                        for k, v in value.items():
                            print(f"    {k}: {v}")
                    else:
                        print(f"  {key}: {value}")
                print(f"\nCUI header:")
                print(get_cui_header(language))

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
