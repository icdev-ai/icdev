#!/usr/bin/env python3
# CUI // SP-CTI
# DEPRECATED: unused as of 2026-05-09. Remove after 2026-08-01.
"""Code Generator — generates implementation code from specifications.

Implements:
- generate_from_spec(project_path, spec, language) -> creates source files
- Generates modules in Python, Java, Go, TypeScript, Rust, and C#
- Applies CUI header in correct comment style per language
- Logs audit trail event (code_generated)
- CLI: python tools/builder/code_generator.py --project-path PATH --spec "REST API endpoint for users" --language python

Implementation split into focused sub-modules:
- code_gen_core.py    — Shared constants and utilities
- code_gen_python.py  — Python generators (api, model, service, cli, module)
- code_gen_multilang.py — Java, Go, TypeScript, Rust, C# generators
- code_gen_agentic.py — Phase 19 agentic + Phase 26 MOSA generators
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

# --- Core: constants and utilities (re-exported for backward compatibility) ---
from tools.builder.code_gen_core import (  # noqa: F401 — re-exported for backward compat
    BASE_DIR,
    DB_PATH,
    CUI_HEADER,
    CUI_HEADER_C_STYLE,
    CUI_HEADERS,
    _slugify,
    _detect_spec_type,
    _extract_entity_name,
    _build_profile_context,
)

# --- Python generators ---
from tools.builder.code_gen_python import (
    _generate_api_code,
    _generate_model_code,
    _generate_service_code,
    _generate_cli_code,
    _generate_module_code,
)

# --- Multi-language generators ---
from tools.builder.code_gen_multilang import (
    _generate_java_api_code,
    _generate_java_model_code,
    _generate_java_service_code,
    _generate_go_api_code,
    _generate_go_model_code,
    _generate_go_service_code,
    _generate_typescript_api_code,
    _generate_typescript_model_code,
    _generate_typescript_service_code,
    _generate_rust_api_code,
    _generate_rust_model_code,
    _generate_rust_service_code,
    _generate_csharp_api_code,
    _generate_csharp_model_code,
    _generate_csharp_service_code,
)

# --- Agentic + MOSA generators ---
from tools.builder.code_gen_agentic import (
    _generate_agent_skill_code,
    _generate_llm_service_code,
    _generate_prompt_template_code,
    _generate_agent_collaboration_code,
    _generate_model_config_code,
    generate_from_blueprint as _generate_from_blueprint,
    _generate_mosa_interface_code,
)


# Template dispatch table (Python — original + Phase 19 agentic + Phase 26 MOSA)
_GENERATORS = {
    "api": _generate_api_code,
    "model": _generate_model_code,
    "service": _generate_service_code,
    "cli": _generate_cli_code,
    "utility": _generate_module_code,
    "middleware": _generate_module_code,
    "config": _generate_module_code,
    "module": _generate_module_code,
    # Phase 19: Agentic spec types
    "agent_skill": _generate_agent_skill_code,
    "llm_service": _generate_llm_service_code,
    "prompt_template": _generate_prompt_template_code,
    "agent_collaboration": _generate_agent_collaboration_code,
    "model_config": _generate_model_config_code,
    # Phase 26: MOSA interface
    "mosa_interface": _generate_mosa_interface_code,
}

# Language-specific generator dispatch tables
LANGUAGE_GENERATORS = {
    "python": _GENERATORS,
    "java": {
        "api": _generate_java_api_code,
        "model": _generate_java_model_code,
        "service": _generate_java_service_code,
    },
    "go": {
        "api": _generate_go_api_code,
        "model": _generate_go_model_code,
        "service": _generate_go_service_code,
    },
    "typescript": {
        "api": _generate_typescript_api_code,
        "model": _generate_typescript_model_code,
        "service": _generate_typescript_service_code,
    },
    "rust": {
        "api": _generate_rust_api_code,
        "model": _generate_rust_model_code,
        "service": _generate_rust_service_code,
    },
    "csharp": {
        "api": _generate_csharp_api_code,
        "model": _generate_csharp_model_code,
        "service": _generate_csharp_service_code,
    },
}


# File extension mapping per language
_LANGUAGE_EXTENSIONS = {
    "python": ".py",
    "java": ".java",
    "go": ".go",
    "typescript": ".ts",
    "rust": ".rs",
    "csharp": ".cs",
}


def generate_from_spec(
    project_path: str,
    spec: str,
    output_dir: Optional[str] = None,
    force_type: Optional[str] = None,
    language: str = "python",
    project_id: Optional[str] = None,
    secure: bool = True,
) -> List[str]:
    """Generate implementation code from a specification.

    Args:
        project_path: Root path of the project.
        spec: Specification text describing what to generate.
        output_dir: Optional output directory (defaults to {project}/src/).
        force_type: Force a specific code type (api, model, service, cli, module).
        language: Target language (python, java, go, typescript, rust, csharp).
        project_id: Optional project ID for dev profile injection (Phase 34, D187).
        secure: Include auth decorators and input validation (D-EPSEC-5).
                Default True. Use --no-auth CLI flag to disable.

    Returns:
        List of paths to generated files.
    """
    # Phase 34: Build profile context for LLM-aware generation
    if project_id:
        _build_profile_context(project_id, "code_generation")
    project = Path(project_path)
    src_dir = Path(output_dir) if output_dir else project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Ensure __init__.py exists for Python projects
    if language == "python":
        init_file = src_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text(f"{CUI_HEADER}\n", encoding="utf-8", newline="")

    # Detect code type and entity
    code_type = force_type or _detect_spec_type(spec)
    entity = _extract_entity_name(spec)

    # Get language-specific generators
    lang_generators = LANGUAGE_GENERATORS.get(language)
    if not lang_generators:
        raise ValueError(f"Unsupported language: {language}. Supported: {', '.join(LANGUAGE_GENERATORS.keys())}")

    # Find the generator for this code type
    generator = lang_generators.get(code_type)
    if not generator:
        # Fall back to module/generic generator if available, or error
        generator = lang_generators.get("module")
        if not generator:
            available = ", ".join(lang_generators.keys())
            raise ValueError(
                f"No '{code_type}' generator for language '{language}'. Available types for {language}: {available}"
            )

    # Pass secure flag to API generators (D-EPSEC-5)
    if "secure" in inspect.signature(generator).parameters:
        code = generator(entity, spec, secure=secure)
    else:
        code = generator(entity, spec)

    # Write the file with appropriate extension
    ext = _LANGUAGE_EXTENSIONS.get(language, ".py")
    filename = f"{_slugify(entity)}{ext}"
    output_file = src_dir / filename
    output_file.write_text(code, encoding="utf-8", newline="")

    generated_files = [str(output_file)]
    print(f"Generated [{language}/{code_type}]: {output_file}")

    # Log audit trail
    _log_audit(project_path, generated_files, spec)

    return generated_files


def generate_from_blueprint(
    project_path: str,
    blueprint: Dict[str, Any],
    language: str = "python",
) -> List[str]:
    """Generate common boilerplate files from a FORGE blueprint.

    Delegates to code_gen_agentic.generate_from_blueprint and logs audit.

    Args:
        project_path: Root path of the project.
        blueprint: Blueprint dict (see code_gen_agentic for schema).
        language: Target language (currently only 'python').

    Returns:
        List of paths to all generated files.
    """
    generated_files = _generate_from_blueprint(project_path, blueprint, language)
    app_name = blueprint.get("name", "app")
    _log_audit(project_path, generated_files, f"Blueprint generation for {app_name}")
    return generated_files


def _log_audit(project_path: str, files: List[str], spec: str) -> None:
    """Log code generation to the audit trail."""
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """INSERT INTO audit_trail (project_id, event_type, actor, action, details, affected_files, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                None,
                "code_generated",
                "builder/code_generator",
                f"Generated code from spec: {spec[:100]}",
                json.dumps({"spec": spec}),
                json.dumps(files),
                "CUI",
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: audit logging failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Generate implementation code from specifications")
    parser.add_argument("--project-path", required=True, help="Root path of the project")
    parser.add_argument("--spec", required=True, help="Specification text")
    parser.add_argument("--output-dir", help="Output directory (defaults to {project}/src/)")
    parser.add_argument(
        "--type",
        choices=[
            "api",
            "model",
            "service",
            "cli",
            "utility",
            "middleware",
            "config",
            "module",
            "agent_skill",
            "llm_service",
            "prompt_template",
            "agent_collaboration",
            "model_config",
        ],
        help="Force a specific code type",
    )
    parser.add_argument(
        "--language",
        default="python",
        choices=["python", "java", "go", "typescript", "csharp", "rust"],
        help="Target language for code generation (default: python)",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Project ID for dev profile injection (Phase 34, D187)",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable auth decorators and validation in generated API code (D-EPSEC-5). "
        "WARNING: Generated code will fail endpoint_security gate.",
    )
    args = parser.parse_args()

    files = generate_from_spec(
        project_path=args.project_path,
        spec=args.spec,
        output_dir=args.output_dir,
        force_type=args.type,
        language=args.language,
        project_id=args.project_id,
        secure=not args.no_auth,
    )
    print(f"\nGenerated {len(files)} file(s) [{args.language}]:")
    for f in files:
        print(f"  {f}")


if __name__ == "__main__":
    main()
