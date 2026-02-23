#!/usr/bin/env python3
# CUI // SP-CTI
"""Phase 3 — LLM-assisted code translation with pass@k candidate generation (D254),
post-order dependency traversal (D244), feature mapping rules (D247),
and mock-and-continue on persistent failure (D256).

Architecture Decision D242: Hybrid 5-phase pipeline.
Architecture Decision D254: pass@k from Google ICSE 2025.
Architecture Decision D256: Mock-and-continue from Amazon Oxidizer."""

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# CUI header templates per language
CUI_HEADERS = {
    "python": "# CUI // SP-CTI",
    "java": "// CUI // SP-CTI",
    "go": "// CUI // SP-CTI",
    "rust": "// CUI // SP-CTI",
    "csharp": "// CUI // SP-CTI",
    "typescript": "// CUI // SP-CTI",
    "javascript": "// CUI // SP-CTI",
}

PROVENANCE_TEMPLATES = {
    "python": "# Translated from {source_lang} by ICDEV Phase 43",
    "java": "// Translated from {source_lang} by ICDEV Phase 43",
    "go": "// Translated from {source_lang} by ICDEV Phase 43",
    "rust": "// Translated from {source_lang} by ICDEV Phase 43",
    "csharp": "// Translated from {source_lang} by ICDEV Phase 43",
    "typescript": "// Translated from {source_lang} by ICDEV Phase 43",
    "javascript": "// Translated from {source_lang} by ICDEV Phase 43",
}

NAMING_CONVENTIONS = {
    "python": "snake_case",
    "java": "camelCase",
    "go": "camelCase",
    "rust": "snake_case",
    "csharp": "PascalCase",
    "typescript": "camelCase",
    "javascript": "camelCase",
}


def _load_config():
    """Load translation config from args/translation_config.yaml."""
    config_path = BASE_DIR / "args" / "translation_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r") as f:
                return yaml.safe_load(f)
        except ImportError:
            pass
    return {
        "translation": {
            "max_chunk_lines": 500,
            "temperature": 0.2,
            "candidates": 3,
            "mock_on_failure": True,
            "max_mock_pct": 20,
            "provenance_comments": True,
            "preserve_comments": True,
        },
        "repair": {
            "max_repair_attempts": 3,
            "include_compiler_errors": True,
            "repair_timeout_seconds": 120,
        },
    }


def _build_prompt(unit, ir_data, source_language, target_language,
                  dependency_mappings, feature_rules, type_mappings,
                  translated_deps, config):
    """Build the translation prompt from hardprompt template + context."""
    prompt_path = BASE_DIR / "hardprompts" / "translation" / "code_translation.md"
    if prompt_path.exists():
        template = prompt_path.read_text(encoding="utf-8")
    else:
        template = "Translate the following {source_language} code to {target_language}:\n\n{source_code}"

    cui_header = CUI_HEADERS.get(target_language, "// CUI // SP-CTI")
    provenance = PROVENANCE_TEMPLATES.get(target_language, "// Translated by ICDEV").format(
        source_lang=source_language
    )

    # Build feature rules text
    feature_text = ""
    for rule in feature_rules:
        feature_text += f"- **{rule.get('id', 'unknown')}**: {rule.get('description', '')}\n"
        feature_text += f"  - Detection pattern: `{rule.get('pattern', '')}`\n"
        feature_text += f"  - Validation: {rule.get('validation', '')}\n"

    # Build dependency mappings text
    dep_text = json.dumps(dependency_mappings, indent=2) if dependency_mappings else "No dependency mappings available."

    # Build type mappings text
    type_text = json.dumps(type_mappings, indent=2) if type_mappings else "Use standard type mappings."

    # Build translated dependencies text
    dep_list = "\n".join(
        f"- {d['name']} ({d['kind']})" for d in translated_deps
    ) if translated_deps else "None yet."

    # Simple template substitution
    prompt = template
    replacements = {
        "{{ source_language }}": source_language,
        "{{ target_language }}": target_language,
        "{{ unit_name }}": unit.get("name", "unknown"),
        "{{ unit_kind }}": unit.get("kind", "function"),
        "{{ source_file }}": unit.get("source_file", ""),
        "{{ chunk_index }}": "1",
        "{{ total_chunks }}": "1",
        "{{ source_code }}": unit.get("source_code", ""),
        "{{ ir_json }}": json.dumps(unit, indent=2),
        "{{ translated_dependencies }}": dep_list,
        "{{ dependency_mappings }}": dep_text,
        "{{ type_mappings }}": type_text,
        "{{ source_naming }}": NAMING_CONVENTIONS.get(source_language, "camelCase"),
        "{{ target_naming }}": NAMING_CONVENTIONS.get(target_language, "camelCase"),
        "{{ cui_header }}": cui_header,
        "{{ provenance_comment }}": provenance,
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, str(value))

    # Replace feature rules block
    if "{% for rule in feature_rules %}" in prompt:
        start = prompt.index("{% for rule in feature_rules %}")
        end = prompt.index("{% endfor %}", start) + len("{% endfor %}")
        prompt = prompt[:start] + feature_text + prompt[end:]

    return prompt


def _invoke_llm(prompt, config, function_name="code_translation"):
    """Invoke LLM via the router. Returns translated code string."""
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        response = router.invoke(
            function=function_name,
            prompt=prompt,
            temperature=config.get("translation", {}).get("temperature", 0.2),
        )
        if isinstance(response, dict):
            return response.get("content", response.get("text", str(response)))
        return str(response)
    except ImportError:
        return None
    except Exception:
        return None


def _generate_mock(unit, target_language):
    """Generate a type-compatible mock/stub for a unit that failed translation (D256)."""
    name = unit.get("name", "unknown")
    kind = unit.get("kind", "function")
    params = unit.get("params", [])
    return_type = unit.get("return_type", "")
    cui_header = CUI_HEADERS.get(target_language, "// CUI // SP-CTI")

    if target_language == "python":
        param_str = ", ".join(p.get("name", "arg") for p in params)
        return (
            f"{cui_header}\n"
            f"# MOCK — Translation failed after max repair attempts\n"
            f"def {name}({param_str}):\n"
            f"    \"\"\"MOCK: Requires manual translation.\"\"\"\n"
            f"    raise NotImplementedError(\"ICDEV translation mock — manual translation required\")\n"
        )
    elif target_language == "java":
        param_str = ", ".join(
            f"Object {p.get('name', 'arg')}" for p in params
        )
        ret = "Object" if return_type else "void"
        return (
            f"{cui_header}\n"
            f"// MOCK — Translation failed after max repair attempts\n"
            f"public {ret} {name}({param_str}) {{\n"
            f"    // MOCK: Requires manual translation\n"
            f"    throw new UnsupportedOperationException(\"ICDEV translation mock\");\n"
            f"}}\n"
        )
    elif target_language == "go":
        param_str = ", ".join(
            f"{p.get('name', 'arg')} interface{{}}" for p in params
        )
        return (
            f"{cui_header}\n"
            f"// MOCK — Translation failed after max repair attempts\n"
            f"func {name}({param_str}) interface{{}} {{\n"
            f"\t// MOCK: Requires manual translation\n"
            f"\tpanic(\"ICDEV translation mock — manual translation required\")\n"
            f"}}\n"
        )
    elif target_language == "rust":
        param_str = ", ".join(
            f"{p.get('name', 'arg')}: ()" for p in params
        )
        return (
            f"{cui_header}\n"
            f"// MOCK — Translation failed after max repair attempts\n"
            f"pub fn {name}({param_str}) {{\n"
            f"    // MOCK: Requires manual translation\n"
            f"    unimplemented!(\"ICDEV translation mock\");\n"
            f"}}\n"
        )
    elif target_language == "csharp":
        param_str = ", ".join(
            f"object {p.get('name', 'arg')}" for p in params
        )
        ret = "object" if return_type else "void"
        return (
            f"{cui_header}\n"
            f"// MOCK — Translation failed after max repair attempts\n"
            f"public {ret} {name}({param_str})\n"
            f"{{\n"
            f"    // MOCK: Requires manual translation\n"
            f"    throw new NotImplementedException(\"ICDEV translation mock\");\n"
            f"}}\n"
        )
    elif target_language in ("typescript", "javascript"):
        param_str = ", ".join(p.get("name", "arg") for p in params)
        return (
            f"{cui_header}\n"
            f"// MOCK — Translation failed after max repair attempts\n"
            f"export function {name}({param_str}) {{\n"
            f"    // MOCK: Requires manual translation\n"
            f"    throw new Error(\"ICDEV translation mock — manual translation required\");\n"
            f"}}\n"
        )
    else:
        return f"// MOCK: {name} — translation failed, manual translation required\n"


def _get_translation_order(ir_data):
    """Return units in post-order dependency traversal (D244).
    Leaf nodes (no internal dependencies) are translated first."""
    units = ir_data.get("units", [])
    if not units:
        return []

    # Build adjacency: unit name -> set of internal dependencies
    unit_names = {u["name"] for u in units}
    deps = {}
    for u in units:
        internal_deps = set()
        for call in u.get("calls", []):
            if call in unit_names and call != u["name"]:
                internal_deps.add(call)
        for base in u.get("bases", []):
            if base in unit_names:
                internal_deps.add(base)
        deps[u["name"]] = internal_deps

    # Topological sort (post-order DFS)
    visited = set()
    order = []

    def dfs(name):
        if name in visited:
            return
        visited.add(name)
        for dep in deps.get(name, set()):
            dfs(dep)
        order.append(name)

    for u in units:
        dfs(u["name"])

    # Map back to unit dicts
    name_to_unit = {u["name"]: u for u in units}
    return [name_to_unit[n] for n in order if n in name_to_unit]


def translate_units(ir_data, source_language, target_language,
                    project_id=None, job_id=None, config=None,
                    dependency_mappings=None, feature_rules=None,
                    type_mappings=None, db_path=None):
    """Translate all units in IR using LLM with pass@k (D254).

    Returns dict with translated_units, mocked_units, failed_units, stats.
    """
    if config is None:
        config = _load_config()

    trans_config = config.get("translation", {})
    candidates_k = trans_config.get("candidates", 3)
    mock_on_failure = trans_config.get("mock_on_failure", True)
    max_mock_pct = trans_config.get("max_mock_pct", 20)

    ordered_units = _get_translation_order(ir_data)
    total = len(ordered_units)

    translated = []
    mocked = []
    failed = []
    translated_deps = []  # accumulate for context

    total_input_tokens = 0
    total_output_tokens = 0

    for idx, unit in enumerate(ordered_units):
        unit_name = unit.get("name", "unknown")

        # Build prompt
        prompt = _build_prompt(
            unit, ir_data, source_language, target_language,
            dependency_mappings or {},
            feature_rules or [],
            type_mappings or {},
            translated_deps,
            config,
        )

        # Try pass@k candidates (D254)
        best_result = None
        for k in range(candidates_k):
            result = _invoke_llm(prompt, config, "code_translation")
            if result and result.strip():
                # For now accept first non-empty result
                # Full validation happens in Phase 5
                best_result = result.strip()
                break

        if best_result:
            translated.append({
                "name": unit_name,
                "kind": unit.get("kind", "function"),
                "source_file": unit.get("source_file", ""),
                "translated_code": best_result,
                "status": "translated",
                "source_hash": unit.get("source_hash", ""),
                "candidate_selected": k + 1,
            })
            translated_deps.append({"name": unit_name, "kind": unit.get("kind", "function")})

            # Record in DB if available
            if db_path and job_id:
                _record_unit(db_path, job_id, unit, "translated", best_result, k + 1)
        else:
            # LLM failed — mock-and-continue (D256) or fail
            if mock_on_failure:
                mock_code = _generate_mock(unit, target_language)
                mocked.append({
                    "name": unit_name,
                    "kind": unit.get("kind", "function"),
                    "source_file": unit.get("source_file", ""),
                    "translated_code": mock_code,
                    "status": "mocked",
                    "source_hash": unit.get("source_hash", ""),
                })
                translated_deps.append({"name": unit_name, "kind": unit.get("kind", "function")})

                if db_path and job_id:
                    _record_unit(db_path, job_id, unit, "mocked", mock_code, 0)
            else:
                failed.append({
                    "name": unit_name,
                    "kind": unit.get("kind", "function"),
                    "source_file": unit.get("source_file", ""),
                    "status": "failed",
                    "error": "LLM returned empty response after all candidates",
                })
                if db_path and job_id:
                    _record_unit(db_path, job_id, unit, "failed", None, 0)

    # Check mock threshold
    mock_pct = (len(mocked) / total * 100) if total > 0 else 0
    mock_exceeded = mock_pct > max_mock_pct

    return {
        "translated_units": translated,
        "mocked_units": mocked,
        "failed_units": failed,
        "stats": {
            "total_units": total,
            "translated_count": len(translated),
            "mocked_count": len(mocked),
            "failed_count": len(failed),
            "mock_percentage": round(mock_pct, 1),
            "mock_threshold_exceeded": mock_exceeded,
            "candidates_k": candidates_k,
        },
    }


def _record_unit(db_path, job_id, unit, status, translated_code, candidate):
    """Record a translation unit result in the database."""
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        unit_id = str(uuid.uuid4())
        c.execute(
            """INSERT INTO translation_units
               (id, job_id, unit_name, unit_kind, source_file,
                source_code, translated_code, status,
                source_hash, candidate_selected)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                unit_id, job_id, unit.get("name", ""),
                unit.get("kind", "function"), unit.get("source_file", ""),
                unit.get("source_code", ""), translated_code, status,
                unit.get("source_hash", ""), candidate,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Phase 3 — LLM-assisted code translation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ir-file", required=True, help="Path to IR JSON file from source_extractor")
    parser.add_argument("--source-language", required=True, help="Source language")
    parser.add_argument("--target-language", required=True, help="Target language")
    parser.add_argument("--output-dir", help="Directory to write translated code")
    parser.add_argument("--project-id", help="Project ID for audit trail")
    parser.add_argument("--job-id", help="Translation job ID")
    parser.add_argument("--candidates", type=int, help="Override pass@k candidates")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Load IR
    ir_path = Path(args.ir_file)
    if not ir_path.exists():
        print(json.dumps({"error": f"IR file not found: {args.ir_file}"}))
        return

    with open(ir_path, "r", encoding="utf-8") as f:
        ir_data = json.load(f)

    config = _load_config()
    if args.candidates:
        config.setdefault("translation", {})["candidates"] = args.candidates

    # Load dependency mappings
    dep_mappings = {}
    try:
        from tools.translation.dependency_mapper import load_mappings, resolve_imports
        mappings = load_mappings()
        # Pre-resolve imports from IR
        imports = ir_data.get("imports", [])
        if imports:
            resolutions = resolve_imports(
                args.source_language, args.target_language,
                imports, mappings
            )
            dep_mappings = {r["source_import"]: r for r in resolutions}
    except ImportError:
        pass

    # Load feature rules
    feature_rules = []
    try:
        from tools.translation.feature_map import FeatureMapLoader
        loader = FeatureMapLoader()
        feature_rules = loader.get_rules(args.source_language, args.target_language)
    except ImportError:
        pass

    # Load type mappings
    type_mappings = {}
    try:
        from tools.translation.type_checker import load_type_mappings
        type_mappings = load_type_mappings()
    except ImportError:
        pass

    # Translate
    result = translate_units(
        ir_data=ir_data,
        source_language=args.source_language,
        target_language=args.target_language,
        project_id=args.project_id,
        job_id=args.job_id,
        config=config,
        dependency_mappings=dep_mappings,
        feature_rules=feature_rules,
        type_mappings=type_mappings,
        db_path=DB_PATH if DB_PATH.exists() else None,
    )

    # Write output files if output dir specified
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        all_units = result["translated_units"] + result["mocked_units"]
        for unit in all_units:
            code = unit.get("translated_code", "")
            if code:
                # Determine output filename
                src_file = unit.get("source_file", unit["name"])
                ext_map = {
                    "python": ".py", "java": ".java", "go": ".go",
                    "rust": ".rs", "csharp": ".cs",
                    "typescript": ".ts", "javascript": ".js",
                }
                ext = ext_map.get(args.target_language, ".txt")
                name = Path(src_file).stem + ext
                out_path = out_dir / name
                out_path.write_text(code, encoding="utf-8")

    # Audit trail
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="translation.unit_translated",
            actor="code_translator",
            action=f"Translated {result['stats']['translated_count']}/{result['stats']['total_units']} units "
                   f"from {args.source_language} to {args.target_language}",
            project_id=args.project_id,
            details=result["stats"],
        )
    except Exception:
        pass

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        stats = result["stats"]
        print(f"Translation: {args.source_language} → {args.target_language}")
        print(f"  Total units:  {stats['total_units']}")
        print(f"  Translated:   {stats['translated_count']}")
        print(f"  Mocked:       {stats['mocked_count']}")
        print(f"  Failed:       {stats['failed_count']}")
        print(f"  Mock %:       {stats['mock_percentage']}%")
        if stats["mock_threshold_exceeded"]:
            print(f"  WARNING: Mock percentage exceeds threshold!")


if __name__ == "__main__":
    main()
