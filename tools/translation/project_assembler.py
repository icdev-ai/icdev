#!/usr/bin/env python3
# CUI // SP-CTI
"""Phase 4 — Project assembly: scaffold target project, write translated files,
apply CUI headers, generate build files.

Architecture Decision D245: Non-destructive output — translation output to separate directory.
Architecture Decision D249: Compliance bridge — CUI markings on all translated files."""

import argparse
import json
import os
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Build file templates per language
BUILD_TEMPLATES = {
    "python": {
        "file": "pyproject.toml",
        "content": """[project]
name = "{project_name}"
version = "0.1.0"
description = "Translated from {source_lang} by ICDEV Phase 43"
requires-python = ">=3.9"
dependencies = [
{dependencies}
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
    },
    "java": {
        "file": "pom.xml",
        "content": """<?xml version="1.0" encoding="UTF-8"?>
<!-- CUI // SP-CTI -->
<!-- Translated from {source_lang} by ICDEV Phase 43 -->
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>mil.icdev</groupId>
    <artifactId>{project_name}</artifactId>
    <version>0.1.0</version>
    <packaging>jar</packaging>
    <properties>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>
    <dependencies>
{dependencies}
    </dependencies>
</project>
""",
    },
    "go": {
        "file": "go.mod",
        "content": """module {project_name}

go 1.21

// Translated from {source_lang} by ICDEV Phase 43
{dependencies}
""",
    },
    "rust": {
        "file": "Cargo.toml",
        "content": """# CUI // SP-CTI
# Translated from {source_lang} by ICDEV Phase 43
[package]
name = "{project_name}"
version = "0.1.0"
edition = "2021"

[dependencies]
{dependencies}
""",
    },
    "csharp": {
        "file": "{project_name}.csproj",
        "content": """<!-- CUI // SP-CTI -->
<!-- Translated from {source_lang} by ICDEV Phase 43 -->
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
{dependencies}
  </ItemGroup>
</Project>
""",
    },
    "typescript": {
        "file": "package.json",
        "content": """{open_brace}
  "name": "{project_name}",
  "version": "0.1.0",
  "description": "Translated from {source_lang} by ICDEV Phase 43",
  "main": "src/index.ts",
  "scripts": {open_brace}
    "build": "tsc",
    "test": "jest"
  {close_brace},
  "dependencies": {open_brace}
{dependencies}
  {close_brace},
  "devDependencies": {open_brace}
    "typescript": "^5.0.0",
    "@types/node": "^20.0.0",
    "jest": "^29.0.0",
    "ts-jest": "^29.0.0",
    "@types/jest": "^29.0.0"
  {close_brace}
{close_brace}
""",
    },
}

# Language-specific directory structure
DIR_STRUCTURES = {
    "python": {"src": "src", "test": "tests"},
    "java": {"src": "src/main/java", "test": "src/test/java"},
    "go": {"src": ".", "test": "."},
    "rust": {"src": "src", "test": "tests"},
    "csharp": {"src": "src", "test": "tests"},
    "typescript": {"src": "src", "test": "tests"},
}

# File extensions per language
FILE_EXTENSIONS = {
    "python": ".py",
    "java": ".java",
    "go": ".go",
    "rust": ".rs",
    "csharp": ".cs",
    "typescript": ".ts",
    "javascript": ".js",
}

CUI_HEADERS = {
    "python": "# CUI // SP-CTI",
    "java": "// CUI // SP-CTI",
    "go": "// CUI // SP-CTI",
    "rust": "// CUI // SP-CTI",
    "csharp": "// CUI // SP-CTI",
    "typescript": "// CUI // SP-CTI",
    "javascript": "// CUI // SP-CTI",
}


def _ensure_cui_header(code, language):
    """Ensure the translated code has a CUI header as the first line."""
    header = CUI_HEADERS.get(language, "// CUI // SP-CTI")
    if code.strip().startswith(header):
        return code
    return header + "\n" + code


def _format_dependencies(dep_resolutions, target_language):
    """Format resolved dependencies for the build file template."""
    if not dep_resolutions:
        return ""

    resolved = [d for d in dep_resolutions
                if d.get("mapping_source") in ("table", "llm_suggested", "manual")
                and d.get("target_import")]

    if not resolved:
        return ""

    if target_language == "python":
        # pyproject.toml dependencies array
        packages = sorted(set(
            d["target_import"].split(".")[0].replace("_", "-")
            for d in resolved
            if d.get("target_import")
        ))
        return "\n".join(f'    "{p}",' for p in packages)

    elif target_language == "java":
        # Maven dependencies — simplified
        packages = sorted(set(d.get("target_import", "") for d in resolved))
        lines = []
        for p in packages:
            if "." in p:
                group = ".".join(p.split(".")[:-1])
                artifact = p.split(".")[-1]
                lines.append(
                    f"        <dependency>\n"
                    f"            <groupId>{group}</groupId>\n"
                    f"            <artifactId>{artifact}</artifactId>\n"
                    f"        </dependency>"
                )
        return "\n".join(lines)

    elif target_language == "go":
        packages = sorted(set(d.get("target_import", "") for d in resolved))
        lines = [f"require {p} v0.0.0" for p in packages if p]
        return "\n".join(lines)

    elif target_language == "rust":
        packages = sorted(set(
            d["target_import"].split("::")[0]
            for d in resolved
            if d.get("target_import")
        ))
        return "\n".join(f'{p} = "*"' for p in packages)

    elif target_language == "csharp":
        packages = sorted(set(
            d["target_import"].split(".")[0]
            for d in resolved
            if d.get("target_import")
        ))
        return "\n".join(
            f'    <PackageReference Include="{p}" Version="*" />'
            for p in packages
        )

    elif target_language in ("typescript", "javascript"):
        packages = sorted(set(
            d["target_import"].split("/")[0].lstrip("@")
            for d in resolved
            if d.get("target_import")
        ))
        return "\n".join(f'    "{p}": "*",' for p in packages)

    return ""


def assemble_project(output_dir, target_language, source_language,
                     translated_units, dep_resolutions=None,
                     project_name=None, project_id=None, job_id=None,
                     db_path=None):
    """Assemble a complete target project from translated units.

    Returns dict with project_path, files_written, build_file.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    proj_name = project_name or f"icdev-translated-{target_language}"
    dirs = DIR_STRUCTURES.get(target_language, {"src": "src", "test": "tests"})
    ext = FILE_EXTENSIONS.get(target_language, ".txt")

    # Create directory structure
    src_dir = out_dir / dirs["src"]
    test_dir = out_dir / dirs["test"]
    src_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    files_written = []

    # Write translated code files
    for unit in translated_units:
        code = unit.get("translated_code", "")
        if not code:
            continue

        # Ensure CUI header
        code = _ensure_cui_header(code, target_language)

        # Determine output path
        src_file = unit.get("source_file", unit.get("name", "module"))
        base_name = Path(src_file).stem
        if unit.get("kind") == "class" and target_language == "java":
            # Java: one class per file, PascalCase filename
            base_name = unit.get("name", base_name)
        out_file = src_dir / (base_name + ext)

        # Avoid overwriting — append if file exists
        if out_file.exists():
            existing = out_file.read_text(encoding="utf-8")
            code = existing + "\n\n" + code
        out_file.write_text(code, encoding="utf-8")
        files_written.append(str(out_file.relative_to(out_dir)))

    # Python: add __init__.py
    if target_language == "python":
        init_path = src_dir / "__init__.py"
        if not init_path.exists():
            init_path.write_text(
                "# CUI // SP-CTI\n"
                f"# Translated from {source_language} by ICDEV Phase 43\n",
                encoding="utf-8",
            )
            files_written.append(str(init_path.relative_to(out_dir)))

    # Go: add package declaration helper
    if target_language == "go" and src_dir == out_dir:
        for go_file in out_dir.glob("*.go"):
            content = go_file.read_text(encoding="utf-8")
            if not content.strip().startswith("package "):
                if "// CUI // SP-CTI" in content:
                    lines = content.split("\n")
                    # Insert package after CUI header
                    insert_idx = 1
                    for i, line in enumerate(lines):
                        if line.startswith("// CUI"):
                            insert_idx = i + 1
                            break
                    lines.insert(insert_idx, f"\npackage {proj_name.replace('-', '_')}\n")
                    go_file.write_text("\n".join(lines), encoding="utf-8")

    # Generate build file
    template_info = BUILD_TEMPLATES.get(target_language)
    if template_info:
        dep_text = _format_dependencies(dep_resolutions, target_language)
        build_filename = template_info["file"].replace("{project_name}", proj_name)
        build_content = template_info["content"].format(
            project_name=proj_name,
            source_lang=source_language,
            dependencies=dep_text,
            open_brace="{",
            close_brace="}",
        )
        build_path = out_dir / build_filename
        build_path.write_text(build_content, encoding="utf-8")
        files_written.append(build_filename)

    # Generate README
    readme_path = out_dir / "README.md"
    readme_content = (
        f"# {proj_name}\n\n"
        f"**CUI // SP-CTI**\n\n"
        f"Translated from {source_language} to {target_language} by ICDEV Phase 43.\n\n"
        f"## Build\n\n"
        f"See build file for dependencies and build instructions.\n\n"
        f"## Compliance\n\n"
        f"This project inherits NIST 800-53 controls from the source project.\n"
        f"All files include CUI markings.\n"
    )
    readme_path.write_text(readme_content, encoding="utf-8")
    files_written.append("README.md")

    # Audit trail
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="translation.assembly_completed",
            actor="project_assembler",
            action=f"Assembled {target_language} project with {len(translated_units)} units",
            project_id=project_id,
            details={
                "target_language": target_language,
                "source_language": source_language,
                "files_written": len(files_written),
                "output_dir": str(out_dir),
            },
            affected_files=files_written,
        )
    except Exception:
        pass

    return {
        "project_path": str(out_dir),
        "project_name": proj_name,
        "target_language": target_language,
        "source_language": source_language,
        "files_written": files_written,
        "file_count": len(files_written),
        "build_file": template_info["file"].replace("{project_name}", proj_name) if template_info else None,
        "src_dir": str(src_dir),
        "test_dir": str(test_dir),
    }


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Phase 4 — Assemble translated project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--translated-file", required=True,
                        help="Path to JSON file with translated units")
    parser.add_argument("--source-language", required=True, help="Source language")
    parser.add_argument("--target-language", required=True, help="Target language")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--project-name", help="Target project name")
    parser.add_argument("--project-id", help="Project ID for audit trail")
    parser.add_argument("--job-id", help="Translation job ID")
    parser.add_argument("--dep-resolutions", help="Path to JSON with dependency resolutions")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Load translated units
    trans_path = Path(args.translated_file)
    if not trans_path.exists():
        print(json.dumps({"error": f"File not found: {args.translated_file}"}))
        return

    with open(trans_path, "r", encoding="utf-8") as f:
        trans_data = json.load(f)

    # Combine translated + mocked units
    all_units = trans_data.get("translated_units", []) + trans_data.get("mocked_units", [])

    # Load dependency resolutions
    dep_resolutions = None
    if args.dep_resolutions:
        dep_path = Path(args.dep_resolutions)
        if dep_path.exists():
            with open(dep_path, "r", encoding="utf-8") as f:
                dep_resolutions = json.load(f)

    result = assemble_project(
        output_dir=args.output_dir,
        target_language=args.target_language,
        source_language=args.source_language,
        translated_units=all_units,
        dep_resolutions=dep_resolutions,
        project_name=args.project_name,
        project_id=args.project_id,
        job_id=args.job_id,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Project assembled: {result['project_path']}")
        print(f"  Language:    {result['target_language']}")
        print(f"  Files:       {result['file_count']}")
        print(f"  Build file:  {result['build_file']}")
        print(f"  Source dir:  {result['src_dir']}")
        print(f"  Test dir:    {result['test_dir']}")


if __name__ == "__main__":
    main()
