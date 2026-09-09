#!/usr/bin/env python3
# CUI // SP-CTI
"""Ungated tests that are RED FROM BIRTH, not only the ones that regressed (rem-hyg-14).

`tools/genesis/reflexes/ungated_test_drift.py` reports TRANSITIONS::

    elif was == 'pass' and verdict['status'] == 'fail':   # regression

A file whose FIRST observation is a failure takes the `was is None` branch,
seeds a 'fail' baseline, and is never mentioned again. So a test that has been
broken since the day it was written is structurally invisible to the one reflex
watching the ungated backlog -- the drift reflex can only see a file FALL, and a
file that was never standing cannot fall.

MEASURED
--------
`tests/test_proposals_ptw_blackhat_api.py` was 10/20 red from the day it landed
(2026-07-07) and stayed red for six weeks. Two PRs merged the same evening on
parallel branches: one added `@require_role` to the proposals write endpoints at
19:53, the other added the tests at 20:56 against a tree without it. Each was
green alone and jointly broken on merge. The file was in
`args/ci_test_backlog.txt`, so CI never ran it, and it never transitioned, so
the drift reflex never mentioned it. It took a human noticing.

THE OTHER HALF OF THE BACKLOG
-----------------------------
`tools/ci/gate_promoter.py` (rem-tst-06) takes the GREEN files out of the
backlog weekly. This takes the measurement of the RED ones -- the files that
have never worked and that nothing will ever report -- so they get fixed or
deleted instead of sitting as debt nobody can see. Between them the backlog
drains from both ends.

REPORT ONLY, AND DELIBERATELY NO `--gate`
------------------------------------------
A survey with a `--gate` earns itself a `|| true` inside a week (kpr-fix-03),
and this one measures the BOARD's accumulated debt rather than anything the
committer did. Exit is 0 on any completed survey, whatever it found; **2** means
the survey could not be produced, which is never the same as a clean survey.

FIVE STATES, AND ONLY ONE IS THE FINDING
-----------------------------------------
    born_red         every observation of this file has been a failure
    regressed        it was observed passing once -- the drift reflex's half
    history_unknown  observed failing, but with no record of a first verdict
    passing          green when last looked at -- gate_promoter's half
    unobserved       NOBODY HAS EVER RUN IT. Not a clean bill of health.

`unobserved` is the state that had to exist. The drift reflex samples 40 files
every six hours, so a full sweep of the 1,701-file backlog takes over ten days;
measured 2026-08-20, 209 of them had been observed AT ALL. Folding the other
1,492 into "no findings" would report the exact reassurance this tool exists to
refuse. `born_red_count` is `None`, never 0, on a deployment that has recorded
nothing, and `observed + unobserved == backlog_total` is asserted so the
uninspected population can never be quietly shrunk.

TWO DURATIONS, NEVER MERGED
---------------------------
`observed_red_days` is PROVEN: the file was seen failing at that moment and has
not been seen passing since. `file_age_days` is how long the file has EXISTED,
which is an upper bound on how long it can have been red. `red_days_basis`
states which one the rank used and how strong the claim is:

    confirmed_at_birth     the file was run at the commit that landed it on the
                           default branch and FAILED there. `file_age_days` is
                           then the real number, not a bound.
    file_age_upper_bound   nothing has ever observed it passing, so it MAY have
                           been red for its whole life. Ranked on that, labelled
                           as a bound.
    observed_only          no git history for the path; only the proven span.

`--confirm N` runs the top N candidates at their landing commit in a detached
worktree. It has three outcomes and the middle one is its own finding:
`confirmed_born_red`, `passed_at_birth` (it worked when it landed and broke
later, SILENTLY -- a regression the drift reflex missed because it never
observed the pass) and `birth_unrunnable` (the old tree could not run it at all;
counted as neither).

WRITES NOTHING
--------------
The reflex stays the only writer of `ungated_test_baseline`. A survey that also
seeded the table would make its own evidence, and the next run could not tell a
reflex observation from its own.

Usage
-----
    python tools/ci/born_red_survey.py                    # human table
    python tools/ci/born_red_survey.py --json
    python tools/ci/born_red_survey.py --limit 40
    python tools/ci/born_red_survey.py --run 25           # measure unobserved files now
    python tools/ci/born_red_survey.py --confirm 5        # run the top N at their birth
    python tools/ci/born_red_survey.py --out .tmp/born-red.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

#: This checkout, from `__file__` and never `os.getcwd()` -- a survey run from a
#: worktree that measured the shared checkout would describe the wrong tree
#: (see ungated_test_census.py's note on the same hazard).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) in sys.path:
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

TABLE = "ungated_test_baseline"

#: Per-file pytest ceiling when `--run` measures an unobserved file.
DEFAULT_TIMEOUT = 300

#: Per-file ceiling for a birth run. Higher: an old tree may rebuild caches.
BIRTH_TIMEOUT = 420

#: The five states. `unobserved` is never folded into any other.
STATES = ("born_red", "regressed", "history_unknown", "passing", "unobserved")


class SurveyError(RuntimeError):
    """The survey could not be produced. Exit 2, never a clean report."""


# --------------------------------------------------------------------------
# population
# --------------------------------------------------------------------------

def effective_backlog(root: Optional[Path] = None) -> List[str]:
    """Backlog lines that are still real, ungated test files in this tree.

    Reads the allowlist through `gated_test_list.resolve`, the single chokepoint
    that already knows `core.txt` plus the `core.d/` fragments -- so a file
    promoted by a fragment cannot be surveyed as ungated.
    """
    from tools.ci.gated_test_list import parse, resolve  # noqa: PLC0415

    root = Path(root or _ROOT)
    backlog_path = root / "args" / "ci_test_backlog.txt"
    if not backlog_path.is_file():
        raise SurveyError(f"{backlog_path} is missing -- there is no backlog to survey")

    gated = set()
    for name in ("core", "windows"):
        try:
            gated |= {e.split("::")[0].replace("\\", "/") for e in resolve(name, root)}
        except Exception:  # noqa: BLE001 -- a missing windows list is normal
            continue

    out = []
    for rel in parse(backlog_path.read_text(encoding="utf-8")):
        rel = rel.split("::")[0].replace("\\", "/")
        if rel in gated:
            continue
        if not (root / rel).is_file():
            continue  # stale census line; gated_test_list --prune-backlog clears it
        out.append(rel)
    return sorted(set(out))


# --------------------------------------------------------------------------
# observations
# --------------------------------------------------------------------------

def load_observations(conn) -> Dict[str, Dict[str, object]]:
    """Every recorded verdict, keyed by path. Raises if the table is absent."""
    cols = "path, status, first_seen, last_checked, last_detail"
    try:
        rows = conn.execute(
            f"SELECT {cols}, first_status, ever_passed FROM {TABLE}"
        ).fetchall()
    except Exception:  # noqa: BLE001 -- pre-migration deployment, still usable
        try:
            rows = conn.execute(f"SELECT {cols} FROM {TABLE}").fetchall()
        except Exception as exc:  # noqa: BLE001
            raise SurveyError(
                f"{TABLE} is unreadable ({exc}) -- the drift reflex's migration "
                "has not run here, so there is nothing to survey"
            ) from exc
    return {dict(r)["path"]: dict(r) for r in rows}


def classify(obs: Optional[Mapping[str, object]]) -> str:
    """One of STATES, from a recorded observation (or its absence).

    `ever_passed` is a latch and outranks `first_status`: a file seeded 'fail'
    that later passed and broke again is a REGRESSION, which the drift reflex
    already reports, and re-reporting it here would double-file it.
    """
    if not obs:
        return "unobserved"
    if str(obs.get("status")) == "pass":
        return "passing"
    ever = obs.get("ever_passed")
    if ever not in (None, "", 0, "0", False):
        return "regressed"
    first = obs.get("first_status")
    if first is None or first == "":
        return "history_unknown"
    return "born_red" if str(first) == "fail" else "regressed"


# --------------------------------------------------------------------------
# git: when did the file land?
# --------------------------------------------------------------------------

def _git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def default_branch_ref(root: Path) -> Optional[str]:
    """`origin/main` if it exists, else `main`, else None."""
    for ref in ("origin/main", "main", "origin/master", "master"):
        if _git(root, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return None


def landed_at(root: Path, rel: str, ref: Optional[str] = None) -> Dict[str, object]:
    """The commit that put *rel* on the default branch, and when.

    `--first-parent` on purpose. The commit that ADDED the file lives on a
    feature branch whose tree does not contain whatever else merged that day, so
    a birth run there answers "was this test green in isolation" -- which is
    exactly the question that was already answered YES for the measured example
    while the file was red on main. The first-parent walk gives the MERGE that
    landed it, whose tree is the tree the file actually had to work in.
    """
    args = ["log", "--first-parent", "--diff-filter=A", "--format=%H|%aI", "-1"]
    if ref:
        args.append(ref)
    proc = _git(root, *args, "--", rel)
    line = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not line or "|" not in line[0]:
        return {"commit": None, "landed_at": None,
                "reason": "no first-parent add commit for this path"}
    sha, iso = line[0].split("|", 1)
    return {"commit": sha.strip(), "landed_at": iso.strip(), "reason": None}


#: Above this many lookups, ONE `git log` over history beats N `git log -1` calls.
#: Measured 2026-08-20 on this repo: 1,492 per-file calls take over ten minutes,
#: the single bulk walk takes 35s.
BULK_LANDING_THRESHOLD = 20


def landing_map(root: Path, paths: Sequence[str],
                ref: Optional[str] = None) -> Dict[str, Dict[str, object]]:
    """Landing commit + date for many paths, from ONE history walk.

    Same question as `landed_at` and the same `--first-parent` reasoning; only
    the cost differs. Scoped by the paths' top-level directories rather than by
    passing every path as a pathspec — 1,492 pathspecs overrun the Windows
    command line, and the whole point is to spawn one process.

    An empty result is NOT an answer: the caller falls back to `landed_at`
    per path, so a git that refuses the bulk form degrades to slow rather than
    to "no file has a landing date", which would silently rank everything
    `observed_only`.
    """
    prefixes = sorted({p.split("/", 1)[0] + "/*" for p in paths if "/" in p})
    if not prefixes:
        return {}
    # NUL delimits the commit blocks: it is the one byte a path cannot contain,
    # so a commit subject or a filename can never be mistaken for a separator.
    nul = chr(0)
    args = ["log", "--first-parent", "--diff-filter=A", "--name-only",
            "--format=%x00%H|%aI"]
    if ref:
        args.append(ref)
    proc = _git(root, *args, "--", *prefixes, timeout=600)
    if proc.returncode != 0:
        return {}

    wanted = set(paths)
    out: Dict[str, Dict[str, object]] = {}
    for block in (proc.stdout or "").split(nul):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines or "|" not in lines[0]:
            continue
        sha, iso = lines[0].split("|", 1)
        for rel in lines[1:]:
            rel = rel.replace("\\", "/")
            # Log is newest-first, so the FIRST add seen is the current file's
            # landing; an older re-add is a previous incarnation.
            if rel in wanted and rel not in out:
                out[rel] = {"commit": sha.strip(), "landed_at": iso.strip(),
                            "reason": None}
    return out


def _days_since(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return round((now - stamp).total_seconds() / 86400.0, 1)


def _resolve_landings(root: Path, paths: Sequence[str],
                      ref: Optional[str] = None) -> Dict[str, Dict[str, object]]:
    """Landing info for *paths*, bulk or per-file, whichever is cheaper here.

    Every path gets an entry — a path the bulk walk did not report still gets
    its own `landed_at` call, so an unreported file reads as "no history" only
    when git really has none for it.
    """
    paths = [p for p in paths if p]
    if not paths:
        return {}
    out: Dict[str, Dict[str, object]] = {}
    if len(paths) > BULK_LANDING_THRESHOLD:
        out.update(landing_map(root, paths, ref))
    for rel in paths:
        if rel not in out:
            out[rel] = landed_at(root, rel, ref)
    return out


# --------------------------------------------------------------------------
# measuring an unobserved file
# --------------------------------------------------------------------------

def measure(root: Path, rel: str, timeout: int = DEFAULT_TIMEOUT,
            db_dir: Optional[Path] = None) -> Dict[str, object]:
    """Run one file ALONE, through the same `run_one` every other gate uses.

    Its own `ICDEV_DB_PATH`: a survey that dirtied the ambient `data/icdev.db`
    would leave residue that later reads as a real finding.
    """
    from tools.ci.isolation_run import run_one  # noqa: PLC0415

    env_extra = {"ICDEV_STORAGE_BACKEND": "sqlite"}
    if db_dir is not None:
        env_extra["ICDEV_DB_PATH"] = str(db_dir / (rel.replace("/", "_") + ".db"))
    res = run_one(root, rel, timeout=timeout, env_extra=env_extra)
    tail = [ln for ln in str(res.get("output") or "").splitlines()
            if " passed" in ln or " failed" in ln or " error" in ln]
    return {
        "status": "pass" if res.get("status") == "passed" else "fail",
        "detail": (tail[-1] if tail else f"exit {res.get('returncode')}")[:300],
        "returncode": res.get("returncode"),
        "seconds": res.get("duration_s"),
    }


# --------------------------------------------------------------------------
# confirming at the landing commit
# --------------------------------------------------------------------------

def _scratch_root() -> Path:
    try:
        from tools.git.worktree_paths import actor_root  # noqa: PLC0415

        return Path(actor_root("verify"))
    except Exception:  # noqa: BLE001 -- fall back to the OS temp dir
        return Path(tempfile.gettempdir()) / "icdev-born-red"


def confirm_at_birth(root: Path, rel: str, commit: str,
                     timeout: int = BIRTH_TIMEOUT) -> Dict[str, object]:
    """Run *rel* in a detached worktree at *commit*. Three outcomes, never two.

    pytest exit 1 is "tests failed" and is the only outcome that confirms.
    Exit 2/3/4 (collection or usage error) and exit 5 (nothing collected) mean
    the OLD TREE could not run the file -- a statement about that checkout's
    dependencies, not about the test -- so they report `birth_unrunnable` and
    are counted as neither confirmation nor refutation.
    """
    base = _scratch_root() / "born-red"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{commit[:12]}-{rel.replace('/', '_')[:60]}"
    started = time.monotonic()
    if path.exists():
        _git(root, "worktree", "remove", "--force", str(path))
        shutil.rmtree(path, ignore_errors=True)

    add = _git(root, "worktree", "add", "--detach", str(path), commit, timeout=300)
    if add.returncode != 0:
        return {"verdict": "birth_unrunnable", "returncode": None,
                "reason": f"worktree add failed: {(add.stderr or '').strip()[:200]}"}
    try:
        if not (path / rel).is_file():
            return {"verdict": "birth_unrunnable", "returncode": None,
                    "reason": f"{rel} is absent at {commit[:12]}"}
        env = dict(os.environ)
        env["PYTHONPATH"] = str(path)
        env["ICDEV_STORAGE_BACKEND"] = "sqlite"
        env["ICDEV_DB_PATH"] = str(path / ".born-red.db")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", rel, "-q", "--tb=line",
                 "-p", "no:cacheprovider"],
                cwd=str(path), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"verdict": "birth_unrunnable", "returncode": None,
                    "reason": f"timed out after {timeout}s at {commit[:12]}"}
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = [ln for ln in out.splitlines()
                if " passed" in ln or " failed" in ln or " error" in ln]
        detail = (tail[-1] if tail else f"exit {proc.returncode}")[:300]
        if proc.returncode == 0:
            verdict = "passed_at_birth"
        elif proc.returncode == 1:
            verdict = "confirmed_born_red"
        else:
            verdict = "birth_unrunnable"
        return {"verdict": verdict, "returncode": proc.returncode,
                "detail": detail, "commit": commit,
                "seconds": round(time.monotonic() - started, 1),
                "reason": None if verdict != "birth_unrunnable"
                else f"pytest exit {proc.returncode} at {commit[:12]}: {detail}"}
    finally:
        _git(root, "worktree", "remove", "--force", str(path))
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------
# the survey
# --------------------------------------------------------------------------

def survey(root: Optional[Path] = None, run_limit: int = 0,
           confirm_limit: int = 0, timeout: int = DEFAULT_TIMEOUT,
           now: Optional[datetime] = None,
           observations: Optional[Dict[str, Dict[str, object]]] = None,
           ) -> Dict[str, object]:
    """Classify the ungated backlog and rank the born-red files.

    `observations` is injectable so the classification and ranking can be tested
    without a database; None means read the reflex's table.
    """
    root = Path(root or _ROOT)
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)

    files = effective_backlog(root)

    if observations is None:
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
            try:
                observations = load_observations(conn)
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        except SurveyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SurveyError(f"storage unavailable: {exc}") from exc

    ref = default_branch_ref(root)

    # Measure unobserved files, oldest-landed first: a file nobody has run that
    # has been in the tree longest is the likeliest long-standing debt.
    measured: Dict[str, Dict[str, object]] = {}
    landings: Dict[str, Dict[str, object]] = {}
    if run_limit:
        unobserved = [f for f in files if f not in observations]
        landings.update(_resolve_landings(root, unobserved, ref))
        unobserved.sort(
            key=lambda f: str((landings.get(f) or {}).get("landed_at") or "9999"))
        db_dir = Path(tempfile.mkdtemp(prefix="born-red-db-"))
        try:
            for rel in unobserved[:run_limit]:
                measured[rel] = measure(root, rel, timeout=timeout, db_dir=db_dir)
        finally:
            shutil.rmtree(db_dir, ignore_errors=True)

    # Classify FIRST, with no git at all — then resolve landing dates only for
    # the born-red files. Asking git about all 1,701 would cost minutes to
    # answer a question 3 rows need.
    rows: List[Dict[str, object]] = []
    counts = {state: 0 for state in STATES}
    resolved: Dict[str, Optional[Mapping[str, object]]] = {}
    for rel in files:
        obs = observations.get(rel)
        source = "baseline"
        if rel in measured:
            m = measured[rel]
            # This run IS the first observation, so a failure here carries
            # exactly the evidence the reflex's seeded row carries, no less.
            obs = {"status": m["status"], "first_status": m["status"],
                   "ever_passed": 1 if m["status"] == "pass" else 0,
                   "first_seen": now.isoformat(), "last_checked": now.isoformat(),
                   "last_detail": m["detail"]}
            source = "measured"
        state = classify(obs)
        counts[state] += 1
        if state == "born_red":
            resolved[rel] = obs
            landings.setdefault(rel, None)

    pending = [rel for rel in resolved if not landings.get(rel)]
    landings.update(_resolve_landings(root, pending, ref))

    for rel, obs in resolved.items():
        source = "measured" if rel in measured else "baseline"
        landing = landings.get(rel) or {"commit": None, "landed_at": None}
        observed_days = _days_since(obs.get("first_seen"), now) if obs else None
        age_days = _days_since(landing.get("landed_at"), now)
        if age_days is None:
            basis, red_days = "observed_only", observed_days
        else:
            basis, red_days = "file_age_upper_bound", age_days
        rows.append({
            "path": rel,
            "state": "born_red",
            "source": source,
            "detail": (obs or {}).get("last_detail"),
            "observed_red_since": (obs or {}).get("first_seen"),
            "observed_red_days": observed_days,
            "landed_at": landing.get("landed_at"),
            "landed_commit": landing.get("commit"),
            "file_age_days": age_days,
            "red_days": red_days,
            "red_days_basis": basis,
            "birth": None,
        })

    rows.sort(key=lambda r: (-(r["red_days"] or 0.0), r["path"]))

    confirmations = {"confirmed_born_red": 0, "passed_at_birth": 0,
                     "birth_unrunnable": 0}
    if confirm_limit:
        for row in rows[:confirm_limit]:
            commit = row.get("landed_commit")
            if not commit:
                row["birth"] = {"verdict": "birth_unrunnable",
                                "reason": "no landing commit for this path"}
                confirmations["birth_unrunnable"] += 1
                continue
            res = confirm_at_birth(root, str(row["path"]), str(commit))
            row["birth"] = res
            verdict = str(res.get("verdict"))
            confirmations[verdict] = confirmations.get(verdict, 0) + 1
            if verdict == "confirmed_born_red":
                row["red_days_basis"] = "confirmed_at_birth"
            elif verdict == "passed_at_birth":
                # It worked when it landed and broke later, with nothing
                # watching. A different defect, and NOT born red -- so the
                # file-age bound is REFUTED and must not travel on the row as
                # if it were still the span. Only the proven span survives.
                row["state"] = "broke_after_birth"
                row["red_days_basis"] = "refuted_at_birth"
                row["red_days"] = row["observed_red_days"]
        rows.sort(key=lambda r: (-(r["red_days"] or 0.0), r["path"]))

    # A deployment that has recorded nothing must not report a clean zero.
    recorded = len([f for f in files if f in observations]) + len(measured)
    unmeasurable = recorded == 0
    born_red_rows = [r for r in rows if r["state"] == "born_red"]

    return {
        "ran": True,
        "state": "unmeasurable" if unmeasurable else (
            "findings" if born_red_rows else "clean"),
        "backlog_total": len(files),
        "observed": recorded,
        # NOT `- len(measured)`: a file measured this run was classified from
        # that measurement, so it never entered `counts["unobserved"]` in the
        # first place. Subtracting again understated the uninspected population
        # by exactly the number of files `--run` had just inspected.
        "unobserved": counts["unobserved"],
        "coverage_pct": (round(100.0 * recorded / len(files), 1) if files else None),
        # None, never 0, when nothing has been observed: "measured clean" and
        # "never measured" justify opposite decisions.
        "counts": {k: (None if unmeasurable and k != "unobserved" else v)
                   for k, v in counts.items()},
        "born_red_count": None if unmeasurable else len(born_red_rows),
        "broke_after_birth_count": len([r for r in rows
                                        if r["state"] == "broke_after_birth"]),
        "confirmations": confirmations if confirm_limit else None,
        "findings": rows,
        "measured_now": len(measured),
        "default_branch_ref": ref,
        "history_available": any(
            o.get("first_status") is not None for o in observations.values()
        ) if observations else False,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "generated_at": now.isoformat(),
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render(report: Mapping[str, object], limit: int = 25) -> str:
    lines: List[str] = []
    state = str(report.get("state"))
    lines.append("Ungated tests that have NEVER been observed passing (rem-hyg-14)")
    lines.append("=" * 72)
    lines.append(
        f"backlog {report.get('backlog_total')} files | "
        f"observed {report.get('observed')} ({report.get('coverage_pct')}%) | "
        f"never run {report.get('unobserved')}"
    )
    if state == "unmeasurable":
        lines.append("")
        lines.append("UNMEASURABLE -- no verdict has been recorded on this deployment.")
        lines.append("Nobody has run any of these files. That is not a clean bill of")
        lines.append("health. Run the ungated_test_drift reflex, or --run N here.")
        return "\n".join(lines)

    counts = dict(report.get("counts") or {})
    lines.append("  " + "  ".join(f"{k}={counts.get(k)}" for k in STATES))
    conf = report.get("confirmations")
    if conf:
        lines.append("  birth runs: "
                     + "  ".join(f"{k}={v}" for k, v in dict(conf).items()))
    lines.append("")

    findings = [dict(r) for r in (report.get("findings") or [])]
    born = [r for r in findings if r["state"] == "born_red"]
    broke = [r for r in findings if r["state"] == "broke_after_birth"]

    if not born:
        lines.append("No born-red file among the OBSERVED backlog. "
                     f"{report.get('unobserved')} files have still never been run.")
    else:
        lines.append(f"BORN RED -- {len(born)} file(s), longest-standing first:")
        lines.append("")
        for row in born[:limit]:
            basis = {"confirmed_at_birth": "CONFIRMED at landing commit",
                     "file_age_upper_bound": "upper bound (never seen passing)",
                     "observed_only": "observed span only"}.get(
                         str(row["red_days_basis"]), str(row["red_days_basis"]))
            lines.append(f"  {row['red_days']:>7} d  {row['path']}")
            lines.append(f"            {basis}")
            lines.append(
                f"            landed {str(row['landed_at'] or '?')[:10]} "
                f"({str(row['landed_commit'] or '?')[:12]}) | "
                f"proven red for {row['observed_red_days']} d"
            )
            if row.get("detail"):
                lines.append(f"            {row['detail']}")
            lines.append("")
        if len(born) > limit:
            lines.append(f"  ... and {len(born) - limit} more (--limit to widen)")
            lines.append("")

    if broke:
        lines.append(f"BROKE AFTER BIRTH -- {len(broke)} file(s) passed at their landing")
        lines.append("commit and fail now. The drift reflex missed these because it")
        lines.append("never observed the passing state it would have transitioned from.")
        for row in broke[:limit]:
            lines.append(f"  {row['path']}  (landed {str(row['landed_at'] or '?')[:10]})")
        lines.append("")

    lines.append("Report only -- no gate. Fix the file and gate it in the same PR")
    lines.append("(args/ci_test_files/core.d/<task-id>.txt), or delete it.")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Ungated tests that are red from birth (report only)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--out", help="write the JSON report to this path")
    ap.add_argument("--limit", type=int, default=25,
                    help="rows rendered in the human table (default 25)")
    ap.add_argument("--run", type=int, default=0, metavar="N",
                    help="measure N never-observed files now, oldest-landed first")
    ap.add_argument("--confirm", type=int, default=0, metavar="N",
                    help="run the top N candidates at their landing commit")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"per-file pytest ceiling for --run (default {DEFAULT_TIMEOUT}s)")
    ap.add_argument("--root", help="repository root (defaults to this checkout)")
    args = ap.parse_args(argv)

    try:
        report = survey(
            root=Path(args.root) if args.root else None,
            run_limit=max(0, args.run),
            confirm_limit=max(0, args.confirm),
            timeout=args.timeout,
        )
    except SurveyError as exc:
        print(f"born_red_survey: could not produce a survey: {exc}", file=sys.stderr)
        return 2

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
