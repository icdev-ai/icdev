# CUI // SP-CTI
"""CAM Code Refactoring Engine — cam_refactor_engine.py

Bridges the Cloud Application Migration (CAM) pipeline with ICDEV's code
transformation toolchain.  For each component in an mc_projects row the
engine:

  1. Reads context/migration/refactor_rules.yaml to decide which refactor
     job type(s) apply (based on source_tech, strategy_7r, language, framework).
  2. Inserts mc_refactor_jobs rows (status='queued').
  3. Executes each job:
       - db_ddl_generation  → db_migration_planner (schema DDL + DMS config)
       - code_scaffold       → per-tech scaffold generators below
       - ai_integration      → AI service integration templates
       - version_upgrade     → version_migrator.py  (when source_repo set)
       - framework_migration → framework_migrator.py (when source_repo set)
       - language_translation→ translation_manager.py (when source_repo set)
  4. Writes artifact files to data/cam_artifacts/<project_id>/<job_id>/
  5. Updates mc_refactor_jobs with status + artifacts_json.

CLI:
    python -m tools.migration_canvas.cam_refactor_engine --project proj-analytics-k8s-aws
    python -m tools.migration_canvas.cam_refactor_engine --project proj-analytics-k8s-aws --dry-run
    python -m tools.migration_canvas.cam_refactor_engine --project proj-analytics-k8s-aws --component oracle
    python -m tools.migration_canvas.cam_refactor_engine --list-jobs proj-analytics-k8s-aws
    python -m tools.migration_canvas.cam_refactor_engine --run-job <job_id>
"""
from __future__ import annotations

import json
import logging
import os
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cam_refactor_engine")

_ROOT = Path(__file__).resolve().parents[2]
_REFACTOR_RULES_PATH = _ROOT / "context" / "migration" / "refactor_rules.yaml"
_ARTIFACTS_BASE = _ROOT / "data" / "cam_artifacts"

# ── Bedrock model identifiers for generated migration artifacts ──────────────
# LLM config via .env — never hardcode model IDs in code.  These are the model
# identifiers baked into the AWS Bedrock scaffolds this engine generates; they
# are configurable via .env so an operator can retarget the customer's Bedrock
# models without editing generator code.  The generated Python artifacts also
# read from the same env vars at runtime (with the resolved value as default),
# so they stay configurable in the customer's own environment.


def _bedrock_embed_model_id() -> str:
    """Resolve the Bedrock embedding model id (env-overridable)."""
    return (
        os.environ.get("MC_BEDROCK_EMBED_MODEL_ID")
        or os.environ.get("BEDROCK_EMBED_MODEL_ID")
        or "amazon.titan-embed-text-v2:0"
    )


def _bedrock_llm_model_arn() -> str:
    """Resolve the Bedrock foundation-model ARN for RAG (env-overridable)."""
    arn = os.environ.get("MC_BEDROCK_LLM_MODEL_ARN") or os.environ.get("BEDROCK_LLM_MODEL_ARN")
    if arn:
        return arn
    region = os.environ.get("MC_BEDROCK_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
    model_id = (
        os.environ.get("MC_BEDROCK_LLM_MODEL_ID")
        or "anthropic.claude-3-sonnet-20240229-v1:0"
    )
    return f"arn:aws:bedrock:{region}::foundation-model/{model_id}"

# ── DB helpers ───────────────────────────────────────────────────────────────

def _mc_conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Rule loading ─────────────────────────────────────────────────────────────

def _load_refactor_rules() -> list[dict]:
    """Load dispatch rules from context/migration/refactor_rules.yaml."""
    try:
        import yaml  # type: ignore[import-untyped]
        with open(_REFACTOR_RULES_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data.get("rules", [])
    except Exception as exc:
        logger.warning("Could not load refactor_rules.yaml: %s", exc)
        return []


def _match_rules(component: dict, rules: list[dict]) -> list[dict]:
    """Return the list of job specs that match this component."""
    # Use full name as substring search — handles "Apache NiFi", "Redis 7 Cache", etc.
    tech = (component.get("name") or "").lower()
    strategy = (component.get("migration_strategy") or "").lower()
    language = (component.get("language") or "").lower()
    version = (component.get("version") or "")
    framework = (component.get("framework") or "").lower()

    matched: list[dict] = []
    for rule in rules:
        if "match_tech" in rule:
            if rule["match_tech"].lower() not in tech:
                continue
            if "match_strategy" in rule and rule["match_strategy"] != strategy:
                continue
            matched.extend(rule.get("jobs", []))
        elif "match_language" in rule:
            if rule["match_language"].lower() != language:
                continue
            if "match_version_prefix" in rule:
                if not version.startswith(rule["match_version_prefix"]):
                    continue
            matched.extend(rule.get("jobs", []))
        elif "match_framework" in rule:
            if rule["match_framework"].lower() not in framework:
                continue
            matched.extend(rule.get("jobs", []))
    return matched


# ── Job creation ─────────────────────────────────────────────────────────────

def dispatch(project_id: str, component_name: str | None = None,
             dry_run: bool = False) -> list[dict]:
    """Queue refactor jobs for all (or one named) component in the project.

    Returns list of job dicts (with status='queued' or 'dry_run').
    """
    conn = _mc_conn()
    try:
        proj = conn.execute(
            "SELECT id, name, mc_session_id FROM mc_projects WHERE id=%s", (project_id,)
        ).fetchone()
        if not proj:
            raise ValueError(f"Project not found: {project_id}")

        session_id = proj["mc_session_id"] if hasattr(proj, "__getitem__") else proj[2]
        components = conn.execute(
            "SELECT * FROM mc_app_inventory WHERE session_id=%s", (session_id,)
        ).fetchall()

        ai_opps = {
            row["component_name"]: row
            for row in conn.execute(
                "SELECT * FROM mc_ai_opportunities WHERE project_id=%s", (project_id,)
            ).fetchall()
        }

        rules = _load_refactor_rules()
        queued: list[dict] = []

        for comp in components:
            comp_dict = dict(comp) if hasattr(comp, "keys") else {}
            comp_name = comp_dict.get("name", "")

            if component_name and component_name.lower() not in comp_name.lower():
                continue

            job_specs = _match_rules(comp_dict, rules)
            for spec in job_specs:
                ai_opp_id = None
                if "ai_service" in spec:
                    for opp in ai_opps.values():
                        opp_d = dict(opp)
                        if opp_d.get("aws_ai_service") == spec["ai_service"]:
                            ai_opp_id = opp_d.get("id")
                            break

                job_id = str(uuid.uuid4())
                tech_key = comp_name.lower().split()[0]
                output_path = str(
                    _ARTIFACTS_BASE / project_id / job_id
                )
                job = {
                    "id": job_id,
                    "project_id": project_id,
                    "app_id": comp_dict.get("id"),
                    "component_name": comp_name,
                    "refactor_type": spec["type"],
                    "source_tech": comp_dict.get("name", tech_key),
                    "target_tech": spec.get("target_tech", spec.get("target_db", spec.get("target_framework", ""))),
                    "source_language": spec.get("source_language", comp_dict.get("language", "")),
                    "target_language": spec.get("target_language", ""),
                    "source_path": comp_dict.get("source_repo", ""),
                    "output_path": output_path,
                    "params_json": json.dumps({
                        k: v for k, v in spec.items()
                        if k not in ("type", "description")
                    }),
                    "status": "queued",
                    "result_summary": spec.get("description", ""),
                    "artifacts_json": "[]",
                    "error_message": "",
                    "triggered_by": "auto",
                    "ai_opp_id": ai_opp_id,
                    "classification": "CUI // SP-CTI",
                    "created_at": _now(),
                }

                if not dry_run:
                    # Skip if identical job already queued/completed for this component
                    existing = conn.execute(
                        "SELECT id FROM mc_refactor_jobs "
                        "WHERE project_id=%s AND component_name=%s AND refactor_type=%s "
                        "  AND params_json=%s AND status NOT IN ('failed')",
                        (project_id, comp_name, spec["type"], job["params_json"]),
                    ).fetchone()
                    if existing:
                        continue

                    conn.execute(
                        "INSERT INTO mc_refactor_jobs "
                        "(id, project_id, app_id, component_name, refactor_type, "
                        " source_tech, target_tech, source_language, target_language, "
                        " source_path, output_path, params_json, status, result_summary, "
                        " artifacts_json, error_message, triggered_by, ai_opp_id, "
                        " classification, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            job["id"], job["project_id"], job["app_id"],
                            job["component_name"], job["refactor_type"],
                            job["source_tech"], job["target_tech"],
                            job["source_language"], job["target_language"],
                            job["source_path"], job["output_path"],
                            job["params_json"], job["status"],
                            job["result_summary"], job["artifacts_json"],
                            job["error_message"], job["triggered_by"],
                            job["ai_opp_id"], job["classification"], job["created_at"],
                        ),
                    )
                    conn.commit()

                queued.append(job)

        return queued
    finally:
        conn.close()


# ── Job execution ────────────────────────────────────────────────────────────

def run_job(job_id: str) -> dict:
    """Execute a single queued refactor job. Returns updated job dict."""
    conn = _mc_conn()
    try:
        row = conn.execute(
            "SELECT * FROM mc_refactor_jobs WHERE id=%s", (job_id,)
        ).fetchone()
        if not row:
            return {"error": f"Job not found: {job_id}"}

        job = dict(row)
        if job["status"] not in ("queued", "failed"):
            return {"error": f"Job is not runnable (status={job['status']})"}

        # Mark running
        conn.execute(
            "UPDATE mc_refactor_jobs SET status='running', started_at=%s WHERE id=%s",
            (_now(), job_id),
        )
        conn.commit()

        try:
            artifacts, summary = _execute_job(job)
            conn.execute(
                "UPDATE mc_refactor_jobs "
                "SET status='completed', completed_at=%s, result_summary=%s, artifacts_json=%s "
                "WHERE id=%s",
                (_now(), summary, json.dumps(artifacts), job_id),
            )
            conn.commit()
            job.update(status="completed", result_summary=summary, artifacts_json=json.dumps(artifacts))
            logger.info("Job %s completed — %d artifact(s)", job_id, len(artifacts))
        except Exception as exc:
            logger.exception("Job %s failed: %s", job_id, exc)
            conn.execute(
                "UPDATE mc_refactor_jobs "
                "SET status='failed', completed_at=%s, error_message=%s WHERE id=%s",
                (_now(), str(exc), job_id),
            )
            conn.commit()
            job.update(status="failed", error_message=str(exc))

        return job
    finally:
        conn.close()


def run_all(project_id: str, component_name: str | None = None,
            dry_run: bool = False) -> dict:
    """Dispatch + immediately run all matching jobs for a project."""
    queued = dispatch(project_id, component_name=component_name, dry_run=dry_run)
    if dry_run:
        return {"dry_run": True, "would_queue": len(queued), "jobs": queued}

    results = [run_job(j["id"]) for j in queued]
    completed = sum(1 for r in results if r.get("status") == "completed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    return {
        "total": len(results),
        "completed": completed,
        "failed": failed,
        "jobs": results,
    }


def get_jobs(project_id: str) -> list[dict]:
    """Return all mc_refactor_jobs for a project, newest first."""
    conn = _mc_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mc_refactor_jobs WHERE project_id=%s ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        jobs = [dict(r) for r in rows]
        for j in jobs:
            j["artifacts"] = json.loads(j.get("artifacts_json") or "[]")
        return jobs
    finally:
        conn.close()


def get_job_detail(job_id: str) -> dict | None:
    """Return single job with parsed artifacts list."""
    conn = _mc_conn()
    try:
        row = conn.execute(
            "SELECT * FROM mc_refactor_jobs WHERE id=%s", (job_id,)
        ).fetchone()
        if not row:
            return None
        job = dict(row)
        job["artifacts"] = json.loads(job.get("artifacts_json") or "[]")
        job["params"] = json.loads(job.get("params_json") or "{}")
        return job
    finally:
        conn.close()


# ── Job dispatcher ───────────────────────────────────────────────────────────

def _execute_job(job: dict) -> tuple[list[dict], str]:
    """Route job to the appropriate tool / scaffold generator."""
    rtype = job["refactor_type"]
    params = json.loads(job.get("params_json") or "{}")
    source_path = job.get("source_path") or ""
    output_dir = Path(job["output_path"])
    output_dir.mkdir(parents=True, exist_ok=True)

    if rtype == "db_ddl_generation":
        return _run_db_ddl_generation(job, params, source_path, output_dir)
    if rtype == "code_scaffold":
        return _run_code_scaffold(job, params, output_dir)
    if rtype == "ai_integration":
        return _run_ai_integration(job, params, output_dir)
    if rtype == "version_upgrade" and source_path:
        return _run_version_upgrade(job, params, source_path, output_dir)
    if rtype == "framework_migration" and source_path:
        return _run_framework_migration(job, params, source_path, output_dir)
    if rtype == "language_translation" and source_path:
        return _run_language_translation(job, params, source_path, output_dir)
    # Fallback for tool-based types when source_repo is not set
    return _gen_stub_readme(job, params, output_dir)


# ── Tool integrations ─────────────────────────────────────────────────────────

def _run_db_ddl_generation(job, params, source_path, output_dir) -> tuple[list[dict], str]:
    """Generate Aurora-compatible DDL + DMS task config."""
    source_db = params.get("source_db", "oracle")
    target_db = params.get("target_db", "aurora-postgresql")

    # Attempt to delegate to db_migration_planner if legacy_applications row exists
    try:
        from tools.modernization.db_migration_planner import (
            generate_schema_ddl,
            load_type_mappings,
        )
        mappings = load_type_mappings(source_db, target_db)
        if mappings and job.get("app_id"):
            generate_schema_ddl(job["app_id"], target_db, str(output_dir))
            artifacts = _scan_artifacts(output_dir, "db_ddl")
            if artifacts:
                return artifacts, f"DDL generated via db_migration_planner ({source_db} → {target_db})"
    except Exception as exc:
        logger.debug("db_migration_planner fallback: %s", exc)

    # Scaffold mode — generate from templates when no legacy DB rows
    return _gen_db_ddl_scaffold(job, source_db, target_db, output_dir)


def _run_version_upgrade(job, params, source_path, output_dir) -> tuple[list[dict], str]:
    src_lang = params.get("source_language", job.get("source_language", "python"))
    src_ver = params.get("source_version", "2")
    tgt_ver = params.get("target_version", "3")

    try:
        from tools.modernization.version_migrator import _dispatch_migration
        result = _dispatch_migration(src_lang, source_path, str(output_dir), src_ver, tgt_ver)
        artifacts = _scan_artifacts(output_dir, "source")
        summary = f"{src_lang} {src_ver} → {tgt_ver} migration: {result.get('files_transformed', 0)} files transformed"
        return artifacts, summary
    except Exception as exc:
        raise RuntimeError(f"version_migrator failed: {exc}") from exc


def _run_framework_migration(job, params, source_path, output_dir) -> tuple[list[dict], str]:
    src_fw = params.get("source_framework", "")
    tgt_fw = params.get("target_framework", "")

    try:
        from tools.modernization.framework_migrator import MIGRATION_MAP
        key = (src_fw, tgt_fw)
        if key in MIGRATION_MAP:
            fn_name = MIGRATION_MAP[key]
            from tools.modernization import framework_migrator as fm
            fn = getattr(fm, fn_name)
            fn(source_path, str(output_dir))
            artifacts = _scan_artifacts(output_dir, "source")
            return artifacts, f"{src_fw} → {tgt_fw} migration completed"
        raise ValueError(f"No migration function for {src_fw} → {tgt_fw}")
    except Exception as exc:
        raise RuntimeError(f"framework_migrator failed: {exc}") from exc


def _run_language_translation(job, params, source_path, output_dir) -> tuple[list[dict], str]:
    src_lang = params.get("source_language", job.get("source_language", ""))
    tgt_lang = params.get("target_language", job.get("target_language", ""))

    try:
        from tools.translation.translation_manager import run_pipeline
        result = run_pipeline(
            source_path=source_path,
            source_language=src_lang,
            target_language=tgt_lang,
            output_dir=str(output_dir),
            project_id=job.get("project_id"),
        )
        if result.get("error"):
            raise RuntimeError(result["error"])
        phases = result.get("phases", {})
        artifacts = _scan_artifacts(output_dir, "source")
        translated = phases.get("translate", {}).get("translated_units", 0)
        return artifacts, f"{src_lang} → {tgt_lang}: {translated} units translated"
    except Exception as exc:
        raise RuntimeError(f"translation_manager failed: {exc}") from exc


def _run_code_scaffold(job, params, output_dir) -> tuple[list[dict], str]:
    scaffold_type = params.get("scaffold_type", "")
    generators = {
        "elasticache_config": _scaffold_elasticache,
        "opensearch_migration": _scaffold_opensearch,
        "glue_etl_template": _scaffold_glue_etl,
        "msk_migration": _scaffold_msk,
        "amplify_migration": _scaffold_amplify,
        "eks_fargate_migration": _scaffold_eks_fargate,
        "documentdb_migration": _scaffold_documentdb,
    }
    gen = generators.get(scaffold_type, _gen_stub_readme_fn)
    return gen(job, params, output_dir)


def _run_ai_integration(job, params, output_dir) -> tuple[list[dict], str]:
    scaffold_type = params.get("scaffold_type", "")
    generators = {
        "pgvector_schema": _scaffold_pgvector,
        "semantic_cache": _scaffold_semantic_cache,
        "bedrock_kb": _scaffold_bedrock_kb,
        "bedrock_agents_hook": _scaffold_bedrock_agents,
    }
    gen = generators.get(scaffold_type, _gen_stub_readme_fn)
    return gen(job, params, output_dir)


# ── Scaffold generators ──────────────────────────────────────────────────────

def _write(path: Path, content: str) -> dict:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8", newline="")
    return {"type": "file", "path": str(path), "name": path.name,
            "size_bytes": path.stat().st_size, "description": path.stem.replace("_", " ")}


def _gen_db_ddl_scaffold(job, source_db, target_db, output_dir) -> tuple[list[dict], str]:
    comp = job["component_name"]
    arts = []

    # Aurora schema DDL template
    arts.append(_write(output_dir / "aurora_schema_migration.sql", f"""\
        -- CUI // SP-CTI
        -- Aurora PostgreSQL schema migration scaffold
        -- Generated for: {comp} ({source_db} → {target_db})
        -- Review and adjust column types before executing against Aurora.

        -- Enable required extensions
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

        -- Example table migration (replace with actual schema from AWs SCT output)
        -- Oracle NUMBER(10) → INTEGER, NUMBER(19,4) → NUMERIC(19,4)
        -- Oracle VARCHAR2(n) → VARCHAR(n), CLOB → TEXT, DATE → TIMESTAMP
        -- Oracle BLOB → BYTEA, XMLTYPE → XML, ROWID → UUID DEFAULT uuid_generate_v4()

        CREATE TABLE IF NOT EXISTS example_entity (
            id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            legacy_id   INTEGER     NOT NULL,            -- maps Oracle ROWNUM / ROWID
            name        VARCHAR(255) NOT NULL,
            description TEXT,
            amount      NUMERIC(19,4),
            created_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
            is_active   BOOLEAN     NOT NULL DEFAULT TRUE
        );

        CREATE INDEX IF NOT EXISTS idx_example_entity_legacy_id ON example_entity(legacy_id);
        CREATE INDEX IF NOT EXISTS idx_example_entity_created ON example_entity(created_at);

        -- Row count validation (run before cutover to confirm DMS load)
        -- SELECT COUNT(*) FROM source_schema.example_entity;   -- Oracle
        -- SELECT COUNT(*) FROM example_entity;                 -- Aurora
    """))

    # DMS task definition
    arts.append(_write(output_dir / "dms_task_definition.json", json.dumps({
        "ReplicationTaskIdentifier": f"dms-{comp.lower().replace(' ', '-')}-migration",
        "SourceEndpointArn": "arn:aws:dms:us-east-1:ACCOUNT_ID:endpoint:SOURCE_ENDPOINT",
        "TargetEndpointArn": "arn:aws:dms:us-east-1:ACCOUNT_ID:endpoint:TARGET_ENDPOINT",
        "ReplicationInstanceArn": "arn:aws:dms:us-east-1:ACCOUNT_ID:rep:REPLICATION_INSTANCE",
        "MigrationType": "full-load-and-cdc",
        "TableMappings": {
            "rules": [
                {
                    "rule-type": "selection",
                    "rule-id": "1",
                    "rule-name": "include-all-tables",
                    "object-locator": {"schema-name": "APP_SCHEMA", "table-name": "%"},
                    "rule-action": "include"
                }
            ]
        },
        "ReplicationTaskSettings": {
            "TargetMetadata": {"TargetSchema": "public", "SupportLobs": True, "FullLobMode": False, "LobChunkSize": 64},
            "FullLoadSettings": {"TargetTablePrepMode": "DO_NOTHING", "CreatePkAfterFullLoad": False},
            "Logging": {"EnableLogging": True, "LogComponents": [{"Id": "SOURCE_UNLOAD", "Severity": "LOGGER_SEVERITY_DEFAULT"}]}
        }
    }, indent=2)))

    # Validation queries
    arts.append(_write(output_dir / "row_count_validation.sql", """\
        -- CUI // SP-CTI
        -- Row count validation queries — run against both source and target
        -- after DMS full-load completes.  Counts must match before enabling CDC.

        -- Run on Oracle source:
        SELECT table_name, num_rows
        FROM   all_tables
        WHERE  owner = 'APP_SCHEMA'
        ORDER  BY table_name;

        -- Run on Aurora target:
        SELECT relname AS table_name,
               reltuples::BIGINT AS approximate_row_count
        FROM   pg_class
        WHERE  relkind = 'r'
          AND  relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
        ORDER  BY relname;

        -- Spot-check a critical table (adjust table/column names):
        -- Oracle:  SELECT COUNT(*), MAX(updated_at) FROM app_schema.orders;
        -- Aurora:  SELECT COUNT(*), MAX(updated_at) FROM orders;
    """))

    summary = f"DDL scaffold for {source_db} → {target_db}: {len(arts)} artifacts"
    return arts, summary


def _scaffold_pgvector(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "pgvector_setup.sql", """\
        -- CUI // SP-CTI
        -- pgvector setup: semantic search layer in Aurora PostgreSQL
        -- Run AFTER DMS migration is complete and Aurora schema is stable.

        CREATE EXTENSION IF NOT EXISTS vector;

        -- Embedding column on an existing table (adjust table/column names)
        ALTER TABLE example_entity
            ADD COLUMN IF NOT EXISTS embedding vector(1536);

        -- HNSW index for fast approximate nearest-neighbour search
        -- m=16, ef_construction=64 are good defaults for 1536-dim Bedrock Titan embeddings
        CREATE INDEX IF NOT EXISTS idx_example_entity_embedding_hnsw
            ON example_entity
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);

        -- IVFFlat alternative (faster build, lower recall):
        -- CREATE INDEX idx_example_entity_embedding_ivfflat
        --     ON example_entity USING ivfflat (embedding vector_cosine_ops)
        --     WITH (lists = 100);

        -- Semantic search function
        CREATE OR REPLACE FUNCTION semantic_search(
            query_embedding vector(1536),
            similarity_threshold FLOAT DEFAULT 0.7,
            max_results INT DEFAULT 10
        )
        RETURNS TABLE(id UUID, name VARCHAR, similarity FLOAT) AS $$
            SELECT id, name,
                   1 - (embedding <=> query_embedding) AS similarity
            FROM   example_entity
            WHERE  embedding IS NOT NULL
              AND  1 - (embedding <=> query_embedding) >= similarity_threshold
            ORDER  BY embedding <=> query_embedding
            LIMIT  max_results;
        $$ LANGUAGE sql STABLE;
    """))

    _embed_backfill_tpl = """\
        # CUI // SP-CTI
        # Backfill embeddings for existing rows using an Amazon Bedrock embed model.
        # Run once after DMS full-load, before HNSW index creation.
        # Model id is configurable via the BEDROCK_EMBED_MODEL_ID env var.
        import boto3
        import json
        import os
        import psycopg2

        BEDROCK = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        MODEL_ID = os.environ.get("BEDROCK_EMBED_MODEL_ID", "__EMBED_MODEL_ID__")
        BATCH_SIZE = 100

        def get_embedding(text: str) -> list[float]:
            resp = BEDROCK.invoke_model(
                modelId=MODEL_ID,
                body=json.dumps({"inputText": text}),
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(resp["body"].read())["embedding"]

        def backfill(conn_str: str, text_column: str = "name", table: str = "example_entity"):
            conn = psycopg2.connect(conn_str)
            cur = conn.cursor()
            cur.execute(f"SELECT id, {text_column} FROM {table} WHERE embedding IS NULL")
            rows = cur.fetchmany(BATCH_SIZE)
            while rows:
                for row_id, text in rows:
                    if text:
                        emb = get_embedding(text)
                        cur.execute(
                            f"UPDATE {table} SET embedding = %s::vector WHERE id = %s",
                            (str(emb), row_id),
                        )
                conn.commit()
                rows = cur.fetchmany(BATCH_SIZE)
            conn.close()
            print("Backfill complete.")

        if __name__ == "__main__":
            backfill(os.environ["AURORA_CONN_STR"])
    """
    arts.append(_write(
        output_dir / "embed_existing_rows.py",
        _embed_backfill_tpl.replace("__EMBED_MODEL_ID__", _bedrock_embed_model_id()),
    ))

    return arts, f"pgvector setup: {len(arts)} artifacts (SQL + embedding backfill script)"


def _scaffold_elasticache(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "elasticache_cluster.tf", """\
        # CUI // SP-CTI — Terraform: ElastiCache for Redis
        resource "aws_elasticache_subnet_group" "redis" {
          name       = "analytics-redis-subnet-group"
          subnet_ids = var.private_subnet_ids
        }

        resource "aws_elasticache_replication_group" "redis" {
          replication_group_id       = "analytics-redis"
          description                = "Analytics platform Redis cache (migrated from K8s)"
          node_type                  = "cache.r7g.large"
          num_cache_clusters         = 2
          automatic_failover_enabled = true
          multi_az_enabled           = true
          engine_version             = "7.1"
          port                       = 6379
          subnet_group_name          = aws_elasticache_subnet_group.redis.name
          security_group_ids         = [aws_security_group.redis.id]
          at_rest_encryption_enabled = true
          transit_encryption_enabled = true
          auth_token                 = var.redis_auth_token

          tags = { Classification = "CUI", Project = "analytics-migration" }
        }

        output "redis_primary_endpoint" {
          value = aws_elasticache_replication_group.redis.primary_endpoint_address
        }
    """))

    arts.append(_write(output_dir / "redis_dump_restore.sh", """\
        #!/usr/bin/env bash
        # CUI // SP-CTI
        # Redis DUMP/RESTORE migration: K8s Redis → ElastiCache
        # Run from a bastion host with access to both endpoints.
        set -euo pipefail

        SOURCE_HOST="${SOURCE_REDIS_HOST:-redis.kube-system.svc.cluster.local}"
        SOURCE_PORT="${SOURCE_REDIS_PORT:-6379}"
        TARGET_HOST="${TARGET_ELASTICACHE_HOST:-analytics-redis.xxxx.cache.amazonaws.com}"
        TARGET_PORT="6379"
        TARGET_AUTH="${TARGET_REDIS_AUTH:-}"

        echo "Scanning source keys..."
        redis-cli -h "$SOURCE_HOST" -p "$SOURCE_PORT" --scan --pattern '*' | while read -r KEY; do
          TTL=$(redis-cli -h "$SOURCE_HOST" -p "$SOURCE_PORT" pttl "$KEY")
          DUMP=$(redis-cli -h "$SOURCE_HOST" -p "$SOURCE_PORT" dump "$KEY")
          if [ -n "$TARGET_AUTH" ]; then
            redis-cli -h "$TARGET_HOST" -p "$TARGET_PORT" -a "$TARGET_AUTH" \\
              RESTORE "$KEY" "$TTL" "$DUMP" REPLACE
          else
            redis-cli -h "$TARGET_HOST" -p "$TARGET_PORT" \\
              RESTORE "$KEY" "$TTL" "$DUMP" REPLACE
          fi
        done
        echo "Migration complete. Verify with: redis-cli -h $TARGET_HOST dbsize"
    """))

    arts.append(_write(output_dir / "redis_client_adapter.py", """\
        # CUI // SP-CTI
        # Drop-in Redis adapter: works with both standalone Redis and ElastiCache.
        # Replace direct redis.Redis() calls with RedisAdapter() in your application.
        import os
        import redis

        class RedisAdapter:
            \"\"\"Thin wrapper that connects to either standalone Redis or ElastiCache.\"\"\"

            def __init__(self):
                host = os.environ.get("REDIS_HOST", "localhost")
                port = int(os.environ.get("REDIS_PORT", 6379))
                password = os.environ.get("REDIS_PASSWORD") or os.environ.get("REDIS_AUTH_TOKEN")
                ssl = os.environ.get("REDIS_TLS", "false").lower() == "true"
                self._client = redis.Redis(
                    host=host, port=port, password=password,
                    ssl=ssl, decode_responses=True,
                    socket_connect_timeout=5, retry_on_timeout=True,
                )

            def __getattr__(self, name):
                return getattr(self._client, name)

            def health_check(self) -> bool:
                try:
                    return self._client.ping()
                except Exception:
                    return False
    """))

    return arts, f"ElastiCache scaffold: {len(arts)} artifacts (Terraform + DUMP/RESTORE + adapter)"


def _scaffold_semantic_cache(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    _semcache_tpl = """\
        # CUI // SP-CTI
        # Semantic cache using Bedrock embeddings + ElastiCache Redis.
        # Reduces Bedrock API spend by caching semantically similar queries.
        # Embed model id is configurable via the BEDROCK_EMBED_MODEL_ID env var.
        import hashlib
        import json
        import os
        import boto3
        import redis

        BEDROCK = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        EMBED_MODEL = os.environ.get("BEDROCK_EMBED_MODEL_ID", "__EMBED_MODEL_ID__")
        SIMILARITY_THRESHOLD = float(os.environ.get("SEMANTIC_CACHE_THRESHOLD", "0.92"))
        TTL_SECONDS = int(os.environ.get("SEMANTIC_CACHE_TTL", "3600"))

        _redis = redis.Redis(
            host=os.environ["REDIS_HOST"], port=6379,
            password=os.environ.get("REDIS_AUTH_TOKEN"),
            ssl=True, decode_responses=True,
        )

        def _embed(text: str) -> list[float]:
            resp = BEDROCK.invoke_model(
                modelId=EMBED_MODEL,
                body=json.dumps({"inputText": text}),
                contentType="application/json", accept="application/json",
            )
            return json.loads(resp["body"].read())["embedding"]

        def _cosine_similarity(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            mag_a = sum(x * x for x in a) ** 0.5
            mag_b = sum(x * x for x in b) ** 0.5
            return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0

        def cached_invoke(prompt: str, invoke_fn) -> str:
            \"\"\"Return cached response if semantically similar prompt exists.\"\"\"
            emb = _embed(prompt)
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:12]

            # Scan recent cache keys for semantic match
            cursor, keys = _redis.scan(cursor=0, match="semcache:*", count=200)
            for key in keys:
                cached = _redis.get(key)
                if not cached:
                    continue
                entry = json.loads(cached)
                sim = _cosine_similarity(emb, entry["embedding"])
                if sim >= SIMILARITY_THRESHOLD:
                    return entry["response"]

            # Cache miss — invoke and store
            response = invoke_fn(prompt)
            cache_key = f"semcache:{prompt_hash}"
            _redis.setex(cache_key, TTL_SECONDS, json.dumps({
                "prompt": prompt, "embedding": emb, "response": response,
            }))
            return response
    """
    arts.append(_write(
        output_dir / "semantic_cache.py",
        _semcache_tpl.replace("__EMBED_MODEL_ID__", _bedrock_embed_model_id()),
    ))

    return arts, "Semantic cache: Bedrock embeddings + ElastiCache Redis — 1 artifact"


def _scaffold_opensearch(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "snapshot_restore.py", """\
        # CUI // SP-CTI
        # Elasticsearch → OpenSearch: S3 snapshot + restore migration
        import boto3, json, time, requests
        from requests_aws4auth import AWS4Auth

        ES_HOST   = "https://elasticsearch.kube-system.svc.cluster.local:9200"
        OS_HOST   = "https://search-analytics-xxxx.us-east-1.es.amazonaws.com"
        S3_BUCKET = "es-migration-snapshots-ACCOUNTID"
        REPO_NAME = "migration-repo"
        SNAPSHOT  = "pre-migration-snapshot"
        REGION    = "us-east-1"

        session = boto3.Session()
        creds = session.get_credentials()
        awsauth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "es",
                           session_token=creds.token)

        def register_repo(host, direction):
            \"\"\"Register S3 snapshot repository on source (ES) or target (OpenSearch).\"\"\"
            body = {"type": "s3", "settings": {
                "bucket": S3_BUCKET, "region": REGION,
                "role_arn": "arn:aws:iam::ACCOUNT_ID:role/ESSnapshotRole",
            }}
            r = requests.put(f"{host}/_snapshot/{REPO_NAME}", auth=awsauth,
                             json=body, verify=False)
            print(direction, r.status_code, r.text)

        def take_snapshot():
            r = requests.put(f"{ES_HOST}/_snapshot/{REPO_NAME}/{SNAPSHOT}",
                             auth=awsauth, json={"indices": "_all"}, verify=False)
            print("Snapshot started:", r.status_code)
            # Poll until complete
            for _ in range(60):
                s = requests.get(f"{ES_HOST}/_snapshot/{REPO_NAME}/{SNAPSHOT}",
                                 auth=awsauth, verify=False).json()
                state = s["snapshots"][0]["state"]
                if state == "SUCCESS":
                    return
                if state in ("FAILED", "PARTIAL"):
                    raise RuntimeError(f"Snapshot failed: {state}")
                time.sleep(10)

        def restore_snapshot():
            r = requests.post(
                f"{OS_HOST}/_snapshot/{REPO_NAME}/{SNAPSHOT}/_restore",
                auth=awsauth,
                json={"indices": "_all", "rename_pattern": "(.+)",
                      "rename_replacement": "$1", "include_global_state": False},
            )
            print("Restore:", r.status_code, r.text)

        if __name__ == "__main__":
            register_repo(ES_HOST, "ES source")
            take_snapshot()
            register_repo(OS_HOST, "OpenSearch target")
            restore_snapshot()
            print("Done — validate index counts before updating application endpoints.")
    """))

    arts.append(_write(output_dir / "index_template_migration.py", """\
        # CUI // SP-CTI
        # Migrate Elasticsearch index templates and aliases to OpenSearch.
        import json, requests
        from requests_aws4auth import AWS4Auth
        import boto3

        ES_HOST = "https://elasticsearch.kube-system.svc.cluster.local:9200"
        OS_HOST = "https://search-analytics-xxxx.us-east-1.es.amazonaws.com"
        REGION  = "us-east-1"

        creds = boto3.Session().get_credentials()
        awsauth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "es",
                           session_token=creds.token)

        # Export templates from ES
        templates = requests.get(f"{ES_HOST}/_template", verify=False).json()
        for name, body in templates.items():
            if name.startswith("."):
                continue
            r = requests.put(f"{OS_HOST}/_template/{name}",
                             auth=awsauth, json=body)
            print(f"Template {name}: {r.status_code}")

        # Export and recreate aliases
        aliases = requests.get(f"{ES_HOST}/_cat/aliases?format=json",
                               verify=False).json()
        for alias in aliases:
            body = {alias["alias"]: {"is_write_index": alias.get("is_write_index") == "true"}}
            r = requests.post(f"{OS_HOST}/_aliases",
                              auth=awsauth,
                              json={"actions": [{"add": {"index": alias["index"], "alias": alias["alias"]}}]})
            print(f"Alias {alias['alias']}: {r.status_code}")
    """))

    return arts, f"OpenSearch migration: {len(arts)} artifacts (snapshot/restore + index templates)"


def _scaffold_bedrock_kb(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    _kb_tpl = """\
        # CUI // SP-CTI
        # Bedrock Knowledge Base + OpenSearch vector store setup.
        # Run after OpenSearch restore and index template migration.
        # Model ARNs are configurable via BEDROCK_EMBED_MODEL_ARN /
        # BEDROCK_LLM_MODEL_ARN (defaults resolved from ICDEV LLM config).
        import boto3, json, os

        REGION = os.environ.get("AWS_REGION", "us-east-1")
        bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)
        OPENSEARCH_HOST = "https://search-analytics-xxxx.us-east-1.es.amazonaws.com"
        EMBED_MODEL_ARN = os.environ.get(
            "BEDROCK_EMBED_MODEL_ARN",
            f"arn:aws:bedrock:{REGION}::foundation-model/__EMBED_MODEL_ID__",
        )
        LLM_MODEL_ARN = os.environ.get("BEDROCK_LLM_MODEL_ARN", "__LLM_MODEL_ARN__")

        def create_knowledge_base():
            resp = bedrock_agent.create_knowledge_base(
                name="analytics-platform-kb",
                description="Knowledge base for analytics platform semantic search",
                roleArn="arn:aws:iam::ACCOUNT_ID:role/BedrockKBRole",
                knowledgeBaseConfiguration={
                    "type": "VECTOR",
                    "vectorKnowledgeBaseConfiguration": {
                        "embeddingModelArn": EMBED_MODEL_ARN
                    },
                },
                storageConfiguration={
                    "type": "OPENSEARCH_SERVERLESS",
                    "opensearchServerlessConfiguration": {
                        "collectionArn": "arn:aws:aoss:us-east-1:ACCOUNT_ID:collection/COLLECTION_ID",
                        "vectorIndexName": "analytics-index",
                        "fieldMapping": {
                            "vectorField": "embedding", "textField": "text",
                            "metadataField": "metadata",
                        },
                    },
                },
            )
            kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
            print(f"Knowledge base created: {kb_id}")
            return kb_id

        def rag_query(kb_id: str, query: str, max_results: int = 5) -> str:
            \"\"\"Retrieve-and-generate using the knowledge base.\"\"\"
            bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
            resp = bedrock_runtime.retrieve_and_generate(
                input={"text": query},
                retrieveAndGenerateConfiguration={
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": kb_id,
                        "modelArn": LLM_MODEL_ARN,
                        "retrievalConfiguration": {
                            "vectorSearchConfiguration": {"numberOfResults": max_results}
                        },
                    },
                },
            )
            return resp["output"]["text"]

        if __name__ == "__main__":
            kb_id = create_knowledge_base()
            # Test query
            answer = rag_query(kb_id, "What are the top metrics for Q1?")
            print("RAG answer:", answer)
    """
    _kb_content = (
        _kb_tpl
        .replace("__EMBED_MODEL_ID__", _bedrock_embed_model_id())
        .replace("__LLM_MODEL_ARN__", _bedrock_llm_model_arn())
    )
    arts.append(_write(output_dir / "bedrock_kb_setup.py", _kb_content))

    return arts, "Bedrock KB setup: 1 artifact (KB creation + RAG query template)"


def _scaffold_glue_etl(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "glue_etl_batch_job.py", """\
        # CUI // SP-CTI
        # AWS Glue PySpark ETL job — replaces NiFi batch flow.
        # Deploy via AWS Glue Studio or `aws glue create-job`.
        import sys
        from awsglue.transforms import *
        from awsglue.utils import getResolvedOptions
        from pyspark.context import SparkContext
        from awsglue.context import GlueContext
        from awsglue.job import Job
        from pyspark.sql.functions import col, current_timestamp

        args = getResolvedOptions(sys.argv, ["JOB_NAME", "source_database",
                                              "source_table", "target_path"])
        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session
        job = Job(glueContext)
        job.init(args["JOB_NAME"], args)

        # --- Extract ---
        source_df = glueContext.create_dynamic_frame.from_catalog(
            database=args["source_database"],
            table_name=args["source_table"],
        ).toDF()

        # --- Transform (replace with actual business logic) ---
        transformed_df = (
            source_df
            .filter(col("is_active") == True)
            .withColumn("etl_timestamp", current_timestamp())
        )

        # --- Load ---
        glueContext.write_dynamic_frame.from_options(
            frame=DynamicFrame.fromDF(transformed_df, glueContext, "output"),
            connection_type="s3",
            connection_options={"path": args["target_path"], "partitionKeys": ["etl_timestamp"]},
            format="parquet",
        )
        job.commit()
    """))

    arts.append(_write(output_dir / "msk_streaming_consumer.py", """\
        # CUI // SP-CTI
        # MSK Kafka streaming consumer — replaces NiFi real-time flow.
        # Deploy as AWS Glue Streaming job or Lambda with MSK trigger.
        import json, os
        from kafka import KafkaConsumer
        import boto3

        MSK_BROKERS = os.environ["MSK_BOOTSTRAP_SERVERS"].split(",")
        TOPIC        = os.environ.get("KAFKA_TOPIC", "analytics-events")
        GROUP_ID     = os.environ.get("KAFKA_GROUP_ID", "analytics-glue-consumer")

        consumer = KafkaConsumer(
            TOPIC,
            bootstrap_servers=MSK_BROKERS,
            group_id=GROUP_ID,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            security_protocol="SASL_SSL",
            sasl_mechanism="AWS_MSK_IAM",
        )

        def process_message(msg: dict) -> None:
            \"\"\"Business logic — replace with actual transformation.\"\"\"
            print(f"Processing: {msg}")

        for message in consumer:
            process_message(message.value)
            consumer.commit()
    """))

    arts.append(_write(output_dir / "eventbridge_schedule.json", json.dumps({
        "Name": "analytics-nifi-replacement-schedule",
        "Description": "Replaces NiFi cron processor — runs ETL batch job on schedule",
        "ScheduleExpression": "cron(0 2 * * ? *)",
        "State": "ENABLED",
        "Target": {
            "Arn": "arn:aws:glue:us-east-1:ACCOUNT_ID:job/analytics-etl-batch",
            "RoleArn": "arn:aws:iam::ACCOUNT_ID:role/EventBridgeGlueRole",
            "Input": json.dumps({"JOB_NAME": "analytics-etl-batch",
                                 "source_database": "analytics_catalog",
                                 "source_table": "raw_events",
                                 "target_path": "s3://analytics-output/processed/"})
        }
    }, indent=2)))

    return arts, f"Glue ETL scaffold: {len(arts)} artifacts (batch job + streaming consumer + EventBridge)"


def _scaffold_msk(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "msk_cluster.tf", """\
        # CUI // SP-CTI — Terraform: Amazon MSK Kafka cluster
        resource "aws_msk_cluster" "analytics" {
          cluster_name           = "analytics-msk"
          kafka_version          = "3.6.0"
          number_of_broker_nodes = 3

          broker_node_group_info {
            instance_type   = "kafka.m5.large"
            client_subnets  = var.private_subnet_ids
            security_groups = [aws_security_group.msk.id]
            storage_info {
              ebs_storage_info { volume_size = 100 }
            }
          }

          encryption_info {
            encryption_in_transit {
              client_broker = "TLS"
              in_cluster    = true
            }
          }

          client_authentication {
            sasl { iam = true }
          }

          tags = { Classification = "CUI", Project = "analytics-migration" }
        }
    """))

    arts.append(_write(output_dir / "mirrormaker2_config.properties", """\
        # CUI // SP-CTI
        # MirrorMaker2 — replicate topics from on-prem Kafka to MSK
        clusters=source,target

        source.bootstrap.servers=kafka.on-prem:9092
        target.bootstrap.servers=REPLACE_WITH_MSK_BOOTSTRAP_SERVERS

        source->target.enabled=true
        source->target.topics=.*

        replication.factor=3
        offset-syncs.topic.replication.factor=3
        heartbeats.topic.replication.factor=3
        checkpoints.topic.replication.factor=3
        tasks.max=4

        target.security.protocol=SASL_SSL
        target.sasl.mechanism=AWS_MSK_IAM
        target.sasl.jaas.config=software.amazon.msk.auth.iam.IAMLoginModule required;
    """))

    return arts, f"MSK scaffold: {len(arts)} artifacts (Terraform cluster + MirrorMaker2 config)"


def _scaffold_amplify(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "amplify.yml", """\
        # CUI // SP-CTI
        # AWS Amplify build spec — migrated from Nginx/K8s deployment
        version: 1
        frontend:
          phases:
            preBuild:
              commands:
                - nvm use 18
                - node --version
                - npm ci
            build:
              commands:
                - npm run build
          artifacts:
            baseDirectory: build
            files:
              - '**/*'
          cache:
            paths:
              - node_modules/**/*
        test:
          phases:
            preTest:
              commands:
                - npm ci
            test:
              commands:
                - npm test -- --watchAll=false --ci
          artifacts:
            baseDirectory: coverage
            files:
              - '**/*'
    """))

    arts.append(_write(output_dir / "cloudfront_security_headers.json", json.dumps({
        "ResponseHeadersPolicyConfig": {
            "Name": "analytics-security-headers",
            "SecurityHeadersConfig": {
                "StrictTransportSecurity": {"Override": True, "AccessControlMaxAgeSec": 31536000, "IncludeSubdomains": True, "Preload": True},
                "ContentTypeOptions": {"Override": True},
                "FrameOptions": {"FrameOption": "DENY", "Override": True},
                "XSSProtection": {"Protection": True, "ModeBlock": True, "Override": True},
                "ContentSecurityPolicy": {
                    "ContentSecurityPolicy": "default-src 'self'; connect-src 'self' https://*.amazonaws.com",
                    "Override": True
                }
            }
        }
    }, indent=2)))

    return arts, f"Amplify scaffold: {len(arts)} artifacts (build spec + CloudFront security headers)"


def _scaffold_bedrock_agents(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "useBedrockAgent.js", """\
        // CUI // SP-CTI
        // React hook: Bedrock Inline Agent for AI-powered UX
        // Drop into your React app and replace manual API calls.
        import { useState, useCallback } from 'react';

        const INVOKE_URL = process.env.REACT_APP_BEDROCK_AGENT_ENDPOINT || '/api/bedrock-agent';

        export function useBedrockAgent(agentId, agentAliasId) {
          const [response, setResponse] = useState('');
          const [loading, setLoading] = useState(false);
          const [error, setError] = useState(null);
          const [sessionId] = useState(() => crypto.randomUUID());

          const invoke = useCallback(async (inputText) => {
            setLoading(true);
            setError(null);
            setResponse('');
            try {
              const res = await fetch(INVOKE_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agentId, agentAliasId, sessionId, inputText }),
              });
              if (!res.ok) throw new Error(`Agent error: ${res.status}`);
              // Stream the response
              const reader = res.body.getReader();
              const decoder = new TextDecoder();
              let result = '';
              while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value);
                result += chunk;
                setResponse(result);
              }
            } catch (err) {
              setError(err.message);
            } finally {
              setLoading(false);
            }
          }, [agentId, agentAliasId, sessionId]);

          return { invoke, response, loading, error };
        }
    """))

    arts.append(_write(output_dir / "bedrock_agent_proxy.py", """\
        # CUI // SP-CTI
        # Flask proxy endpoint: React → Bedrock InlineAgent (streaming)
        # Add to your Flask app to avoid exposing AWS credentials to the browser.
        import boto3
        from flask import Blueprint, request, Response, stream_with_context

        bedrock_bp = Blueprint("bedrock", __name__)
        bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name="us-east-1")

        @bedrock_bp.route("/api/bedrock-agent", methods=["POST"])
        def invoke_agent():
            body = request.get_json(force=True)
            agent_id       = body.get("agentId")
            agent_alias_id = body.get("agentAliasId")
            session_id     = body.get("sessionId")
            input_text     = body.get("inputText", "")

            def generate():
                resp = bedrock_runtime.invoke_agent(
                    agentId=agent_id,
                    agentAliasId=agent_alias_id,
                    sessionId=session_id,
                    inputText=input_text,
                )
                for event in resp["completion"]:
                    if "chunk" in event:
                        yield event["chunk"]["bytes"].decode("utf-8")

            return Response(stream_with_context(generate()), content_type="text/plain")
    """))

    return arts, f"Bedrock Agents scaffold: {len(arts)} artifacts (React hook + Flask proxy)"


def _scaffold_eks_fargate(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "fargate_profile.yaml", """\
        # CUI // SP-CTI
        # EKS Fargate profile — run app pods without managing EC2 nodes
        apiVersion: eksctl.io/v1alpha5
        kind: ClusterConfig
        metadata:
          name: analytics-eks
          region: us-east-1
          version: "1.29"

        fargateProfiles:
          - name: app-profile
            selectors:
              - namespace: analytics
              - namespace: default
          - name: kube-system
            selectors:
              - namespace: kube-system

        iam:
          withOIDC: true        # Required for IRSA

        addons:
          - name: vpc-cni
          - name: coredns
            configurationValues: '{"computeType": "Fargate"}'
          - name: kube-proxy
          - name: aws-ebs-csi-driver
    """))

    arts.append(_write(output_dir / "irsa_binding.yaml", """\
        # CUI // SP-CTI
        # IRSA: IAM Roles for Service Accounts — replaces K8s RBAC + kiam
        # Apply after eksctl creates the OIDC provider.
        apiVersion: v1
        kind: ServiceAccount
        metadata:
          name: analytics-app-sa
          namespace: analytics
          annotations:
            eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT_ID:role/analytics-app-irsa-role
        ---
        # IAM role trust policy (set via AWS console or Terraform):
        # {
        #   "Version": "2012-10-17",
        #   "Statement": [{
        #     "Effect": "Allow",
        #     "Principal": {
        #       "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/oidc.eks.REGION.amazonaws.com/id/CLUSTER_OIDC_ID"
        #     },
        #     "Action": "sts:AssumeRoleWithWebIdentity",
        #     "Condition": {
        #       "StringEquals": {
        #         "oidc.eks.REGION.amazonaws.com/id/CLUSTER_OIDC_ID:sub": "system:serviceaccount:analytics:analytics-app-sa"
        #       }
        #     }
        #   }]
        # }
    """))

    arts.append(_write(output_dir / "convert_to_fargate.py", """\
        # CUI // SP-CTI
        # Convert K8s manifests to Fargate-compatible specs.
        # Removes DaemonSet resources, hostPath volumes, privileged containers.
        import sys
        from pathlib import Path
        import yaml

        INCOMPATIBLE_KINDS = {"DaemonSet"}
        DISALLOWED_VOLUME_TYPES = {"hostPath", "emptyDir"}

        def convert_manifest(path: Path) -> dict | None:
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            if not doc:
                return None
            kind = doc.get("kind", "")
            if kind in INCOMPATIBLE_KINDS:
                print(f"  SKIP {path.name}: {kind} not supported on Fargate")
                return None

            spec = doc.get("spec", {})
            template = spec.get("template", spec)
            pod_spec = template.get("spec", {})

            # Remove hostPath volumes
            volumes = [v for v in pod_spec.get("volumes", [])
                       if not any(vtype in v for vtype in DISALLOWED_VOLUME_TYPES)]
            if volumes != pod_spec.get("volumes"):
                pod_spec["volumes"] = volumes
                print(f"  WARN {path.name}: removed incompatible volumes")

            # Remove privileged security contexts
            for container in pod_spec.get("containers", []):
                sc = container.get("securityContext", {})
                if sc.get("privileged"):
                    del sc["privileged"]
                    print(f"  WARN {path.name}/{container['name']}: removed privileged=true")

            return doc

        if __name__ == "__main__":
            src_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "k8s/")
            out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "k8s-fargate/")
            out_dir.mkdir(parents=True, exist_ok=True)
            for manifest in src_dir.glob("**/*.yaml"):
                result = convert_manifest(manifest)
                if result:
                    out_path = out_dir / manifest.relative_to(src_dir)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_path, "w", encoding="utf-8") as fh:
                        yaml.dump(result, fh)
                    print(f"  OK  {out_path}")
    """))

    return arts, f"EKS Fargate scaffold: {len(arts)} artifacts (profile YAML + IRSA + manifest converter)"


def _scaffold_documentdb(job, params, output_dir) -> tuple[list[dict], str]:
    arts = []
    arts.append(_write(output_dir / "mongodump_restore.sh", """\
        #!/usr/bin/env bash

from tools.logging.icdev_logger import get_logger
        # CUI // SP-CTI
        # MongoDB → DocumentDB migration using mongodump/mongorestore
        set -euo pipefail
        SOURCE_URI="${SOURCE_MONGO_URI:-mongodb://localhost:27017}"
        TARGET_URI="${TARGET_DOCUMENTDB_URI:-mongodb://DOCDB_USER:DOCDB_PASS@CLUSTER.docdb.amazonaws.com:27017/?tls=true&tlsCAFile=rds-combined-ca-bundle.pem&replicaSet=rs0&readPreference=secondaryPreferred&retryWrites=false}"

        echo "Dumping from MongoDB..."
        mongodump --uri="$SOURCE_URI" --out=/tmp/mongodump-export --gzip

        echo "Restoring to DocumentDB..."
        mongorestore --uri="$TARGET_URI" /tmp/mongodump-export --gzip --drop

        echo "Done."
    """))
    return arts, "DocumentDB migration: 1 artifact (mongodump/restore script)"


def _gen_stub_readme_fn(job, params, output_dir) -> tuple[list[dict], str]:
    return _gen_stub_readme(job, params, output_dir)


def _gen_stub_readme(job, params, output_dir) -> tuple[list[dict], str]:
    scaffold_type = params.get("scaffold_type", job.get("refactor_type", "unknown"))
    art = _write(output_dir / "README.md", f"""\
        # CUI // SP-CTI
        # Refactor job: {job['component_name']} — {scaffold_type}
        #
        # This job requires source_repo to be set on the mc_app_inventory row
        # before the full tool chain can execute.
        #
        # Once source_repo is populated, re-run:
        #   python -m tools.migration_canvas.cam_refactor_engine --run-job {job['id']}
    """)
    return [art], f"Stub README for {scaffold_type} — set source_repo to enable full transformation"


def _scan_artifacts(directory: Path, artifact_type: str) -> list[dict]:
    """Scan an output directory and return artifact dicts."""
    return [
        {"type": artifact_type, "path": str(f), "name": f.name,
         "size_bytes": f.stat().st_size, "description": f.stem.replace("_", " ")}
        for f in sorted(directory.iterdir()) if f.is_file()
    ]


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="CAM Code Refactoring Engine")
    p.add_argument("--project", metavar="PROJECT_ID", help="Run all jobs for project")
    p.add_argument("--component", metavar="NAME", help="Limit to one component name")
    p.add_argument("--dry-run", action="store_true", help="Show what would be queued without running")
    p.add_argument("--list-jobs", metavar="PROJECT_ID", help="List jobs for project")
    p.add_argument("--run-job", metavar="JOB_ID", help="Execute a single queued job")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args()

    if args.list_jobs:
        jobs = get_jobs(args.list_jobs)
        if args.as_json:
            print(json.dumps(jobs, default=str))
        else:
            print(f"Jobs for {args.list_jobs}: {len(jobs)}")
            for j in jobs:
                arts = json.loads(j.get("artifacts_json") or "[]")
                print(f"  [{j['status']:>10}] {j['component_name']:20} {j['refactor_type']:25} {len(arts)} artifact(s)")
        return

    if args.run_job:
        result = run_job(args.run_job)
        if args.as_json:
            print(json.dumps(result, default=str))
        else:
            arts = json.loads(result.get("artifacts_json") or "[]")
            print(f"Job {args.run_job}: {result['status']} — {len(arts)} artifact(s)")
            for a in arts:
                print(f"  {a['name']}  ({a.get('size_bytes', 0)} bytes)")
        return

    if args.project:
        result = run_all(args.project, component_name=args.component, dry_run=args.dry_run)
        if args.as_json:
            print(json.dumps(result, default=str))
        else:
            if args.dry_run:
                print(f"Would queue {result['would_queue']} job(s):")
                for j in result["jobs"]:
                    print(f"  {j['component_name']:20} {j['refactor_type']}")
            else:
                print(f"Completed: {result['completed']}/{result['total']} | Failed: {result['failed']}")
        return

    p.print_help()


if __name__ == "__main__":
    main()
