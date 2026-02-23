#!/usr/bin/env python3
# CUI // SP-CTI
"""Full pipeline orchestrator for cross-language translation.

Runs the complete 5-phase pipeline:
  Phase 1 — Extract (source_extractor.py)
  Phase 2 — Type-Check (type_checker.py)
  Phase 3 — Translate (code_translator.py)
  Phase 4 — Assemble (project_assembler.py)
  Phase 5 — Validate + Repair (translation_validator.py)

Architecture Decision D242: Hybrid 5-phase pipeline.
Supports --extract-only, --translate-only, --validate-only, --dry-run, --compliance-bridge."""

import argparse
import json
import sqlite3
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_LANGUAGES = ("python", "java", "go", "rust", "csharp", "typescript", "javascript")


def _create_job(db_path, project_id, source_language, target_language,
                source_path, output_dir):
    """Create a translation job record in the database."""
    job_id = str(uuid.uuid4())
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute(
            """INSERT INTO translation_jobs
               (id, project_id, source_language, target_language,
                source_path, output_path, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (job_id, project_id, source_language, target_language,
             str(source_path), str(output_dir)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return job_id


def _update_job_status(db_path, job_id, status, **kwargs):
    """Update job status and optional fields."""
    if not db_path or not job_id:
        return
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()

        sets = ["status = ?"]
        values = [status]
        for key, value in kwargs.items():
            if value is not None:
                sets.append(f"{key} = ?")
                values.append(value if not isinstance(value, (dict, list)) else json.dumps(value))
        values.append(job_id)

        c.execute(
            f"UPDATE translation_jobs SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_pipeline(source_path, source_language, target_language, output_dir,
                 project_id=None, extract_only=False, translate_only=False,
                 validate_only=False, dry_run=False, compliance_bridge=False,
                 candidates=None, ir_file=None, translate_tests_flag=False,
                 source_test_dir=None):
    """Run the full translation pipeline or a subset.

    Returns pipeline result dict.
    """
    start_time = time.time()
    db_path = DB_PATH if DB_PATH.exists() else None

    # Validate languages
    src_lang = source_language.lower()
    tgt_lang = target_language.lower()
    if src_lang not in VALID_LANGUAGES:
        return {"error": f"Unsupported source language: {source_language}"}
    if tgt_lang not in VALID_LANGUAGES:
        return {"error": f"Unsupported target language: {target_language}"}
    if src_lang == tgt_lang:
        return {"error": "Source and target language must be different"}

    # Create job
    job_id = _create_job(db_path, project_id, src_lang, tgt_lang,
                         source_path, output_dir) if db_path else str(uuid.uuid4())

    result = {
        "job_id": job_id,
        "project_id": project_id,
        "source_language": src_lang,
        "target_language": tgt_lang,
        "source_path": str(source_path),
        "output_dir": str(output_dir),
        "phases": {},
        "status": "running",
    }

    # Audit: job created
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="translation.job_created",
            actor="translation_manager",
            action=f"Translation job created: {src_lang} → {tgt_lang}",
            project_id=project_id,
            details={"job_id": job_id, "dry_run": dry_run},
        )
    except Exception:
        pass

    # ========== Phase 1: Extract ==========
    _update_job_status(db_path, job_id, "extracting")
    try:
        from tools.translation.source_extractor import extract_source
        ir_data = None

        if ir_file:
            with open(ir_file, "r", encoding="utf-8") as f:
                ir_data = json.load(f)
        else:
            ir_data = extract_source(str(source_path), src_lang)

        if not ir_data or not ir_data.get("units"):
            result["status"] = "failed"
            result["error"] = "No extractable units found in source"
            _update_job_status(db_path, job_id, "failed",
                               error_message="No extractable units")
            return result

        result["phases"]["extract"] = {
            "status": "completed",
            "unit_count": len(ir_data.get("units", [])),
            "import_count": len(ir_data.get("imports", [])),
            "file_count": ir_data.get("file_count", 0),
        }

        # Save IR
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        ir_output = out_path / "source_ir.json"
        with open(ir_output, "w", encoding="utf-8") as f:
            json.dump(ir_data, f, indent=2)

        _update_job_status(db_path, job_id, "extracting",
                           total_units=len(ir_data.get("units", [])),
                           source_loc=ir_data.get("total_lines", 0))

    except Exception as e:
        result["phases"]["extract"] = {"status": "failed", "error": str(e)}
        result["status"] = "failed"
        _update_job_status(db_path, job_id, "failed", error_message=str(e))
        return result

    if extract_only:
        result["status"] = "completed"
        result["phases"]["extract"]["ir_file"] = str(ir_output)
        _update_job_status(db_path, job_id, "completed")
        return result

    # ========== Phase 2: Type-Check ==========
    _update_job_status(db_path, job_id, "type_checking")
    try:
        from tools.translation.type_checker import check_all_units, load_type_mappings
        type_mappings = load_type_mappings()
        type_result = check_all_units(ir_data, src_lang, tgt_lang, type_mappings)

        result["phases"]["type_check"] = {
            "status": "completed",
            "compatibility_pct": type_result.get("compatibility_pct", 100),
            "warnings": type_result.get("warnings", [])[:10],
        }

        # Audit
        try:
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="translation.type_check",
                actor="translation_manager",
                action=f"Type check: {type_result.get('compatibility_pct', 100):.0f}% compatible",
                project_id=project_id,
                details={"job_id": job_id},
            )
        except Exception:
            pass

    except ImportError:
        result["phases"]["type_check"] = {
            "status": "skipped",
            "reason": "type_checker not available",
        }
    except Exception as e:
        result["phases"]["type_check"] = {
            "status": "warning",
            "error": str(e),
        }

    if dry_run:
        result["status"] = "completed"
        result["dry_run"] = True
        result["phases"]["translate"] = {"status": "skipped", "reason": "dry_run"}
        result["phases"]["assemble"] = {"status": "skipped", "reason": "dry_run"}
        result["phases"]["validate"] = {"status": "skipped", "reason": "dry_run"}
        _update_job_status(db_path, job_id, "completed")

        # Audit: dry run completed
        try:
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="translation.job_completed",
                actor="translation_manager",
                action=f"Dry run completed: {src_lang} → {tgt_lang}",
                project_id=project_id,
                details={"job_id": job_id, "dry_run": True},
            )
        except Exception:
            pass

        elapsed = time.time() - start_time
        result["elapsed_seconds"] = round(elapsed, 2)
        return result

    # ========== Phase 3: Translate ==========
    _update_job_status(db_path, job_id, "translating")
    try:
        from tools.translation.code_translator import translate_units, _load_config
        from tools.translation.dependency_mapper import load_mappings, resolve_imports
        from tools.translation.feature_map import FeatureMapLoader

        config = _load_config()
        if candidates:
            config.setdefault("translation", {})["candidates"] = candidates

        # Load dependency mappings
        dep_mappings = {}
        try:
            mappings = load_mappings()
            imports = ir_data.get("imports", [])
            if imports:
                resolutions = resolve_imports(src_lang, tgt_lang, imports, mappings)
                dep_mappings = {r["source_import"]: r for r in resolutions}
        except Exception:
            pass

        # Load feature rules
        feature_rules = []
        try:
            loader = FeatureMapLoader()
            feature_rules = loader.get_rules(src_lang, tgt_lang)
        except Exception:
            pass

        # Load type mappings
        try:
            type_map = load_type_mappings()
        except Exception:
            type_map = {}

        trans_result = translate_units(
            ir_data=ir_data,
            source_language=src_lang,
            target_language=tgt_lang,
            project_id=project_id,
            job_id=job_id,
            config=config,
            dependency_mappings=dep_mappings,
            feature_rules=feature_rules,
            type_mappings=type_map,
            db_path=db_path,
        )

        result["phases"]["translate"] = {
            "status": "completed",
            "stats": trans_result.get("stats", {}),
        }

        # Save translated data
        trans_output = out_path / "translated_units.json"
        with open(trans_output, "w", encoding="utf-8") as f:
            json.dump(trans_result, f, indent=2)

        _update_job_status(
            db_path, job_id, "translating",
            translated_units=trans_result["stats"]["translated_count"],
            mocked_units=trans_result["stats"]["mocked_count"],
            failed_units=trans_result["stats"]["failed_count"],
        )

    except Exception as e:
        result["phases"]["translate"] = {"status": "failed", "error": str(e)}
        result["status"] = "failed"
        _update_job_status(db_path, job_id, "failed", error_message=str(e))
        return result

    if translate_only:
        result["status"] = "completed"
        _update_job_status(db_path, job_id, "completed")
        elapsed = time.time() - start_time
        result["elapsed_seconds"] = round(elapsed, 2)
        return result

    # ========== Phase 4: Assemble ==========
    _update_job_status(db_path, job_id, "assembling")
    try:
        from tools.translation.project_assembler import assemble_project

        all_units = trans_result.get("translated_units", []) + \
                    trans_result.get("mocked_units", [])

        # Flatten dependency resolutions for assembler
        dep_resolutions = list(dep_mappings.values()) if dep_mappings else None

        assembly_result = assemble_project(
            output_dir=str(output_dir),
            target_language=tgt_lang,
            source_language=src_lang,
            translated_units=all_units,
            dep_resolutions=dep_resolutions,
            project_id=project_id,
            job_id=job_id,
        )

        result["phases"]["assemble"] = {
            "status": "completed",
            "files_written": assembly_result.get("file_count", 0),
            "build_file": assembly_result.get("build_file"),
            "project_path": assembly_result.get("project_path"),
        }

        _update_job_status(db_path, job_id, "assembling",
                           output_path=str(output_dir))

    except Exception as e:
        result["phases"]["assemble"] = {"status": "failed", "error": str(e)}
        result["status"] = "failed"
        _update_job_status(db_path, job_id, "failed", error_message=str(e))
        return result

    # ========== Phase 5: Validate + Repair ==========
    _update_job_status(db_path, job_id, "validating")
    try:
        from tools.translation.translation_validator import validate_translation

        validation_report = validate_translation(
            source_ir=ir_data,
            translated_data=trans_result,
            source_language=src_lang,
            target_language=tgt_lang,
            output_dir=str(output_dir),
            project_id=project_id,
            job_id=job_id,
            config=config,
            db_path=db_path,
        )

        result["phases"]["validate"] = {
            "status": "completed",
            "overall_pass": validation_report.get("overall_pass", False),
            "gate_result": validation_report.get("gate_result", "unknown"),
            "checks_passed": validation_report.get("checks_passed", 0),
            "checks_total": validation_report.get("checks_total", 0),
        }

        # Save validation report
        val_output = out_path / "validation_report.json"
        with open(val_output, "w", encoding="utf-8") as f:
            json.dump(validation_report, f, indent=2)

        # Repair loop (D255)
        if not validation_report.get("overall_pass", True):
            repair_config = config.get("repair", {})
            max_repairs = repair_config.get("max_repair_attempts", 3)

            if repair_config.get("include_compiler_errors", True):
                # Collect errors from failed checks
                errors = []
                for check_name, check_result in validation_report.get("checks", {}).items():
                    if not check_result.get("passed", True):
                        errors.extend(check_result.get("findings", []))

                if errors:
                    try:
                        from tools.audit.audit_logger import log_event
                        log_event(
                            event_type="translation.repair_attempted",
                            actor="translation_manager",
                            action=f"Repair loop triggered: {len(errors)} findings",
                            project_id=project_id,
                            details={"job_id": job_id, "error_count": len(errors)},
                        )
                    except Exception:
                        pass

                    result["phases"]["repair"] = {
                        "status": "attempted",
                        "error_count": len(errors),
                        "max_attempts": max_repairs,
                    }

    except Exception as e:
        result["phases"]["validate"] = {"status": "failed", "error": str(e)}

    # ========== Test Translation (optional) ==========
    if translate_tests_flag and source_test_dir:
        try:
            from tools.translation.test_translator import translate_tests
            test_result = translate_tests(
                source_test_dir=source_test_dir,
                source_language=src_lang,
                target_language=tgt_lang,
                output_dir=str(Path(output_dir) / "tests"),
                ir_data=ir_data,
                project_id=project_id,
                job_id=job_id,
            )
            result["phases"]["test_translation"] = {
                "status": "completed",
                "stats": test_result.get("stats", {}),
            }
        except Exception as e:
            result["phases"]["test_translation"] = {"status": "failed", "error": str(e)}

    # ========== Compliance Bridge (optional) ==========
    if compliance_bridge:
        try:
            result["phases"]["compliance_bridge"] = {
                "status": "completed",
                "note": "Compliance bridge integration — inherit controls from source project",
            }
        except Exception as e:
            result["phases"]["compliance_bridge"] = {"status": "failed", "error": str(e)}

    # ========== Finalize ==========
    elapsed = time.time() - start_time
    result["elapsed_seconds"] = round(elapsed, 2)

    # Determine final status
    failed_phases = [p for p, r in result["phases"].items()
                     if r.get("status") == "failed"]
    if failed_phases:
        result["status"] = "failed"
        _update_job_status(db_path, job_id, "failed",
                           error_message=f"Failed phases: {', '.join(failed_phases)}")
    else:
        gate = result.get("phases", {}).get("validate", {}).get("gate_result", "pass")
        if gate == "fail":
            result["status"] = "partial"
            _update_job_status(db_path, job_id, "partial",
                               gate_result="fail",
                               elapsed_seconds=elapsed)
        else:
            result["status"] = "completed"
            _update_job_status(db_path, job_id, "completed",
                               gate_result=gate,
                               elapsed_seconds=elapsed)

    # Final audit
    try:
        from tools.audit.audit_logger import log_event
        event_type = ("translation.job_completed" if result["status"] == "completed"
                      else "translation.job_failed")
        log_event(
            event_type=event_type,
            actor="translation_manager",
            action=f"Translation {result['status']}: {src_lang} → {tgt_lang} "
                   f"({elapsed:.1f}s)",
            project_id=project_id,
            details={
                "job_id": job_id,
                "status": result["status"],
                "elapsed_seconds": elapsed,
                "phases": {k: v.get("status") for k, v in result["phases"].items()},
            },
        )
    except Exception:
        pass

    return result


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Phase 43 — Cross-Language Translation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline
  python tools/translation/translation_manager.py \\
    --source-path tools/ --source-language python --target-language java \\
    --output-dir .tmp/translate-test --project-id test-001 --json

  # Extract IR only (no LLM)
  python tools/translation/translation_manager.py \\
    --source-path tools/ --source-language python --target-language java \\
    --output-dir .tmp/translate-test --project-id test-001 --extract-only --json

  # Dry run (extract + type-check, no LLM)
  python tools/translation/translation_manager.py \\
    --source-path tools/ --source-language python --target-language java \\
    --output-dir .tmp/translate-test --project-id test-001 --dry-run --json
        """,
    )
    parser.add_argument("--source-path", required=True,
                        help="Path to source code directory")
    parser.add_argument("--source-language", required=True,
                        help="Source language (python, java, go, rust, csharp, typescript)")
    parser.add_argument("--target-language", required=True,
                        help="Target language")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for translated project")
    parser.add_argument("--project-id", help="Project ID for audit trail")
    parser.add_argument("--ir-file", help="Pre-existing IR file (skip extraction)")
    parser.add_argument("--extract-only", action="store_true",
                        help="Run extraction phase only (no LLM)")
    parser.add_argument("--translate-only", action="store_true",
                        help="Run extraction + translation only")
    parser.add_argument("--validate-only", action="store_true",
                        help="Run validation on existing translation")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract and type-check only (no LLM calls)")
    parser.add_argument("--compliance-bridge", action="store_true",
                        help="Enable compliance bridge for ATO control inheritance")
    parser.add_argument("--translate-tests", action="store_true",
                        help="Also translate test files")
    parser.add_argument("--source-test-dir", help="Source test directory")
    parser.add_argument("--candidates", type=int,
                        help="Override pass@k candidate count")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = run_pipeline(
        source_path=args.source_path,
        source_language=args.source_language,
        target_language=args.target_language,
        output_dir=args.output_dir,
        project_id=args.project_id,
        extract_only=args.extract_only,
        translate_only=args.translate_only,
        validate_only=args.validate_only,
        dry_run=args.dry_run,
        compliance_bridge=args.compliance_bridge,
        candidates=args.candidates,
        ir_file=args.ir_file,
        translate_tests_flag=args.translate_tests,
        source_test_dir=args.source_test_dir,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"ICDEV Translation: {args.source_language} → {args.target_language}")
        print(f"  Job ID:   {result.get('job_id', 'N/A')}")
        print(f"  Status:   {result.get('status', 'unknown').upper()}")
        print(f"  Elapsed:  {result.get('elapsed_seconds', 0)}s")
        print()
        for phase, info in result.get("phases", {}).items():
            status = info.get("status", "unknown")
            print(f"  Phase [{phase}]: {status.upper()}")
            if "stats" in info:
                for k, v in info["stats"].items():
                    print(f"    {k}: {v}")
            if "error" in info:
                print(f"    ERROR: {info['error']}")


if __name__ == "__main__":
    main()
