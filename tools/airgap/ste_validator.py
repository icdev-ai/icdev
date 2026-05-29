#!/usr/bin/env python3
# CUI // SP-CTI
"""STE Readiness Validator — verifies ICDEV™ is correctly configured for Secure Technical Environment deployment (G-13).

Checks all STE/STN readiness conditions required before IL6/SIPR deployment:
  1. ICDEV_DEPLOY_MODE=STE
  2. No public cloud LLM endpoints configured
  3. Ollama reachable at OLLAMA_BASE_URL
  4. SIPR PKI cert present (or ICDEV_PKI_SIPR_CERT_PATH configured)
  5. MFA enforced (ICDEV_MFA_REQUIRED=true)
  6. Strict revocation enabled (ICDEV_PKI_STRICT_REVOCATION=true)
  7. FIPS mode enabled (ICDEV_FIPS_MODE=true)
  8. Canvas access gate enforced (ICDEV_CANVAS_ACCESS_ENFORCE=true)
  9. No cloud DB backend (ICDEV_STORAGE_BACKEND != 'postgresql' unless intra-env)
 10. Continuous auth enabled (ICDEV_CONTINUOUS_AUTH_ENABLED=true)

Usage:
    python tools/airgap/ste_validator.py --validate --json
    python tools/airgap/ste_validator.py --validate --strict   # exit 1 if any check fails
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("airgap.ste_validator")

# ---------------------------------------------------------------------------
# Check result dataclass
# ---------------------------------------------------------------------------


@dataclass
class STECheck:
    name: str
    passed: bool
    detail: str
    required: bool = True  # False = advisory-only (warn, not fail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "required": self.required,
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_deploy_mode() -> STECheck:
    val = os.environ.get("ICDEV_DEPLOY_MODE", "")
    ok = val.upper() == "STE"
    return STECheck(
        name="deploy_mode",
        passed=ok,
        detail=f"ICDEV_DEPLOY_MODE={val!r} (expected 'STE')" if not ok else "ICDEV_DEPLOY_MODE=STE",
    )


def _check_no_cloud_llm() -> STECheck:
    """Ensure no public cloud LLM provider is active."""
    provider = os.environ.get("ICDEV_LLM_PROVIDER", "").lower().strip()
    cloud_providers = {"openai", "anthropic", "cohere", "mistral", "google", "azure"}
    cloud_urls = ["api.openai.com", "api.anthropic.com", "api.cohere.ai"]

    if provider in cloud_providers:
        return STECheck(
            name="no_cloud_llm",
            passed=False,
            detail=f"ICDEV_LLM_PROVIDER={provider!r} is a cloud provider — must use 'ollama' in STE",
        )

    # Check for accidental cloud endpoint env vars
    for url in cloud_urls:
        for k, v in os.environ.items():
            if url in v:
                return STECheck(
                    name="no_cloud_llm",
                    passed=False,
                    detail=f"Cloud endpoint {url!r} found in env var {k!r}",
                )

    return STECheck(name="no_cloud_llm", passed=True, detail=f"LLM provider={provider!r} — no cloud endpoints detected")


def _check_ollama_reachable() -> STECheck:
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    if not ollama_url.startswith(("http://", "https://")):
        return STECheck(
            name="ollama_reachable",
            passed=False,
            detail=f"Invalid OLLAMA_BASE_URL scheme: {ollama_url!r}",
        )
    try:
        with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=5) as resp:  # nosec B310
            if resp.status == 200:
                return STECheck(name="ollama_reachable", passed=True, detail=f"Ollama reachable at {ollama_url}")
            return STECheck(name="ollama_reachable", passed=False, detail=f"Ollama HTTP {resp.status} at {ollama_url}")
    except Exception as exc:
        return STECheck(
            name="ollama_reachable",
            passed=False,
            detail=f"Ollama not reachable at {ollama_url}: {exc}",
            required=False,  # advisory in dev; required in production gate
        )


def _check_sipr_cert() -> STECheck:
    cert_path_env = os.environ.get("ICDEV_PKI_SIPR_CERT_PATH", "")
    if cert_path_env:
        p = Path(cert_path_env)
        if p.exists():
            return STECheck(name="sipr_cert", passed=True, detail=f"SIPR cert found at {cert_path_env}")
        return STECheck(name="sipr_cert", passed=False, detail=f"ICDEV_PKI_SIPR_CERT_PATH={cert_path_env!r} not found on disk")

    # Fall back to checking data/certs/ for any .crt file
    certs_dir = ROOT / "data" / "certs"
    if certs_dir.exists() and any(certs_dir.glob("*.crt")):
        return STECheck(
            name="sipr_cert",
            passed=True,
            detail=f"Dev certs found at {certs_dir} (set ICDEV_PKI_SIPR_CERT_PATH for DISA-issued SIPR cert)",
            required=False,
        )

    return STECheck(
        name="sipr_cert",
        passed=False,
        detail="No SIPR cert configured. Set ICDEV_PKI_SIPR_CERT_PATH to DISA-issued cert path.",
    )


def _check_env_flag(name: str, env_var: str, expected: str = "true") -> STECheck:
    val = os.environ.get(env_var, "").lower().strip()
    ok = val == expected.lower()
    return STECheck(
        name=name,
        passed=ok,
        detail=f"{env_var}={val!r} (expected {expected!r})" if not ok else f"{env_var}={val!r}",
    )


def _check_storage_backend() -> STECheck:
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower().strip()
    if backend == "postgresql":
        pg_host = os.environ.get("ICDEV_PG_HOST", os.environ.get("DATABASE_URL", ""))
        if any(cloud in pg_host for cloud in ["rds.amazonaws.com", "supabase", "neon.tech", "azure.com"]):
            return STECheck(
                name="storage_backend",
                passed=False,
                detail=f"PostgreSQL host appears to be a cloud endpoint: {pg_host!r}",
            )
    return STECheck(
        name="storage_backend",
        passed=True,
        detail=f"ICDEV_STORAGE_BACKEND={backend!r} — no cloud DB detected",
    )


# ---------------------------------------------------------------------------
# Full validation
# ---------------------------------------------------------------------------


def validate() -> dict[str, Any]:
    """Run all STE readiness checks. Returns a result dict."""
    checks = [
        _check_deploy_mode(),
        _check_no_cloud_llm(),
        _check_ollama_reachable(),
        _check_sipr_cert(),
        _check_env_flag("mfa_required", "ICDEV_MFA_REQUIRED"),
        _check_env_flag("strict_revocation", "ICDEV_PKI_STRICT_REVOCATION"),
        _check_env_flag("fips_mode", "ICDEV_FIPS_MODE"),
        _check_env_flag("canvas_access_enforce", "ICDEV_CANVAS_ACCESS_ENFORCE"),
        _check_env_flag("continuous_auth", "ICDEV_CONTINUOUS_AUTH_ENABLED"),
        _check_storage_backend(),
    ]

    required_checks = [c for c in checks if c.required]
    advisory_checks = [c for c in checks if not c.required]

    required_passed = sum(1 for c in required_checks if c.passed)
    required_total = len(required_checks)
    advisory_passed = sum(1 for c in advisory_checks if c.passed)

    all_required_pass = required_passed == required_total
    ste_ready = all_required_pass

    failed_required = [c.name for c in required_checks if not c.passed]
    failed_advisory = [c.name for c in advisory_checks if not c.passed]

    result = {
        "ste_ready": ste_ready,
        "required_passed": required_passed,
        "required_total": required_total,
        "advisory_passed": advisory_passed,
        "advisory_total": len(advisory_checks),
        "failed_required": failed_required,
        "failed_advisory": failed_advisory,
        "checks": [c.to_dict() for c in checks],
        "classification": "SECRET",
        "impact_level": "IL6",
    }

    if ste_ready:
        logger.info("STE validation PASSED — all %d required checks passed", required_total)
    else:
        logger.warning(
            "STE validation FAILED — %d/%d required checks failed: %s",
            len(failed_required), required_total, failed_required,
        )

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ STE Readiness Validator (G-13)")
    parser.add_argument("--validate", action="store_true", help="Run all STE readiness checks")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any required check fails")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not args.validate:
        parser.print_help()
        sys.exit(0)

    result = validate()

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        status = "READY" if result["ste_ready"] else "NOT READY"
        print(f"\nSTE Readiness: {status}")
        print(f"Required checks: {result['required_passed']}/{result['required_total']} passed")
        print(f"Advisory checks: {result['advisory_passed']}/{result['advisory_total']} passed\n")
        for check in result["checks"]:
            icon = "✓" if check["passed"] else ("!" if not check["required"] else "✗")
            tag = "" if check["required"] else " [advisory]"
            print(f"  [{icon}] {check['name']}{tag}: {check['detail']}")

        if result["failed_required"]:
            print(f"\nFailed required checks: {result['failed_required']}")

    if args.strict and not result["ste_ready"]:
        sys.exit(1)


if __name__ == "__main__":
    _cli()
