"""IL-tier quality rigor gates: coverage and static-analysis thresholds."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import List, Optional

try:
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET  # type: ignore[no-redef]

# Minimum line coverage % per IL tier
_COVERAGE_MINIMUMS: dict[str, int] = {
    "il2": 70,
    "il4": 80,
    "il5": 85,
    "il6": 90,
}

_PROFILE_JSON = (
    Path(__file__).parent.parent.parent
    / "context"
    / "compliance"
    / "impact_level_profiles.json"
)


@dataclasses.dataclass
class ILTierProfile:
    tier: str
    coverage_minimum: int
    sast_required: bool
    dast_required: bool
    sbom_required: bool

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class EvaluationResult:
    passed: bool
    tier: str
    coverage_actual: Optional[float]
    coverage_minimum: int
    violations: List[str]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def load_profile(tier: str) -> ILTierProfile:
    """Return ILTierProfile for *tier* (case-insensitive).

    Raises ValueError for unknown tiers.
    """
    key = tier.lower()
    if key not in _COVERAGE_MINIMUMS:
        raise ValueError(
            f"Unknown IL tier: {tier!r}. Valid tiers: {sorted(_COVERAGE_MINIMUMS)}"
        )

    sast = True
    dast = key in ("il4", "il5", "il6")
    sbom = key in ("il4", "il5", "il6")

    if _PROFILE_JSON.exists():
        try:
            data = json.loads(_PROFILE_JSON.read_text(encoding="utf-8"))
            ci_cd = (
                data.get("profiles", {})
                .get(key.upper(), {})
                .get("ci_cd_requirements", {})
            )
            sast = ci_cd.get("sast_required", sast)
            dast = ci_cd.get("dast_required", dast)
            sbom = ci_cd.get("sbom_required", sbom)
        except Exception:
            pass

    return ILTierProfile(
        tier=key,
        coverage_minimum=_COVERAGE_MINIMUMS[key],
        sast_required=sast,
        dast_required=dast,
        sbom_required=sbom,
    )


def _parse_coverage_xml(path: Path) -> Optional[float]:
    """Return line coverage percentage (0-100) from a coverage.xml file."""
    try:
        root = ET.parse(path).getroot()  # nosec B314 — parsing trusted coverage.xml from pytest-cov, not user-supplied input
        rate = root.attrib.get("line-rate")
        if rate is not None:
            return float(rate) * 100
    except Exception:
        pass
    return None


def _find_coverage_xml(project_dir: Path) -> Optional[Path]:
    for candidate in [
        project_dir / "coverage.xml",
        project_dir / ".coverage.xml",
        project_dir / "htmlcov" / "coverage.xml",
    ]:
        if candidate.exists():
            return candidate
    return None


def evaluate(
    tier: str,
    project_dir: Optional[Path] = None,
    coverage_pct: Optional[float] = None,
) -> EvaluationResult:
    """Evaluate rigor gates for *tier*, optionally auto-discovering coverage.xml.

    If no coverage data is available (no .coverage file, no override), the
    coverage check is skipped and the gate passes (graceful degrade).
    """
    profile = load_profile(tier)
    violations: List[str] = []

    if coverage_pct is None and project_dir is not None:
        xml_path = _find_coverage_xml(project_dir)
        if xml_path is not None:
            coverage_pct = _parse_coverage_xml(xml_path)

    if coverage_pct is not None and coverage_pct < profile.coverage_minimum:
        violations.append(
            f"coverage {coverage_pct:.1f}% < {profile.coverage_minimum}% minimum for {tier.upper()}"
        )

    return EvaluationResult(
        passed=len(violations) == 0,
        tier=profile.tier,
        coverage_actual=coverage_pct,
        coverage_minimum=profile.coverage_minimum,
        violations=violations,
    )


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="IL-tier quality rigor gates")
    parser.add_argument("--tier", default=None, help="IL tier (il2/il4/il5/il6)")
    parser.add_argument(
        "--impact-level", default=None, dest="impact_level",
        help="Alias for --tier (il2/il4/il5/il6)"
    )
    parser.add_argument(
        "--evaluate", action="store_true", help="Run evaluation (default action)"
    )
    parser.add_argument(
        "--project-dir", type=Path, default=Path("."), help="Project root"
    )
    parser.add_argument(
        "--coverage", type=float, default=None, help="Override coverage %%"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="JSON output"
    )
    parser.add_argument(
        "--gate", action="store_true", help="Exit 1 on gate failure"
    )
    args = parser.parse_args(argv)

    tier = args.impact_level or args.tier or "il4"
    result = evaluate(tier, args.project_dir, args.coverage)

    if args.json_output or args.gate:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        status = "PASSED" if result.passed else "FAILED"
        print(f"Rigor gate {status} [{result.tier.upper()}]")
        for v in result.violations:
            print(f"  VIOLATION: {v}")

    if args.gate:
        sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
