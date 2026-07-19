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
import time
import uuid
from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger
from pathlib import Path

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_LANGUAGES = ("python", "java", "go", "rust", "csharp", "typescript", "javascript")


def _create_job(db_path, project_id, source_language, target_language, source_path, output_dir):
    """Create a translation job record in the database."""
    job_id = str(uuid.uuid4())
    try:
        conn = get_connection(db_path=str(db_path))
        c = conn.cursor()
        c.execute(
            """INSERT INTO translation_jobs
               (id, project_id, source_language, target_language,
                source_path, output_path, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending')""",
            (job_id, project_id, source_language, target_language, str(source_path), str(output_dir)),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("translation _create_job persist failed for %s: %s", job_id, exc)
    return job_id


def _update_job_status(db_path, job_id, status, **kwargs):
    """Update job status and optional fields."""
    if not db_path or not job_id:
        return
    try:
        conn = get_connection(db_path=str(db_path))
        c = conn.cursor()

        # Runtime SQL is authored for PostgreSQL (%s paramstyle); translate_sql
        # rewrites %s -> ? for the SQLite init fallback. Mixing ? and %s in a
        # single statement is invalid on PG (the ? placeholders raise), which
        # previously left every status/metrics UPDATE swallowed and unpersisted
        # on the primary backend. Keep the whole statement %s.
        sets = ["status = %s"]
        values = [status]
        for key, value in kwargs.items():
            if value is not None:
                sets.append(f"{key} = %s")
                values.append(value if not isinstance(value, (dict, list)) else json.dumps(value))
        values.append(job_id)

        c.execute(
            f"UPDATE translation_jobs SET {', '.join(sets)} WHERE id = %s",  # nosec B608 -- table/column names are internal constants, not user input
            values,
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("translation _update_job_status(%s -> %s) failed: %s", job_id, status, exc)


def _collect_gate_errors(validation_report):
    """Collect findings from *verified*, failed checks — the compiler/validator
    feedback fed back into the repair prompt (D255).

    Not-verified checks (e.g. absent toolchain) carry no actionable error and are
    skipped, so a not-verified state never triggers a futile repair attempt.
    """
    errors = []
    for _check_name, check_result in (validation_report.get("checks", {}) or {}).items():
        if check_result.get("verified", True) and not check_result.get("passed", True):
            errors.extend(check_result.get("findings", []) or [])
    return errors


def _run_repair_loop(
    trans_result,
    source_ir,
    validation_report,
    src_lang,
    tgt_lang,
    output_dir,
    out_path,
    config,
    dep_mappings,
    project_id=None,
    job_id=None,
    db_path=None,
):
    """Bounded compiler-feedback repair loop (D255 — the designed "Validate+Repair"
    phase). Actually invokes ``repair_translation`` on real (non-mock) translated
    units whose validation failed, re-assembles, and re-validates after each
    attempt — replacing the previous no-op that only reported status "attempted".

    Bounded by ``repair.max_repair_attempts``. Stops early when the gate passes or
    when an attempt produces no code change. Returns a summary dict:
      ``{final_report, attempts, max_attempts, repaired_units, resolved}``.
    """
    # Source-module imports so tests can patch the source symbols directly.
    from tools.translation.translation_validator import repair_translation, validate_translation

    repair_config = config.get("repair", {}) if config else {}
    max_attempts = int(repair_config.get("max_repair_attempts", 3) or 0)
    include_compiler_errors = repair_config.get("include_compiler_errors", True)

    report = validation_report
    real_units = trans_result.get("translated_units", [])
    src_code_by_name = {u.get("name"): u.get("source_code", "") for u in source_ir.get("units", [])}

    repaired_names = set()
    attempts_used = 0

    for _attempt in range(1, max_attempts + 1):
        if report.get("overall_pass", False):
            break

        errors = _collect_gate_errors(report)
        if include_compiler_errors and not errors:
            # No actionable, verified error to feed back — nothing to repair.
            break

        attempts_used += 1
        changed_this_attempt = False

        for unit in real_units:
            # Never "repair" a mock — mocks are handled by the mock-and-continue
            # path and must not masquerade as repaired real translations.
            if unit.get("mock") is True or unit.get("status") == "mocked":
                continue

            current_code = unit.get("translated_code", "") or ""
            repaired_code = repair_translation(
                unit=unit,
                source_code=src_code_by_name.get(unit.get("name"), unit.get("source_code", "")),
                translated_code=current_code,
                errors=errors,
                source_language=src_lang,
                target_language=tgt_lang,
                config=config,
            )
            if repaired_code and repaired_code.strip() and repaired_code.strip() != current_code.strip():
                unit["translated_code"] = repaired_code.strip()
                unit["repaired"] = True
                unit["repair_attempts"] = int(unit.get("repair_attempts", 0)) + 1
                repaired_names.add(unit.get("name"))
                changed_this_attempt = True

        if not changed_this_attempt:
            # Repair produced no improvement — stop rather than burn attempts.
            break

        # Re-assemble so the syntax re-check sees the repaired code on disk.
        if output_dir:
            try:
                from tools.translation.project_assembler import assemble_project

                all_units = trans_result.get("translated_units", []) + trans_result.get("mocked_units", [])
                dep_resolutions = list(dep_mappings.values()) if dep_mappings else None
                assemble_project(
                    output_dir=str(output_dir),
                    target_language=tgt_lang,
                    source_language=src_lang,
                    translated_units=all_units,
                    dep_resolutions=dep_resolutions,
                    project_id=project_id,
                    job_id=job_id,
                )
            except Exception as exc:
                logger.warning("translation repair re-assembly failed: %s", exc)

        # Re-validate the repaired translation.
        report = validate_translation(
            source_ir=source_ir,
            translated_data=trans_result,
            source_language=src_lang,
            target_language=tgt_lang,
            output_dir=str(output_dir) if output_dir else None,
            project_id=project_id,
            job_id=job_id,
            config=config,
            db_path=db_path,
        )

    return {
        "final_report": report,
        "attempts": attempts_used,
        "max_attempts": max_attempts,
        "repaired_units": sorted(n for n in repaired_names if n),
        "resolved": bool(report.get("overall_pass", False)),
    }


def run_pipeline(
    source_path,
    source_language,
    target_language,
    output_dir,
    project_id=None,
    extract_only=False,
    translate_only=False,
    validate_only=False,
    dry_run=False,
    compliance_bridge=False,
    candidates=None,
    ir_file=None,
    translate_tests_flag=False,
    source_test_dir=None,
):
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
    job_id = (
        _create_job(db_path, project_id, src_lang, tgt_lang, source_path, output_dir) if db_path else str(uuid.uuid4())
    )

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
            _update_job_status(db_path, job_id, "failed", error_message="No extractable units")
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

        _update_job_status(
            db_path,
            job_id,
            "extracting",
            total_units=len(ir_data.get("units", [])),
            source_loc=ir_data.get("total_lines", 0),
        )

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
                # resolve_imports(import_list, source_lang, target_lang, mappings)
                # — the import list is the FIRST arg. Passing languages first left
                # _normalize_lang() calling .lower() on the imports list, raising
                # AttributeError that was swallowed, so dep_mappings was always {}.
                resolutions = resolve_imports(imports, src_lang, tgt_lang, mappings)
                dep_mappings = {r["source_import"]: r for r in resolutions}
        except Exception as exc:
            logger.warning("translation dependency resolution failed (%s->%s): %s", src_lang, tgt_lang, exc)

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
            db_path,
            job_id,
            "translating",
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

        all_units = trans_result.get("translated_units", []) + trans_result.get("mocked_units", [])

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

        _update_job_status(db_path, job_id, "assembling", output_path=str(output_dir))

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

        # Repair loop (D255) — actually invoke repair_translation, bounded, and
        # re-validate. Runs before the report is saved so the persisted report
        # reflects the post-repair state.
        if not validation_report.get("overall_pass", True):
            pre_repair_errors = _collect_gate_errors(validation_report)

            try:
                from tools.audit.audit_logger import log_event

                log_event(
                    event_type="translation.repair_attempted",
                    actor="translation_manager",
                    action=f"Repair loop triggered: {len(pre_repair_errors)} findings",
                    project_id=project_id,
                    details={"job_id": job_id, "error_count": len(pre_repair_errors)},
                )
            except Exception:
                pass

            repair_summary = _run_repair_loop(
                trans_result=trans_result,
                source_ir=ir_data,
                validation_report=validation_report,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                output_dir=output_dir,
                out_path=out_path,
                config=config,
                dep_mappings=dep_mappings,
                project_id=project_id,
                job_id=job_id,
                db_path=db_path,
            )

            # Adopt the post-repair validation report.
            validation_report = repair_summary["final_report"]
            result["phases"]["validate"] = {
                "status": "completed",
                "overall_pass": validation_report.get("overall_pass", False),
                "gate_result": validation_report.get("gate_result", "unknown"),
                "checks_passed": validation_report.get("checks_passed", 0),
                "checks_total": validation_report.get("checks_total", 0),
            }

            repaired_units = repair_summary["repaired_units"]
            result["phases"]["repair"] = {
                # "attempted" only when the loop actually ran but rewrote nothing;
                # "completed" when at least one unit was repaired. No longer a
                # misleading no-op that always claims "attempted".
                "status": "completed" if repaired_units else "attempted",
                "error_count": len(pre_repair_errors),
                "attempts": repair_summary["attempts"],
                "max_attempts": repair_summary["max_attempts"],
                "repaired_count": len(repaired_units),
                "repaired_units": repaired_units,
                "resolved": repair_summary["resolved"],
            }

            try:
                from tools.audit.audit_logger import log_event

                log_event(
                    event_type="translation.repair_completed",
                    actor="translation_manager",
                    action=(
                        f"Repair loop finished: {len(repaired_units)} unit(s) repaired "
                        f"over {repair_summary['attempts']} attempt(s), "
                        f"resolved={repair_summary['resolved']}"
                    ),
                    project_id=project_id,
                    details={
                        "job_id": job_id,
                        "repaired_count": len(repaired_units),
                        "attempts": repair_summary["attempts"],
                        "resolved": repair_summary["resolved"],
                    },
                )
            except Exception:
                pass

        # Save validation report (post-repair).
        val_output = out_path / "validation_report.json"
        with open(val_output, "w", encoding="utf-8") as f:
            json.dump(validation_report, f, indent=2)

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

    # Job summary (nav-intel-05) — distinguishes real / mock / repaired units and
    # EXCLUDES mocks from success metrics. A mock is an LLM failure degraded to a
    # stub (D256); it must never be counted as a successful translation.
    _tstats = trans_result.get("stats", {})
    _total_units = _tstats.get("total_units", 0)
    _real_count = _tstats.get("translated_count", 0)  # already excludes mocks
    _mock_count = _tstats.get("mocked_count", 0)
    _failed_count = _tstats.get("failed_count", 0)
    _repaired_count = result.get("phases", {}).get("repair", {}).get("repaired_count", 0)
    result["summary"] = {
        "total_units": _total_units,
        # Real (non-mock) translations only — the success numerator.
        "real_translations": _real_count,
        "mocked_units": _mock_count,
        "failed_units": _failed_count,
        "repaired_units": _repaired_count,
        "mock_percentage": _tstats.get("mock_percentage", 0),
        "mock_threshold_exceeded": _tstats.get("mock_threshold_exceeded", False),
        # Success rate over real translations, mocks explicitly excluded.
        "success_count": _real_count,
        "success_rate": round(_real_count / _total_units, 3) if _total_units else 0.0,
    }

    # Determine final status
    failed_phases = [p for p, r in result["phases"].items() if r.get("status") == "failed"]
    if failed_phases:
        result["status"] = "failed"
        _update_job_status(db_path, job_id, "failed", error_message=f"Failed phases: {', '.join(failed_phases)}")
    else:
        gate = result.get("phases", {}).get("validate", {}).get("gate_result", "pass")
        if gate == "fail":
            result["status"] = "partial"
            _update_job_status(db_path, job_id, "partial", gate_result="fail", elapsed_seconds=elapsed)
        else:
            result["status"] = "completed"
            _update_job_status(db_path, job_id, "completed", gate_result=gate, elapsed_seconds=elapsed)

    # Final audit
    try:
        from tools.audit.audit_logger import log_event

        event_type = "translation.job_completed" if result["status"] == "completed" else "translation.job_failed"
        log_event(
            event_type=event_type,
            actor="translation_manager",
            action=f"Translation {result['status']}: {src_lang} → {tgt_lang} ({elapsed:.1f}s)",
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
        description="ICDEV™ Phase 43 — Cross-Language Translation Pipeline",
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
    parser.add_argument("--source-path", required=True, help="Path to source code directory")
    parser.add_argument(
        "--source-language", required=True, help="Source language (python, java, go, rust, csharp, typescript)"
    )
    parser.add_argument("--target-language", required=True, help="Target language")
    parser.add_argument("--output-dir", required=True, help="Output directory for translated project")
    parser.add_argument("--project-id", help="Project ID for audit trail")
    parser.add_argument("--ir-file", help="Pre-existing IR file (skip extraction)")
    parser.add_argument("--extract-only", action="store_true", help="Run extraction phase only (no LLM)")
    parser.add_argument("--translate-only", action="store_true", help="Run extraction + translation only")
    parser.add_argument("--validate-only", action="store_true", help="Run validation on existing translation")
    parser.add_argument("--dry-run", action="store_true", help="Extract and type-check only (no LLM calls)")
    parser.add_argument(
        "--compliance-bridge", action="store_true", help="Enable compliance bridge for ATO control inheritance"
    )
    parser.add_argument("--translate-tests", action="store_true", help="Also translate test files")
    parser.add_argument("--source-test-dir", help="Source test directory")
    parser.add_argument("--candidates", type=int, help="Override pass@k candidate count")
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
        print(f"ICDEV™ Translation: {args.source_language} → {args.target_language}")
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
