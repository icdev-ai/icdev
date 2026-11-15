# CUI // SP-CTI
"""Plan the waves of ONE application's own migration plan, from its components and their dependencies (ICDEV[OS]
osx-fth-13).

The migration canvas's wave planner plans server inventories; an application's migration plan had no waves, so
ICDEV[OS]'s ``migration-plan`` gate refused every approval ("the plan has no waves"). This module derives them,
deterministically, from what ``legacy_analyzer`` already recorded (``legacy_components``, ``legacy_dependencies``),
in the strangler order:

* an edge ``a -> b`` means ``a`` depends on ``b``; only edges between two components of this application count;
* components in a dependency cycle move together (they are one strongly connected component);
* wave 1 is every component that depends on nothing else in the application -- the leaves; wave ``k`` holds the
  components whose dependencies are all in earlier waves (the longest dependency path below them). So nothing a wave
  moves still calls into code left in the legacy application, except through the strangler facade.

Each wave carries its backout, derived and never written by hand: route its components back to the legacy
application through the facade (the legacy code stays deployed and authoritative until the wave closes), and, when a
later wave depends on it, back that wave out first. No dates: a wave is planned, never scheduled here. An application
with no dependency resolved inside it is one wave, and ``basis`` says why. The output is what
``apptwin_export.py --waves`` carries in the bundle's ``migration.waves``.

    python tools/modernization/app_wave_planner.py --app-id <legacy app id> [--plan-id <plan>] [--out waves.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TOOL = "icdev-it/app_wave_planner"
METHOD = ("strangler order over the application's own dependencies: leaves first, a dependency cycle moved together, "
          "each wave depending only on earlier waves (deterministic; never scheduled)")


def _sccs(nodes: list[str], deps: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's strongly connected components, iterative (a long dependency chain must not exhaust the stack)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on, stack, out = set(), [], []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(deps.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(sorted(deps.get(nxt, ())))))
                elif nxt in on:
                    low[v] = min(low[v], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
    return out


def plan(components: dict[str, str], edges: list[tuple[str, str]], *, app: str = "the application") -> dict:
    """Waves over ``components`` ({component row id: the bundle's component id}) and ``edges`` ([(source, target)]:
    source depends on target). Pure: the same inputs give the same waves."""
    nodes = sorted(components)
    deps: dict[str, set[str]] = {n: set() for n in nodes}
    for s, t in edges:
        if s in deps and t in deps and s != t:
            deps[s].add(t)
    sccs = _sccs(nodes, deps)
    of = {n: i for i, comp in enumerate(sccs) for n in comp}
    level: dict[int, int] = {}
    for i, comp in enumerate(sccs):          # Tarjan emits a component after every component it depends on
        below = {of[t] for n in comp for t in deps[n]} - {i}
        level[i] = 1 + max((level[j] for j in below), default=0)
    waves: dict[int, set[int]] = {}
    for i, k in level.items():
        waves.setdefault(k, set()).add(i)
    # which later waves call into each wave: they must be backed out first
    dependants: dict[int, set[int]] = {k: set() for k in waves}
    for n in nodes:
        for t in deps[n]:
            a, b = level[of[n]], level[of[t]]
            if a != b:
                dependants[b].add(a)
    resolved = sum(len(v) for v in deps.values())
    out = []
    for k in sorted(waves):
        ids = sorted({components[n] for i in waves[k] for n in sccs[i]})
        cycles = sorted(len(sccs[i]) for i in waves[k] if len(sccs[i]) > 1)
        first = "components that depend on nothing else in it" if k == 1 else f"components that depend only on waves 1-{k - 1}"
        title = f"Wave {k}: {len(ids)} {first}" + (f" (cycles of {', '.join(map(str, cycles))} moved together)" if cycles else "")
        backout = (f"Route these {len(ids)} components back to the legacy {app} through the strangler facade; the legacy "
                   f"code stays deployed and authoritative until this wave closes.")
        if dependants[k]:
            backout += f" Back out wave{'s' if len(dependants[k]) > 1 else ''} {', '.join(str(x) for x in sorted(dependants[k]))} first: they depend on this one."
        out.append({"id": f"w{k}", "name": title, "wave_number": k, "status": "planned", "components": ids,
                    "backout": backout, "depends_on": sorted({level[of[t]] for i in waves[k] for n in sccs[i]
                                                             for t in deps[n]} - {k})})
    basis = (f"{resolved} dependencies between {len(nodes)} components of the application" if resolved else
             f"no dependency between the application's {len(nodes)} components was resolved, so they move as one wave")
    return {"tool": TOOL, "method": METHOD, "basis": basis, "waves": out}


def report(app_id: str, conn, *, plan_id: str | None = None) -> dict:
    """The application's waves, from the scratch database's ``legacy_*`` tables."""
    app = conn.execute("SELECT name FROM legacy_applications WHERE id = %s", (app_id,)).fetchone()
    if app is None:
        raise ValueError(f"no legacy application {app_id!r}")
    comps = conn.execute("SELECT id, name, qualified_name FROM legacy_components WHERE legacy_app_id = %s",
                         (app_id,)).fetchall()
    edges = conn.execute("SELECT source_component_id, target_component_id FROM legacy_dependencies "
                         "WHERE legacy_app_id = %s AND target_component_id IS NOT NULL", (app_id,)).fetchall()
    got = plan({c["id"]: c["qualified_name"] or c["name"] for c in comps},
               [(e["source_component_id"], e["target_component_id"]) for e in edges], app=app["name"])
    return {**got, "app_id": app_id, **({"plan_id": plan_id} if plan_id else {})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Plan the waves of an application's own migration plan")
    ap.add_argument("--app-id", required=True, help="the legacy application's id (legacy_applications.id)")
    ap.add_argument("--plan-id", help="the migration plan the waves belong to (recorded, not checked)")
    ap.add_argument("--out", type=Path, help="write the waves here (default: stdout)")
    a = ap.parse_args(argv)
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rep = report(a.app_id, conn, plan_id=a.plan_id)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    finally:
        conn.close()
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "out": str(a.out), "waves": len(rep["waves"]), "basis": rep["basis"]}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
