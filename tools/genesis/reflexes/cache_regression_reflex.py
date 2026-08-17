#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Prompt-Cache Regression (cache_regression_reflex, cch-obs-02).

Caching that worked and stops looks exactly like caching that was never enabled.
``tools/cache_savings/regression.py`` decides when the per-call ledger says one of
those happened; this reflex is the part that makes it reach a human, using the
channel this platform already uses for findings — a kanban card. No second alert
surface was invented.

WHAT IT FILES, AND WHAT IT DELIBERATELY DOES NOT
------------------------------------------------
One card per (rung, provider) finding, and nothing else. Every non-finding the
detector names — ``mechanism_no_billing``, ``pre_instrumentation_unknown``,
``no_traffic``, ``insufficient_calls`` — is returned in the result for the run
log and files nothing. A reflex whose first act is a card per provider is turned
off within the hour, which is the failure mode this card exists to avoid, not to
reproduce.

``status: unmeasurable`` from the detector files nothing either. On a fresh
worktree or an ephemeral CI database every provider looks like it has never
cached, because NOTHING has been recorded — not because caching broke. That
distinction is the same one ``check_capability_liveness`` and
``capability_consumption`` draw, and it is the whole point of the card.

DEDUPE
------
Card ids are deterministic in ``(rung, provider)``. A uuid would refile the same
finding every cycle and earn the reflex its own suppression; title-matching would
collapse two genuinely distinct findings into one. The cost is the accepted one:
once a card for a given (rung, provider) has been filed, a LATER recurrence
updates nothing and files nothing — the same trade ``ungated_test_drift`` makes.
The run result always reports every finding, filed or not, so a recurrence is
still visible in the reflex log.

COOLDOWN: 6h (args/genesis_config.yaml). GREEN tier — reads telemetry, writes
kanban rows, touches no provider.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import hashlib
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.genesis.cache_regression_reflex")

#: What each rung means to whoever picks the card up.
_RUNG_TITLE = {
    "stopped": "{provider} stopped reporting cached tokens",
    "collapsed": "{provider} cache hit share collapsed",
    "never_cached": "{provider} is configured for caching and has never cached",
}


def _card_id(prefix: str, rung: str, provider: str) -> str:
    """Deterministic id so re-detecting the same regression never duplicates."""
    digest = hashlib.sha256(f"{rung}|{provider}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{digest}"


def _describe(finding: Dict[str, Any], report: Dict[str, Any]) -> str:
    provider = finding["provider"]
    rung = finding["verdict"]
    w = report.get("windows", {})
    th = report.get("thresholds", {})

    lines = [
        f"The `{provider}` provider triggered the **{rung}** rung of the prompt-cache "
        "regression signal (cch-obs-02).",
        "",
        "| | recent | baseline |",
        "|---|---:|---:|",
        f"| window start | {w.get('recent_start')} | {w.get('baseline_start')} |",
        f"| calls | {finding.get('recent_calls')} | {finding.get('baseline_calls')} |",
        f"| cache-read tokens | {finding.get('recent_cache_read')} | "
        f"{finding.get('baseline_cache_read')} |",
        f"| cache-read share | {finding.get('recent_share')} | "
        f"{finding.get('baseline_share')} |",
        "",
    ]

    if rung == "stopped":
        lines += [
            f"`{provider}` reported cached tokens across the whole baseline window "
            "and reports **exactly zero** across the recent one, with real traffic "
            "in both. It was caching, and it is not now.",
            "",
            "This is the failure that motivated the card: Azure served cached tokens "
            "and discarded the count for its entire life, and nothing reported it. "
            "A stop and a never-enabled look identical in every aggregate — only "
            "the transition tells them apart.",
        ]
    elif rung == "collapsed":
        lines += [
            f"`{provider}`'s cache-read share fell "
            f"{float(finding.get('drop_ratio', 0)) * 100:.0f}% relative, past the "
            f"{float(th.get('collapse_drop_ratio', 0)) * 100:.0f}% threshold. That "
            "threshold was fitted against this ledger's own historical variation "
            "(0.00% false-fire rate over 79 window pairs — see "
            "args/cache_regression.yaml), so this is outside normal movement rather "
            "than a quiet week.",
        ]
    else:
        lines += [
            f"`{provider}` declares a **{finding.get('mechanism')}** cache mechanism "
            "— one that bills cached tokens back — and has made "
            f"{finding.get('instrumented_calls')} instrumented calls "
            f"(threshold {th.get('never_cached_min_calls')}) without one single "
            "cache read or cache write.",
            "",
            "Only calls logged at or after "
            f"`{report.get('instrumented_since')}` are counted "
            f"(source: {report.get('instrumented_since_source')}). Rows before that "
            "hold 0 because they were BACKFILLED by the cch-tel-01 migration, not "
            "because a provider was asked and answered zero.",
            "",
            "Either the provider is not actually caching for these prompts (prefixes "
            "too short, cache markers not set, a routing change), or it is caching "
            "and the adapter is discarding the count — which is exactly the defect "
            "cch-tel-01 found still live in the CLI bridge.",
        ]

    lines += [
        "",
        "**Where to look**",
        "",
        "```",
        "python -m tools.cache_savings.regression --json",
        "```",
        "",
        f"- `ai_telemetry` rows for `{provider}`: the raw per-call record (cch-tel-01).",
        "- `tools/llm/router.py::_log_telemetry` — where the counts are read off the "
        "response. A provider adapter that never populates the fields records 0 here "
        "indistinguishably from one that was asked and cached nothing.",
        "- `args/cache_regression.yaml` — thresholds, mechanism map and the "
        "instrumentation floor.",
        "",
        "**Do not close this by widening a threshold.** They were measured against "
        "real traffic before being armed; raising one to silence a finding is how a "
        "signal becomes decorative.",
        "",
        "Filed automatically by the `cache_regression_reflex` genesis reflex. Its id "
        "is deterministic in (rung, provider), so this card is not refiled while it "
        "is open.",
    ]
    return "\n".join(lines)


def run(payload: dict, ctx: Any = None) -> Dict[str, Any]:
    """Detect prompt-cache regressions and file a card per finding.

    payload keys (all optional):
        dry_run (bool): detect and report, but file no cards
        window_end (str): ISO instant the recent window ends at (replay)
    """
    started = time.time()
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", False))

    result: Dict[str, Any] = {
        "success": True,   # a missing 'success' key is scored a failure forever
        "status": "ok",
        "findings": 0,
        "cards_filed": 0,
        "card_ids": [],
        "providers_evaluated": 0,
        "verdicts": {},
        "errors": [],
    }

    try:
        from tools.cache_savings import regression
    except Exception as exc:  # noqa: BLE001
        result.update(success=False, status="error",
                      errors=[f"detector unavailable: {exc}"])
        return _stamp_metric(result)

    cfg = regression.load_config()
    if not cfg.get("enabled", True):
        result["status"] = "disabled"
        return _stamp_metric(result)

    window_end = None
    if payload.get("window_end"):
        from datetime import datetime, timezone

        window_end = datetime.fromisoformat(str(payload["window_end"]))
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)

    try:
        report = regression.detect(config=cfg, window_end=window_end)
    except Exception as exc:  # noqa: BLE001
        result.update(success=False, status="error", errors=[str(exc)[:300]])
        logger.exception("cache_regression_reflex: detection failed: %s", exc)
        return _stamp_metric(result)

    result["providers_evaluated"] = len(report.get("providers", []))
    verdicts: Dict[str, int] = {}
    for p in report.get("providers", []):
        v = str(p.get("verdict"))
        verdicts[v] = verdicts.get(v, 0) + 1
    result["verdicts"] = verdicts
    result["instrumented_since"] = report.get("instrumented_since")
    result["instrumented_since_source"] = report.get("instrumented_since_source")

    if report.get("status") == regression.STATUS_UNMEASURABLE:
        # Not an error and NOT a clean bill. A fresh worktree or an ephemeral CI
        # database makes every provider look like it never cached; filing on that
        # would be fabrication, and reporting "ok" would be a false all-clear.
        result["status"] = "unmeasurable"
        result["reason"] = report.get("reason")
        logger.info("cache_regression_reflex: unmeasurable (%s)", report.get("reason"))
        result["elapsed_seconds"] = round(time.time() - started, 2)
        return _stamp_metric(result)

    findings = report.get("findings", [])
    result["findings"] = len(findings)
    result["finding_detail"] = [
        {"provider": f["provider"], "rung": f["verdict"]} for f in findings
    ]

    if findings and not dry_run:
        filed, ids = _file_cards(findings, report, cfg)
        result["cards_filed"] = filed
        result["card_ids"] = ids

    result["elapsed_seconds"] = round(time.time() - started, 2)
    _stamp_metric(result)
    logger.info(
        "cache_regression_reflex: %d provider(s), %d finding(s), %d card(s) — %s",
        result["providers_evaluated"], result["findings"], result["cards_filed"],
        verdicts,
    )
    return result


def _stamp_metric(result: Dict[str, Any]) -> Dict[str, Any]:
    """Populate the keys the daemon actually reads off a reflex result.

    ``daemon._run_reflex_impl_inner`` records ``result["metric_value"]`` and
    ``result["details"]``, and defaults both. A reflex that declares a
    ``success_metric`` in args/genesis_config.yaml but never sets metric_value
    therefore records 0.0 forever while looking like it reported something —
    the declared-but-inert shape, one layer down. The declared metric here is
    ``providers_evaluated``.
    """
    result["metric_value"] = float(result.get("providers_evaluated", 0) or 0)
    result["details"] = {
        "status": result.get("status"),
        "findings": result.get("findings", 0),
        "cards_filed": result.get("cards_filed", 0),
        "card_ids": result.get("card_ids", []),
        "verdicts": result.get("verdicts", {}),
        "instrumented_since": result.get("instrumented_since"),
        "instrumented_since_source": result.get("instrumented_since_source"),
        "reason": result.get("reason"),
    }
    return result


def _file_cards(
    findings: List[Dict[str, Any]], report: Dict[str, Any], cfg: Dict[str, Any]
) -> tuple:
    """One card per finding, deterministic id. Returns ``(count, ids)``."""
    try:
        from tools.kanban.task_factory import create_tasks
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_regression_reflex: task_factory unavailable: %s", exc)
        return 0, []

    card = cfg.get("card") or {}
    prefix = str(card.get("id_prefix", "cache-regr-"))
    specs = []
    for f in findings:
        rung = f["verdict"]
        specs.append({
            "id": _card_id(prefix, rung, f["provider"]),
            "title": _RUNG_TITLE[rung].format(provider=f["provider"]),
            "task_type": str(card.get("task_type", "fix")),
            "priority": str(card.get("priority", "high")),
            "status": str(card.get("status", "backlog")),
            "description": _describe(f, report),
        })
    try:
        created = create_tasks(specs)
        return len(created), list(created)
    except Exception as exc:  # noqa: BLE001 -- a card write must never break the daemon
        logger.warning("cache_regression_reflex: card write failed: %s", exc)
        return 0, []


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv as _load_dotenv

        _load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    import json

    print(json.dumps(run({"dry_run": "--dry-run" in sys.argv}), indent=2, default=str))
