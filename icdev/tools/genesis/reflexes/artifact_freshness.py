#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Pinned-artifact freshness (artifact_freshness, xrv-pin-01).

``tools/airgap/artifact_freshness.py`` asks upstream whether a pin this tree
carries is still the newest release. Without a consumer it would be one more
survey a human has to think to run -- the declared-but-never-consumed defect
this repository ships most, and the reason ``status_churn``,
``born_red_survey`` and ``recovery_summary`` each sat imported by nobody until
``detector_findings`` ran them.

WHAT IT DOES
------------
One survey per cycle (24h). Every ``behind`` artifact gets ONE card carrying
the artifact, what we pin, what upstream publishes, which basis decided it, and
the exact command that re-derives the verdict. A card, never a bump: moving a
digest pin is a supply-chain act and it belongs in a reviewed diff -- which is
the whole reason the pins are digests. Nothing here pulls an image or edits a
pin file, and the module it calls cannot (``tools/airgap/artifact_freshness``
never imports ``subprocess`` and issues GET/HEAD only, AST-pinned).

UNMEASURABLE IS NOT SUCCESS-SHAPED, AND IT IS ALSO NOT A FAILURE
-----------------------------------------------------------------
An air-gapped host -- the deployment ``tools/airgap/`` exists for -- can reach
no registry, so every artifact comes back ``unmeasurable`` and the cycle
reports ``status: unmeasurable`` with ``metric_value 0``. It does NOT report
``ok``: a freshness reflex whose "nothing to do" and whose "I could not look"
are the same line is the defect, not a degraded mode of it.

``success`` stays True there on purpose. That key drives the circuit breaker,
and an air-gapped or merely offline deployment tripping its breaker in three
cycles would make this reflex PERMANENTLY inert on exactly the hosts that most
need it to start working the moment a connection exists. The distinction is
carried on ``status`` and on ``details.counts``, which is what a consumption
probe reads back out of ``genesis_audit``. A PARTIAL cycle reports on the
artifacts it measured and NAMES the unmeasurable ones -- a truncated sweep that
reported only its successes would read as full coverage.

DEDUPE
------
``idempotency_key = artifact-freshness:<name>:<newest>`` and a deterministic
card id derived from the same pair. So the same artifact being behind the same
upstream release files ONE card however many cycles observe it, and a FURTHER
release upstream is a new finding with a new key -- which is right: "postgres
is behind 18.6" and "postgres is behind 19.0" are different facts about the
pin. ``task_factory.create_tasks`` enforces the key inside the insert; the id
is the second lock.

Cards land in ``suggested`` (the HITL quarantine), because a pin bump is a
human decision.

COOLDOWN: 24h (args/genesis_config.yaml). GREEN tier -- it reads a manifest,
makes read-only HTTP requests and writes kanban rows.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import hashlib
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# The sys.path BOOTSTRAP only: this name's every use is a sys.path expression,
# because it resolves the IMPORT root and is correct after a move. The repo root
# proper comes from the ONE resolver below (xit-decl-03) -- a second
# `parents[3]` bound to a name that is then used as a PATH is a private,
# hard-coded claim about where this file sits.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

BASE_DIR = repo_root(__file__)
logger = get_logger("icdev.genesis.artifact_freshness")

REFLEX_NAME = "artifact_freshness"

#: Result statuses. ``unmeasurable`` is a real status, not a failure and not ok.
STATUS_OK = "ok"
STATUS_FINDINGS = "findings"
STATUS_UNMEASURABLE = "unmeasurable"
STATUS_ERROR = "error"

#: The ``success_metric`` declared in args/genesis_config.yaml: artifacts that
#: produced a verdict other than ``unmeasurable``.
METRIC_NAME = "artifacts_measured"

DEFAULT_CARD: Dict[str, str] = {
    "id_prefix": "artifact-fresh-",
    "task_type": "chore",
    "priority": "medium",
    "status": "suggested",
}

DEFAULT_MAX_CARDS_PER_RUN = 5


def _card_id(prefix: str, name: str, newest: str) -> str:
    """Deterministic in (artifact, upstream release) -- the idempotency key's pair."""
    digest = hashlib.sha256(f"{name}:{newest}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{digest}"


def idempotency_key(name: str, newest: str) -> str:
    """The key the card is deduped on, as the card spec states it."""
    return f"artifact-freshness:{name}:{newest}"


def _describe(row: Dict[str, Any]) -> str:
    name = row.get("name")
    lines = [
        f"The pinned artifact `{name}` is BEHIND its upstream "
        "(artifact_freshness reflex, xrv-pin-01).",
        "",
        f"**This tree pins:** `{row.get('ref')}:{row.get('pinned')}`",
        f"**Upstream publishes:** `{row.get('newest')}`",
        f"**Declared in:** `{row.get('pin_source')}`",
        f"**Decided on:** `{row.get('basis')}` "
        "(`version_tag` = a strictly greater tag of the SAME shape exists; "
        "`digest` = a MUTABLE tag has moved off the digest we recorded; "
        "`version` = a package index answered with a newer version).",
        "",
    ]
    if row.get("reason"):
        lines += [f"**Why:** {row['reason']}", ""]
    if row.get("digest_drift") is True:
        lines += [
            "**Digest drift as well:** the pinned tag no longer serves the digest "
            f"`{row.get('declared_digest')}` -- upstream now serves "
            f"`{row.get('observed_digest')}`. That is a SECOND finding: a tag that "
            "moved under a digest pin is not the same thing as a pin that is "
            "merely old, and an air-gap bundle re-cut from the tag would no "
            "longer contain what the pin file names.",
            "",
        ]
    if row.get("pin_strength") == "floor":
        lines += [
            "**This is a FLOOR, not a pin.** The declaration is a `>=`, so "
            "`behind` here means only that a newer release exists than the floor "
            "written down -- a weaker statement than it is for a digest-pinned "
            "image.",
            "",
        ]
    lines += [
        "**Re-derive it yourself**",
        "",
        "```",
        f"python -m tools.airgap.artifact_freshness --artifact {name} --json",
        "```",
        "",
        "**Moving the pin is a supply-chain act and this card does not make it.** "
        "Nothing in the reflex or the survey pulls an image, edits a pin file, "
        "touches docker-compose.yml or rewrites requirements.txt -- an AST test "
        "holds that. A newer release is not automatically the right one: the "
        "measured floci runtime set was obtained by DRIVING a live emulator "
        "(args/floci_runtime_images.yaml), so a digest changed here without a "
        "re-measurement is a claim nobody observed. Decide, then move the pin in "
        "a reviewed diff and re-run the survey.",
        "",
        "Filed automatically by the `artifact_freshness` genesis reflex. Its "
        "idempotency key is `"
        + idempotency_key(str(name), str(row.get("newest")))
        + "`, so this finding is filed once -- a FURTHER upstream release is a new "
        "key and a new card.",
    ]
    return "\n".join(lines)


def run(config: Dict[str, Any], ctx: Any = None) -> Dict[str, Any]:
    """Survey every declared pin; file one card per ``behind``; refuse nothing.

    ``config`` is this reflex's block from args/genesis_config.yaml. Keys read:
        dry_run (bool): survey and report, but file no cards
        max_cards_per_run (int): bound; deferred findings are NAMED
        card (dict): id_prefix / task_type / priority / status overrides
    """
    started = time.time()
    config = config or {}
    dry_run = bool(config.get("dry_run", False))
    max_cards = int(config.get("max_cards_per_run", DEFAULT_MAX_CARDS_PER_RUN))

    result: Dict[str, Any] = {
        "success": True,   # a missing 'success' key is scored a failure forever
        "status": STATUS_OK,
        "artifacts": 0,
        "artifacts_measured": 0,
        "counts": {},
        "offline": None,
        "findings": 0,
        "finding_detail": [],
        "digest_drift": [],
        "unmeasurable_artifacts": [],
        "cards_filed": 0,
        "card_ids": [],
        "cards_deferred": [],
        "dry_run": dry_run,
        "errors": [],
    }

    try:
        from tools.airgap import artifact_freshness as af
    except Exception as exc:  # noqa: BLE001
        result.update(success=False, status=STATUS_ERROR,
                      errors=[f"survey unavailable: {exc}"])
        return _stamp_metric(result, started)

    try:
        report = af.survey()
    except Exception as exc:  # noqa: BLE001
        # An unreadable manifest is an ERROR, not `unmeasurable`: the survey
        # could not be produced at all, which is a different thing from a
        # survey that ran and could reach nothing.
        result.update(success=False, status=STATUS_ERROR, errors=[str(exc)[:300]])
        logger.exception("artifact_freshness: survey failed: %s", exc)
        return _stamp_metric(result, started)

    rows: List[Dict[str, Any]] = list(report.get("results") or [])
    counts = dict(report.get("counts") or {})
    result["artifacts"] = int(report.get("artifacts") or 0)
    result["counts"] = counts
    result["offline"] = bool(report.get("offline"))
    result["unmeasurable_artifacts"] = list(report.get("unmeasurable") or [])
    result["digest_drift"] = list(report.get("digest_drift") or [])
    measured = int(report.get("measured") or 0)
    result["artifacts_measured"] = measured

    if measured == 0:
        # NOT ok and NOT an error. Nothing was asked: an air-gapped host, a
        # network outage, an empty manifest. Reporting `ok` here would be a
        # freshness reflex certifying pins it never looked at.
        result["status"] = STATUS_UNMEASURABLE
        result["reason"] = (
            "air-gapped: no upstream is reachable from here" if result["offline"]
            else "no declared artifact could be measured on this host"
        )
        result["unmeasurable_detail"] = {
            r["name"]: (r.get("reason") or "")[:200]
            for r in rows if r.get("status") == af.STATUS_UNMEASURABLE
        }
        logger.info("artifact_freshness: unmeasurable (%s)", result["reason"])
        return _stamp_metric(result, started)

    behind = [r for r in rows if r.get("status") == af.STATUS_BEHIND]
    result["findings"] = len(behind)
    result["finding_detail"] = [
        {
            "name": r.get("name"),
            "ref": r.get("ref"),
            "pinned": r.get("pinned"),
            "newest": r.get("newest"),
            "basis": r.get("basis"),
            "pin_source": r.get("pin_source"),
            "digest_drift": r.get("digest_drift"),
        }
        for r in behind
    ]
    if behind:
        result["status"] = STATUS_FINDINGS

    if behind and not dry_run:
        filed, ids, deferred = _file_cards(behind, config, max_cards)
        result["cards_filed"] = filed
        result["card_ids"] = ids
        result["cards_deferred"] = deferred

    _stamp_metric(result, started)
    logger.info(
        "artifact_freshness: %d artifact(s), %d measured, %d behind, %d card(s) — %s",
        result["artifacts"], measured, result["findings"], result["cards_filed"], counts,
    )
    return result


def _stamp_metric(result: Dict[str, Any], started: float) -> Dict[str, Any]:
    """Populate the keys the daemon actually reads off a reflex result.

    ``daemon._run_reflex_impl_inner`` records ``result["metric_value"]`` and
    ``result["details"]`` and defaults both -- rmf-inert-03 measured a reflex
    whose 17 runs all recorded ``{}`` because it never set ``details``, so every
    field it "reported" went nowhere. The declared metric is
    ``artifacts_measured``.
    """
    result["elapsed_seconds"] = round(time.time() - started, 2)
    result["metric_value"] = float(result.get("artifacts_measured", 0) or 0)
    result["details"] = {
        "status": result.get("status"),
        "artifacts": result.get("artifacts", 0),
        "artifacts_measured": result.get("artifacts_measured", 0),
        "counts": result.get("counts", {}),
        "offline": result.get("offline"),
        "findings": result.get("findings", 0),
        "finding_detail": result.get("finding_detail", []),
        "digest_drift": result.get("digest_drift", []),
        "unmeasurable_artifacts": result.get("unmeasurable_artifacts", []),
        "cards_filed": result.get("cards_filed", 0),
        "card_ids": result.get("card_ids", []),
        "cards_deferred": result.get("cards_deferred", []),
        "reason": result.get("reason"),
        "dry_run": result.get("dry_run", False),
        "errors": result.get("errors", []),
    }
    return result


def _file_cards(
    findings: List[Dict[str, Any]], config: Dict[str, Any], max_cards: int
) -> Tuple[int, List[str], List[str]]:
    """One card per behind artifact, bounded. ``(count, ids, deferred_names)``."""
    try:
        from tools.kanban.task_factory import create_tasks
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact_freshness: task_factory unavailable: %s", exc)
        return 0, [], [str(f.get("name")) for f in findings]

    card = dict(DEFAULT_CARD)
    card.update({k: str(v) for k, v in (config.get("card") or {}).items()})

    # Bounded per run and the bound is REPORTED -- a truncated sweep that named
    # only its successes would read as full coverage.
    acting, deferred = findings[:max_cards], findings[max_cards:]
    specs = []
    for row in acting:
        name, newest = str(row.get("name")), str(row.get("newest"))
        specs.append({
            "id": _card_id(card["id_prefix"], name, newest),
            "title": f"Pinned artifact is behind upstream: {name} -> {newest}",
            "task_type": card["task_type"],
            "priority": card["priority"],
            "status": card["status"],
            "description": _describe(row),
            "acceptance_criteria": (
                f"Either `{row.get('pin_source')}` pins {name} at a release decided "
                f"by a human (and the measured runtime-image evidence is re-derived "
                f"if the digest moved), or the card records in writing why "
                f"{row.get('pinned')!r} is deliberately kept. "
                f"`python -m tools.airgap.artifact_freshness --artifact {name} --json` "
                f"then reports `current`, or reports `behind` against a NEWER release "
                f"than {newest!r}."
            ),
            "idempotency_key": idempotency_key(name, newest),
        })
    try:
        created = create_tasks(specs)
        return len(created), list(created), [str(f.get("name")) for f in deferred]
    except Exception as exc:  # noqa: BLE001 -- a card write must never break the daemon
        logger.warning("artifact_freshness: card write failed: %s", exc)
        return 0, [], [str(f.get("name")) for f in findings]


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv as _load_dotenv

        _load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    import json

    print(json.dumps(run({"dry_run": "--dry-run" in sys.argv}), indent=2, default=str))
