# CUI // SP-CTI
"""Schema ownership manifest (xit-decl-04) -- every table has ONE owner: core | it | ft.

THE PROBLEM
-----------
tools/db/init_icdev_db.py is 11,757 lines and 527 CREATE TABLEs in one string
with no domain partitioning; 14 canvases, tools/kanban, tools/trading and 430
migrations declare ~1,350 more. Nothing says which of the 1,874 tables belong
to the domain-neutral kernel both ICDEV parents install, which to ICDEV[IT],
and which to the trading domain that is leaving for a private repository
(docs/programmes/icdev-domain-split.md). Without that, a core migration can
touch an IT table, an IT migration can touch an FT table, and the split has
no checkable boundary.

WHAT THIS IS
------------
An OWNERSHIP MANIFEST, not a DDL rewrite. Nothing moves and no schema changes.
``--regenerate`` reads every DDL source (tools/**/*.py, tools/db/migrations/**)
and args/schema_ownership_rules.yaml (ordered prefix rules, then the owner of
the declaring package, then a default) and writes two generated manifests:

    icdev/core/schema/tables.yaml     owner core
    tools/db/schema/tables.yaml       owner it and ft

Each manifest is one line per table (``name: owner``) plus an ``rls_exempt``
list -- tables the row-security predicate does NOT apply to, the
generalisation of get_canvas_connection. NOTHING is exempt today, so this task
changes no RLS behaviour. ``--table <name>`` rescans to show where a table is
declared.

``--check`` fails when:
  * a table named by any CREATE/ALTER/DROP TABLE in the tree is in NO manifest
    (closure: a new table cannot appear without an owner);
  * a table is in BOTH manifests;
  * the manifests disagree with the rules (stale -- run --regenerate);
  * a migration or DDL site in THIS repository touches a table whose owner is
    not in ``allowed_owners_here`` (today core, it AND ft, because FathomDesk
    still lives here; the removal PR drops ft, and from then on an ad_* table
    cannot come back through a migration).

    python tools/db/schema_ownership.py --regenerate
    python tools/db/schema_ownership.py --check [--changed <files>] [--json]
    python tools/db/schema_ownership.py --table kanban_tasks
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return start


REPO = _find_repo_root(Path(__file__).resolve().parent)
RULES_RELPATH = Path("args") / "schema_ownership_rules.yaml"
GATE_RELPATH = Path("args") / "schema_ownership_gate.yaml"
CORE_MANIFEST_RELPATH = Path("icdev") / "core" / "schema" / "tables.yaml"
DOMAIN_MANIFEST_RELPATH = Path("tools") / "db" / "schema" / "tables.yaml"
OWNERS = ("core", "it", "ft")

_CREATE_RE = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\"?\w+\"?\.)?\"?(\w+)\"?", re.I)
_TOUCH_RE = re.compile(
    r"(?:ALTER\s+TABLE|DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?)(?:ONLY\s+)?(?:\"?\w+\"?\.)?\"?(\w+)\"?", re.I
)
_SKIP_DIRS = {"__pycache__", "node_modules", ".git"}
_NOISE = {"if", "not", "exists", "table", "only"}


# ── inventory ────────────────────────────────────────────────────────────────
def ddl_sources(repo: Path = REPO) -> list[Path]:
    out: list[Path] = []
    for base in (repo / "tools",):
        for p in base.rglob("*"):
            if p.suffix in (".py", ".sql") and not any(part in _SKIP_DIRS for part in p.parts):
                out.append(p)
    return sorted(set(out))


def scan_tables(paths: list[Path], repo: Path = REPO) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(created, touched): table -> set of repo-relative files."""
    created: dict[str, set[str]] = defaultdict(set)
    touched: dict[str, set[str]] = defaultdict(set)
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = p.relative_to(repo).as_posix()
        for m in _CREATE_RE.finditer(text):
            name = m.group(1)
            if name.lower() not in _NOISE and not name.startswith("{"):
                created[name].add(rel)
        for m in _TOUCH_RE.finditer(text):
            name = m.group(1)
            if name.lower() not in _NOISE and not name.startswith("{"):
                touched[name].add(rel)
    return created, touched


# ── rules ────────────────────────────────────────────────────────────────────
def load_rules(repo: Path = REPO) -> dict:
    import yaml  # noqa: PLC0415

    data = yaml.safe_load((repo / RULES_RELPATH).read_text(encoding="utf-8")) or {}
    data["_compiled"] = [(re.compile(r"^" + str(r["match"])), str(r["owner"])) for r in data.get("prefix", [])]
    pkg_owner: dict[str, str] = {}
    for owner, pkgs in (data.get("packages") or {}).items():
        for pkg in pkgs or []:
            pkg_owner[str(pkg)] = str(owner)
    data["_pkg_owner"] = pkg_owner
    return data


def _declaring_packages(files: set[str]) -> list[str]:
    pkgs = []
    for f in sorted(files):
        parts = f.split("/")
        if len(parts) >= 2 and parts[0] == "tools":
            if parts[1] == "db" and len(parts) >= 3 and parts[2] == "migrations":
                pkgs.append("migrations")
            else:
                pkgs.append(parts[1])
    return pkgs


def assign_owner(table: str, files: set[str], rules: dict) -> tuple[str, str]:
    """Return (owner, how) -- how is 'prefix:<regex>' | 'package:<pkg>' | 'default'."""
    for rx, owner in rules["_compiled"]:
        if rx.match(table):
            return owner, f"prefix:{rx.pattern[1:]}"
    for pkg in _declaring_packages(files):
        if pkg == "migrations":
            continue
        owner = rules["_pkg_owner"].get(pkg)
        if owner:
            return owner, f"package:{pkg}"
    return str(rules.get("default", "it")), "default"


# ── manifests ────────────────────────────────────────────────────────────────
def build_manifests(repo: Path = REPO) -> dict[str, dict]:
    rules = load_rules(repo)
    created, _touched = scan_tables(ddl_sources(repo), repo)
    entries: dict[str, dict] = {}
    exempt = {str(t) for t in (rules.get("rls_exempt") or [])}
    for table, files in created.items():
        owner, how = assign_owner(table, files, rules)
        entries[table] = {"owner": owner, "rls": table not in exempt, "how": how, "declared_in": sorted(files)[:4]}
    return entries


def _dump(entries: dict[str, dict], owners: tuple[str, ...], header: str) -> str:
    """Compact, one line per table: ``name: owner``. ``rls_exempt`` lists the
    explicit opt-outs (none today). ``declared_in`` is derivable -- --table
    rescans -- and would triple the size of a committed artefact."""
    import yaml  # noqa: PLC0415

    subset = {t: e["owner"] for t, e in sorted(entries.items()) if e["owner"] in owners}
    exempt = sorted(t for t, e in entries.items() if e["owner"] in owners and e.get("rls") is False)
    return header + yaml.safe_dump({"tables": subset, "rls_exempt": exempt}, sort_keys=True, width=120)


_CORE_HEADER = """# CUI // SP-CTI
# GENERATED by `python tools/db/schema_ownership.py --regenerate` from
# args/schema_ownership_rules.yaml -- do not edit by hand; change a rule.
# Tables owned by the domain-neutral core (xit-decl-04). `rls: true` means the
# row-security predicate applies; an explicit false is the generalisation of
# get_canvas_connection. `declared_in` lists up to four declaring sources.
"""
_DOMAIN_HEADER = """# CUI // SP-CTI
# GENERATED by `python tools/db/schema_ownership.py --regenerate` from
# args/schema_ownership_rules.yaml -- do not edit by hand; change a rule.
# Tables owned by a DOMAIN: `it` (ICDEV[IT]) or `ft` (FathomDesk, leaving for
# the private ICDEV[FT] repository). See icdev/core/schema/tables.yaml for core.
"""


def regenerate(repo: Path = REPO) -> dict[str, int]:
    entries = build_manifests(repo)
    core_path = repo / CORE_MANIFEST_RELPATH
    dom_path = repo / DOMAIN_MANIFEST_RELPATH
    core_path.parent.mkdir(parents=True, exist_ok=True)
    dom_path.parent.mkdir(parents=True, exist_ok=True)
    core_path.write_text(_dump(entries, ("core",), _CORE_HEADER), encoding="utf-8", newline="\n")
    dom_path.write_text(_dump(entries, ("it", "ft"), _DOMAIN_HEADER), encoding="utf-8", newline="\n")
    counts = {o: sum(1 for e in entries.values() if e["owner"] == o) for o in OWNERS}
    counts["total"] = len(entries)
    return counts


def load_manifests(repo: Path = REPO) -> dict[str, dict]:
    import yaml  # noqa: PLC0415

    out: dict[str, dict] = {}
    for rel in (CORE_MANIFEST_RELPATH, DOMAIN_MANIFEST_RELPATH):
        p = repo / rel
        if not p.exists():
            continue
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        exempt = set(data.get("rls_exempt") or [])
        for table, owner in (data.get("tables") or {}).items():
            if table in out:
                out[table]["_duplicate"] = True
            else:
                out[table] = {"owner": str(owner), "rls": table not in exempt}
    return out


def owner_of(table: str, repo: Path = REPO) -> str | None:
    return (load_manifests(repo).get(table) or {}).get("owner")


def rls_exempt_tables(repo: Path = REPO) -> frozenset[str]:
    """Tables whose manifest entry says ``rls: false`` -- the opt-out set."""
    try:
        return frozenset(t for t, e in load_manifests(repo).items() if e.get("rls") is False)
    except Exception:  # noqa: BLE001 -- an unreadable manifest exempts nothing
        return frozenset()


# ── check ────────────────────────────────────────────────────────────────────
def load_gate(repo: Path = REPO) -> dict:
    import yaml  # noqa: PLC0415

    p = repo / GATE_RELPATH
    if not p.exists():
        return {"allowed_owners_here": list(OWNERS)}
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("schema_ownership", {})


def _staged_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def build_report(repo: Path = REPO, only: list[str] | None = None) -> dict:
    manifests = load_manifests(repo)
    gate = load_gate(repo)
    allowed = set(gate.get("allowed_owners_here") or OWNERS)
    if only is not None:
        paths = [repo / f for f in only if f.endswith((".py", ".sql")) and f.replace("\\", "/").startswith("tools/")]
        paths = [p for p in paths if p.exists()]
    else:
        paths = ddl_sources(repo)
    created, touched = scan_tables(paths, repo)

    unowned = sorted(t for t in created if t not in manifests)
    # A name that is only ALTERed/DROPped and never created here is a rename
    # temp (kanban_tasks_old_012), a table an older init created, or prose the
    # regex caught ("DROP TABLE s"). Reported so a reviewer can see it; it does
    # not fail the gate, because the closure is over tables this tree CREATES.
    touched_only = sorted(t for t in touched if t not in manifests and t not in created)
    duplicates = sorted(t for t, e in manifests.items() if e.get("_duplicate"))
    foreign = sorted(
        f"{t} (owner {manifests[t]['owner']}) touched by {sorted(created.get(t, set()) | touched.get(t, set()))[0]}"
        for t in set(created) | set(touched)
        if t in manifests and manifests[t].get("owner") not in allowed
    )
    stale: list[str] = []
    if only is None:
        fresh = build_manifests(repo)
        for t, e in fresh.items():
            have = manifests.get(t)
            if have is None or have.get("owner") != e["owner"]:
                stale.append(f"{t}: manifest {have and have.get('owner')} vs rules {e['owner']}")
        for t in manifests:
            if t not in fresh:
                stale.append(f"{t}: in manifest, declared nowhere")
    report = {
        "scope": "changed" if only is not None else "tree",
        "files_scanned": len(paths),
        "tables_seen": len(set(created) | set(touched)),
        "manifest_size": len(manifests),
        "allowed_owners_here": sorted(allowed),
        "unowned": unowned,
        "touched_only": touched_only,
        "duplicates": duplicates,
        "foreign_owner_touched": foreign,
        "stale": stale,
        "owners": {o: sum(1 for e in manifests.values() if e.get("owner") == o) for o in OWNERS},
    }
    report["ok"] = not (unowned or duplicates or foreign or stale)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--regenerate", action="store_true")
    ap.add_argument("--check", action="store_true", help="exit 1 on an unowned, duplicated, foreign or stale table")
    ap.add_argument("--changed", nargs="*")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--table", help="print the owner of one table")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.regenerate:
        counts = regenerate()
        print(f"Schema ownership: regenerated -- {counts}")
        return 0
    if args.table:
        created, _ = scan_tables(ddl_sources(REPO), REPO)
        entry = load_manifests().get(args.table) or {}
        print(json.dumps({"table": args.table, "owner": entry.get("owner"), "rls": entry.get("rls"),
                          "declared_in": sorted(created.get(args.table, set()))}, indent=2))
        return 0

    only = None
    if args.staged:
        only = _staged_files(REPO)
    elif args.changed is not None:
        only = list(args.changed)
    report = build_report(REPO, only)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Schema ownership ({report['scope']}): {report['tables_seen']} table(s) seen, manifest "
            f"{report['manifest_size']} {report['owners']}; unowned {len(report['unowned'])}, duplicates "
            f"{len(report['duplicates'])}, foreign-owner {len(report['foreign_owner_touched'])}, stale {len(report['stale'])}"
        )
        for t in report["unowned"][:30]:
            print(f"  UNOWNED  {t}  -> add a rule to args/schema_ownership_rules.yaml and --regenerate")
        for t in report["foreign_owner_touched"][:30]:
            print(f"  FOREIGN  {t}")
        for t in report["stale"][:30]:
            print(f"  STALE    {t}  -> --regenerate")
    if args.check and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
