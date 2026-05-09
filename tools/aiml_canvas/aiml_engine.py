# CUI // SP-CTI
"""AIMC — AI/ML Model Canvas core engine.

Handles design CRUD, graph assessment, compliance rule evaluation,
artifact generation, and version management.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.aiml_canvas.constants import (
    AIMC_COMPLIANCE_RULES,
    FOUNDATION_MODELS,
)
from tools.aiml_canvas.db.init_db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_by_id(model_id: str) -> dict | None:
    for m in FOUNDATION_MODELS:
        if m["id"] == model_id:
            return m
    return None


# ── Design CRUD ───────────────────────────────────────────────────────────────

def create_design(
    name: str,
    description: str = "",
    classification: str = "CUI",
    il_level: str = "IL4",
    primary_use_case: str = "",
    adaptation_strategy: str = "prompt",
    template_id: str | None = None,
    graph_json: dict | None = None,
) -> dict:
    design_id = str(uuid.uuid4())
    if graph_json is None:
        graph_json = {"nodes": [], "edges": [], "boundaries": []}

    # If template_id given, clone template graph
    if template_id and not graph_json["nodes"]:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT graph_json FROM aiml_templates WHERE id=?", (template_id,)
            ).fetchone()
            if row:
                graph_json = json.loads(row["graph_json"])
        finally:
            conn.close()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO aiml_designs
               (id, name, description, graph_json, template_id, classification,
                il_level, primary_use_case, adaptation_strategy, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                design_id, name, description, json.dumps(graph_json),
                template_id, classification, il_level, primary_use_case,
                adaptation_strategy, _now(), _now(),
            ),
        )
        conn.execute(
            "INSERT INTO aiml_audit (design_id, action, detail) VALUES (?,?,?)",
            (design_id, "create", f"Design '{name}' created"),
        )
        conn.commit()
    finally:
        conn.close()

    return get_design(design_id)


def get_design(design_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM aiml_designs WHERE id=?", (design_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["graph"] = json.loads(d.pop("graph_json", "{}"))
        return d
    finally:
        conn.close()


def list_designs(limit: int = 50, offset: int = 0) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, description, classification, il_level, adaptation_strategy, created_at, updated_at "
            "FROM aiml_designs ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_design(design_id: str, graph: dict, name: str | None = None,
                description: str | None = None, il_level: str | None = None,
                classification: str | None = None) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM aiml_designs WHERE id=?", (design_id,)).fetchone()
        if not row:
            raise ValueError(f"Design {design_id} not found")

        # Save version snapshot before overwriting
        version_count = conn.execute(
            "SELECT COUNT(*) FROM aiml_versions WHERE design_id=?", (design_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO aiml_versions (id, design_id, version_number, graph_json, created_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), design_id, version_count + 1, row["graph_json"], _now()),
        )

        updates: list[str] = ["graph_json=?", "updated_at=?"]
        params: list[Any] = [json.dumps(graph), _now()]
        if name is not None:
            updates.append("name=?"); params.append(name)
        if description is not None:
            updates.append("description=?"); params.append(description)
        if il_level is not None:
            updates.append("il_level=?"); params.append(il_level)
        if classification is not None:
            updates.append("classification=?"); params.append(classification)
        params.append(design_id)

        conn.execute(f"UPDATE aiml_designs SET {', '.join(updates)} WHERE id=?", params)
        conn.execute(
            "INSERT INTO aiml_audit (design_id, action, detail) VALUES (?,?,?)",
            (design_id, "save", f"Graph saved ({len(graph.get('nodes',[]))} nodes)"),
        )
        conn.commit()
    finally:
        conn.close()

    return get_design(design_id)


def delete_design(design_id: str) -> bool:
    conn = get_connection()
    try:
        result = conn.execute("DELETE FROM aiml_designs WHERE id=?", (design_id,))
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


# ── Compliance Assessment ─────────────────────────────────────────────────────

def _get_nodes_by_type(graph: dict, node_type_prefix: str) -> list[dict]:
    return [n for n in graph.get("nodes", []) if n.get("type", "").startswith(node_type_prefix)]


def _boundary_containing_node(graph: dict, node_id: str) -> dict | None:
    """Return the innermost boundary that spatially contains a node."""
    node = next((n for n in graph.get("nodes", []) if n.get("id") == node_id), None)
    if not node:
        return None
    nx, ny = node.get("x", 0), node.get("y", 0)
    for b in graph.get("boundaries", []):
        bx, by = b.get("x", 0), b.get("y", 0)
        bw, bh = b.get("width", 0), b.get("height", 0)
        if bx <= nx <= bx + bw and by <= ny <= by + bh:
            return b
    return None


def _node_has_governance_edge(graph: dict, node_id: str, gov_type: str) -> bool:
    gov_nodes = {n["id"] for n in graph.get("nodes", []) if n.get("type") == gov_type}
    for e in graph.get("edges", []):
        if e.get("target") == node_id and e.get("source") in gov_nodes:
            return True
    return False


def _node_has_safety_on_incoming(graph: dict, node_id: str, safety_type: str) -> bool:
    safety_nodes = {n["id"] for n in graph.get("nodes", []) if n.get("type") == safety_type}
    # Check if safety node has edge into node_id (direct) or into another node that feeds node_id
    for e in graph.get("edges", []):
        if e.get("target") == node_id and e.get("source") in safety_nodes:
            return True
    return False


def _has_node_type(graph: dict, node_type: str) -> bool:
    return any(n.get("type") == node_type for n in graph.get("nodes", []))


def _edge_exists(graph: dict, source_type: str, target_type: str) -> bool:
    src_ids = {n["id"] for n in graph.get("nodes", []) if n.get("type") == source_type}
    tgt_ids = {n["id"] for n in graph.get("nodes", []) if n.get("type") == target_type}
    for e in graph.get("edges", []):
        if e.get("source") in src_ids and e.get("target") in tgt_ids:
            return True
    return False


def assess_graph(graph: dict, design: dict) -> list[dict]:
    """Run deterministic compliance checks. Returns list of findings."""
    findings: list[dict] = []
    il_level = design.get("il_level", "IL4")
    il_int = int(il_level.replace("IL", "")) if il_level.startswith("IL") else 4

    llm_nodes   = _get_nodes_by_type(graph, "model-llm")
    vlm_nodes   = _get_nodes_by_type(graph, "model-vlm")
    model_nodes = llm_nodes + vlm_nodes
    rag_nodes   = _get_nodes_by_type(graph, "adapt-rag")
    ft_nodes    = _get_nodes_by_type(graph, "adapt-finetune")
    deploy_nodes = _get_nodes_by_type(graph, "deploy-")
    eval_nodes  = (
        _get_nodes_by_type(graph, "eval-benchmark") +
        _get_nodes_by_type(graph, "eval-rubric") +
        _get_nodes_by_type(graph, "eval-red-team") +
        _get_nodes_by_type(graph, "eval-ab-test")
    )

    for rule in AIMC_COMPLIANCE_RULES:
        check = rule["check"]
        passed = True
        detail = ""

        if check == "il_model_air_gap" and il_int >= 5:
            for node in model_nodes:
                props = node.get("properties_json", {})
                if isinstance(props, str):
                    try: props = json.loads(props)
                    except Exception: props = {}
                model_id = props.get("model_id", "")
                m = _model_by_id(model_id)
                if m and not m.get("air_gap_ready", False):
                    passed = False
                    detail = f"Node '{node.get('label', node['id'])}' uses non-air-gap model at {il_level}"
                    break

        elif check == "model_il_coverage":
            for node in model_nodes:
                props = node.get("properties_json", {})
                if isinstance(props, str):
                    try: props = json.loads(props)
                    except Exception: props = {}
                model_id = props.get("model_id", "")
                m = _model_by_id(model_id)
                if m:
                    required_il = int(il_level.replace("IL", ""))
                    supported = [int(str(x).replace("IL", "").replace("il", "")) for x in m.get("il_suitability", [])]
                    if required_il not in supported:
                        passed = False
                        detail = f"Model '{m['name']}' does not support {il_level}"
                        break

        elif check == "input_guardrail_present":
            for node in llm_nodes:
                if not _node_has_safety_on_incoming(graph, node["id"], "safety-guardrail"):
                    passed = False
                    detail = f"LLM '{node.get('label', node['id'])}' has no Input Guardrail on incoming path"
                    break

        elif check == "output_validator_present":
            for node in llm_nodes:
                # Check if output validator is downstream
                ov_ids = {n["id"] for n in graph.get("nodes", []) if n.get("type") == "safety-output-validator"}
                has_ov = any(
                    e.get("source") == node["id"] and e.get("target") in ov_ids
                    for e in graph.get("edges", [])
                )
                if not has_ov:
                    passed = False
                    detail = f"LLM '{node.get('label', node['id'])}' has no Output Validator downstream"
                    break

        elif check == "pii_scrubber_on_classified_input":
            if il_int >= 4 and not _has_node_type(graph, "safety-pii-scrubber"):
                passed = False
                detail = f"{il_level} design missing PII Scrubber node on classified data path"

        elif check == "model_card_connected":
            for node in model_nodes:
                if not _node_has_governance_edge(graph, node["id"], "gov-model-card"):
                    passed = False
                    detail = f"Model '{node.get('label', node['id'])}' not connected to a Model Card"
                    break

        elif check == "nist_ai_rmf_present":
            if not _has_node_type(graph, "gov-nist-ai-rmf"):
                passed = False
                detail = "No NIST AI RMF governance node found in design"

        elif check == "dod_rai_present_for_dod_il":
            if il_int >= 4 and not _has_node_type(graph, "gov-dod-rai"):
                passed = False
                detail = f"DoD {il_level} design missing DoD RAI governance node"

        elif check == "ai_bom_connected":
            if model_nodes and not _has_node_type(graph, "gov-ai-bom"):
                passed = False
                detail = "No AI BOM governance node connected to model nodes"

        elif check == "eval_before_deploy":
            if deploy_nodes and not eval_nodes:
                passed = False
                detail = "Design has Deployment node but no Evaluation node"

        elif check == "red_team_for_classified":
            if il_int >= 4 and not _has_node_type(graph, "eval-red-team"):
                passed = False
                detail = f"Classified {il_level} design should include Red Team evaluation set"

        elif check == "rag_has_vector_store":
            if rag_nodes and not _has_node_type(graph, "data-vector-store"):
                passed = False
                detail = "RAG Pipeline node present but no Vector Store data node"

        elif check == "finetune_has_dataset":
            if ft_nodes and not _has_node_type(graph, "data-dataset"):
                passed = False
                detail = "Fine-Tune Job node present but no Training Dataset node"

        elif check == "il6_local_deploy_only":
            if il_int >= 6:
                for node in deploy_nodes:
                    if node.get("type") not in ("deploy-ollama", "deploy-vllm"):
                        passed = False
                        detail = f"IL6 enclave has non-local deployment node: '{node.get('label', '')}'"
                        break

        elif check == "components_classified":
            unzoned = []
            for node in graph.get("nodes", []):
                if node.get("type", "").startswith("bnd-"):
                    continue
                b = _boundary_containing_node(graph, node["id"])
                if b is None:
                    unzoned.append(node.get("label") or node["id"])
            if unzoned:
                passed = False
                detail = f"Ungrouped nodes (no zone boundary): {', '.join(unzoned[:3])}"

        severity = rule["severity"]
        findings.append({
            "rule_id": rule["id"],
            "title": rule["title"],
            "severity": severity,
            "category": rule["category"],
            "passed": passed,
            "detail": detail if not passed else "OK",
        })

    return findings


def run_assessment(design_id: str) -> dict:
    """Run full compliance assessment and persist results."""
    design = get_design(design_id)
    if not design:
        raise ValueError(f"Design {design_id} not found")

    graph = design.get("graph", {})
    findings = assess_graph(graph, design)

    total = len(findings)
    passed_count = sum(1 for f in findings if f["passed"])
    score = round((passed_count / total) * 100, 1) if total else 0.0

    assessment_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO aiml_assessments
               (id, design_id, framework_id, framework_name, findings_json, score, passed, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                assessment_id, design_id,
                "aimc-canvas", "AIMC Canvas Compliance",
                json.dumps(findings), score, 1 if score >= 80 else 0,
                _now(),
            ),
        )
        conn.execute(
            "INSERT INTO aiml_audit (design_id, action, detail) VALUES (?,?,?)",
            (design_id, "assess", f"Score: {score}% ({passed_count}/{total} checks passed)"),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "assessment_id": assessment_id,
        "design_id": design_id,
        "score": score,
        "passed": score >= 80,
        "findings": findings,
        "summary": {
            "total": total,
            "passed": passed_count,
            "failed": total - passed_count,
            "cat1_failures": sum(1 for f in findings if not f["passed"] and f["severity"] == "CAT1"),
            "cat2_failures": sum(1 for f in findings if not f["passed"] and f["severity"] == "CAT2"),
        },
    }


# ── Artifact Generation ────────────────────────────────────────────────────────

def generate_model_card(design_id: str) -> dict:
    """Generate NIST AI 100-1 model card from design graph."""
    design = get_design(design_id)
    if not design:
        raise ValueError(f"Design {design_id} not found")

    graph = design["graph"]
    llm_nodes = _get_nodes_by_type(graph, "model-llm") + _get_nodes_by_type(graph, "model-vlm")
    first_model_node = llm_nodes[0] if llm_nodes else {}
    props = first_model_node.get("properties_json", {})
    if isinstance(props, str):
        try: props = json.loads(props)
        except Exception: props = {}

    model_id = props.get("model_id", "")
    model_meta = _model_by_id(model_id) or {}
    adaptation = design.get("adaptation_strategy", "prompt")

    card = {
        "schema_version": "NIST-AI-100-1-v1",
        "generated_at": _now(),
        "classification": design.get("classification", "CUI"),
        "model_details": {
            "name": first_model_node.get("label") or model_meta.get("name", design["name"]),
            "model_id": model_id,
            "provider": model_meta.get("provider", ""),
            "family": model_meta.get("family", ""),
            "type": model_meta.get("type", "llm"),
            "version": props.get("version", "1.0"),
            "context_window": model_meta.get("context_window", 0),
            "license": props.get("license", "Commercial License / Llama 3 Community License"),
        },
        "intended_use": {
            "primary_use_case": design.get("primary_use_case", ""),
            "il_level": design.get("il_level", "IL4"),
            "adaptation_strategy": adaptation,
            "intended_users": props.get("intended_users", "Authorized DoD/Federal personnel"),
            "out_of_scope_uses": [
                "Autonomous lethal targeting decisions",
                "Replacing human judgment in life-critical decisions",
                "Processing data beyond declared IL level",
            ],
        },
        "training_details": _card_training_section(adaptation, graph, props),
        "evaluation": _card_eval_section(graph),
        "safety_and_limitations": {
            "known_limitations": props.get("known_limitations", [
                "May hallucinate facts not present in training or retrieved context",
                "Performance degrades on highly specialized military terminology not in training data",
            ]),
            "safety_measures": _card_safety_measures(graph),
            "bias_considerations": props.get("bias_considerations",
                "Training data may reflect historical biases; fairness evaluation recommended"),
            "failure_modes": [
                "Prompt injection if input guardrail bypassed",
                "Classification leak if output validator disabled",
                "Context window overflow on large documents",
            ],
        },
        "governance": {
            "classification_marking": design.get("classification", "CUI"),
            "il_designation": design.get("il_level", "IL4"),
            "air_gap_required": design.get("il_level", "IL4") in ("IL5", "IL6"),
            "nist_ai_rmf_coverage": _has_node_type(graph, "gov-nist-ai-rmf"),
            "dod_rai_assessed": _has_node_type(graph, "gov-dod-rai"),
            "ai_bom_generated": _has_node_type(graph, "gov-ai-bom"),
        },
    }

    art_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO aiml_artifacts (id, design_id, artifact_type, title, content_json, format, created_at) VALUES (?,?,?,?,?,?,?)",
            (art_id, design_id, "model-card", f"Model Card — {design['name']}", json.dumps(card), "json", _now()),
        )
        conn.commit()
    finally:
        conn.close()

    return {"artifact_id": art_id, "type": "model-card", "content": card}


def _card_training_section(adaptation: str, graph: dict, props: dict) -> dict:
    has_dataset = _has_node_type(graph, "data-dataset")
    has_rag     = _has_node_type(graph, "adapt-rag") or _has_node_type(graph, "adapt-hybrid")
    has_ft      = _has_node_type(graph, "adapt-finetune") or _has_node_type(graph, "adapt-hybrid")
    return {
        "adaptation_method": adaptation,
        "fine_tuned": has_ft,
        "rag_enabled": has_rag,
        "training_dataset_provided": has_dataset,
        "training_framework": "QLoRA / LoRA (Unsloth)" if has_ft else "N/A — prompt-only or RAG",
        "quantization": props.get("quantization", "Q4_K_M") if not has_ft else "fp16 (training)",
    }


def _card_eval_section(graph: dict) -> dict:
    evals = []
    if _has_node_type(graph, "eval-benchmark"): evals.append("Standard benchmark suite")
    if _has_node_type(graph, "eval-rubric"):    evals.append("LLM-as-judge rubric (Prometheus pattern)")
    if _has_node_type(graph, "eval-red-team"):  evals.append("Red team adversarial evaluation (MITRE ATLAS)")
    if _has_node_type(graph, "eval-ab-test"):   evals.append("A/B model comparison")
    return {
        "evaluation_methods": evals if evals else ["Evaluation not yet configured — add Eval nodes"],
        "judge_model": "Qwen3 (Local)" if _has_node_type(graph, "model-judge") else "N/A",
    }


def _card_safety_measures(graph: dict) -> list[str]:
    measures = []
    if _has_node_type(graph, "safety-guardrail"):        measures.append("Input prompt injection detection")
    if _has_node_type(graph, "safety-output-validator"):  measures.append("Output content safety validation")
    if _has_node_type(graph, "safety-pii-scrubber"):      measures.append("PII/CUI redaction (input + output)")
    if _has_node_type(graph, "safety-content-filter"):    measures.append("Harmful content filtering")
    return measures or ["No safety nodes configured — add Safety nodes to the canvas"]


def generate_deployment_manifest(design_id: str) -> dict:
    """Generate Docker Compose manifest for configured deployment nodes."""
    design = get_design(design_id)
    if not design:
        raise ValueError(f"Design {design_id} not found")

    graph = design["graph"]
    deploy_nodes = _get_nodes_by_type(graph, "deploy-")

    services: dict = {}
    for d in deploy_nodes:
        dtype = d.get("type", "")
        props = d.get("properties_json", {})
        if isinstance(props, str):
            try: props = json.loads(props)
            except Exception: props = {}

        svc_name = d.get("label", dtype).lower().replace(" ", "-")
        if dtype == "deploy-ollama":
            services[svc_name] = {
                "image": "ollama/ollama:latest",
                "container_name": svc_name,
                "volumes": ["ollama_data:/root/.ollama"],
                "ports": ["11434:11434"],
                "restart": "unless-stopped",
                "deploy": {"resources": {"reservations": {"devices": [{"capabilities": ["gpu"]}]}}},
            }
        elif dtype == "deploy-vllm":
            model_id = props.get("model_id", "meta-llama/Llama-3-8B-Instruct")
            services[svc_name] = {
                "image": "vllm/vllm-openai:latest",
                "container_name": svc_name,
                "command": f"--model {model_id} --tensor-parallel-size 1 --max-model-len 8192",
                "ports": ["8000:8000"],
                "environment": ["HF_TOKEN=${HF_TOKEN}"],
                "restart": "unless-stopped",
            }
        elif dtype == "deploy-tgi":
            model_id = props.get("model_id", "meta-llama/Llama-3-8B-Instruct")
            services[svc_name] = {
                "image": "ghcr.io/huggingface/text-generation-inference:latest",
                "container_name": svc_name,
                "command": f"--model-id {model_id}",
                "ports": ["8080:80"],
                "volumes": ["hf_cache:/data"],
                "restart": "unless-stopped",
            }

    manifest = {
        "version": "3.8",
        "services": services,
        "volumes": {"ollama_data": {}, "hf_cache": {}},
        "_metadata": {
            "design_id": design_id,
            "design_name": design["name"],
            "il_level": design.get("il_level", "IL4"),
            "classification": design.get("classification", "CUI"),
            "generated_at": _now(),
        },
    }

    art_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO aiml_artifacts (id, design_id, artifact_type, title, content_json, format, created_at) VALUES (?,?,?,?,?,?,?)",
            (art_id, design_id, "deploy-manifest", f"Deployment Manifest — {design['name']}", json.dumps(manifest), "yaml", _now()),
        )
        conn.commit()
    finally:
        conn.close()

    return {"artifact_id": art_id, "type": "deploy-manifest", "content": manifest}


# ── Templates + Snippets ──────────────────────────────────────────────────────

def list_templates() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM aiml_templates ORDER BY category, name").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            result.append(d)
        return result
    finally:
        conn.close()


def get_template(template_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM aiml_templates WHERE id=?", (template_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["graph"] = json.loads(d.pop("graph_json", "{}"))
        d["tags"] = json.loads(d.get("tags") or "[]")
        return d
    finally:
        conn.close()


def list_snippets(category: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM aiml_snippets WHERE category=? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM aiml_snippets ORDER BY category, name").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["graph"] = json.loads(d.pop("graph_json", "{}"))
            result.append(d)
        return result
    finally:
        conn.close()


# ── Version History ────────────────────────────────────────────────────────────

def list_versions(design_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, design_id, version_number, change_summary, created_at "
            "FROM aiml_versions WHERE design_id=? ORDER BY version_number DESC",
            (design_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_version(version_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM aiml_versions WHERE id=?", (version_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["graph"] = json.loads(d.pop("graph_json", "{}"))
        return d
    finally:
        conn.close()


# ── Dashboard Stats ────────────────────────────────────────────────────────────

def get_stats() -> dict:
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM aiml_designs").fetchone()[0]
        assessed = conn.execute(
            "SELECT COUNT(DISTINCT design_id) FROM aiml_assessments"
        ).fetchone()[0]
        avg_score_row = conn.execute("SELECT AVG(score) FROM aiml_assessments").fetchone()
        avg_score = round(avg_score_row[0] or 0.0, 1)
        artifacts = conn.execute("SELECT COUNT(*) FROM aiml_artifacts").fetchone()[0]
        strategy_rows = conn.execute(
            "SELECT adaptation_strategy, COUNT(*) as cnt FROM aiml_designs GROUP BY adaptation_strategy"
        ).fetchall()
        il_rows = conn.execute(
            "SELECT il_level, COUNT(*) as cnt FROM aiml_designs GROUP BY il_level"
        ).fetchall()
        return {
            "total_designs": total,
            "assessed_designs": assessed,
            "avg_compliance_score": avg_score,
            "total_artifacts": artifacts,
            "by_strategy": {r["adaptation_strategy"]: r["cnt"] for r in strategy_rows},
            "by_il": {r["il_level"]: r["cnt"] for r in il_rows},
        }
    finally:
        conn.close()
