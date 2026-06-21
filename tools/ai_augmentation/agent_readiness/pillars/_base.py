# CUI // SP-CTI
"""Shared types for the agent-readiness pillar system."""
from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Shared YAML config loader — used by all pillars that define configurable
# anomaly-detection thresholds in args/agent_readiness_config.yaml.
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[4] / "args" / "agent_readiness_config.yaml"


@lru_cache(maxsize=1)
def _load_agent_readiness_config() -> dict:
    """Load the full args/agent_readiness_config.yaml once, cached for the process lifetime."""
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        return yaml.safe_load(raw) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_pillar_config(pillar_key: str) -> dict[str, Any]:
    """Return the pillars.<pillar_key> sub-dict from the config, or {} if absent/malformed.

    Pillar files use this instead of duplicating the YAML-load boilerplate:

        cfg = load_pillar_config("append_only_audit")
        sample_size = int(cfg.get("audit_log_inserts", {}).get("scan_sample_size", 40))
    """
    return _load_agent_readiness_config().get("pillars", {}).get(pillar_key, {})


# ---------------------------------------------------------------------------
# Score-level anomaly detection — flags pillars with anomalously low pass rates
# and optionally enriches findings with Claude Haiku reasoning.
# ---------------------------------------------------------------------------

# Defaults for score_anomaly config block in agent_readiness_config.yaml.
# Values are used verbatim when the config key is absent or malformed.
_SCORE_ANOMALY_DEFAULTS: dict[str, Any] = {
    # A pillar percentage below this is flagged as anomalously low.
    # 0.3 means a pillar passing fewer than 30 % of its criteria is anomalous.
    "min_passing_pct": 0.3,
    # Set to true to call Claude Haiku for natural-language remediation guidance.
    "ai_analysis_enabled": False,
    "ai_model": "claude-haiku-4-5-20251001",
    "ai_max_tokens": 256,
}


def load_score_anomaly_config() -> dict[str, Any]:
    """Load score-anomaly detection config from args/agent_readiness_config.yaml.

    Falls back to _SCORE_ANOMALY_DEFAULTS if the key is absent or malformed.
    """
    cfg = _load_agent_readiness_config().get("score_anomaly", {})
    return {
        "min_passing_pct": float(cfg.get("min_passing_pct", _SCORE_ANOMALY_DEFAULTS["min_passing_pct"])),
        "ai_analysis_enabled": bool(cfg.get("ai_analysis_enabled", _SCORE_ANOMALY_DEFAULTS["ai_analysis_enabled"])),
        "ai_model": str(cfg.get("ai_model", _SCORE_ANOMALY_DEFAULTS["ai_model"])),
        "ai_max_tokens": int(cfg.get("ai_max_tokens", _SCORE_ANOMALY_DEFAULTS["ai_max_tokens"])),
    }


@dataclass
class AnomalyReport:
    """Result of a score-anomaly detection pass for one pillar."""
    pillar_id: str
    score_pct: float
    threshold: float
    is_anomalous: bool
    reason: str
    ai_reasoning: str = ""


def _analyze_anomaly_with_ai(
    pillar_id: str,
    score_pct: float,
    threshold: float,
    failing_messages: list[str],
    model: str,
    max_tokens: int,
) -> str:
    """Call Claude Haiku to explain a score anomaly in one or two sentences.

    Returns an empty string when the API key is absent, the provider import
    fails, or the LLM call raises an exception — callers must not depend on
    a non-empty result.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""
    try:
        from tools.llm.anthropic_provider import AnthropicLLMProvider  # type: ignore[import]
        from tools.llm.provider import LLMRequest  # type: ignore[import]
    except ImportError:
        return ""

    msgs_str = "\n".join(f"- {m}" for m in failing_messages[:5])
    prompt = (
        f"Agent-readiness pillar '{pillar_id}' scored {score_pct:.0%}, "
        f"below the anomaly threshold of {threshold:.0%}.\n\n"
        f"Failing criteria:\n{msgs_str}\n\n"
        "In 1-2 sentences, explain why this score is anomalously low and "
        "name the single most impactful remediation step.\n"
        "Plain text only — no JSON, no bullet points."
    )
    try:
        provider = AnthropicLLMProvider(api_key=api_key)
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        model_cfg = {"max_output_tokens": max_tokens}
        response = provider.invoke(request, model, model_cfg)
        return response.content.strip()
    except Exception:  # noqa: BLE001
        return ""


def detect_score_anomalies(
    pillar_scores: dict[str, dict],
    criterion_results: Optional[dict[str, list[dict]]] = None,
) -> list[AnomalyReport]:
    """Detect pillars with anomalously low pass rates.

    Args:
        pillar_scores: Mapping of pillar_id → {"passed": int, "total": int,
            "percentage": float} as returned by Pillar.score().
        criterion_results: Optional mapping of pillar_id → list of criterion
            result dicts ({"criterion_id", "passed", "message", ...}).
            Required for AI-enriched reasoning; ignored when ai_analysis is off.

    Returns:
        One AnomalyReport per pillar that has at least one evaluated criterion.
        Reports for anomalous pillars set is_anomalous=True; non-anomalous
        pillars are included so callers can inspect the full picture.
    """
    cfg = load_score_anomaly_config()
    min_pct = cfg["min_passing_pct"]
    ai_enabled = cfg["ai_analysis_enabled"]

    reports: list[AnomalyReport] = []
    for pillar_id, score in pillar_scores.items():
        total = score.get("total", 0)
        if total == 0:
            continue
        pct = float(score.get("percentage", 0.0))

        if pct >= min_pct:
            reports.append(AnomalyReport(
                pillar_id=pillar_id,
                score_pct=pct,
                threshold=min_pct,
                is_anomalous=False,
                reason=f"Score {pct:.0%} meets anomaly threshold {min_pct:.0%}.",
            ))
            continue

        # Anomaly detected — optionally enrich with AI reasoning.
        reason = (
            f"Pillar '{pillar_id}' scored {pct:.0%} — "
            f"anomalously low (configured threshold: {min_pct:.0%})."
        )
        ai_reasoning = ""
        if ai_enabled and criterion_results:
            failing = [
                r.get("message", "")
                for r in criterion_results.get(pillar_id, [])
                if not r.get("passed") and not r.get("skipped")
            ]
            if failing:
                ai_reasoning = _analyze_anomaly_with_ai(
                    pillar_id=pillar_id,
                    score_pct=pct,
                    threshold=min_pct,
                    failing_messages=failing,
                    model=cfg["ai_model"],
                    max_tokens=cfg["ai_max_tokens"],
                )

        reports.append(AnomalyReport(
            pillar_id=pillar_id,
            score_pct=pct,
            threshold=min_pct,
            is_anomalous=True,
            reason=reason,
            ai_reasoning=ai_reasoning,
        ))

    return reports


@dataclass
class CriterionResult:
    criterion_id: str
    passed: bool
    message: str
    details: str = ""
    skipped: bool = False


@dataclass
class Criterion:
    id: str
    name: str
    description: str
    pillar_id: str
    level: int  # 1–5 maturity
    check: Callable[[pathlib.Path], CriterionResult] = field(repr=False)


@dataclass
class Pillar:
    id: str
    name: str
    description: str
    criteria: list[Criterion]

    def run(self, repo_path: pathlib.Path) -> list[CriterionResult]:
        results = []
        for c in self.criteria:
            try:
                results.append(c.check(repo_path))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    CriterionResult(
                        criterion_id=c.id,
                        passed=False,
                        message=f"Check raised an exception: {exc}",
                        skipped=True,
                    )
                )
        return results

    def score(self, results: list[CriterionResult]) -> dict:
        evaluated = [r for r in results if not r.skipped]
        passed = sum(1 for r in evaluated if r.passed)
        total = len(evaluated)
        return {
            "pillar_id": self.id,
            "passed": passed,
            "total": total,
            "percentage": round(passed / total, 4) if total > 0 else 0.0,
        }


# ---------------------------------------------------------------------------
# File-system helpers (sync, pure Python — no external deps)
# ---------------------------------------------------------------------------

def _read(repo: pathlib.Path, *rel_parts: str) -> Optional[str]:
    p = repo.joinpath(*rel_parts)
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _exists(repo: pathlib.Path, *globs: str) -> Optional[str]:
    for g in globs:
        if "*" in g or "?" in g:
            hits = list(repo.glob(g))
            if hits:
                return str(hits[0].relative_to(repo))
        else:
            p = repo / g
            if p.exists():
                return g
    return None


def _glob_files(repo: pathlib.Path, pattern: str, ignore_dirs: tuple = ("node_modules", "vendor", ".git")) -> list[pathlib.Path]:
    results = []
    for p in repo.glob(pattern):
        parts = p.parts
        if any(d in parts for d in ignore_dirs):
            continue
        results.append(p)
    return results


def _search(text: str, pattern: str, flags: int = re.IGNORECASE) -> bool:
    return bool(re.search(pattern, text, flags))
