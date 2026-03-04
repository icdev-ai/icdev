#!/usr/bin/env python3
# CUI // SP-CTI
"""TinyML model lifecycle manager for SparkPilot.

Manages TFLite Micro models: registration, deployment, OTA model update, inference tracking.

Usage:
    python tools/edge_ai/model_manager.py --register --name "anomaly-detector" --task anomaly_detection --size 48000 --json
    python tools/edge_ai/model_manager.py --list --json
    python tools/edge_ai/model_manager.py --deploy --model-id mdl-001 --device-id dev-001 --json
    python tools/edge_ai/model_manager.py --inference-stats --device-id dev-001 --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sparkpilot.db"

# TinyML model templates for common tasks
MODEL_TEMPLATES = {
    "anomaly_detection": {
        "description": "Autoencoder for sensor anomaly detection",
        "typical_size_bytes": 32000,
        "arena_size_bytes": 16384,
        "input_shape": [1, 10],
        "output_shape": [1, 10],
        "quantization": "int8",
    },
    "keyword_spotting": {
        "description": "Micro Speech model for wake word detection",
        "typical_size_bytes": 48000,
        "arena_size_bytes": 32768,
        "input_shape": [1, 49, 40],
        "output_shape": [1, 4],
        "quantization": "int8",
    },
    "image_classification": {
        "description": "MobileNet v1 for image classification (96x96)",
        "typical_size_bytes": 300000,
        "arena_size_bytes": 131072,
        "input_shape": [1, 96, 96, 1],
        "output_shape": [1, 10],
        "quantization": "int8",
    },
    "predictive_maintenance": {
        "description": "Vibration pattern classifier for predictive maintenance",
        "typical_size_bytes": 24000,
        "arena_size_bytes": 8192,
        "input_shape": [1, 128],
        "output_shape": [1, 3],
        "quantization": "int8",
    },
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def register_model(name: str, task_type: str, model_size: int = 0,
                   source: str = "custom", project_id: str = "") -> dict:
    """Register a TinyML model."""
    model_id = f"mdl-{uuid.uuid4().hex[:8]}"
    template = MODEL_TEMPLATES.get(task_type, {})
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO ml_models (id, project_id, model_name, model_type, task_type, "
            "model_size_bytes, arena_size_bytes, input_shape, output_shape, "
            "quantization, source) VALUES (?, ?, ?, 'tflite_micro', ?, ?, ?, ?, ?, ?, ?)",
            (model_id, project_id, name, task_type,
             model_size or template.get("typical_size_bytes", 0),
             template.get("arena_size_bytes", 16384),
             json.dumps(template.get("input_shape", [])),
             json.dumps(template.get("output_shape", [])),
             template.get("quantization", "int8"),
             source),
        )
        conn.commit()
        return {
            "status": "success",
            "model_id": model_id,
            "name": name,
            "task_type": task_type,
            "size_bytes": model_size or template.get("typical_size_bytes", 0),
            "arena_bytes": template.get("arena_size_bytes", 16384),
            "ram_required_kb": round((model_size or template.get("typical_size_bytes", 0)) / 1024 +
                                     template.get("arena_size_bytes", 16384) / 1024, 1),
        }
    finally:
        conn.close()


def list_models() -> dict:
    """List registered models."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM ml_models ORDER BY created_at DESC").fetchall()
        return {"status": "success", "count": len(rows),
                "models": [dict(r) for r in rows]}
    finally:
        conn.close()


def deploy_model(model_id: str, device_id: str) -> dict:
    """Deploy model to device via OTA."""
    conn = _get_db()
    try:
        ota_id = f"ota-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO ota_update_log (id, device_id, model_id, update_type, status) "
            "VALUES (?, ?, ?, 'model', 'pending')",
            (ota_id, device_id, model_id),
        )
        conn.execute(
            "INSERT INTO audit_trail (id, event_type, actor, action) "
            "VALUES (?, 'model.deploy', 'edge-ai-agent', ?)",
            (f"aud-{uuid.uuid4().hex[:8]}", f"Deploy model {model_id} to {device_id}"),
        )
        conn.commit()
        return {
            "status": "success",
            "ota_id": ota_id,
            "model_id": model_id,
            "device_id": device_id,
            "mqtt_topic": f"sparkpilot/devices/{device_id}/ota/model",
        }
    finally:
        conn.close()


def get_inference_stats(device_id: str) -> dict:
    """Get inference telemetry for a device."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT model_id, COUNT(*) as count, "
            "AVG(latency_ms) as avg_latency, AVG(confidence) as avg_confidence, "
            "AVG(arena_used_bytes) as avg_arena "
            "FROM inference_telemetry WHERE device_id = ? GROUP BY model_id",
            (device_id,),
        ).fetchall()
        return {
            "status": "success",
            "device_id": device_id,
            "models": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def list_templates() -> dict:
    """List available model templates."""
    return {
        "status": "success",
        "templates": {k: {**v, "task_type": k} for k, v in MODEL_TEMPLATES.items()},
    }


def main():
    parser = argparse.ArgumentParser(description="TinyML Model Manager")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--inference-stats", action="store_true")
    parser.add_argument("--templates", action="store_true")
    parser.add_argument("--name", help="Model name")
    parser.add_argument("--task", help="Task type", choices=list(MODEL_TEMPLATES.keys()))
    parser.add_argument("--size", type=int, default=0, help="Model size in bytes")
    parser.add_argument("--model-id", help="Model ID")
    parser.add_argument("--device-id", help="Device ID")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.register:
        result = register_model(args.name or "model", args.task or "anomaly_detection", args.size)
    elif args.list:
        result = list_models()
    elif args.deploy:
        result = deploy_model(args.model_id, args.device_id)
    elif args.inference_stats:
        result = get_inference_stats(args.device_id)
    elif args.templates:
        result = list_templates()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
