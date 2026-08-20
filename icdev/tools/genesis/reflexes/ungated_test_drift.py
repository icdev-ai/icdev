#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Ungated Test Drift (ungated_test_drift).

CI runs an allowlist: ~207 of 2,162 collectible test modules. The other ~1,823
are grandfathered by ``args/ci_test_backlog.txt`` and gate nothing, so a file in
that set can go from passing to failing and NOTHING reports it. That is not
hypothetical — it is how ``remediation_simulator._run_nqe_layer`` sat dead from
June to August, and how three ``tests/test_govcon_*`` files accumulated 70
failures between 2026-08-11 and 2026-08-13 without a single red build.

The TSG card fixed a CATALOGUE of 87 files. It did not fix the MECHANISM: the
census ratchet (tsg-policy-01/02) stops NEW ungated files appearing, but says
nothing about the 1,823 already grandfathered. This reflex is the missing half —
it samples that set continuously and reports TRANSITIONS.

WHAT IT REPORTS, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------
Only ``pass -> fail`` files a card. A file that was already failing is known
debt, catalogued and prioritised elsewhere; re-reporting it every few hours
would bury the one line that matters under 87 that do not. The signal here is
"something that worked yesterday does not work today", because that is the class
nobody else is watching.

The blind spot that leaves, and where it is covered (rem-hyg-14): a file whose
FIRST observation is a failure takes the ``was is None`` branch, seeds a 'fail'
baseline and is never reported again — so a test broken since the day it was
written is invisible HERE, by design, and would be invisible everywhere if
nothing else looked. ``tools/ci/born_red_survey.py`` is what looks. This reflex
records the evidence it needs (``first_status``, and ``ever_passed`` as a latch
that a later failure never clears) and reports nothing new itself.

Equally deliberate: on a database with no baseline this run SEEDS and reports
nothing. A fresh worktree or an ephemeral CI database would otherwise make every
file look like a brand-new regression, and a reflex whose first act is to file
1,823 cards would be turned off within the hour — the same reasoning as
``coherence_checker.check_capability_liveness``.

WHY ONE PROCESS PER FILE
------------------------
A shared pytest process carries module globals, leaked monkeypatches, sys.path
edits and DB state between files, so a batched result can be a neighbour's
fault. Measured 2026-08-11: ``test_documented_clis_have_a_bootstrap`` passed
alone and failed in a batch. Isolation costs a process spawn per file and is the
only way the verdict means what it says.

SAMPLING
--------
1,823 files at ~2s each is over an hour; run it whole and it either times out or
becomes the reason the daemon is slow. Each run takes the N least-recently-
checked files, so the whole set is covered over time and a file that was checked
today is not checked again before one that has never been checked at all.

COOLDOWN_HOURS = 6 (enforced by the Genesis daemon).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.genesis.ungated_test_drift")

TABLE = "ungated_test_baseline"

#: Files sampled per run. 40 x ~2s keeps a run near two minutes and covers the
#: grandfathered set in roughly a week at a 6h cadence.
DEFAULT_SAMPLE = int(os.environ.get("ICDEV_DRIFT_SAMPLE", "40"))

#: Per-file pytest timeout. A file that hangs is a regression too, but it must
#: not hold the daemon.
PER_FILE_TIMEOUT = int(os.environ.get("ICDEV_DRIFT_FILE_TIMEOUT", "120"))

#: Suites needing a live dashboard, browser or network fail environmentally
#: rather than because anything broke, so drift is meaningless for them.
EXCLUDED_PREFIXES = (
    "tests/e2e/",
    "tests/e2e_selenium/",
    "tests/browser/",
    "tests/playwright/",
    "tests/selenium/",
)


def _table_exists(conn) -> bool:
    from tools.db.storage import get_backend

    try:
        if get_backend() == "postgresql":
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s",
                (TABLE,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
                (TABLE,),
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 — absence is the answer, not an error
        return False


def _has_history_columns(conn) -> bool:
    """Does this deployment carry `first_status` / `ever_passed` yet?

    Read from the live catalogue, never assumed from the DDL: `CREATE TABLE IF
    NOT EXISTS` never alters an existing table, so an INSERT naming a column the
    deployed table lacks raises at runtime and is swallowed by the surrounding
    handler — the exact failure CLAUDE.md's INSERT/schema-parity rule exists for.
    """
    from tools.db.storage import get_backend

    try:
        if get_backend() == "postgresql":
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                (TABLE,),
            ).fetchall()
            cols = {dict(r)["column_name"] for r in rows}
        else:
            rows = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
            cols = set()
            for r in rows:
                d = dict(r)
                cols.add(d.get("name") or list(d.values())[1])
        return {"first_status", "ever_passed"} <= cols
    except Exception:  # noqa: BLE001 — absence is the answer, not an error
        return False


def _ungated_files() -> List[str]:
    """Every collectible test module that no CI allowlist runs.

    The allowlist is `core.txt` PLUS every `core.d/<task-id>.txt` fragment
    (tsg-policy-03) — reading only the list files would report a freshly
    promoted file as ungated and sample it forever.
    """
    listed = set()
    for name in ("core.txt", "windows.txt"):
        p = BASE_DIR / "args" / "ci_test_files" / name
        sources = [p] if p.is_file() else []
        frag_dir = BASE_DIR / "args" / "ci_test_files" / (p.stem + ".d")
        if frag_dir.is_dir():
            sources.extend(sorted(f for f in frag_dir.glob("*.txt") if f.is_file()))
        for src in sources:
            for line in src.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    listed.add(line.split("::")[0].replace("\\", "/"))

    out = []
    for path in (BASE_DIR / "tests").rglob("test_*.py"):
        rel = path.relative_to(BASE_DIR).as_posix()
        if rel in listed or rel.startswith(EXCLUDED_PREFIXES):
            continue
        out.append(rel)
    return sorted(out)


def _run_alone(rel: str) -> Dict[str, Any]:
    """Run one file in its own process. Returns {'status', 'detail'}."""
    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", rel, "-q", "--no-header",
             "-p", "no:cacheprovider", "--timeout=60"],
            cwd=str(BASE_DIR), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONPATH": str(BASE_DIR),
                 "ICDEV_STORAGE_BACKEND": "sqlite"},
            timeout=PER_FILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "detail": f"timed out after {PER_FILE_TIMEOUT}s",
                "seconds": round(time.time() - t0, 1)}
    except Exception as exc:  # noqa: BLE001 — a spawn failure is not a verdict
        return {"status": "unknown", "detail": f"could not run: {exc}",
                "seconds": round(time.time() - t0, 1)}

    tail = [ln for ln in (r.stdout or "").splitlines()
            if " passed" in ln or " failed" in ln or " error" in ln]
    return {
        "status": "pass" if r.returncode == 0 else "fail",
        "detail": (tail[-1] if tail else f"exit {r.returncode}")[:300],
        "seconds": round(time.time() - t0, 1),
    }


def _card_id(rel: str) -> str:
    """Deterministic id so re-detecting the same drift never spawns a duplicate.

    A uuid here would defeat INSERT-OR-IGNORE dedupe and refile the same finding
    every cycle, which is how a reflex earns its own suppression.
    """
    return "tsg-drift-" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:10]


def run(payload: dict, ctx: Any = None) -> Dict[str, Any]:
    """Sample the ungated set and report files that stopped passing.

    payload keys (all optional):
        sample (int):   how many files to check this run
        dry_run (bool): detect and report, but file no cards
    """
    started = time.time()
    payload = payload or {}
    sample = int(payload.get("sample") or DEFAULT_SAMPLE)
    dry_run = bool(payload.get("dry_run", False))

    result: Dict[str, Any] = {
        "success": True,          # a missing 'success' key is scored a failure
        "status": "ok",           # forever, and the reflex circuit-breaks itself
        "checked": 0,
        "regressions": [],
        "recoveries": [],
        "seeded": 0,
        "cards_filed": 0,
        "baseline_seeded_this_run": False,
        "errors": [],
    }

    try:
        from tools.db.storage import get_connection
    except Exception as exc:  # noqa: BLE001
        result.update(success=False, status="error",
                      errors=[f"storage unavailable: {exc}"])
        return result

    files = _ungated_files()
    if not files:
        result["errors"].append("no ungated test files found — allowlist parse looks wrong")
        result["success"] = False
        result["status"] = "error"
        return result

    conn = get_connection()
    try:
        if not _table_exists(conn):
            # Not an error: the migration has not run here yet. Report honestly
            # rather than crashing the daemon or inventing a clean bill.
            result.update(status="skipped",
                          errors=[f"{TABLE} missing — migration not applied"])
            return result

        known = {}
        for row in conn.execute(f"SELECT path, status FROM {TABLE}").fetchall():
            d = dict(row)
            known[d["path"]] = d["status"]

        first_run = not known
        result["baseline_seeded_this_run"] = first_run

        # The born-red columns arrive with migration 20260820231102. A
        # deployment that has not applied it still records transitions
        # correctly; it simply records no first-verdict history, which
        # `born_red_survey` reports as `history_unknown` rather than guessing.
        history = _has_history_columns(conn)
        result["history_recorded"] = history

        # Least-recently-checked first; never-checked files sort first because
        # they have no row at all.
        unseen = [f for f in files if f not in known]
        seen_rows = conn.execute(
            f"SELECT path FROM {TABLE} ORDER BY last_checked ASC NULLS FIRST"
        ).fetchall()
        seen_order = [dict(r)["path"] for r in seen_rows if dict(r)["path"] in set(files)]
        batch = (unseen + seen_order)[:sample]

        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()

        for rel in batch:
            verdict = _run_alone(rel)
            result["checked"] += 1
            if verdict["status"] == "unknown":
                result["errors"].append(f"{rel}: {verdict['detail']}")
                continue

            was = known.get(rel)
            if was is None:
                result["seeded"] += 1
            elif was == "pass" and verdict["status"] == "fail":
                result["regressions"].append({"path": rel, "detail": verdict["detail"]})
            elif was == "fail" and verdict["status"] == "pass":
                result["recoveries"].append({"path": rel})

            passed = verdict["status"] == "pass"
            if history:
                # first_status is written by the INSERT and never updated: a row
                # that already exists keeps whatever its first observation was.
                conn.execute(
                    f"INSERT OR IGNORE INTO {TABLE} "
                    "(path, status, first_seen, last_checked, last_detail, "
                    "first_status, ever_passed) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (rel, verdict["status"], now, now, verdict["detail"],
                     verdict["status"], 1 if passed else 0),
                )
            else:
                conn.execute(
                    f"INSERT OR IGNORE INTO {TABLE} "
                    "(path, status, first_seen, last_checked, last_detail) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (rel, verdict["status"], now, now, verdict["detail"]),
                )
            # ever_passed is a LATCH — set on a pass, never cleared on a fail.
            # Clearing it would erase the one fact that separates a regression
            # from a file that has never worked.
            set_passed = ", ever_passed = 1" if (history and passed) else ""
            conn.execute(
                f"UPDATE {TABLE} SET status = %s, last_checked = %s, "
                f"last_detail = %s{set_passed} WHERE path = %s",
                (verdict["status"], now, verdict["detail"], rel),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        result.update(success=False, status="error")
        result["errors"].append(str(exc)[:300])
        return result
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    # A first run has no "before", so every file looks new. Seeding only.
    if first_run:
        result["regressions"] = []
        logger.info("ungated_test_drift: seeded %d baseline row(s), reported nothing",
                    result["seeded"])

    if result["regressions"] and not dry_run:
        result["cards_filed"] = _file_cards(result["regressions"])

    result["elapsed_seconds"] = round(time.time() - started, 1)
    result["ungated_total"] = len(files)
    logger.info(
        "ungated_test_drift: checked=%d regressions=%d recoveries=%d seeded=%d "
        "of %d ungated",
        result["checked"], len(result["regressions"]), len(result["recoveries"]),
        result["seeded"], len(files),
    )
    return result


def _file_cards(regressions: List[Dict[str, Any]]) -> int:
    """One card per newly-broken file, with a deterministic id."""
    try:
        from tools.kanban.task_factory import create_tasks
    except Exception as exc:  # noqa: BLE001
        logger.warning("ungated_test_drift: task_factory unavailable: %s", exc)
        return 0

    specs = []
    for reg in regressions:
        rel = reg["path"]
        specs.append({
            "id": _card_id(rel),
            "title": f"Ungated test regressed: {Path(rel).name}",
            "task_type": "fix",
            "priority": "high",
            "status": "backlog",
            "description": (
                f"`{rel}` passed the last time it was checked and fails now.\n\n"
                f"    {reg['detail']}\n\n"
                "This file is NOT in args/ci_test_files/*.txt, so no build will "
                "ever go red for it — it was found by the ungated_test_drift "
                "reflex sampling the grandfathered set, not by CI.\n\n"
                "It is reported because it is a REGRESSION, not merely a failure: "
                "the ~1,823 files that were already failing are known debt and are "
                "deliberately not re-reported. Something that worked stopped "
                "working, and this is the only thing watching for that.\n\n"
                "Run it ALONE first — a batched result can be a neighbouring "
                "file's leaked state:\n"
                f"    python -m pytest {rel} -q\n\n"
                "When it passes again, add it to args/ci_test_files/core.txt in "
                "the same PR. That is the sanctioned way to widen the allowlist "
                "and the only way this file stops being able to rot silently. Do "
                "NOT bulk-add other ungated files to 'fix' the category."
            ),
        })
    try:
        return len(create_tasks(specs))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ungated_test_drift: card write failed: %s", exc)
        return 0


if __name__ == "__main__":
    import json

    out = run({"sample": int(sys.argv[1]) if len(sys.argv) > 1 else 5,
               "dry_run": "--dry-run" in sys.argv})
    print(json.dumps(out, indent=2)[:4000])
