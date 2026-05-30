#!/usr/bin/env python3
"""DIC finetune bridge — air-gap local fine-tuning/training over a collection.

[TEMPLATE: CUI // SP-CTI]

This module is an OPTION: the Document Intelligence Canvas works without it.
When present it lets an operator fine-tune a small local model over a single
collection's OWN chunks/KG, entirely inside the enclave.

Air-gap by construction
------------------------
* The training provider is constrained to LOCAL ONLY — ``unsloth_local``
  (GPU QLoRA) or CPU-only ``ollama`` serving. The cloud providers
  (``openai`` / ``bedrock`` / ``azure_openai``) are *excluded* whenever
  ``tools.airgap.is_airgap()`` is true OR the config flag
  ``finetune.airgap_only`` is true (the default). Any attempt to resolve a
  cloud provider under that policy raises :class:`AirgapViolation`.
* Training pairs are built from the collection's own RAG chunks
  (``dic_documents`` source table) and its Knowledge Graph, versioned with
  ``dataset_manager``, and stamped CUI/classification on every example. Pairs
  never leave the enclave.

Pipeline
--------
``build_dataset`` -> ``train`` -> ``evaluate`` -> ``export_and_serve`` ->
register in ``model_registry`` -> served via local Ollama. ``run_finetune``
runs the whole chain with graceful degradation.

Hard rule
---------
The resulting fine-tuned model MAY back *optional hybrid* generation paths.
It is NEVER allowed on the no-LLM grounded search path (citations-only search
stays deterministic and model-free).

Bookkeeping
-----------
Writes ``dic_ft_datasets`` / ``dic_ft_jobs`` / ``dic_ft_models`` (all with
``airgap=1`` and tenant_id/classification, so writes participate in
RBAC+ABAC+RLS — dic-authz-01).

GPU is detected via ``gpu_detector``; with no training-capable GPU the bridge
degrades to CPU/Ollama serving or reports ``training_available=False`` rather
than raising.

Depends on dic-ingest-03 (chunks/KG must already be ingested); secondary
dic-authz-01 (security context).
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repo root on path when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection

# --------------------------------------------------------------------------- #
# Air-gap policy
# --------------------------------------------------------------------------- #

# Providers permitted while air-gapped. Local QLoRA training (Unsloth) or
# CPU-only serving (Ollama). Everything else leaves the enclave.
ALLOWED_AIRGAP_PROVIDERS = frozenset({"unsloth_local", "ollama"})
CLOUD_PROVIDERS = frozenset({"openai", "bedrock", "azure_openai"})

# Quantization for the exported GGUF served by Ollama.
DEFAULT_QUANTIZATION = "q4_k_m"


class AirgapViolation(RuntimeError):
    """Raised when a cloud provider/operation is attempted under air-gap policy."""


def _load_ft_config() -> dict[str, Any]:
    """Load ``args/finetune_config.yaml`` (best-effort; empty dict if missing)."""
    config_path = _REPO_ROOT / "args" / "finetune_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def is_airgap_only(config: dict[str, Any] | None = None) -> bool:
    """Return True when fine-tuning must stay local-only.

    Policy = (detected air-gap environment) OR (config flag, default True).
    The flag is read from ``finetune.airgap_only`` or a top-level
    ``airgap_only`` key, defaulting to True so the safe path is the default.
    """
    if config is None:
        config = _load_ft_config()

    flag = None
    ft = config.get("finetune")
    if isinstance(ft, dict) and "airgap_only" in ft:
        flag = bool(ft["airgap_only"])
    elif "airgap_only" in config:
        flag = bool(config["airgap_only"])
    if flag is None:
        flag = True  # default: enclave-only

    if flag:
        return True

    # Even if the flag is off, a detected air-gapped environment forces local.
    try:
        from tools.airgap import is_airgap

        return bool(is_airgap())
    except Exception:
        return False


def assert_local_provider(provider_name: str, *, airgap: bool | None = None) -> str:
    """Validate ``provider_name`` against air-gap policy, returning it unchanged.

    Raises :class:`AirgapViolation` if a cloud provider is requested while the
    air-gap policy is in force.
    """
    if airgap is None:
        airgap = is_airgap_only()
    if airgap and provider_name in CLOUD_PROVIDERS:
        raise AirgapViolation(
            f"provider '{provider_name}' is a cloud provider; air-gap policy "
            f"permits only {sorted(ALLOWED_AIRGAP_PROVIDERS)}. Set "
            f"finetune.airgap_only=false AND leave the air-gap network to use cloud."
        )
    return provider_name


# --------------------------------------------------------------------------- #
# GPU detection / provider resolution
# --------------------------------------------------------------------------- #

def detect_training_capability() -> dict[str, Any]:
    """Detect GPU and report whether local QLoRA training is possible.

    Never raises — on any failure it reports training unavailable and falls
    back to CPU/Ollama serving.
    """
    try:
        from tools.finetune.gpu_detector import detect_gpu

        gpu = detect_gpu()
        return {
            "training_available": bool(gpu.can_train),
            "can_serve": bool(gpu.can_serve),
            "gpu_count": gpu.gpu_count,
            "total_vram_mb": gpu.total_vram_mb,
            "detection_method": gpu.detection_method,
            "recommended_batch_size": gpu.recommended_batch_size,
            "recommended_lora_rank": gpu.recommended_lora_rank,
            "gpu_result": gpu,
        }
    except Exception as e:  # noqa: BLE001 — degrade, never crash the canvas
        return {
            "training_available": False,
            "can_serve": True,  # Ollama CPU serving still possible
            "gpu_count": 0,
            "total_vram_mb": 0,
            "detection_method": "unavailable",
            "error": str(e),
            "gpu_result": None,
        }


def get_airgap_provider(
    provider_name: str | None = None,
    *,
    gpu_result: Any = None,
):
    """Return a LOCAL-ONLY :class:`FineTuneProvider`, enforcing air-gap policy.

    Args:
        provider_name: explicit local provider ('unsloth_local'). When None it
            is auto-selected from GPU/config but coerced to a local provider.
        gpu_result: pre-computed GPU detection result (avoids re-detection).

    Raises:
        AirgapViolation: if policy resolves to a cloud provider.
        ImportError: if the local provider SDK (Unsloth) is not installed.
    """
    from tools.finetune.provider_factory import get_provider

    airgap = is_airgap_only()
    if provider_name is not None:
        assert_local_provider(provider_name, airgap=airgap)
        return get_provider(provider_name=provider_name, gpu_result=gpu_result)

    # Auto-select, then enforce. The factory would only pick a cloud provider
    # when no GPU is present AND a cloud provider is enabled; under air-gap we
    # refuse that and pin to unsloth_local instead.
    provider = get_provider(provider_name=None, gpu_result=gpu_result)
    name = getattr(provider, "provider_name", "")
    if airgap and name in CLOUD_PROVIDERS:
        # Coerce to the only sanctioned local trainer.
        return get_provider(provider_name="unsloth_local", gpu_result=gpu_result)
    return provider


# --------------------------------------------------------------------------- #
# DIC fine-tune bookkeeping schema (idempotent; airgap-stamped)
# --------------------------------------------------------------------------- #

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS dic_ft_datasets (
        dic_dataset_id  TEXT PRIMARY KEY,
        ft_dataset_id   TEXT NOT NULL,
        collection_id   TEXT NOT NULL,
        name            TEXT,
        purpose         TEXT,
        base_model      TEXT,
        example_count   INTEGER DEFAULT 0,
        rag_pairs       INTEGER DEFAULT 0,
        kg_pairs        INTEGER DEFAULT 0,
        airgap          INTEGER NOT NULL DEFAULT 1,
        status          TEXT NOT NULL DEFAULT 'draft',
        created_at      TEXT NOT NULL,
        created_by      TEXT,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_ft_jobs (
        dic_job_id      TEXT PRIMARY KEY,
        ft_dataset_id   TEXT NOT NULL,
        collection_id   TEXT NOT NULL,
        provider        TEXT,
        base_model      TEXT,
        job_id          TEXT,
        status          TEXT NOT NULL DEFAULT 'pending',
        adapter_path    TEXT,
        gguf_path       TEXT,
        airgap          INTEGER NOT NULL DEFAULT 1,
        error           TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT,
        tenant_id       TEXT,
        classification  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dic_ft_models (
        dic_model_id        TEXT PRIMARY KEY,
        dic_job_id          TEXT,
        collection_id       TEXT NOT NULL,
        model_version_id    TEXT,
        model_name          TEXT,
        base_model          TEXT,
        ollama_model_name   TEXT,
        gguf_path           TEXT,
        quantization        TEXT,
        bleu                REAL,
        rouge_l             REAL,
        perplexity          REAL,
        hybrid_only         INTEGER NOT NULL DEFAULT 1,
        airgap              INTEGER NOT NULL DEFAULT 1,
        status              TEXT NOT NULL DEFAULT 'registered',
        created_at          TEXT NOT NULL,
        tenant_id           TEXT,
        classification      TEXT
    )
    """,
]


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    for ddl in _SCHEMA:
        cur.execute(ddl)
    conn.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_context(tenant_id: str | None, classification: str | None) -> tuple[str, str]:
    """Resolve tenant_id/classification: explicit args > Flask security ctx > defaults."""
    tid, cls = tenant_id, classification
    if tid is None or cls is None:
        try:
            from flask import g, has_request_context

            if has_request_context():
                ctx = getattr(g, "security_context", None)
                if ctx is not None:
                    tid = tid or getattr(ctx, "tenant_id", None)
                    cls = cls or getattr(ctx, "classification", None)
        except Exception:
            pass
    # DIC defaults to CUI (this is a CUI canvas), not UNCLASSIFIED.
    return (tid or "default"), (cls or "CUI")


# --------------------------------------------------------------------------- #
# Step 1 — build a versioned dataset from the collection's OWN chunks + KG
# --------------------------------------------------------------------------- #

def build_dataset(
    collection_id: str,
    *,
    name: str = "",
    purpose: str = "general",
    base_model: str = "qwen3:latest",
    questions_per_chunk: int = 3,
    chunk_limit: int = 200,
    kg_graph_id: str | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
    created_by: str | None = None,
    conn=None,
) -> dict[str, Any]:
    """Create a versioned FT dataset from a collection's RAG chunks + KG.

    Pairs are generated from ``dic_documents`` RAG chunks
    (``pair_generator.generate_from_rag_source``) and from the collection's
    Knowledge Graph (``kg_pair_generator.generate_pairs_from_graph``). Every
    example carries the resolved classification/tenant and stays in the enclave.

    Returns a result dict with ``ft_dataset_id`` and per-source pair counts.
    """
    from tools.finetune import dataset_manager, pair_generator

    tid, cls = _resolve_context(tenant_id, classification)
    ds_name = name or f"dic-{collection_id}-ft"

    created = dataset_manager.create_dataset(
        name=ds_name,
        purpose=purpose,
        description=f"DIC air-gap fine-tune dataset for collection {collection_id}",
        base_model=base_model,
        classification=cls,
        tenant_id=tid,
        project_id=collection_id,
        created_by=created_by or "",
    )
    if not created.get("success"):
        return {"success": False, "error": created.get("error", "dataset create failed")}
    ft_dataset_id = created["dataset_id"]

    rag_pairs = 0
    kg_pairs = 0
    errors: list[str] = []

    # RAG-chunk pairs — the collection's own ingested document chunks.
    try:
        rag_res = pair_generator.generate_from_rag_source(
            dataset_id=ft_dataset_id,
            source_table="dic_documents",
            purpose=purpose,
            questions_per_chunk=questions_per_chunk,
            limit=chunk_limit,
            classification=cls,
            tenant_id=tid,
            project_id=collection_id,
        )
        if rag_res.get("success"):
            rag_pairs = int(rag_res.get("added", rag_res.get("generated", 0)) or 0)
        else:
            errors.append(f"rag pairs: {rag_res.get('error')}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"rag pairs failed: {e}")

    # KG pairs — entity/relationship + community/crosswalk over the collection graph.
    try:
        from tools.finetune import kg_pair_generator

        graph_id = kg_graph_id or collection_id
        kg_res = kg_pair_generator.generate_pairs_from_graph(
            graph_id=graph_id,
            dataset_id=ft_dataset_id,
            store=True,
        )
        if kg_res.get("status") not in ("error", "disabled"):
            kg_pairs = int(kg_res.get("stored", kg_res.get("total", 0)) or 0)
        elif kg_res.get("status") == "error":
            errors.append(f"kg pairs: {kg_res.get('error')}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"kg pairs failed: {e}")

    example_count = rag_pairs + kg_pairs

    # DIC bookkeeping row (airgap-stamped).
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_schema(conn)
        dic_dataset_id = f"dic_ftds_{uuid.uuid4().hex[:12]}"
        conn.cursor().execute(
            """
            INSERT INTO dic_ft_datasets
                (dic_dataset_id, ft_dataset_id, collection_id, name, purpose,
                 base_model, example_count, rag_pairs, kg_pairs, airgap, status,
                 created_at, created_by, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                dic_dataset_id, ft_dataset_id, collection_id, ds_name, purpose,
                base_model, example_count, rag_pairs, kg_pairs,
                "ready" if example_count else "empty",
                _now(), created_by, tid, cls,
            ),
        )
        conn.commit()
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "success": example_count > 0,
        "dic_dataset_id": dic_dataset_id,
        "ft_dataset_id": ft_dataset_id,
        "collection_id": collection_id,
        "example_count": example_count,
        "rag_pairs": rag_pairs,
        "kg_pairs": kg_pairs,
        "tenant_id": tid,
        "classification": cls,
        "airgap": True,
        "errors": errors,
    }


# --------------------------------------------------------------------------- #
# Step 2 — train (local QLoRA), with graceful GPU degradation
# --------------------------------------------------------------------------- #

def train(
    ft_dataset_id: str,
    collection_id: str,
    *,
    base_model: str = "qwen3:latest",
    provider_name: str | None = None,
    epochs: int = 3,
    tenant_id: str | None = None,
    classification: str | None = None,
    conn=None,
) -> dict[str, Any]:
    """Start a local QLoRA training job for a dataset.

    Degrades gracefully: if no training-capable GPU is present (or Unsloth is
    not installed) it records the job as ``unavailable`` and returns
    ``training_available=False`` rather than raising.
    """
    tid, cls = _resolve_context(tenant_id, classification)

    cap = detect_training_capability()
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_schema(conn)
        dic_job_id = f"dic_ftjob_{uuid.uuid4().hex[:12]}"

        if not cap["training_available"]:
            conn.cursor().execute(
                """
                INSERT INTO dic_ft_jobs
                    (dic_job_id, ft_dataset_id, collection_id, provider, base_model,
                     job_id, status, airgap, error, created_at, updated_at,
                     tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, 'unavailable', 1, ?, ?, ?, ?, ?)
                """,
                (
                    dic_job_id, ft_dataset_id, collection_id, "none", base_model,
                    "", cap.get("error", "no training-capable GPU"),
                    _now(), _now(), tid, cls,
                ),
            )
            conn.commit()
            return {
                "success": False,
                "training_available": False,
                "dic_job_id": dic_job_id,
                "reason": "training unavailable: no training-capable GPU; "
                          "serve existing models via Ollama instead",
                "capability": {k: v for k, v in cap.items() if k != "gpu_result"},
            }

        # Resolve a LOCAL-ONLY provider and export the dataset to JSONL.
        try:
            provider = get_airgap_provider(
                provider_name=provider_name, gpu_result=cap.get("gpu_result")
            )
        except (AirgapViolation, ImportError) as e:
            conn.cursor().execute(
                """
                INSERT INTO dic_ft_jobs
                    (dic_job_id, ft_dataset_id, collection_id, provider, base_model,
                     job_id, status, airgap, error, created_at, updated_at,
                     tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, 'failed', 1, ?, ?, ?, ?, ?)
                """,
                (
                    dic_job_id, ft_dataset_id, collection_id, "none", base_model,
                    "", str(e), _now(), _now(), tid, cls,
                ),
            )
            conn.commit()
            return {
                "success": False,
                "training_available": True,
                "dic_job_id": dic_job_id,
                "error": str(e),
            }

        from tools.finetune import dataset_manager
        from tools.finetune.provider import FineTuneRequest

        export_dir = _REPO_ROOT / "data" / "finetune" / "dic" / collection_id
        export_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = export_dir / f"{ft_dataset_id}.jsonl"
        try:
            dataset_manager.export_jsonl(ft_dataset_id, str(jsonl_path))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "training_available": True,
                    "dic_job_id": dic_job_id, "error": f"export failed: {e}"}

        request = FineTuneRequest(
            dataset_path=str(jsonl_path),
            base_model=base_model,
            epochs=epochs,
            lora_rank=cap.get("recommended_lora_rank", 16),
            batch_size=cap.get("recommended_batch_size", 2),
            output_dir=str(export_dir / "adapter"),
            classification=cls,
            tenant_id=tid,
            project_id=collection_id,
        )
        status = provider.start_training(request)

        conn.cursor().execute(
            """
            INSERT INTO dic_ft_jobs
                (dic_job_id, ft_dataset_id, collection_id, provider, base_model,
                 job_id, status, adapter_path, gguf_path, airgap, error,
                 created_at, updated_at, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                dic_job_id, ft_dataset_id, collection_id,
                getattr(provider, "provider_name", "unsloth_local"), base_model,
                status.job_id, status.status, status.adapter_path,
                status.gguf_path, status.error, _now(), _now(), tid, cls,
            ),
        )
        conn.commit()
        return {
            "success": status.status not in ("failed", "canceled"),
            "training_available": True,
            "dic_job_id": dic_job_id,
            "job_id": status.job_id,
            "status": status.status,
            "provider": getattr(provider, "provider_name", "unsloth_local"),
            "adapter_path": status.adapter_path,
        }
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Step 3/4 — evaluate, export Q4_K_M, register, serve via Ollama
# --------------------------------------------------------------------------- #

def export_and_serve(
    dic_job_id: str,
    *,
    adapter_path: str = "",
    job_id: str = "",
    ft_dataset_id: str = "",
    collection_id: str = "",
    base_model: str = "qwen3:latest",
    model_name: str = "",
    evaluate: bool = True,
    tenant_id: str | None = None,
    classification: str | None = None,
    conn=None,
) -> dict[str, Any]:
    """Export a trained adapter to GGUF (Q4_K_M), register it, and serve locally.

    Steps: GGUF export -> register_model_version -> evaluate (BLEU/ROUGE/ppl) ->
    register with local Ollama -> write ``dic_ft_models`` (airgap, hybrid_only).
    """
    from tools.finetune import evaluator, gguf_exporter

    tid, cls = _resolve_context(tenant_id, classification)
    model_name = model_name or f"dic-{collection_id or 'collection'}"
    errors: list[str] = []

    # Backfill job details from dic_ft_jobs if not supplied.
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        _ensure_schema(conn)
        if not adapter_path or not collection_id:
            row = conn.cursor().execute(
                "SELECT ft_dataset_id, collection_id, job_id, adapter_path, base_model "
                "FROM dic_ft_jobs WHERE dic_job_id = ?",
                (dic_job_id,),
            ).fetchone()
            if row is not None:
                ft_dataset_id = ft_dataset_id or (row[0] or "")
                collection_id = collection_id or (row[1] or "")
                job_id = job_id or (row[2] or "")
                adapter_path = adapter_path or (row[3] or "")
                base_model = row[4] or base_model

        if not adapter_path:
            return {"success": False, "error": "no adapter_path for job; train first"}

        # 1) GGUF export (Q4_K_M).
        exp = gguf_exporter.export_to_gguf(
            adapter_path=adapter_path,
            model_name=model_name,
            quantization=DEFAULT_QUANTIZATION,
        )
        if not exp.get("success"):
            return {"success": False, "error": f"gguf export failed: {exp.get('error')}"}
        gguf_path = exp.get("gguf_path", "")

        # 2) Register the model version.
        reg = gguf_exporter.register_model_version(
            job_id=job_id or dic_job_id,
            model_name=model_name,
            base_model=base_model,
            adapter_path=adapter_path,
            gguf_path=gguf_path,
            classification=cls,
            tenant_id=tid,
            project_id=collection_id,
        )
        model_version_id = reg.get("model_version_id", reg.get("mv_id", ""))

        # 3) Serve via local Ollama.
        ollama_model_name = ""
        try:
            srv = gguf_exporter.register_with_ollama(gguf_path, model_name)
            if srv.get("success"):
                ollama_model_name = srv.get("ollama_model_name", model_name)
            else:
                errors.append(f"ollama register: {srv.get('error')}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"ollama register failed: {e}")

        # 4) Evaluate (pure-Python BLEU/ROUGE/perplexity) — best-effort.
        bleu = rouge_l = ppl = None
        if evaluate and model_version_id:
            try:
                ev = evaluator.evaluate_model(
                    model_version_id=model_version_id,
                    dataset_id=ft_dataset_id,
                    ollama_model_name=ollama_model_name,
                    classification=cls,
                    tenant_id=tid,
                    project_id=collection_id,
                )
                if ev.get("success", True):
                    bleu = ev.get("bleu")
                    rouge_l = ev.get("rouge_l", ev.get("rougeL"))
                    ppl = ev.get("perplexity", ev.get("perplexity_estimate"))
            except Exception as e:  # noqa: BLE001
                errors.append(f"evaluate failed: {e}")

        # 5) DIC model bookkeeping (airgap + hybrid_only flags).
        dic_model_id = f"dic_ftmodel_{uuid.uuid4().hex[:12]}"
        conn.cursor().execute(
            """
            INSERT INTO dic_ft_models
                (dic_model_id, dic_job_id, collection_id, model_version_id,
                 model_name, base_model, ollama_model_name, gguf_path, quantization,
                 bleu, rouge_l, perplexity, hybrid_only, airgap, status,
                 created_at, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'registered', ?, ?, ?)
            """,
            (
                dic_model_id, dic_job_id, collection_id, model_version_id,
                model_name, base_model, ollama_model_name, gguf_path,
                DEFAULT_QUANTIZATION, bleu, rouge_l, ppl, _now(), tid, cls,
            ),
        )
        conn.commit()
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "success": True,
        "dic_model_id": dic_model_id,
        "model_version_id": model_version_id,
        "model_name": model_name,
        "ollama_model_name": ollama_model_name,
        "gguf_path": gguf_path,
        "quantization": DEFAULT_QUANTIZATION,
        "metrics": {"bleu": bleu, "rouge_l": rouge_l, "perplexity": ppl},
        "hybrid_only": True,
        "airgap": True,
        "errors": errors,
    }


# --------------------------------------------------------------------------- #
# Full pipeline orchestrator
# --------------------------------------------------------------------------- #

@dataclass
class FinetuneOutcome:
    collection_id: str
    airgap: bool
    training_available: bool
    ft_dataset_id: str = ""
    dic_job_id: str = ""
    dic_model_id: str = ""
    ollama_model_name: str = ""
    example_count: int = 0
    status: str = "pending"
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection_id": self.collection_id,
            "airgap": self.airgap,
            "training_available": self.training_available,
            "ft_dataset_id": self.ft_dataset_id,
            "dic_job_id": self.dic_job_id,
            "dic_model_id": self.dic_model_id,
            "ollama_model_name": self.ollama_model_name,
            "example_count": self.example_count,
            "status": self.status,
            "errors": self.errors,
        }


def run_finetune(
    collection_id: str,
    *,
    base_model: str = "qwen3:latest",
    purpose: str = "general",
    epochs: int = 3,
    provider_name: str | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
    do_train: bool = True,
) -> FinetuneOutcome:
    """Run the full air-gap fine-tune pipeline for one collection.

    build_dataset -> train -> export_and_serve, degrading gracefully at each
    step. Returns a :class:`FinetuneOutcome`. Never raises for the normal
    "no GPU / option not configured" cases — those report status instead.
    """
    airgap = is_airgap_only()
    outcome = FinetuneOutcome(
        collection_id=collection_id, airgap=airgap, training_available=False
    )

    ds = build_dataset(
        collection_id,
        purpose=purpose,
        base_model=base_model,
        tenant_id=tenant_id,
        classification=classification,
    )
    outcome.ft_dataset_id = ds.get("ft_dataset_id", "")
    outcome.example_count = ds.get("example_count", 0)
    outcome.errors.extend(ds.get("errors", []))
    if not ds.get("success"):
        outcome.status = "no_training_data"
        return outcome

    if not do_train:
        outcome.status = "dataset_ready"
        return outcome

    tr = train(
        outcome.ft_dataset_id,
        collection_id,
        base_model=base_model,
        provider_name=provider_name,
        epochs=epochs,
        tenant_id=tenant_id,
        classification=classification,
    )
    outcome.training_available = tr.get("training_available", False)
    outcome.dic_job_id = tr.get("dic_job_id", "")
    if not outcome.training_available:
        outcome.status = "training_unavailable"
        if tr.get("reason"):
            outcome.errors.append(tr["reason"])
        return outcome
    if not tr.get("success"):
        outcome.status = "train_failed"
        if tr.get("error"):
            outcome.errors.append(tr["error"])
        return outcome

    # Training may run asynchronously; only export when the adapter is ready.
    if tr.get("status") == "completed" and tr.get("adapter_path"):
        ex = export_and_serve(
            outcome.dic_job_id,
            adapter_path=tr.get("adapter_path", ""),
            job_id=tr.get("job_id", ""),
            ft_dataset_id=outcome.ft_dataset_id,
            collection_id=collection_id,
            base_model=base_model,
            tenant_id=tenant_id,
            classification=classification,
        )
        outcome.dic_model_id = ex.get("dic_model_id", "")
        outcome.ollama_model_name = ex.get("ollama_model_name", "")
        outcome.errors.extend(ex.get("errors", []))
        outcome.status = "served" if ex.get("success") else "export_failed"
    else:
        outcome.status = "training_started"

    return outcome


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DIC air-gap fine-tuning bridge (OPTION; canvas works without it)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("capability", help="Report GPU/training capability + air-gap policy")
    p_cap.add_argument("--json", action="store_true")

    p_ds = sub.add_parser("build-dataset", help="Build a versioned FT dataset from a collection")
    p_ds.add_argument("collection_id")
    p_ds.add_argument("--base-model", default="qwen3:latest")
    p_ds.add_argument("--purpose", default="general")
    p_ds.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run", help="Run the full air-gap fine-tune pipeline")
    p_run.add_argument("collection_id")
    p_run.add_argument("--base-model", default="qwen3:latest")
    p_run.add_argument("--epochs", type=int, default=3)
    p_run.add_argument("--no-train", action="store_true", help="Build dataset only")
    p_run.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "capability":
        cap = detect_training_capability()
        cap.pop("gpu_result", None)
        cap["airgap_only"] = is_airgap_only()
        cap["allowed_providers"] = sorted(ALLOWED_AIRGAP_PROVIDERS)
        print(json.dumps(cap, indent=2, default=str))
        return 0

    if args.cmd == "build-dataset":
        res = build_dataset(args.collection_id, base_model=args.base_model, purpose=args.purpose)
        print(json.dumps(res, indent=2, default=str))
        return 0 if res.get("success") else 1

    if args.cmd == "run":
        outcome = run_finetune(
            args.collection_id,
            base_model=args.base_model,
            epochs=args.epochs,
            do_train=not args.no_train,
        )
        print(json.dumps(outcome.to_dict(), indent=2, default=str))
        return 0 if outcome.status in ("served", "training_started", "dataset_ready") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
