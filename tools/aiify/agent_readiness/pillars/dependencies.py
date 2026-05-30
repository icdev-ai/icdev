# CUI // SP-CTI
"""Pillar 5 — Dependencies: lock files, freshness, pinned versions, SBOMs."""
from __future__ import annotations

import pathlib
import time
from functools import lru_cache
from typing import Any

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _exists,
    _glob_files,
    _read,
    _search,
)

# ---------------------------------------------------------------------------
# Anomaly-detection threshold loader
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[5] / "args" / "agent_readiness_config.yaml"
_DEFAULTS: dict[str, Any] = {
    # Lock file older than this many months is anomalously stale.
    "max_age_months": 6,
    # requirements.txt pin ratio below this is anomalously low for reproducible builds.
    "min_pin_ratio": 0.8,
}


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[str, Any]:
    """Load dependencies-pillar anomaly-detection thresholds from args/agent_readiness_config.yaml.

    Falls back to hard-coded defaults if the config file is absent or malformed.
    """
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillars", {}).get("dependencies", {})
        freshness = cfg.get("lock_file_freshness", {})
        pinned = cfg.get("pinned_versions", {})
        return {
            "max_age_months": int(freshness.get("max_age_months", _DEFAULTS["max_age_months"])),
            "min_pin_ratio": float(pinned.get("min_pin_ratio", _DEFAULTS["min_pin_ratio"])),
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


def _check_lock_file(repo: pathlib.Path) -> CriterionResult:
    cid = "lock-file"
    lock_files = [
        "poetry.lock", "Pipfile.lock", "requirements.txt",
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
        "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock",
        "packages.lock.json", "Package.resolved",
    ]
    found = _exists(repo, *lock_files)
    if found:
        return CriterionResult(cid, True, f"Dependency lock file found: {found}")
    return CriterionResult(cid, False, "No dependency lock file found.",
                           "Add a lock file to pin dependency versions for reproducible builds.")


def _check_lock_file_freshness(repo: pathlib.Path) -> CriterionResult:
    cid = "lock-file-freshness"
    thresholds = _load_thresholds()
    max_age_months = thresholds["max_age_months"]
    max_age_sec = max_age_months * 30 * 24 * 60 * 60
    lock_files = [
        "poetry.lock", "Pipfile.lock", "package-lock.json", "yarn.lock",
        "pnpm-lock.yaml", "go.sum", "Cargo.lock", "Gemfile.lock",
    ]
    for fn in lock_files:
        p = repo / fn
        if p.exists():
            age = time.time() - p.stat().st_mtime
            days = int(age / 86400)
            if age < max_age_sec:
                return CriterionResult(cid, True, f"{fn} updated {days} day(s) ago")
            months = int(age / (30 * 86400))
            return CriterionResult(
                cid, False,
                f"{fn} last modified ~{months} month(s) ago — anomalously stale (max {max_age_months} month(s)).",
                "Run dependency updates to keep lock file fresh and avoid vulnerabilities.",
            )
    return CriterionResult(cid, True, "No lock file found; freshness check skipped.", skipped=True)


def _check_pinned_versions(repo: pathlib.Path) -> CriterionResult:
    cid = "pinned-versions"
    thresholds = _load_thresholds()
    min_ratio = thresholds["min_pin_ratio"]
    req = _read(repo, "requirements.txt")
    if req:
        lines = [l.strip() for l in req.splitlines() if l.strip() and not l.startswith("#")]
        if not lines:
            return CriterionResult(cid, True, "requirements.txt is empty; check skipped.", skipped=True)
        pinned = sum(1 for l in lines if "==" in l)
        ratio = pinned / len(lines)
        if ratio >= min_ratio:
            return CriterionResult(cid, True, f"{pinned}/{len(lines)} requirements pinned with ==")
        return CriterionResult(
            cid, False,
            f"Only {pinned}/{len(lines)} requirements pinned ({ratio:.0%}) — "
            f"anomalously low (min {min_ratio:.0%}).",
            "Pin all dependencies with == in requirements.txt for reproducibility.",
        )
    # For other ecosystems, having a lock file is the proxy for pinning
    if _exists(repo, "poetry.lock", "Cargo.lock", "go.sum", "package-lock.json", "yarn.lock"):
        return CriterionResult(cid, True, "Lock file acts as version pin for all dependencies")
    return CriterionResult(cid, False, "No pinned version evidence found.",
                           "Use a lock file or pin all dependency versions explicitly.")


def _check_sbom(repo: pathlib.Path) -> CriterionResult:
    cid = "sbom-present"
    found = _exists(repo, "sbom.json", "sbom.xml", "bom.json", "bom.xml",
                    "cyclonedx.json", "cyclonedx.xml", "spdx.json", "spdx.tv")
    if found:
        return CriterionResult(cid, True, f"SBOM file found: {found}")
    # Check CI for SBOM generation
    ci_files = (
        _glob_files(repo, ".github/workflows/*.yml")
        + _glob_files(repo, ".github/workflows/*.yaml")
    )
    for f in ci_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, r"sbom|cyclonedx|syft|spdx"):
            return CriterionResult(cid, True, f"SBOM generation step found in CI: {f.name}")
    return CriterionResult(cid, False, "No SBOM found.",
                           "Generate an SBOM (CycloneDX or SPDX) as part of your build pipeline.")


PILLAR = Pillar(
    id="dependencies",
    name="Dependencies",
    description="Lock files, freshness, pinned versions, and SBOM generation.",
    criteria=[
        Criterion("lock-file", "Lock file present", "A dependency lock file is committed.", "dependencies", 1, _check_lock_file),
        Criterion("lock-file-freshness", "Lock file freshness", "The lock file was updated within the configured freshness window.", "dependencies", 3, _check_lock_file_freshness),
        Criterion("pinned-versions", "Pinned versions", "Dependency versions are pinned for reproducibility.", "dependencies", 2, _check_pinned_versions),
        Criterion("sbom-present", "SBOM present", "A Software Bill of Materials (SBOM) is generated.", "dependencies", 4, _check_sbom),
    ],
)
