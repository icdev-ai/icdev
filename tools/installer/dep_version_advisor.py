# CUI // SP-CTI
"""Dependency Version Advisor — LLM-based review of pyproject.toml version pins.

Implements the ``llm_generation`` AI paradigm for the ``hardcoded_threshold``
pattern (AAC opportunity aac-opp-481).  Replaces the purely manual process of
deciding when to bump minimum-version pins in pyproject.toml with LLM-assisted
analysis that flags stale or overly-conservative constraints.

Related tool: tools/ai_augmentation/version_anomaly_detector.py covers
*runtime* version-bound comparisons (e.g. assert major >= 3).  This tool
covers *packaging* version pins (e.g. ``pyyaml>=6.0`` in pyproject.toml).

Public API:
    advise_pins(pyproject_path=None) -> dict

CLI:
    python tools/installer/dep_version_advisor.py
    python tools/installer/dep_version_advisor.py --pyproject pyproject.toml
    python tools/installer/dep_version_advisor.py --json
    python tools/installer/dep_version_advisor.py --package pyyaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

_DEFAULT_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PKG_CONFIG = _REPO_ROOT / "args" / "package_config.yaml"

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_cfg() -> dict:
    if _yaml is None or not _PKG_CONFIG.exists():
        return {}
    try:
        with open(_PKG_CONFIG, encoding="utf-8") as fh:
            return _yaml.safe_load(fh) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# pyproject.toml parser — extract version pins without a full TOML library
# ---------------------------------------------------------------------------

_PIN_RE = re.compile(
    r'^"?(?P<pkg>[A-Za-z0-9_.\-]+)\s*(?P<spec>[><=!~^][^",\s]*(?:\s*,\s*[><=!~^][^",\s]*)*)?',
)


def _parse_pins(pyproject_path: Path) -> dict[str, str]:
    """Return {package: version_spec} extracted from pyproject.toml.

    Handles both ``dependencies = [...]`` and ``[project.optional-dependencies]``
    groups.  Uses plain regex to avoid a TOML dependency.
    """
    src = pyproject_path.read_text(encoding="utf-8")
    pins: dict[str, str] = {}

    # Collect all quoted strings that look like PEP 508 requirements
    for raw in re.findall(r'"([^"]+)"', src):
        m = _PIN_RE.match(raw.strip())
        if m:
            pkg = m.group("pkg").lower().replace("-", "_").replace(".", "_")
            spec = (m.group("spec") or "").strip()
            if spec and pkg not in pins:
                pins[pkg] = spec
    return pins


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_PROMPT = """\
You are a Python packaging expert reviewing minimum-version pins in pyproject.toml.

Package name   : {package}
Current pin    : {spec}
Usage context  : {context}

Assess whether the current minimum-version pin is appropriate for a production
Python project in 2025.  Consider:
1. Whether the pinned version is likely still widely available.
2. Whether a higher minimum would be safer (security fixes, API stability).
3. Whether the pin is too conservative (e.g. restricts users unnecessarily).

Respond with a JSON object only — no markdown, no extra text:
{{
  "status": "ok" | "stale" | "overly_conservative" | "unknown",
  "confidence": <0.0-1.0>,
  "reason": "<one sentence>",
  "recommendation": "<keep/bump — one sentence with suggested version if applicable>"
}}
"""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _llm_assess(package: str, spec: str, context: str) -> dict:
    """Invoke the LLM router for a single package pin assessment."""
    cfg = _load_cfg().get("dep_advisor", {})
    llm_func = cfg.get("llm_function", "llm_generation")
    max_tokens = int(cfg.get("max_tokens", 512))

    from tools.llm.provider import LLMRequest
    from tools.llm.router import LLMRouter

    router = LLMRouter()
    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": _PROMPT.format(
                    package=package,
                    spec=spec,
                    context=context or "general production Python project",
                ),
            }
        ],
        system_prompt=(
            "You are a Python packaging expert. "
            "Reply only with the JSON object as specified — no prose, no fences."
        ),
        agent_id="dep-version-advisor",
        classification="CUI",
        max_tokens=max_tokens,
        effort="medium",
    )
    response = router.invoke(llm_func, request)
    return _parse_llm(response.content or "", package, spec)


def _parse_llm(raw: str, package: str, spec: str) -> dict:
    text = re.sub(r"```(?:json)?|```", "", raw).strip()
    data: dict[str, Any] = {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

    return {
        "package": package,
        "current_pin": spec,
        "status": str(data.get("status", "unknown")),
        "confidence": float(data.get("confidence", 0.5)),
        "reason": str(data.get("reason", "Unable to assess.")),
        "recommendation": str(data.get("recommendation", "Manual review recommended.")),
        "raw": raw,
    }


def _static_fallback(package: str, spec: str) -> dict:
    """Return a safe static result when no LLM is available."""
    return {
        "package": package,
        "current_pin": spec,
        "status": "unknown",
        "confidence": 0.0,
        "reason": "LLM unavailable — static fallback.",
        "recommendation": "Manual review recommended.",
        "raw": "",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advise_pins(
    pyproject_path: Path | None = None,
    packages: list[str] | None = None,
) -> dict:
    """Assess version pins in pyproject.toml using LLM generation.

    Args:
        pyproject_path: Path to pyproject.toml.  Defaults to repo root.
        packages:       Optional list of package names to limit the review.
                        When omitted, all detected pins are checked.

    Returns:
        Dict with keys:
            pyproject_path  (str)   — resolved path checked
            pins_found      (int)   — total pins detected
            pins_checked    (int)   — pins assessed (may be < found if filtered)
            results         (list)  — per-package assessment dicts
            summary         (dict)  — counts by status
    """
    path = Path(pyproject_path) if pyproject_path else _DEFAULT_PYPROJECT
    if not path.exists():
        raise FileNotFoundError(f"pyproject.toml not found: {path}")

    pins = _parse_pins(path)
    if packages:
        normalised = {p.lower().replace("-", "_").replace(".", "_") for p in packages}
        pins = {k: v for k, v in pins.items() if k in normalised}

    results: list[dict] = []
    for pkg, spec in sorted(pins.items()):
        try:
            result = _llm_assess(pkg, spec, context="")
        except Exception:  # noqa: BLE001
            result = _static_fallback(pkg, spec)
        results.append(result)

    summary: dict[str, int] = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    return {
        "pyproject_path": str(path),
        "pins_found": len(_parse_pins(path)),
        "pins_checked": len(results),
        "results": results,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "DEP VERSION ADVISOR — LLM-based review of pyproject.toml version pins.\n"
            "AAC opportunity aac-opp-481 (hardcoded_threshold → llm_generation)."
        ),
        epilog="CUI // SP-CTI",
    )
    p.add_argument(
        "--pyproject",
        metavar="PATH",
        default=None,
        help="Path to pyproject.toml (default: repo root)",
    )
    p.add_argument(
        "--package",
        metavar="NAME",
        action="append",
        dest="packages",
        help="Limit review to this package (repeatable)",
    )
    p.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Emit JSON output",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    report = advise_pins(
        pyproject_path=args.pyproject,
        packages=args.packages,
    )

    if args.output_json:
        print(json.dumps(report, indent=2))
        return

    print("=" * 68)
    print("CUI // SP-CTI")
    print("DEP VERSION ADVISOR — AAC opp aac-opp-481")
    print("=" * 68)
    print(f"  pyproject.toml : {report['pyproject_path']}")
    print(f"  Pins found     : {report['pins_found']}")
    print(f"  Pins checked   : {report['pins_checked']}")
    print()

    for r in report["results"]:
        icon = {"ok": "[OK  ]", "stale": "[STALE]", "overly_conservative": "[CONS ]"}.get(
            r["status"], "[?   ]"
        )
        print(f"  {icon} {r['package']:<30} {r['current_pin']}")
        print(f"         Reason : {r['reason']}")
        print(f"         Action : {r['recommendation']}")
        print()

    print("Summary:", json.dumps(report["summary"]))
    print("=" * 68)
    print("CUI // SP-CTI")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
