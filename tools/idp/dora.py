# CUI // SP-CTI
"""Delivery evidence: the DORA four keys and rework of AI-assisted work, from declared sources (icx-coe-03).

``/sre`` already bands DORA from ``audit_trail`` and ``ci_pipeline_runs`` (``tools/idp/delivery_events.py``
feeds them from the board). That view is one database's, for ICDEV[IT] alone. A conformance bundle needs the
same four keys for EVERY repo (ICDEV[IT], ICDEV[OS], ICDEV[FT]), computed from sources anyone can re-read and
digest, so this module computes them from three declared sources instead, and nothing else:

* **git** (``icdev-it/git-history/v1``): the merge commits on the main branch's first-parent line, each with the
  commits it brought in (their authored times, whether a machine co-authored them, the card id they name) and
  the reverts on main. Read live from a checkout (:func:`read_git`), or from a snapshot of that read.
* **CI** (``icdev-it/ci-runs/v1``): completed CI runs on main by commit sha. Always a snapshot passed in (a
  ``gh run list --json`` export, or a GitLab pipeline export, normalised by :func:`normalise_ci`); never
  fetched here.
* **board** (``icdev-it/board-export/v1``): kanban cards (id, status, depends_on_task_id, completed_at), from an
  export or a scratch copy of the board; never the live board.

Definitions live in ``args/coe_conformance.yaml`` (``delivery``) and are carried, digested, in every result.
:func:`compute` is PURE: the same sources, window and definitions give the same metrics, byte for byte.

**A metric with no data is UNVERIFIED** (``status: unverified``, ``value: null``, with why): a source that is
missing, a window with no deployment, no CI outcome to judge a failure by, no failure to restore from, no
AI-assisted deployment old enough to have been reworked. Never zero, never passed. Each metric carries its
window, its sample size and the digests of the sources it used. Outcomes, not adoption counts: nothing here
counts how many people use a tool.

    python tools/idp/dora.py --repo-path . --ref origin/main --at 2026-11-17 [--ci ci.json] [--board b.json] --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess  # nosec B404 — git, with fixed arguments, never a shell
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

GIT_SCHEMA = "icdev-it/git-history/v1"
CI_SCHEMA = "icdev-it/ci-runs/v1"
BOARD_SCHEMA = "icdev-it/board-export/v1"

METRICS = ("deployment_frequency", "lead_time", "change_failure_rate", "time_to_restore", "ai_rework_rate")
UNITS = {"deployment_frequency": "per_week", "lead_time": "hours", "change_failure_rate": "ratio",
         "time_to_restore": "hours", "ai_rework_rate": "ratio"}

#: git log field and record separators (unit and record separators: never in a subject).
_FS, _RS = "\x1f", "\x1e"
_GIT_TIMEOUT = 120


class SourceError(ValueError):
    """A source could not be read or is not the shape it says it is."""


# ── canonical form ───────────────────────────────────────────────────────────


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _utc(ts: Any) -> Optional[datetime]:
    """An ISO 8601 string or a Unix time as an aware UTC datetime, or None."""
    if ts in (None, ""):
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc)
    s = str(ts).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def window(as_of: str, days: int) -> dict:
    """The ``days`` UTC days ending on and including ``as_of`` (YYYY-MM-DD): ``[start, end)``."""
    end_day = date.fromisoformat(str(as_of)[:10]) + timedelta(days=1)
    start = datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone.utc) - timedelta(days=int(days))
    end = start + timedelta(days=int(days))
    return {"start": _iso(start), "end": _iso(end), "days": int(days), "as_of": str(as_of)[:10]}


# ── the definitions ──────────────────────────────────────────────────────────


def definitions(cfg: dict) -> dict:
    """The delivery definitions in force (``args/coe_conformance.yaml`` ``delivery``), normalised."""
    d = dict(cfg or {})
    return {
        "window_days": int(d.get("window_days") or 30),
        "failure_window_days": int(d.get("failure_window_days") or 7),
        "rework_days": int(d.get("rework_days") or 7),
        "ai_coauthor_address": str(d.get("ai_coauthor_address") or r"^noreply@"),
        "card_id": str(d.get("card_id") or r"\b([a-z]{2,6}-[a-z0-9]{2,10}-[0-9]{2,3})\b"),
        "revert": str(d.get("revert") or r"This reverts commit ([0-9a-f]{7,40})"),
        "ci_success": sorted(str(x).lower() for x in (d.get("ci_success") or ["success"])),
        "ci_failure": sorted(str(x).lower() for x in (d.get("ci_failure") or ["failure"])),
    }


# ── git ──────────────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,  # nosec B603 B607
                             timeout=_GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceError(f"git could not run in {path}: {exc}") from exc
    if out.returncode != 0:
        raise SourceError(f"git {args[0]} failed in {path}: {out.stderr.strip()[:300]}")
    return out.stdout


def _ai(trailers: str, pattern: re.Pattern) -> bool:
    for line in (trailers or "").splitlines():
        m = re.search(r"<([^>]+)>", line)
        if m and pattern.search(m.group(1).strip()):
            return True
    return False


def _card(texts: Iterable[str], pattern: re.Pattern) -> Optional[str]:
    for t in texts:
        m = pattern.search(t or "")
        if m:
            return m.group(1)
    return None


def _log(path: Path, rev: str, *extra: str) -> list[dict]:
    """``git log`` records: sha, parents, authored and committed times, co-author trailers, subject and body."""
    fmt = _FS.join(("%H", "%P", "%aI", "%cI", "%(trailers:key=Co-authored-by,valueonly,separator=%x0a)",
                    "%s", "%b")) + _RS
    raw = _git(path, "log", f"--format={fmt}", *extra, rev, "--")
    out = []
    for rec in raw.split(_RS):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split(_FS)
        if len(parts) < 7:
            continue
        out.append({"sha": parts[0], "parents": parts[1].split(), "authored_at": parts[2],
                    "committed_at": parts[3], "trailers": parts[4], "subject": parts[5], "body": parts[6]})
    return out


def read_git(path: Path | str, ref: str, *, until: str, since: str, defs: dict, repo: str = "") -> dict:
    """The main branch's merges in ``[since, until)``, each with the commits it brought in, plus the reverts.

    ``since``/``until`` are ISO times (``window``'s start and end); reverts are read up to ``until`` too, so the
    read does not depend on when it ran. Commit subjects are NOT kept: only times, shas, card ids and whether a
    machine co-authored each commit, so a snapshot of another repo carries none of its text.
    """
    path = Path(path).expanduser()
    if not (path / ".git").exists() and not path.joinpath("HEAD").exists():
        raise SourceError(f"{path} is not a git checkout")
    ai_re = re.compile(defs["ai_coauthor_address"], re.IGNORECASE)
    card_re = re.compile(defs["card_id"])
    rev_re = re.compile(defs["revert"])
    head = _git(path, "rev-parse", ref).strip()
    lo, hi = _utc(since), _utc(until)
    first = _log(path, head, "--first-parent", f"--until={until}")
    merges, reverts = [], []
    for c in first:
        at = _utc(c["committed_at"])
        for sha in rev_re.findall(c["body"] or ""):
            reverts.append({"sha": c["sha"], "at": _iso(at), "reverts": sha})
        if len(c["parents"]) < 2 or at is None or at >= hi:
            continue
        if at < lo:
            continue
        brought = _log(path, f"{c['parents'][0]}..{c['parents'][1]}", "--no-merges")
        for b in brought:
            for sha in rev_re.findall(b["body"] or ""):
                reverts.append({"sha": b["sha"], "at": _iso(at), "reverts": sha})
        merges.append({
            "sha": c["sha"], "merged_at": _iso(at),
            "commits": sorted([{"sha": b["sha"], "authored_at": _iso(_utc(b["authored_at"])),
                                "ai": _ai(b["trailers"], ai_re)} for b in brought],
                              key=lambda x: (x["authored_at"] or "", x["sha"])),
            "ai": _ai(c["trailers"], ai_re) or any(_ai(b["trailers"], ai_re) for b in brought),
            "card": _card([c["subject"], c["body"]] + [b["subject"] for b in brought], card_re),
        })
    # Reverts before the window still matter only for merges in it; a revert of a commit is matched by sha
    # prefix, so keep them all (each is a sha, a time and a sha: no text).
    reverts = sorted({(r["sha"], r["at"], r["reverts"]) for r in reverts if r["at"] and _utc(r["at"]) < hi})
    return {"schema": GIT_SCHEMA, "repo": repo, "ref": ref, "head": head,
            "since": since, "until": until,
            "merges": sorted(merges, key=lambda m: (m["merged_at"], m["sha"])),
            "reverts": [{"sha": s, "at": a, "reverts": r} for s, a, r in reverts]}


def check_git(snap: dict) -> dict:
    if not isinstance(snap, dict) or snap.get("schema") != GIT_SCHEMA:
        raise SourceError(f"not a {GIT_SCHEMA} snapshot")
    for m in snap.get("merges") or []:
        if not m.get("sha") or not _utc(m.get("merged_at")):
            raise SourceError("a merge in the git snapshot has no sha or time")
    return snap


# ── CI and board ─────────────────────────────────────────────────────────────


def normalise_ci(doc: Any, *, source: str = "", repo: str = "", fetched_at: str = "",
                 workflow: str = "") -> dict:
    """A CI snapshot as ``icdev-it/ci-runs/v1``: ``runs: [{sha, conclusion, finished_at}]`` on main.

    Accepts the normalised form itself, or a ``gh run list --json headSha,conclusion,updatedAt,headBranch``
    export (a list), keeping only runs on main / master, and only ``workflow``'s runs when the export names
    workflows (``workflowName``): another workflow's success never masks a failed main run. Anything else is
    refused, never guessed at.
    """
    if isinstance(doc, dict) and doc.get("schema") == CI_SCHEMA:
        runs = doc.get("runs") or []
        meta = {k: doc.get(k) for k in ("source", "repo", "fetched_at")}
    elif isinstance(doc, list):
        runs = [{"sha": r.get("headSha"), "conclusion": r.get("conclusion"),
                 "finished_at": r.get("updatedAt") or r.get("createdAt")}
                for r in doc if isinstance(r, dict) and str(r.get("headBranch") or "main") in ("main", "master")
                and (not workflow or r.get("workflowName") in (None, workflow))]
        meta = {"source": source or "github-actions", "repo": repo, "fetched_at": fetched_at,
                **({"workflow": workflow} if workflow else {})}
    else:
        raise SourceError(f"not a {CI_SCHEMA} snapshot nor a gh run list export")
    out = []
    for r in runs:
        if not isinstance(r, dict) or not r.get("sha"):
            raise SourceError("a CI run has no sha")
        out.append({"sha": str(r["sha"]), "conclusion": str(r.get("conclusion") or "").lower(),
                    "finished_at": _iso(_utc(r.get("finished_at")))})
    out.sort(key=lambda r: (r["sha"], r["finished_at"] or "", r["conclusion"]))
    return {"schema": CI_SCHEMA, **meta, "runs": out}


def normalise_board(doc: Any, *, exported_at: str = "") -> dict:
    """A board export as ``icdev-it/board-export/v1``: ``tasks: [{id, status, depends_on_task_id, completed_at}]``."""
    if isinstance(doc, dict) and doc.get("schema") == BOARD_SCHEMA:
        tasks, at = doc.get("tasks") or [], doc.get("exported_at")
    elif isinstance(doc, list):
        tasks, at = doc, exported_at
    else:
        raise SourceError(f"not a {BOARD_SCHEMA} export")
    out = []
    for t in tasks:
        if not isinstance(t, dict) or not t.get("id"):
            raise SourceError("a board card has no id")
        out.append({"id": str(t["id"]), "status": str(t.get("status") or ""),
                    "depends_on_task_id": str(t.get("depends_on_task_id") or "") or None,
                    "completed_at": _iso(_utc(t.get("completed_at")))})
    return {"schema": BOARD_SCHEMA, "exported_at": at, "tasks": sorted(out, key=lambda t: t["id"])}


# ── the metrics ──────────────────────────────────────────────────────────────


def _metric(key: str, *, value: Any = None, sample: int = 0, why: str = "", sources: Iterable[str] = (),
            win: dict, detail: Optional[dict] = None) -> dict:
    measured = value is not None
    out = {"key": key, "status": "measured" if measured else "unverified", "value": value, "unit": UNITS[key],
           "window_days": win["days"], "sample_size": int(sample), "sources": sorted(set(sources))}
    if not measured:
        out["why"] = why or "no data"
    if detail:
        out["detail"] = detail
    return out


def _hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600.0, 2)


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def _reverted_at(merge: dict, reverts: list[dict], until: datetime) -> Optional[datetime]:
    """When a later commit on main first reverted one of the commits this merge brought (or the merge itself)."""
    shas = [merge["sha"]] + [c["sha"] for c in merge.get("commits") or []]
    at = _utc(merge["merged_at"])
    best = None
    for r in reverts:
        when = _utc(r.get("at"))
        if when is None or when <= at or when > until:
            continue
        target = str(r.get("reverts") or "")
        if len(target) >= 7 and any(s.startswith(target) for s in shas):
            best = when if best is None or when < best else best
    return best


def compute(*, git: Optional[dict], ci: Optional[dict], board: Optional[dict], as_of: str, defs: dict,
            missing: Optional[dict] = None) -> dict:
    """Every delivery metric over the window ending on ``as_of``. Pure.

    ``missing`` says why a source is absent (``{"git": "...", "ci": "...", "board": "..."}``); each metric that
    needs it is then unverified with that reason.
    """
    missing = dict(missing or {})
    win = window(as_of, defs["window_days"])
    lo, hi = _utc(win["start"]), _utc(win["end"])
    src = {k: (digest(v) if v is not None else None) for k, v in (("git", git), ("ci", ci), ("board", board))}
    sources = [{"kind": k, "digest": src[k], "schema": (v or {}).get("schema"),
                **({"ref": git.get("ref"), "head": git.get("head")} if k == "git" and git else {}),
                **({"system": ci.get("source"), "fetched_at": ci.get("fetched_at"), "runs": len(ci.get("runs") or [])}
                   if k == "ci" and ci else {}),
                **({"exported_at": board.get("exported_at"), "cards": len(board.get("tasks") or [])}
                   if k == "board" and board else {}),
                **({"missing": missing.get(k) or "not given"} if v is None else {})}
               for k, v in (("git", git), ("ci", ci), ("board", board))]
    out = {"window": win, "definitions": defs, "definitions_digest": digest(defs), "sources": sources}
    gd, cd, bd = src["git"], src["ci"], src["board"]

    if git is None:
        why = f"no git history: {missing.get('git') or 'not given'}"
        out["metrics"] = {k: _metric(k, why=why, win=win) for k in METRICS}
        return out

    deps = [m for m in git.get("merges") or [] if lo <= _utc(m["merged_at"]) < hi]
    reverts = git.get("reverts") or []
    metrics: dict[str, dict] = {}

    # deployment frequency
    if deps:
        metrics["deployment_frequency"] = _metric(
            "deployment_frequency", value=round(len(deps) / win["days"] * 7, 2), sample=len(deps),
            sources=[gd], win=win,
            detail={"deployments": len(deps), "deploy_days": len({m["merged_at"][:10] for m in deps})})
    else:
        metrics["deployment_frequency"] = _metric(
            "deployment_frequency", why="no merge to main in the window: nothing to measure", sources=[gd], win=win)

    # lead time
    leads = []
    for m in deps:
        times = [_utc(c["authored_at"]) for c in m.get("commits") or [] if _utc(c.get("authored_at"))]
        if times:
            leads.append(max(0.0, _hours(min(times), _utc(m["merged_at"]))))
    if leads:
        metrics["lead_time"] = _metric(
            "lead_time", value=round(statistics.median(leads), 2), sample=len(leads), sources=[gd], win=win,
            detail={"p50_hours": round(statistics.median(leads), 2), "p90_hours": _pct(leads, 0.9),
                    "excluded_no_commits": len(deps) - len(leads)})
    else:
        metrics["lead_time"] = _metric(
            "lead_time", why=("no deployment in the window" if not deps else
                              "no deployment in the window brought a commit of its own"), sources=[gd], win=win)

    # change failure rate and time to restore
    fail_days = timedelta(days=defs["failure_window_days"])
    ok, bad = set(defs["ci_success"]), set(defs["ci_failure"])
    latest: dict[str, dict] = {}
    for r in (ci or {}).get("runs") or []:
        if r["conclusion"] in ok | bad:
            cur = latest.get(r["sha"])
            if cur is None or (r["finished_at"] or "") >= (cur["finished_at"] or ""):
                latest[r["sha"]] = r
    observed, failed = [], []
    for m in deps:
        run = latest.get(m["sha"])
        if run is None:
            continue
        rv = _reverted_at(m, reverts, min(_utc(m["merged_at"]) + fail_days, hi))
        observed.append(m)
        if run["conclusion"] in bad or rv is not None:
            failed.append((m, rv))
    if ci is None:
        why = f"no CI run records: {missing.get('ci') or 'not given'}"
        metrics["change_failure_rate"] = _metric("change_failure_rate", why=why, sources=[gd], win=win)
        metrics["time_to_restore"] = _metric("time_to_restore", why=why, sources=[gd], win=win)
    elif not observed:
        why = ("no deployment in the window" if not deps else
               "no deployment in the window has a completed CI run on main: no outcome is observed")
        metrics["change_failure_rate"] = _metric("change_failure_rate", why=why, sources=[gd, cd], win=win)
        metrics["time_to_restore"] = _metric("time_to_restore", why=why, sources=[gd, cd], win=win)
    else:
        metrics["change_failure_rate"] = _metric(
            "change_failure_rate", value=round(len(failed) / len(observed), 4), sample=len(observed),
            sources=[gd, cd], win=win,
            detail={"failed": len(failed), "observed": len(observed),
                    "unobserved": len(deps) - len(observed),
                    "by_revert": sum(1 for _, rv in failed if rv is not None)})
        allm = sorted(git.get("merges") or [], key=lambda m: m["merged_at"])
        restores = []
        for m, rv in failed:
            at = _utc(m["merged_at"])
            nxt = next((n for n in allm if _utc(n["merged_at"]) > at
                        and (latest.get(n["sha"]) or {}).get("conclusion") in ok), None)
            ends = [t for t in (rv, _utc(nxt["merged_at"]) if nxt else None) if t is not None and t <= hi]
            if ends:
                restores.append(_hours(at, min(ends)))
        if restores:
            metrics["time_to_restore"] = _metric(
                "time_to_restore", value=round(statistics.median(restores), 2), sample=len(restores),
                sources=[gd, cd], win=win,
                detail={"p50_hours": round(statistics.median(restores), 2), "p90_hours": _pct(restores, 0.9),
                        "failed": len(failed), "not_yet_restored": len(failed) - len(restores)})
        else:
            metrics["time_to_restore"] = _metric(
                "time_to_restore", why=("no failed deployment in the window: nothing to restore from"
                                        if not failed else "no failed deployment has been restored yet"),
                sources=[gd, cd], win=win, sample=0)

    # rework of AI-assisted work
    rework = timedelta(days=defs["rework_days"])
    ai = [m for m in deps if m.get("ai")]
    ripe = [m for m in ai if _utc(m["merged_at"]) + rework <= hi]
    allm = git.get("merges") or []
    done = [t for t in (board or {}).get("tasks") or [] if t.get("completed_at")]
    reworked, why_counts = 0, {"revert": 0, "follow_up": 0, "board": 0}
    for m in ripe:
        at = _utc(m["merged_at"])
        until = at + rework
        hit = None
        if _reverted_at(m, reverts, until) is not None:
            hit = "revert"
        elif m.get("card") and any(n["sha"] != m["sha"] and n.get("card") == m["card"]
                                   and at < _utc(n["merged_at"]) <= until for n in allm):
            hit = "follow_up"
        elif m.get("card") and any(t.get("depends_on_task_id") == m["card"]
                                   and at < _utc(t["completed_at"]) <= until for t in done):
            hit = "board"
        if hit:
            reworked += 1
            why_counts[hit] += 1
    used = [gd] + ([bd] if board is not None else [])
    if ripe:
        metrics["ai_rework_rate"] = _metric(
            "ai_rework_rate", value=round(reworked / len(ripe), 4), sample=len(ripe), sources=used, win=win,
            detail={"reworked": reworked, **why_counts, "ai_assisted": len(ai),
                    "too_recent": len(ai) - len(ripe), "rework_days": defs["rework_days"],
                    **({"board": "not given: follow-up cards were not read"} if board is None else {})})
    else:
        metrics["ai_rework_rate"] = _metric(
            "ai_rework_rate", why=("no AI-assisted deployment in the window" if not ai else
                                   f"no AI-assisted deployment is {defs['rework_days']} days old yet"),
            sources=used, win=win, detail={"ai_assisted": len(ai)})
    out["metrics"] = metrics
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Delivery evidence (DORA four keys + AI rework) from declared sources")
    ap.add_argument("--repo-path", default=".", help="a git checkout")
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--at", default=datetime.now(timezone.utc).date().isoformat(), help="as-of date YYYY-MM-DD")
    ap.add_argument("--ci", type=Path, help="CI runs snapshot (icdev-it/ci-runs/v1 or gh run list JSON)")
    ap.add_argument("--board", type=Path, help="board export (icdev-it/board-export/v1)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from tools.idp.conformance import load_config

    cfg = load_config()
    defs = definitions(cfg.get("delivery") or {})
    win = window(a.at, defs["window_days"])
    git = read_git(a.repo_path, a.ref, since=win["start"], until=win["end"], defs=defs)
    ci = normalise_ci(json.loads(a.ci.read_text(encoding="utf-8"))) if a.ci else None
    board = normalise_board(json.loads(a.board.read_text(encoding="utf-8"))) if a.board else None
    res = compute(git=git, ci=ci, board=board, as_of=a.at, defs=defs)
    if a.json:
        print(json.dumps(res, indent=2, sort_keys=True))
    else:
        for k, m in res["metrics"].items():
            print(f"{k:22} {m['status']:10} {m['value']!s:>10} {m['unit']:9} n={m['sample_size']} {m.get('why', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
