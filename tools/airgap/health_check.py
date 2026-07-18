#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Air-gap-aware health check.

Replaces cloud-dependent checks (Claude Code CLI, Anthropic API, Bedrock)
with local equivalents.  Reports what works in air-gap mode vs. what is
degraded.

Usage::

    python tools/airgap/health_check.py [--json]
"""

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = get_logger("icdev.airgap.health_check")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def check_local_llm() -> Dict[str, Any]:
    """Check all local LLM servers (Ollama, vLLM, llama.cpp, LM Studio, etc.).

    In no-LLM mode (ICDEV_NO_LLM=true) this returns ``info`` with a note
    that LLM checks are intentionally skipped — ICDEV™ runs in
    deterministic-only mode and missing LLM servers are not a failure.
    """
    import os as _os

    if _os.environ.get("ICDEV_NO_LLM", "").lower() in ("true", "1", "yes"):
        return {
            "status": "info",
            "message": "ICDEV_NO_LLM=true — local LLM probing skipped (deterministic-only mode)",
            "no_llm_mode": True,
        }

    try:
        from tools.airgap.detector import probe_local_llm_servers

        servers = probe_local_llm_servers()
        reachable = [s for s in servers if s["reachable"]]

        if not reachable:
            return {
                "status": "warn",
                "message": "No local LLM servers reachable — ICDEV™ will run in no-LLM "
                "(deterministic-only) mode. Set ICDEV_NO_LLM=true to silence this warning.",
                "probed": [s["name"] for s in servers],
                "no_llm_mode": True,
            }

        # For Ollama specifically, also list models
        ollama_models = []
        for s in reachable:
            if s["name"] == "ollama":
                try:
                    import urllib.request

                    base = s["url"].rstrip("/")
                    resp = urllib.request.urlopen(f"{base}/api/tags", timeout=5)  # nosec B310 localhost only
                    data = json.loads(resp.read())
                    ollama_models = [m["name"] for m in data.get("models", [])]
                except Exception:
                    pass

        return {
            "status": "pass",
            "servers": [{"name": s["name"], "url": s["url"]} for s in reachable],
            "server_count": len(reachable),
            "ollama_models": ollama_models,
        }
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def check_database() -> Dict[str, Any]:
    """Check ICDEV database accessibility."""
    db_path = BASE_DIR / "data" / "icdev.db"
    if not db_path.exists():
        return {"status": "fail", "error": f"Database not found: {db_path}"}

    try:
        from tools.db.storage import get_connection

        with get_connection() as conn:
            # pg-portability: sqlite-only path — air-gap health check gated on the
            # data/icdev.db file existing (checked above), i.e. a SQLite deployment;
            # a pure-PG deployment returns "Database not found" before reaching here.
            tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        return {"status": "pass", "table_count": tables}
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def check_python_deps() -> Dict[str, Any]:
    """Check critical Python dependencies for air-gap operation."""
    deps = {
        "yaml": "pyyaml",
        "jinja2": "Jinja2",
        "flask": "Flask",
        "requests": "requests",
        "pytest": "pytest",
    }
    results = {}
    for module, package in deps.items():
        try:
            __import__(module)
            results[package] = "installed"
        except ImportError:
            results[package] = "missing"

    all_ok = all(v == "installed" for v in results.values())
    return {
        "status": "pass" if all_ok else "warn",
        "dependencies": results,
    }


def check_llm_routing() -> Dict[str, Any]:
    """Check that LLM routing can resolve to local models.

    In no-LLM mode this returns ``info`` because routing without any
    backing provider is the expected, supported configuration.
    """
    import os as _os

    if _os.environ.get("ICDEV_NO_LLM", "").lower() in ("true", "1", "yes"):
        return {
            "status": "info",
            "message": "ICDEV_NO_LLM=true — LLM routing intentionally disabled",
            "no_llm_mode": True,
        }

    try:
        from tools.llm.router import LLMRouter
        from tools.airgap.config_patcher import _is_provider_local

        router = LLMRouter()
        # Use the new has_any_llm() preflight to distinguish "no provider
        # at all" from "all probed providers timed out". Either way, the
        # health check should report degraded — not unhealthy — because
        # ICDEV™'s deterministic tools still work without an LLM.
        if not router.has_any_llm():
            return {
                "status": "warn",
                "message": "No LLM provider in any chain is reachable — ICDEV™ will run in "
                "no-LLM (deterministic-only) mode. Set ICDEV_NO_LLM=true to silence "
                "future probes and short-circuit LLM calls cleanly.",
                "no_llm_mode": True,
                "has_local_model": False,
                "has_any_model": False,
            }

        # Determine which providers are local by URL analysis
        local_provider_names = set()
        for pname, pcfg in router._config.get("providers", {}).items():
            if _is_provider_local(pcfg):
                local_provider_names.add(pname)

        # Test a few critical functions
        test_functions = [
            "code_generation",
            "compliance_export",
            "narrative_generation",
            "default",
        ]
        results = {}
        for func in test_functions:
            provider, model_id, cfg = router.get_provider_for_function(func)
            if provider:
                # Check if this provider's name maps to a local config
                prov_name = cfg.get("provider", provider.provider_name)
                results[func] = {
                    "provider": provider.provider_name,
                    "model": model_id,
                    "local": prov_name in local_provider_names,
                }
            else:
                results[func] = {"provider": None, "model": None, "local": False}

        has_any = any(r.get("provider") for r in results.values())
        has_local = any(r.get("local") for r in results.values())
        if has_local:
            status = "pass"
        elif has_any:
            status = "warn"  # Models resolve but all are cloud — will fail in true air-gap
        else:
            status = "fail"
        return {
            "status": status,
            "routes": results,
            "has_local_model": has_local,
            "has_any_model": has_any,
            "local_providers": sorted(local_provider_names),
        }
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def check_hooks() -> Dict[str, Any]:
    """Check that hook compatibility layer is functional."""
    try:
        from tools.airgap.hook_compat import get_session_id, get_project_dir

        sid = get_session_id()
        pdir = get_project_dir()
        return {
            "status": "pass",
            "session_id": sid,
            "project_dir": str(pdir),
            "project_exists": pdir.exists(),
        }
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def check_pdf_extraction() -> Dict[str, Any]:
    """Check local PDF extraction capability."""
    providers_available = []

    # pypdf (text-only, always works)
    try:
        import importlib.util

        if importlib.util.find_spec("pypdf"):
            providers_available.append("pypdf")
    except Exception:
        pass

    # Vision OCR via any local LLM with multimodal support (LLaVA, Gemma3, etc.)
    try:
        from tools.airgap.detector import probe_local_llm_servers

        for server in probe_local_llm_servers():
            if server["reachable"] and server["name"] == "ollama":
                import urllib.request

                base = server["url"].rstrip("/")
                resp = urllib.request.urlopen(f"{base}/api/tags", timeout=3)  # nosec B310 localhost only
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                vision_models = [
                    m for m in models if any(v in m.lower() for v in ("llava", "gemma3", "bakllava", "moondream"))
                ]
                if vision_models:
                    providers_available.append(f"vision:{','.join(vision_models[:3])}")
                break
    except Exception:
        pass

    return {
        "status": "pass" if providers_available else "warn",
        "providers": providers_available,
        "note": "pypdf handles text PDFs; llava handles scanned PDFs"
        if providers_available
        else "No PDF providers — install pypdf",
    }


def check_git() -> Dict[str, Any]:
    """Check git availability."""
    if not shutil.which("git"):
        return {"status": "fail", "error": "git not found on PATH"}
    return {"status": "pass"}


def check_no_llm_capabilities() -> Dict[str, Any]:
    """Verify ICDEV™'s deterministic tools work without any LLM.

    Even in environments with no LLM at all (no Ollama, no API keys, no
    vLLM), the bulk of ICDEV™ — compliance template generation, security
    scanning, audit trail, database operations, project scaffolding,
    Jinja2 rendering — must still function. This check imports a
    representative sample of those modules and confirms they load
    without depending on the LLM router.
    """
    deterministic_modules = [
        "tools.audit.audit_logger",
        "tools.compliance.narrative_generator",
        "tools.compliance.classification_manager",
        "tools.db.storage",
        "tools.project.project_create",
    ]
    results: Dict[str, str] = {}
    for mod in deterministic_modules:
        try:
            __import__(mod)
            results[mod] = "ok"
        except ImportError as exc:
            results[mod] = "missing: {}".format(exc)
        except Exception as exc:
            results[mod] = "error: {}".format(exc)

    failures = [m for m, v in results.items() if not v.startswith("ok")]

    # Verify the router itself raises LLMUnavailableError cleanly when
    # invoked in no-LLM mode (rather than crashing the caller). This is
    # the contract that lets every tool fall back to deterministic
    # behavior on a single ``except LLMUnavailableError``.
    router_contract_ok = False
    contract_error = ""
    try:
        from tools.llm.router import LLMRouter, LLMUnavailableError
        from tools.llm.provider import LLMRequest

        old_env = os.environ.get("ICDEV_NO_LLM")
        try:
            os.environ["ICDEV_NO_LLM"] = "true"
            router = LLMRouter()
            try:
                router.invoke(
                    "default",
                    LLMRequest(messages=[{"role": "user", "content": "ping"}], skip_injection_scan=True),
                )
            except LLMUnavailableError:
                router_contract_ok = True
            except Exception as exc:
                contract_error = "router raised {} instead of LLMUnavailableError: {}".format(
                    type(exc).__name__, exc
                )
        finally:
            if old_env is None:
                os.environ.pop("ICDEV_NO_LLM", None)
            else:
                os.environ["ICDEV_NO_LLM"] = old_env
    except ImportError as exc:
        contract_error = "router import failed: {}".format(exc)

    if failures or not router_contract_ok:
        return {
            "status": "fail",
            "modules": results,
            "router_no_llm_contract": router_contract_ok,
            "contract_error": contract_error,
            "failures": failures,
        }
    return {
        "status": "pass",
        "modules": results,
        "router_no_llm_contract": True,
        "message": "Deterministic tools and no-LLM router contract verified",
    }


def check_cloud_unreachable() -> Dict[str, Any]:
    """Verify cloud APIs are NOT reachable (confirms air-gap)."""
    from tools.airgap.detector import detect_environment

    env = detect_environment(use_cache=False)

    if env["anthropic_api"] or env["bedrock"]:
        return {
            "status": "info",
            "message": "Cloud APIs are reachable — not fully air-gapped",
            "anthropic": env["anthropic_api"],
            "bedrock": env["bedrock"],
        }
    return {
        "status": "pass",
        "message": "Cloud APIs unreachable — confirmed air-gapped",
    }


def run_health_check() -> Dict[str, Any]:
    """Run all air-gap health checks."""
    checks = {
        "local_llm": check_local_llm,
        "database": check_database,
        "python_deps": check_python_deps,
        "llm_routing": check_llm_routing,
        "no_llm_capabilities": check_no_llm_capabilities,
        "hooks": check_hooks,
        "pdf_extraction": check_pdf_extraction,
        "git": check_git,
        "cloud_status": check_cloud_unreachable,
    }

    results = {}
    for name, fn in checks.items():
        try:
            results[name] = fn()
        except Exception as exc:
            results[name] = {"status": "error", "error": str(exc)}

    # Overall status
    statuses = [r.get("status", "error") for r in results.values()]
    if "fail" in statuses:
        overall = "unhealthy"
    elif "warn" in statuses or "error" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "overall": overall,
        "mode": "air-gap",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": results,
    }


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV™ Air-Gap Health Check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = run_health_check()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nICDEV™ Air-Gap Health Check — {result['overall'].upper()}")
        print(f"Timestamp: {result['timestamp']}\n")
        for name, check in result["checks"].items():
            status = check.get("status", "unknown")
            icon = {"pass": "+", "fail": "X", "warn": "!", "info": "i", "error": "E"}.get(status, "?")
            print(f"  [{icon}] {name}: {status}")
            if "error" in check:
                print(f"      {check['error']}")
            if "models" in check:
                print(f"      Models: {', '.join(check['models'][:5])}")
            if "providers" in check:
                print(f"      Providers: {', '.join(check['providers'])}")

    sys.exit(0 if result["overall"] != "unhealthy" else 1)
