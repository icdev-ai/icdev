#!/usr/bin/env python3
# CUI // SP-CTI
"""GGUF exporter — convert LoRA adapters to GGUF and register with Ollama (D-FT-5).

Pipeline: adapter → Unsloth save_pretrained_gguf() → Q4_K_M GGUF → ollama create

Naming convention: {app}-{purpose}-v{version}

Usage:
    python tools/finetune/gguf_exporter.py --adapter-path data/finetune/adapters/xxx --model-name "govproposal-draft-v1" --json  # noqa: E501
    python tools/finetune/gguf_exporter.py --register --model-name "govproposal-draft-v1" --gguf-path data/finetune/gguf/xxx.gguf --json  # noqa: E501
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_hash(path: str) -> str:
    """SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def export_to_gguf(
    adapter_path: str,
    output_dir: str = "",
    model_name: str = "",
    quantization: str = "q4_k_m",
) -> Dict[str, Any]:
    """Export LoRA adapter to GGUF format via Unsloth.

    This calls the _export_worker.py subprocess which loads Unsloth
    and calls save_pretrained_gguf().
    """
    if not output_dir:
        output_dir = str(BASE_DIR / "data" / "finetune" / "gguf")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    gguf_filename = f"{model_name or 'model'}-{quantization}.gguf"
    gguf_path = str(Path(output_dir) / gguf_filename)

    script = str(BASE_DIR / "tools" / "finetune" / "_export_worker.py")
    cmd = [
        sys.executable,
        script,
        "--adapter-path",
        adapter_path,
        "--output-path",
        gguf_path,
        "--quantization",
        quantization,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(BASE_DIR),
        )
        if proc.returncode == 0:
            try:
                json.loads(proc.stdout.strip())
            except json.JSONDecodeError:
                pass

            file_size = Path(gguf_path).stat().st_size if Path(gguf_path).exists() else 0
            return {
                "success": True,
                "gguf_path": gguf_path,
                "quantization": quantization,
                "file_size_bytes": file_size,
                "file_hash": _file_hash(gguf_path) if Path(gguf_path).exists() else "",
            }
        else:
            return {"success": False, "error": f"GGUF export failed: {proc.stderr[:500]}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "GGUF export timed out (10 min)"}
    except FileNotFoundError:
        return {"success": False, "error": "Export worker script not found"}


def register_with_ollama(
    model_name: str,
    gguf_path: str,
    base_model: str = "qwen3:latest",
) -> Dict[str, Any]:
    """Register a GGUF model with Ollama via 'ollama create' (D-FT-5).

    Creates a Modelfile and runs 'ollama create <model_name>'.
    """
    if not Path(gguf_path).exists():
        return {"success": False, "error": f"GGUF file not found: {gguf_path}"}

    # Create Modelfile
    modelfile_content = f"FROM {gguf_path}\n"
    modelfile_path = Path(gguf_path).parent / f"Modelfile.{model_name}"

    try:
        with open(modelfile_path, "w") as f:
            f.write(modelfile_content)

        proc = subprocess.run(
            ["ollama", "create", model_name, "-f", str(modelfile_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode == 0:
            return {
                "success": True,
                "ollama_model_name": model_name,
                "message": f"Model '{model_name}' registered with Ollama",
            }
        else:
            return {"success": False, "error": f"ollama create failed: {proc.stderr[:500]}"}
    except FileNotFoundError:
        return {"success": False, "error": "Ollama CLI not found. Is Ollama installed?"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "ollama create timed out (5 min)"}


def register_model_version(
    job_id: str,
    model_name: str,
    base_model: str,
    adapter_path: str = "",
    gguf_path: str = "",
    ollama_model_name: str = "",
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Register a trained model version in the database (D-FT-7)."""
    conn = _get_db(db_path)
    try:
        # Get next version number
        row = conn.execute(
            "SELECT MAX(version) as max_v FROM ft_model_versions WHERE model_name = %s",
            (model_name,),
        ).fetchone()
        version = (row["max_v"] or 0) + 1

        mv_id = f"mv-{uuid.uuid4().hex[:12]}"
        adapter_hash = _file_hash(adapter_path) if adapter_path and Path(adapter_path).exists() else ""
        file_size = 0
        if gguf_path and Path(gguf_path).exists():
            file_size = Path(gguf_path).stat().st_size

        conn.execute(
            """INSERT INTO ft_model_versions
               (id, job_id, model_name, version, base_model, adapter_path,
                gguf_path, ollama_model_name, adapter_hash, file_size_bytes,
                status, classification, tenant_id, project_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'created', %s, %s, %s, %s)""",
            (
                mv_id,
                job_id,
                model_name,
                version,
                base_model,
                adapter_path,
                gguf_path,
                ollama_model_name,
                adapter_hash,
                file_size,
                classification,
                tenant_id,
                project_id,
                _now(),
            ),
        )
        conn.commit()

        return {
            "success": True,
            "model_version_id": mv_id,
            "model_name": model_name,
            "version": version,
            "ollama_model_name": ollama_model_name,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="GGUF exporter and Ollama registrar")
    parser.add_argument("--export", action="store_true", help="Export adapter to GGUF")
    parser.add_argument("--register", action="store_true", help="Register GGUF with Ollama")
    parser.add_argument("--adapter-path", type=str, default="")
    parser.add_argument("--gguf-path", type=str, default="")
    parser.add_argument("--output-dir", type=str, default="")
    parser.add_argument("--model-name", type=str, default="")
    parser.add_argument("--quantization", type=str, default="q4_k_m")
    parser.add_argument("--base-model", type=str, default="qwen3:latest")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.export:
        result = export_to_gguf(
            adapter_path=args.adapter_path,
            output_dir=args.output_dir,
            model_name=args.model_name,
            quantization=args.quantization,
        )
    elif args.register:
        result = register_with_ollama(
            model_name=args.model_name,
            gguf_path=args.gguf_path,
            base_model=args.base_model,
        )
    else:
        result = {"success": False, "error": "Specify --export or --register"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
