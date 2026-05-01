"""
tools/studio/template_linter.py
Detect and optionally auto-fix isolated nodes in workflow YAML templates.

Usage:
  python tools/studio/template_linter.py --check           # report only
  python tools/studio/template_linter.py --fix             # rewrite YAMLs in-place
  python tools/studio/template_linter.py --check --json    # machine-readable
  python tools/studio/template_linter.py --check --gate    # exit 1 if any isolated nodes found
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pyyaml not installed — pip install pyyaml", file=sys.stderr)
    sys.exit(1)

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "args" / "workflow_templates"


# ── DAG analysis ─────────────────────────────────────────────────────────────

def _normalize_deps(deps) -> list[str]:
    if not deps:
        return []
    if isinstance(deps, str):
        return [deps]
    return list(deps)


def analyze(steps: list[dict]) -> dict:
    """Return sets: roots, leaves, isolated, has_incoming, has_outgoing."""
    has_incoming: set[str] = set()
    has_outgoing: set[str] = set()
    for s in steps:
        for d in _normalize_deps(s.get("depends_on")):
            has_incoming.add(s["id"])
            has_outgoing.add(d)
    ids = {s["id"] for s in steps}
    return {
        "has_incoming": has_incoming,
        "has_outgoing": has_outgoing,
        "roots":    [s["id"] for s in steps if s["id"] not in has_incoming and s["id"] in has_outgoing],
        "leaves":   [s["id"] for s in steps if s["id"] in has_incoming and s["id"] not in has_outgoing],
        "isolated": [s["id"] for s in steps if s["id"] not in has_incoming and s["id"] not in has_outgoing],
        "dangling_deps": [d for s in steps for d in _normalize_deps(s.get("depends_on")) if d not in ids],
    }


# ── Auto-fix heuristic ────────────────────────────────────────────────────────

def auto_fix(steps: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Connect isolated nodes using declaration-order heuristics:
    - Find the "hub" — the non-isolated node with the most incoming edges.
    - If isolated node appears before the hub (by index) → wire it INTO the hub.
    - If it appears after the hub → wire hub INTO it, or append to the last leaf.
    Returns (patched_steps, list_of_changes).
    """
    import copy
    steps = copy.deepcopy(steps)
    changes: list[str] = []

    info = analyze(steps)
    isolated_ids = set(info["isolated"])
    if not isolated_ids:
        return steps, []

    idx = {s["id"]: i for i, s in enumerate(steps)}
    by_idx = sorted(steps, key=lambda s: idx[s["id"]])

    # Find hub: non-isolated node with most incoming edges
    incoming_count: dict[str, int] = {}
    for s in steps:
        for d in _normalize_deps(s.get("depends_on")):
            incoming_count[s["id"]] = incoming_count.get(s["id"], 0) + 1

    non_isolated = [s for s in steps if s["id"] not in isolated_ids]
    if not non_isolated:
        # All nodes isolated → wire them sequentially
        for i in range(1, len(steps)):
            steps[i].setdefault("depends_on", [])
            deps = _normalize_deps(steps[i]["depends_on"])
            deps.append(steps[i - 1]["id"])
            steps[i]["depends_on"] = deps
            changes.append(f"  {steps[i]['id']} ← {steps[i-1]['id']} (sequential fallback)")
        return steps, changes

    hub = max(non_isolated, key=lambda s: incoming_count.get(s["id"], 0))

    for iso_step in by_idx:
        if iso_step["id"] not in isolated_ids:
            continue
        iso_idx = idx[iso_step["id"]]
        hub_idx = idx[hub["id"]]

        if iso_idx < hub_idx:
            # Wire isolated → hub
            hub_step = next(s for s in steps if s["id"] == hub["id"])
            deps = _normalize_deps(hub_step.get("depends_on", []))
            deps.append(iso_step["id"])
            hub_step["depends_on"] = deps
            changes.append(f"  {hub['id']}.depends_on += [{iso_step['id']}]  (isolated before hub)")
        else:
            # Wire hub → isolated (isolated becomes a new leaf)
            deps = _normalize_deps(iso_step.get("depends_on", []))
            deps.append(hub["id"])
            iso_step["depends_on"] = deps
            changes.append(f"  {iso_step['id']}.depends_on += [{hub['id']}]  (isolated after hub)")

    return steps, changes


# ── File I/O ──────────────────────────────────────────────────────────────────

def load_template(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_template(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        # Preserve header comment
        f.write("# CUI // SP-CTI\n")
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(check_only: bool, as_json: bool, gate: bool) -> int:
    results = []
    any_isolated = False

    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        try:
            data = load_template(path)
        except Exception as e:
            results.append({"file": path.name, "error": str(e)})
            continue

        steps = data.get("steps", [])
        if not steps:
            continue

        info = analyze(steps)
        isolated = info["isolated"]
        dangling = info["dangling_deps"]
        ok = not isolated and not dangling

        entry: dict = {
            "file": path.name,
            "template_id": path.stem,
            "steps": len(steps),
            "isolated": isolated,
            "dangling_deps": dangling,
            "status": "ok" if ok else "fail",
        }

        if not ok:
            any_isolated = True
            if not check_only:
                patched, changes = auto_fix(steps)
                data["steps"] = patched
                save_template(path, data)
                entry["auto_fixed"] = changes
                entry["status"] = "fixed"

        results.append(entry)

    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            icon = {"ok": "OK   ", "fail": "FAIL ", "fixed": "FIXED"}.get(r.get("status", ""), "?    ")
            print(f"  {icon} {r['file']}", end="")
            if r.get("isolated"):
                print(f"  isolated: {r['isolated']}", end="")
            if r.get("dangling_deps"):
                print(f"  dangling: {r['dangling_deps']}", end="")
            if r.get("auto_fixed"):
                for c in r["auto_fixed"]:
                    print(f"\n      {c}", end="")
            print()

        total_fail = sum(1 for r in results if r.get("status") == "fail")
        total_fixed = sum(1 for r in results if r.get("status") == "fixed")
        total_ok = sum(1 for r in results if r.get("status") == "ok")
        print(f"\n  {total_ok} ok | {total_fixed} fixed | {total_fail} still failing")

    if gate and any_isolated and check_only:
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Workflow template DAG linter")
    ap.add_argument("--check", action="store_true", help="Report only, do not write files")
    ap.add_argument("--fix",   action="store_true", help="Auto-fix isolated nodes in-place")
    ap.add_argument("--json",  action="store_true", help="Output JSON")
    ap.add_argument("--gate",  action="store_true", help="Exit 1 if isolated nodes found (for CI)")
    args = ap.parse_args()

    check_only = args.check or not args.fix
    sys.exit(run(check_only=check_only, as_json=args.json, gate=args.gate))


if __name__ == "__main__":
    main()
