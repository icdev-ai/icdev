# CUI // SP-CTI
"""Migration Canvas Compliance Gate.

Deterministic IL-level / target-environment / framework checks for migration scenarios.
No LLM, no external calls.
"""
from __future__ import annotations

from tools.migration_canvas.constants import IL_LEVEL_REQUIREMENTS

_GOVCLOUD_KEYWORDS = ("govcloud", "government", "gov cloud")
_COMMERCIAL_KEYWORDS = ("commercial",)


def _classify_env(target_env: str) -> tuple[bool, bool, bool]:
    """Return (is_govcloud, is_commercial, is_dod)."""
    env = (target_env or "").lower()
    is_govcloud = any(k in env for k in _GOVCLOUD_KEYWORDS)
    is_commercial = any(k in env for k in _COMMERCIAL_KEYWORDS) and not is_govcloud
    is_dod = is_govcloud or any(k in env for k in ("dod", "sipr"))
    return is_govcloud, is_commercial, is_dod


def _unknown_il_finding(il_level: str, is_dod: bool) -> dict:
    if is_dod:
        msg = (
            f"Unknown IL level '{il_level}' targeting DoD/GovCloud environment — "
            "verify authorization before proceeding."
        )
    else:
        msg = f"Unknown IL level '{il_level}' — compliance posture cannot be determined."
    return {"rule": "CGT-004", "message": msg, "severity": "warn"}


def _resolve_il_findings(
    il_upper: str,
    il_reqs: dict | None,
    is_commercial: bool,
    is_govcloud: bool,
    is_dod: bool,
) -> tuple[list[dict], list[str]]:
    """Return (findings, extra_frameworks_to_apply)."""
    extra: list[str] = []
    if il_reqs is None:
        return [_unknown_il_finding(il_upper, is_dod)], extra

    findings: list[dict] = []
    if is_commercial and not il_reqs.get("commercial_ok", True):
        findings.append({
            "rule": "CGT-001",
            "message": (
                f"{il_upper} workloads cannot be hosted in commercial cloud. "
                "GovCloud or on-prem deployment is required."
            ),
            "severity": "block",
        })
    if is_govcloud:
        extra.append("fedramp")
        if il_upper in ("IL5", "IL6"):
            extra.append("disa_stig")
    return findings, extra


def _framework_finding(frameworks: list[str] | None, is_govcloud: bool) -> dict | None:
    if frameworks is not None and is_govcloud and "fedramp" not in frameworks:
        return {
            "rule": "CGT-002",
            "message": (
                "GovCloud target requires FedRAMP authorization. "
                "Add 'fedramp' to the declared frameworks list."
            ),
            "severity": "warn",
        }
    return None


def _resolve_status(findings: list[dict]) -> tuple[str, bool]:
    if any(f["severity"] == "block" for f in findings):
        return "block", False
    if findings:
        return "warn", True
    return "pass", True


def check_migration_compliance(
    il_level: str,
    target_env: str,
    migration_type: str | None = None,
    frameworks: list[str] | None = None,
) -> dict:
    """Check migration compliance for IL level, target environment, and migration type.

    Args:
        il_level: Impact level — 'IL2', 'IL4', 'IL5', 'IL6', or unknown value.
        target_env: Target environment — 'govcloud', 'commercial', 'dod', etc.
        migration_type: Optional migration type — 'p2c', 'p2v_cloud', etc.
        frameworks: Declared compliance frameworks. When provided, checked for
                    required items (e.g. 'fedramp' for GovCloud). When None,
                    framework-completeness checks are skipped.

    Returns:
        {
            "proceed": bool,             # False only on BLOCK status
            "status": "block"|"warn"|"pass",
            "findings": [...],           # non-empty on warn/block
            "frameworks_applied": [...], # nist_800_53 always present
        }
    """
    # NIST 800-53 is the baseline framework for all IL levels (IL2 through IL6)
    frameworks_applied: list[str] = ["nist_800_53"]
    is_govcloud, is_commercial, is_dod = _classify_env(target_env)
    il_upper = (il_level or "").upper().strip()
    il_reqs = IL_LEVEL_REQUIREMENTS.get(il_upper)

    findings, extra = _resolve_il_findings(il_upper, il_reqs, is_commercial, is_govcloud, is_dod)
    frameworks_applied.extend(extra)

    fw_finding = _framework_finding(frameworks, is_govcloud)
    if fw_finding:
        findings.append(fw_finding)

    status, proceed = _resolve_status(findings)
    return {
        "proceed": proceed,
        "status": status,
        "findings": findings,
        "frameworks_applied": list(dict.fromkeys(frameworks_applied)),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="MCE Compliance Gate — check migration IL/environment compliance")
    ap.add_argument("--il-level", default="IL4", help="Impact level: IL2, IL4, IL5, IL6 (default: IL4)")
    ap.add_argument("--target-env", required=True, help="Target environment (govcloud, commercial, dod, ...)")
    ap.add_argument("--migration-type", default=None, help="Migration type hint (p2v, p2c, v2c, ...)")
    ap.add_argument("--frameworks", default=None, help="Comma-separated declared frameworks (e.g. fedramp,nist_800_53)")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    fw_list = [f.strip() for f in args.frameworks.split(",")] if args.frameworks else None
    result = check_migration_compliance(
        il_level=args.il_level,
        target_env=args.target_env,
        migration_type=args.migration_type,
        frameworks=fw_list,
    )

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        status_icon = {"pass": "✓", "warn": "⚠", "block": "✗"}.get(result["status"], "?")
        print(f"[compliance_gate] {status_icon} {result['status'].upper()} — proceed={result['proceed']}")
        for f in result["findings"]:
            print(f"  [{f['severity'].upper():5s}] [{f['rule']}] {f['message']}")
        if not result["findings"]:
            print("  All compliance checks passed.")
        print(f"  Frameworks: {', '.join(result['frameworks_applied'])}")


if __name__ == "__main__":
    main()
