#!/usr/bin/env python3
"""Flask wrapper for SageMaker — proxies /invocations to llama.cpp /v1/chat/completions.

SageMaker sends POST /invocations and GET /ping.
This wrapper translates to llama.cpp's OpenAI-compatible API on port 8081.
"""
import json
import logging

import requests
from flask import Flask, Response, request

app = Flask(__name__)
logger = logging.getLogger("sagemaker-judge")

LLAMA_URL = "http://localhost:8081"


@app.route("/ping", methods=["GET"])
def ping():
    """SageMaker health check."""
    try:
        resp = requests.get(f"{LLAMA_URL}/health", timeout=5)
        if resp.ok:
            return Response(status=200)
    except Exception:
        pass
    return Response(status=503)


@app.route("/invocations", methods=["POST"])
def invocations():
    """SageMaker inference endpoint — proxies to llama.cpp."""
    data = request.get_json(force=True)
    prompt = data.get("prompt", "")
    max_tokens = data.get("max_tokens", 1024)
    temperature = data.get("temperature", 0.0)

    try:
        resp = requests.post(
            f"{LLAMA_URL}/v1/chat/completions",
            json={
                "model": "prometheus",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        return Response(
            json.dumps({"content": content, "model": "prometheus-7b-v2.0"}),
            mimetype="application/json",
        )
    except Exception as e:
        logger.error("Inference error: %s", e)
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )
