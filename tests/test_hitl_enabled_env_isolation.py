# CUI // SP-CTI
"""`_hitl_enabled()` must read its flag from `.env` WITHOUT rewriting the
dashboard process environment.

It used to call `load_dotenv(.env, override=True)` on every HITL request, so
loading `/workflow/` reset EVERY key in the running dashboard to the `.env`
value -- including `ICDEV_DATABASE_URL`. An isolated E2E run (dashboard on a
throwaway `icdev_e2e`) switched to the canonical `icdev` the moment the
Workflow page was hit, and every later spec wrote its fixtures into the live
board (measured 2026-10-07: NOC incidents/RFCs/MOPs, studio workflows,
proposal sections). CI never saw it because CI has no `.env` file.
"""
from __future__ import annotations

import importlib
import os

import dotenv


def _hitl():
    return importlib.import_module("tools.workflow_hitl.blueprint")


def test_hitl_flag_read_does_not_rewrite_process_env(monkeypatch):
    env_file = {
        "ICDEV_HITL_ENABLED": "true",
        "ICDEV_DATABASE_URL": "postgresql://u:p@127.0.0.1:5432/icdev",
    }
    calls = []

    def _fake_load_dotenv(path=None, *args, override=False, **kwargs):
        calls.append(override)
        for k, v in env_file.items():
            if override or k not in os.environ:
                os.environ[k] = v
        return True

    monkeypatch.setattr(dotenv, "load_dotenv", _fake_load_dotenv)
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: dict(env_file))
    monkeypatch.setenv("ICDEV_DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/icdev_e2e")
    monkeypatch.delenv("ICDEV_HITL_ENABLED", raising=False)

    assert _hitl()._hitl_enabled() is True
    assert os.environ["ICDEV_DATABASE_URL"].endswith("/icdev_e2e"), (
        "reading the HITL flag rewrote the process DSN from .env"
    )
    assert True not in calls, "load_dotenv(override=True) must not run inside the dashboard"


def test_hitl_flag_falls_back_to_process_env_when_env_file_lacks_it(monkeypatch):
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: {})
    monkeypatch.setenv("ICDEV_HITL_ENABLED", "true")
    assert _hitl()._hitl_enabled() is True
    monkeypatch.setenv("ICDEV_HITL_ENABLED", "false")
    assert _hitl()._hitl_enabled() is False


def test_env_file_value_still_wins_so_a_toggle_needs_no_restart(monkeypatch):
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: {"ICDEV_HITL_ENABLED": "false"})
    monkeypatch.setenv("ICDEV_HITL_ENABLED", "true")
    assert _hitl()._hitl_enabled() is False
