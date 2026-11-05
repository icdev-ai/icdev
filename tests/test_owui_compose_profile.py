#!/usr/bin/env python3
# CUI // SP-CTI
"""The `openwebui` compose profile starts GOVERNED (owui-ship-01, ADR D-OWUI-1).

Open WebUI is admitted only as a client with no authority: ICDEV's /v1 is its
sole model backend and its own bypass paths are off. Each assertion here is one
of the forbidden configurations in D-OWUI-1, pinned so an edit to
docker-compose.yml cannot quietly re-open it.
"""

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((BASE_DIR / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def svc(compose):
    return compose["services"]["openwebui"]


def test_is_opt_in(svc):
    assert svc["profiles"] == ["openwebui"]


def test_image_is_pinned_by_digest(svc):
    assert re.fullmatch(r"ghcr\.io/open-webui/open-webui:v[\d.]+@sha256:[0-9a-f]{64}", svc["image"])


def test_only_model_backend_is_the_icdev_gateway(svc, compose):
    env = svc["environment"]
    assert env["ENABLE_OLLAMA_API"] == "false"
    assert env["OPENAI_API_BASE_URL"] == "http://icdev-api-gateway:8443/v1"
    assert "icdev-api-gateway" in compose["services"]
    assert "OPENAI_API_BASE_URLS" not in env, "a second model backend would bypass the router"


def test_bypass_paths_are_off(svc):
    env = svc["environment"]
    assert env["ENABLE_DIRECT_CONNECTIONS"] == "false"
    assert env["BYPASS_EMBEDDING_AND_RETRIEVAL"] == "true"
    assert env["ENABLE_SIGNUP"] == "false"
    assert env["OFFLINE_MODE"] == "true"
    assert env["ANONYMIZED_TELEMETRY"] == "false"


def test_secrets_are_required_not_defaulted(svc):
    env = svc["environment"]
    for key in ("OPENAI_API_KEY", "WEBUI_SECRET_KEY"):
        assert ":?" in env[key], f"{key} must fail the profile when unset, never default"


def test_loopback_only_and_carries_a_classification_banner(svc):
    assert all(p.startswith("127.0.0.1:") for p in svc["ports"])
    banner = json.loads(svc["environment"]["WEBUI_BANNERS"])[0]
    assert banner["content"].startswith("CUI") and banner["dismissible"] is False
