# CUI // SP-CTI
"""Seed what the standards-scorecards and conformance e2e needs (icx-coe-03).

* Two FRESH, card-named test people (synthetic), each with its own dashboard API key so the spec signs each into
  its own browser context: ``coe03-preparer`` and ``coe03-publisher``, both role reviewer, which holds the publish
  step. The preparer holds it on purpose: what stops them publishing their own bundle on the page is separation of
  duties (``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new
  people.
* The standards register's seeds (``standards.seed``, the labelled bootstrap identity, idempotent).
* ONE ALREADY-EXPIRED exemption on ``e2e-spec`` (the rule ENG-E2E-SPEC checks), for a run-named component, so a
  real lapse fails the standard on the page: requested and granted through the product's own functions under an
  INJECTED CLOCK 40 days in the past, for 30 days, the requester never the approver. Labelled ``CUI``, the level a
  console session reads at (as icx-coe-02's seed does). The journey revokes it at the end (a stop, on the
  exemptions page), so the scorecard's exemption gate is back to pass for the specs that run after it;
  ``--revoke <key> --by <person id>`` is the same stop, for the spec's clean-up when the journey did not reach it.
* Synthetic delivery sources, written to ``tests/e2e/fixtures/.coe03_sources/`` (git-ignored), the directory the
  e2e server reads (``ICDEV_COE_SOURCES_DIR``, with ``ICDEV_COE_LIVE_GIT=0``): ICDEV[IT]'s git history of main, CI
  runs and a board export, dated relative to today so they fall in the window; ICDEV[OS]'s git history only (no CI
  snapshot, so its change failure rate is unverified); nothing for ICDEV[FT] (every metric unverified).

Everything goes through the product's own functions, never a hand-written INSERT. The server must be on a throwaway
database (globalSetup asserts it). Prints one JSON line: the run id, each person's id and raw key (a throwaway key
on a throwaway database; the spec only uses it to sign in), the expired exemption and what the sources should give.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SOURCES = ROOT / "tests" / "e2e" / "fixtures" / ".coe03_sources"


def person(name: str, role: str, run: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="icx-coe-03-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"icx-coe-03 e2e {run}", created_by="icx-coe-03-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def expired_exemption(requester: dict, approver: dict, component: str) -> dict:
    from tools.idp import exemptions as e

    then = datetime.now(timezone.utc) - timedelta(days=40)
    clock = lambda: then  # noqa: E731 — the clock the functions are handed
    expires = (then.date() + timedelta(days=30)).isoformat()
    row = e.request_exemption("component-readiness", "e2e-spec", component,
                              reason="Synthetic (icx-coe-03 e2e): granted for 30 days, now lapsed.",
                              requested_by=requester["id"], expires=expires, classification="CUI", clock=clock)
    current = e._latest(e._open_connection(), row["exemption_key"])
    e.decide_exemption(row["exemption_key"], decision="grant", reason="Synthetic (icx-coe-03 e2e): granted then.",
                       read_digest=e.exemption_digest(current), actor={"id": approver["id"], "role": approver["role"]},
                       clock=clock)
    return {"exemption_key": row["exemption_key"], "ref": row["ref"], "component": component, "expires_on": expires}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sources(run: str) -> dict:
    """Synthetic sources dated from today (UTC): what ICDEV[IT] and ICDEV[OS] delivered, as git, CI and the board."""
    day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def ago(days: int, hours: int = 0) -> str:
        return _iso(day0 - timedelta(days=days) + timedelta(hours=hours))

    def merge(n: int, days: int, *, ai: bool, card: str | None) -> dict:
        sha = f"{n:02d}{run}".ljust(40, "e")
        return {"sha": sha, "merged_at": ago(days, 12), "ai": ai, "card": card,
                "commits": [{"sha": sha[:-1] + "c", "authored_at": ago(days, 10), "ai": ai}]}

    it = [merge(1, 20, ai=True, card="syn-coe-01"), merge(2, 15, ai=False, card="syn-coe-02"),
          merge(3, 14, ai=True, card="syn-coe-01"), merge(4, 10, ai=True, card="syn-coe-04"),
          merge(5, 8, ai=True, card="syn-coe-05"), merge(6, 2, ai=True, card="syn-coe-06")]
    git_it = {"schema": "icdev-it/git-history/v1", "repo": "it", "ref": "origin/main", "head": "a" * 40,
              "since": ago(30), "until": ago(-1), "merges": it,
              "reverts": [{"sha": "b" * 40, "at": ago(9, 12), "reverts": it[3]["commits"][0]["sha"]}]}
    runs = [{"sha": m["sha"], "conclusion": c, "finished_at": ago(d, 13)}
            for m, c, d in zip(it, ("success", "failure", "success", "success", "success", "cancelled"),
                               (20, 15, 14, 10, 8, 2))]
    ci_it = {"schema": "icdev-it/ci-runs/v1", "source": "github-actions", "repo": "it", "fetched_at": ago(0),
             "runs": runs}
    board_it = {"schema": "icdev-it/board-export/v1", "exported_at": ago(0),
                "tasks": [{"id": "syn-coe-07", "status": "done", "depends_on_task_id": "syn-coe-05",
                           "completed_at": ago(6)}]}
    git_os = {**git_it, "repo": "os", "ref": "gitlab/main", "merges": it[:3], "reverts": []}
    SOURCES.mkdir(parents=True, exist_ok=True)
    for name, doc in (("it.git", git_it), ("it.ci", ci_it), ("it.board", board_it), ("os.git", git_os)):
        (SOURCES / f"{name}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    for stale in ("ft.git", "ft.ci", "ft.board", "os.ci", "os.board"):
        (SOURCES / f"{stale}.json").unlink(missing_ok=True)
    # What the definitions give (args/coe_conformance.yaml), so the spec checks numbers, not shapes.
    return {"dir": str(SOURCES), "it": {"deployments": 6, "observed": 5, "lead_time_hours": 2.0,
                                        "change_failure_rate": 0.4, "rework_sample": 4},
            "os": {"deployments": 3}}


def revoke(key: str, by: str) -> int:
    """The spec's clean-up: revoke the lapsed exemption (a stop) if the journey did not."""
    from tools.idp import exemptions as e

    state = e._latest(e._open_connection(), key)
    if state and state.get("status") == e.STATUS_APPROVED:
        e.revoke_by_key(key, revoked_by={"id": by, "role": "reviewer"},
                        reason="Synthetic (icx-coe-03 e2e): clean-up of the lapsed exemption.")
    print(json.dumps({"revoked": key}))
    return 0


def main() -> int:
    from tools.idp import standards

    if len(sys.argv) == 5 and sys.argv[1] == "--revoke" and sys.argv[3] == "--by":
        return revoke(sys.argv[2], sys.argv[4])
    run = uuid.uuid4().hex[:8]
    preparer = person("coe03-preparer", "reviewer", run)
    publisher = person("coe03-publisher", "reviewer", run)
    out = {
        "run": run,
        "preparer": preparer,
        "publisher": publisher,
        "standards": standards.seed(),
        "expired": expired_exemption(preparer, publisher, f"coe03-lapsed-{run}"),
        "sources": sources(run),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
