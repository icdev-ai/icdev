#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the ICDEV[RT] stream onto the board (card ``rt``, args/projects.yaml).

ICDEV[RT] is the third parent of the ICDEV family and builds in
``icdev-ai/icdev_rt``, registered in ``args/kanban_external_repos.yaml`` as repo
``icdev_rt`` behind ``ICDEV_KANBAN_REPO_RT``. Until that env var is set every
``rt-`` task resolves external-and-unconfigured and is PARKED -- never built
inside this PUBLIC checkout, which is the whole point of registering the prefix
before seeding it.

Everything goes through ``task_factory.create_tasks`` (never a raw INSERT), is
held behind ``rt-gate-00`` via ``depends_on_task_id``, and is CLAIMED for this
session when ``--claim`` is passed -- because the session that seeds these is
the session that builds them, and a seeded-but-unclaimed row is exactly the
race that produced four duplicate PRs in two days.

    python tools/kanban/seed_rt_parent.py --dry-run
    python tools/kanban/seed_rt_parent.py --claim

A gate holds ONLY tasks that declare ``depends_on_task_id`` on it. A claim does
not stop dispatch on its own, and ``--pause-runner`` halts the whole board and
lapses silently after four hours; neither is a substitute for the gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Run-by-path bootstrap: sys.path[0] is this file's directory, never the import
# root. parents[2] is whatever holds this `tools` package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban.task_factory import create_tasks  # noqa: E402

PREFIX = "rt-"
GATE_ID = f"{PREFIX}gate-00"
REPO = "icdev-ai/icdev_rt"

_GATE_TAIL = (
    "\n\nThis task is a HOLD, not work. Leave it `in_progress`: "
    "promote_backlog_to_scheduled skips any task whose depends_on_task_id "
    "points at a task that is not done, so closing it releases the whole "
    f"{PREFIX}* stream to the runner. Do NOT open a kanban/<id> PR for it."
)

GATE: dict = {
    "id": GATE_ID,
    "title": f"MANUAL GATE - hold the {PREFIX}* stream (do not close)",
    "description": (
        "RISK: every rt- task builds in a SEPARATE PRIVATE REPO "
        f"({REPO}) whose root env var ICDEV_KANBAN_REPO_RT is deliberately "
        "UNSET. An unheld rt- task is not merely un-buildable: this checkout is "
        "PUBLIC and ICDEV[RT]'s subject matter is personal financial records. "
        "The engines are also strictly ordered -- the tax engine is an input to "
        "the projection, which is an input to the Monte Carlo, which is an "
        "input to the Roth optimizer -- and the runner cannot see that ordering."
        "\n\nRelease only once (a) the rt card is on main, (b) "
        "ICDEV_KANBAN_REPO_RT points at the checkout, and (c) the epic being "
        "released has its inputs landed. Release per EPIC, never wholesale."
        + _GATE_TAIL
    ),
    "status": "in_progress",
    "priority": "high",
    "task_type": "chore",
}


def _t(tid: str, title: str, description: str, *,
       task_type: str = "build", priority: str = "high") -> dict:
    return {
        "id": tid,
        "title": title,
        "description": description.strip() + (
            f"\n\nBuilds in {REPO}, NOT in this checkout. Held behind {GATE_ID}."
        ),
        "task_type": task_type,
        "priority": priority,
    }


TASKS: list[dict] = [
    # -- BOOT: the parent itself -------------------------------------------
    _t("rt-boot-01", "Parent declaration, package skeleton and health verdict",
       """
LANDED 2026-08-30 (icdev_rt commits 3bc1053, cf76da7) -- recorded here so the
card's denominator counts the work that actually happened rather than starting
mid-stream.

icdev_domain.yaml declaring key=rt, env_prefix=RET, databases=[icdev_rt],
port 5300 and an EXPLICIT sensitivity.order (without it the core degenerates to
[default] + egress_restricted, a ladder with no intermediate ranks, and this
parent's data is personal financial records). Plus icdev_ret/domain.py (refuses
any declaration whose key is not `rt`), icdev_ret/doctor.py, rt_api with
fail-closed bearer auth, launch_rt.py, scripts/create_pg_db.sql, and CI on a
self-hosted runner (label rt-local) asserting the identity guard BOTH ways.
""", priority="critical"),

    _t("rt-boot-02", "setup_rt.py -- the stdlib-only, idempotent easy button",
       """
`python setup_rt.py --yes` from a bare clone must produce a working install:
preflight, venv, `pip install icdev-core @ git+<core-url>@<pin>` FIRST (the IT
checkout declares an abstract icdev-core>=0.2.0 that resolves to nothing on its
own), clone ICDEV[IT] into _core/, `pip install -e ".[dev,api]"`, write .env
with a generated RET_API_TOKEN, run migrations, then `python -m
icdev_ret.doctor`.

Model: C:/ai/icdev_ft/setup_ft.py. Stdlib only -- it runs BEFORE anything is
installed. Two properties its CI asserts and RT's must too: re-running leaves
.env BYTE-IDENTICAL, and an existing .env is never overwritten.
""", priority="critical"),

    _t("rt-boot-03", "icdev_ret/db/migrate.py over the core MigrationRunner",
       """
`python -m icdev_ret.db.migrate --status | --up | --create "name"`, wrapping
tools.db.migration_runner.MigrationRunner pointed at this repo's migrations/.
14-digit UTC timestamps, so versions cannot collide with the core's and both
sets share schema_migrations.

Two things it MUST do, both learned from ICDEV[FT]:
 * assert_target_is_declared() before touching anything -- without the repo's
   own .env the connection layer falls back to ICDEV_DATABASE_URL, which is
   ICDEV[IT]'s database. FT measured exactly this: `--status` from a fresh
   worktree reported "417 applied, 3 pending" against the wrong parent.
 * override the core's default data/icdev.db with RET_DB_PATH on the SQLite
   path.
""", priority="critical"),

    _t("rt-boot-04", "First migration + a bootstrap-smoke CI job that is not vacuous",
       """
The smoke job asserts every migration in migrations/ appears in
schema_migrations. With an EMPTY migrations/ that assertion passes vacuously --
a green that measured nothing, which is the shape this family of repos exists
to refuse. So this task lands the first real migration together with the job.

First migration: rt_households and rt_people -- the two entities every
retirement plan has whatever the ledger design turns out to be. Both carry the
`classification` column icdev_domain.yaml's sensitivity ladder declares.

CI job (model: FT's bootstrap-smoke): run setup_rt.py from a bare checkout,
assert .env was written, assert the migration count matches the DIRECTORY (not
a hardcoded number), assert doctor reports no `fail`, assert `launch_rt.py
--dry-run` resolves, and assert a re-run leaves .env byte-identical.
""", priority="critical"),

    _t("rt-boot-05", "supervise_rt.py -- auto-redeploy with commit-or-rollback",
       """
Model: C:/ai/icdev_ft/supervise_ft.py. Runs launch_rt.py as a child, polls
origin/main, and on a new commit does pull --ff-only, migrate, build the UI
only if the diff touches ui/, os.execv self re-exec, restart the child, probe
GET /api/v1/health/deep, then commit or roll back against a last-good SHA.

Known trap from FT, carried forward: the migrate CHILD needs PYTHONPATH set to
the _core checkout or no deploy can succeed (icdev_ft commit fd85b70).
""", priority="medium"),

    # -- LDGR: the ledger ---------------------------------------------------
    _t("rt-ldgr-01", "Account and holding model, with tax lots",
       """
Accounts: taxable, traditional IRA/401k, Roth IRA/401k, HSA, 529, cash, real
estate, business, annuity, and policy (the IUL lives here as an asset).
Holdings carry TAX LOTS, not just a balance -- cost basis and acquisition date
are inputs to both the withdrawal ordering and the Roth optimizer, and a
balance-only model cannot answer either.

FIFO lot-closing is ~20 lines and exists as reference at 5c3ce6104^ in the
removed FathomDesk backtester (_close_fifo). REIMPLEMENT it under a neutral
name -- a `git checkout` of that path would re-publish domain code into the
public IT repo and the domain leak gate refuses it. The algorithm is not the
sensitive part; the path and provenance are.
""", priority="critical"),

    _t("rt-ldgr-02", "Income streams, expenses and debts",
       """
Income: wages, self-employment, Social Security (with claiming age), pension
(with survivor election), annuity, rental, other. Expenses by CATEGORY, because
inflation is not one number -- general CPI, medical and housing diverge over a
30-year horizon and a single rate quietly misstates the whole plan. Debts with
amortisation schedules.

Every entity carries an effective date range: a plan is a timeline, not a
snapshot.
""", priority="critical"),

    _t("rt-ldgr-03", "Persistence, migrations and the sensitivity labels",
       """
Migrations for the ldgr entities. Every table carries `classification` and the
row is labelled at its true level from the declared ladder (public, internal,
pii, account, tax_record, health) -- an account number is `account`, a filed
return is `tax_record`, a mortality assumption is `health`. Labelling
everything `internal` because it is easier is how the ladder becomes
decorative.
""", priority="high"),

    _t("rt-ldgr-04", "Ledger API + the first real screen",
       """
CRUD over the ledger through rt_api, and the Accounts screen. Mutating verbs
require the bearer token; reads stay open on the loopback bind.
""", priority="medium"),

    # -- TAX: the engine that does not exist anywhere ------------------------
    _t("rt-tax-01", "Bracket engine and the tax-table DATA files",
       """
Federal ordinary brackets, standard and itemised deduction, LTCG and qualified
dividend brackets, NIIT. Rates and thresholds live in args/tax_tables/<year>.yaml
as DATA -- a new tax year must be a YAML file, never a code change.

There is NO tax code anywhere in the ICDEV tree today: measured 2026-08-30,
every `tax` hit under tools/ is `taxonomy`. This is genuinely from scratch.
""", priority="critical"),

    _t("rt-tax-02", "Social Security taxability via provisional income",
       """
The provisional-income formula and the two thresholds that make 0/50/85% of
benefits taxable. This is the single most misunderstood interaction in
retirement planning and it drives the Roth optimizer's whole search space --
a conversion that pushes provisional income over a threshold taxes benefits
that were previously free.
""", priority="critical"),

    _t("rt-tax-03", "RMDs: Uniform Lifetime and Joint Life tables",
       """
Required minimum distributions from the IRS tables, with the correct start age
under current law and the spousal exception when the sole beneficiary is more
than ten years younger. Tables are DATA, like the brackets.
""", priority="high"),

    _t("rt-tax-04", "IRMAA with its two-year lookback",
       """
Medicare Part B and D surcharges keyed off MAGI from TWO YEARS PRIOR. The
lookback is the whole difficulty: a Roth conversion at 63 raises the premium at
65, and an optimizer that ignores the lag will recommend conversions that are
free in its model and expensive in reality. Tiers are a cliff, not a ramp.
""", priority="high"),

    _t("rt-tax-05", "ACA premium tax credit cliff, and 72(t) penalties",
       """
For a pre-65 retiree the ACA subsidy cliff is usually the BINDING constraint on
Roth conversions -- more so than any bracket -- and it is a common blind spot in
consumer planners. Plus the 10% early-withdrawal penalty and the 72(t) SEPP
exception.
""", priority="high"),

    # -- PROJ: the deterministic walk ---------------------------------------
    _t("rt-proj-01", "The year-by-year projection loop",
       """
The deterministic walk from today to age 100+, which does not exist anywhere in
this ecosystem: nothing loops over periods except a ~40-line undiscounted ROI
projection in the migration-cost API.

Order within a year: income, Social Security, RMDs, expenses inflated per
category, withdrawals per the ordering strategy, taxes, surplus to taxable,
then growth. Deterministic and replayable -- the Monte Carlo DRIVES this loop,
it never replaces it. Every figure a surface shows must be traceable to the
ledger row that produced it.
""", priority="critical"),

    _t("rt-proj-02", "Inflation and real-vs-nominal, stated on every number",
       """
Per-category inflation, and an explicit basis on every output: today's dollars
or future dollars. Boldin's own export carries this hazard -- it reflects
whichever of optimistic/average/pessimistic is toggled -- so RT records the
basis it read on import and the basis it reports on output. A number whose
basis is unstated is not a number.
""", priority="high"),

    _t("rt-proj-03", "Reconcile the projection against a Boldin export",
       """
Line-by-line against a real Boldin export for the same inputs. Differences are
EXPLAINED -- a modelling difference named and justified -- never averaged away
or absorbed into a tolerance. This is the acceptance evidence for the whole
projection engine and the first honest answer to "is my version right".
""", task_type="test", priority="critical"),

    _t("rt-proj-04", "Projection screen: the expandable year-by-year ledger",
       """
The projection rendered as an expandable ledger -- each year opening into the
income, tax and withdrawal rows that produced it. The point is auditability,
not a chart: a plan you cannot interrogate is a plan you cannot trust.
""", priority="medium"),
]


def _plan() -> list[dict]:
    specs = [GATE]
    for spec in TASKS:
        spec.setdefault("depends_on_task_id", GATE_ID)
        specs.append(spec)
    return specs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed the ICDEV[RT] stream")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    ap.add_argument("--claim", action="store_true",
                    help="claim the work tasks for THIS session (never the gate)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    specs = _plan()

    if args.dry_run:
        if args.json:
            print(json.dumps(specs, indent=2))
        else:
            for s in specs:
                dep = s.get("depends_on_task_id") or "-"
                print(f"  {s['id']:14s} {s.get('status', 'backlog'):12s} "
                      f"{s['task_type']:8s} dep={dep:12s} {s['title'][:58]}")
            print(f"\n{len(specs)} task(s) planned; nothing written (--dry-run)")
        return 0

    # The gate is NEVER claimed: it is a hold, and a lease on it would expire.
    gate, work = specs[0], specs[1:]
    created = create_tasks([gate])
    created += create_tasks(work, claim=args.claim)

    if args.json:
        print(json.dumps({"created": created, "claimed": bool(args.claim)}, indent=2))
    else:
        for tid in created:
            print(f"  created {tid}")
        print(f"\n{len(created)} created"
              + (" and claimed for this session" if args.claim else "")
              + f"; {len(specs) - len(created)} already existed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
