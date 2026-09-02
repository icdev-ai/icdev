# CUI // SP-CTI
"""Domain leak gate (xit-leak-01) -- this repository is PUBLIC, and FathomDesk is leaving it.

Two things must never land here once the ICDEV[domain] split has moved the
trading domain to its private repository (docs/programmes/icdev-domain-split.md):

1. THE CODE COMING BACK. A file under a denied PATH -- tools/trading/**,
   tools/market_intel/**, the mirror copies, the trading/fathomdesk/options
   args -- or a DATA file carrying an `ad_*` table dump (`COPY ad_`,
   `INSERT INTO ad_` in a .sql/.dump/.csv). A stale branch rebased after the
   removal, a `git checkout <old-ref> -- tools/trading`, a helpful restore:
   each re-publishes the domain.
2. A BROKER CREDENTIAL. Alpaca key ids and the APCA-API headers, Kraken
   private keys, Tradier / Tastytrade / Schwab / IBKR tokens, Coinbase CDP key
   names, exchange secrets. The patterns live in
   tools/security/secret_detector.py::BUILTIN_PATTERNS beside the AWS ones
   (category "broker_credential") so the existing scanner learns them too;
   this gate is the CI/pre-commit consumer.

TWO MODES, BECAUSE THE TWO HALVES ARM AT DIFFERENT TIMES
--------------------------------------------------------
* `patterns.mode: enforce` from day one. SURVEYED 2026-08-21 over every
  tracked file: 0 hits for every rule (the Alpaca live-key shape excludes the
  AWS `AKIA` prefix the repo's test fixtures use), so arming refuses nothing
  that exists.
* `paths.mode: report` until the removal lands (xit-rm-*): the denied paths
  are still legitimately present today, so `enforce` would fail every commit.
  The flip to `enforce` is ONE line in args/domain_leak_gate.yaml and belongs
  to the removal PR, where it is reviewable.

A match in an ALLOWED path (args/domain_leak_gate.yaml `allow`, each with a
written reason -- the negative-control test fixtures) is reported and not
counted. Stand the gate down only with ICDEV_DOMAIN_LEAK_GUARD=0, never a
shell neutraliser.

    python tools/ci/domain_leak_gate.py --check            # the gate (tree)
    python tools/ci/domain_leak_gate.py --staged --check   # pre-commit
    python tools/ci/domain_leak_gate.py --changed a.py b.sql --check
    python tools/ci/domain_leak_gate.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

# Run by path, sys.path[0] is this file's directory and `tools.*` would resolve
# to whichever checkout `icdev` is pip-installed from -- the MAIN checkout, not
# the worktree being gated. Bootstrap the import root first (the sys.path idiom
# the self-root census explicitly allows).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

GUARD_ENV = "ICDEV_DOMAIN_LEAK_GUARD"
GATE_KEY = "domain_leak_gate"
BROKER_CATEGORY = "broker_credential"
# The categories enforced when the config does not say. Keeping the historical
# set as the default means an older args/domain_leak_gate.yaml is unchanged.
DEFAULT_CATEGORIES = ("broker_credential",)

_TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini",
    ".env", ".template", ".sample", ".sql", ".dump", ".csv", ".sh", ".ps1",
    ".bat", ".js", ".ts", ".html", ".j2", ".xml", ".rst", "",
}
_DATA_SUFFIXES = {".sql", ".dump", ".csv", ".tsv", ".txt"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tmp", "playwright-report"}


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return start


REPO = _find_repo_root(Path(__file__).resolve().parent)


def gate_file(repo: Path = REPO) -> Path:
    return repo / "args" / "domain_leak_gate.yaml"


def load_gate(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else gate_file(REPO)
    import yaml  # noqa: PLC0415 -- pyyaml IS declared

    if not path.exists():
        raise SystemExit(f"domain_leak_gate: missing gate config {path}")
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(GATE_KEY, {})


def _builtin_patterns() -> list[dict]:
    try:
        from tools.security.secret_detector import BUILTIN_PATTERNS
    except Exception:  # noqa: BLE001 -- fall back to the packaged copy
        from icdev.tools.security.secret_detector import BUILTIN_PATTERNS  # type: ignore
    return list(BUILTIN_PATTERNS)


def broker_patterns() -> list[dict]:
    """The broker-credential rules, read from the ONE secret-pattern table."""
    return [p for p in _builtin_patterns() if p.get("category") == BROKER_CATEGORY]


def gate_patterns(cfg: dict) -> list[dict]:
    """Every rule this gate enforces, by CATEGORY, from the one table.

    `patterns.categories` defaults to the broker set alone, so a config that
    predates this key behaves exactly as before. ICDEV[RT] adds
    `personal_financial`: that parent's data IS personal financial records and
    this repository is PUBLIC.

    An unknown category name is a CONFIG ERROR, not an empty result. Silently
    matching nothing is how a gate reports clean over a rule set it never
    loaded -- the failure shape this whole file exists to refuse.
    """
    declared = list((cfg.get("patterns", {}) or {}).get("categories") or DEFAULT_CATEGORIES)
    table = _builtin_patterns()
    known = {p.get("category") for p in table if p.get("category")}
    unknown = [c for c in declared if c not in known]
    if unknown:
        raise SystemExit(
            f"domain_leak_gate: patterns.categories names {unknown!r}, which no rule in "
            f"secret_detector.BUILTIN_PATTERNS declares (known: {sorted(known)})"
        )
    wanted = set(declared)
    return [p for p in table if p.get("category") in wanted]


def _allowed(rel: str, cfg: dict) -> str | None:
    for entry in cfg.get("allow", []) or []:
        if fnmatch(rel, entry.get("path", "")):
            return str(entry.get("reason", ""))
    return None


def _denied_path(rel: str, cfg: dict) -> bool:
    return any(fnmatch(rel, pat) for pat in (cfg.get("paths", {}) or {}).get("deny", []) or [])


def _tracked_files(repo: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=False).stdout
    files = [line.strip() for line in out.splitlines() if line.strip()]
    if files:
        return files
    # not a git checkout (a test fixture): walk
    found = []
    for p in repo.rglob("*"):
        if p.is_file() and not any(part in _SKIP_DIRS for part in p.parts):
            found.append(p.relative_to(repo).as_posix())
    return sorted(found)


def _staged_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def scan(repo: Path, files: list[str], cfg: dict) -> dict:
    rules = [(re.compile(p["pattern"]), p["name"], p.get("severity", "high")) for p in gate_patterns(cfg)]
    markers = [m for m in (cfg.get("sql_markers") or [])]
    # DATA-FILE-ONLY rules. An SSN shape measures 45 hits across this tree and
    # every one is a fixture or doc example of the redaction subsystem itself
    # (40 .py, 5 .md, all the canonical 123-45-6789). In a DATA file it is 0 --
    # and a data file is what a real leak looks like: an export, not source.
    # Same scoping `sql_markers` already uses, and for the same reason.
    data_rules = [
        (re.compile(d["pattern"]), d.get("name", d["pattern"]), d.get("severity", "critical"))
        for d in (cfg.get("data_patterns") or [])
    ]
    # PATH-SCOPED rules. Same idea as data_patterns, scoped by PATH rather than
    # suffix. Bid pricing (a ROM total, a cost-share figure, a labor basis of
    # estimate) is competition-sensitive in a PUBLIC repo -- but the same prose
    # is legitimate in a test fixture, and measured across this tree the
    # unscoped rule hits 11 fixtures of the publish/redaction gates that exist
    # to handle exactly this content. A rule that refuses those is one people
    # learn to bypass. Scoped to govcon SOURCE the same rules measure 0 hits
    # over 214 files, and 6 on the pre-fix tree they were written for.
    scoped_rules = [
        (re.compile(s["pattern"]), s.get("name", s["pattern"]),
         s.get("severity", "critical"), [g for g in (s.get("paths") or [])],
         s.get("kind", "pursuit_sensitive"))
        for s in (cfg.get("scoped_patterns") or [])
    ]
    findings: list[dict] = []
    denied: list[str] = []
    allowed_hits: list[dict] = []
    for rel in files:
        rel = rel.replace("\\", "/")
        path = repo / rel
        if _denied_path(rel, cfg):
            denied.append(rel)
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits: list[dict] = []
        for lineno, line in enumerate(text.splitlines(), 1):
            for rx, name, severity in rules:
                if rx.search(line):
                    hits.append({"file": rel, "line": lineno, "rule": name, "severity": severity,
                                 "kind": "broker_credential"})
            if path.suffix.lower() in _DATA_SUFFIXES:
                for rx, name, severity in data_rules:
                    if rx.search(line):
                        hits.append({"file": rel, "line": lineno, "rule": name,
                                     "severity": severity, "kind": "personal_data_dump"})
                for m in markers:
                    if m in line:
                        hits.append({"file": rel, "line": lineno, "rule": f"data dump marker {m!r}",
                                     "severity": "critical", "kind": "ad_table_dump"})
            for rx, name, severity, globs, kind in scoped_rules:
                if any(fnmatch(rel, g) for g in globs) and rx.search(line):
                    hits.append({"file": rel, "line": lineno, "rule": name,
                                 "severity": severity, "kind": kind})
        if not hits:
            continue
        reason = _allowed(rel, cfg)
        if reason is not None:
            for h in hits:
                h["allowed_reason"] = reason
            allowed_hits.extend(hits)
        else:
            findings.extend(hits)
    return {"findings": findings, "denied_paths": sorted(set(denied)), "allowed_hits": allowed_hits}


def build_report(repo: Path = REPO, only: list[str] | None = None) -> dict:
    cfg = load_gate(gate_file(repo))
    files = only if only is not None else _tracked_files(repo)
    result = scan(repo, files, cfg)
    paths_mode = str((cfg.get("paths") or {}).get("mode", "report"))
    patterns_mode = str((cfg.get("patterns") or {}).get("mode", "enforce"))
    enforced = os.environ.get(GUARD_ENV, "1").strip().lower() not in ("0", "false", "no", "monitor")
    path_violation = bool(result["denied_paths"]) and paths_mode == "enforce"
    pattern_violation = bool(result["findings"]) and patterns_mode == "enforce"
    return {
        "scope": "changed" if only is not None else "tree",
        "files_scanned": len(files),
        # The rules ACTUALLY ENFORCED on this run, not the broker set. It read
        # len(broker_patterns()) regardless of configuration, so once a second
        # category was declared the report understated its own coverage -- a
        # number that no longer measures what it names.
        "rules": len(gate_patterns(cfg)),
        "paths_mode": paths_mode,
        "patterns_mode": patterns_mode,
        "enforced": enforced,
        "denied_paths_present": result["denied_paths"],
        "findings": result["findings"],
        "allowed_hits": result["allowed_hits"],
        "ok": not (enforced and (path_violation or pattern_violation)),
        "violations": {"paths": path_violation, "patterns": pattern_violation},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on a violation")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--changed", nargs="*", help="limit the scan to these files")
    ap.add_argument("--staged", action="store_true", help="scan only staged files")
    ap.add_argument("--root", default=None, help="repository root (default: this checkout)")
    args = ap.parse_args(argv)
    repo = Path(args.root).resolve() if args.root else REPO

    only = None
    if args.staged:
        only = _staged_files(repo)
    elif args.changed is not None:
        only = list(args.changed)
    report = build_report(repo, only)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Domain leak gate ({report['scope']}): {report['files_scanned']} file(s), "
            f"{report['rules']} broker rule(s); {len(report['findings'])} finding(s), "
            f"{len(report['denied_paths_present'])} denied path(s) present "
            f"[paths:{report['paths_mode']} patterns:{report['patterns_mode']}"
            f"{'' if report['enforced'] else ' GUARD OFF'}]"
        )
        for f in report["findings"][:40]:
            print(f"  LEAK  {f['file']}:{f['line']}  {f['rule']}  [{f['severity']}]")
        if report["denied_paths_present"] and report["paths_mode"] == "enforce":
            for p in report["denied_paths_present"][:40]:
                print(f"  DENIED PATH  {p}")
        elif report["denied_paths_present"]:
            print(f"  (report) {len(report['denied_paths_present'])} denied path(s) still present — "
                  "paths.mode flips to enforce in the removal PR")
        for h in report["allowed_hits"][:10]:
            print(f"  allowed  {h['file']}:{h['line']}  {h['rule']}  ({h['allowed_reason']})")

    if args.check and not report["ok"]:
        print(
            "\nThis repository is PUBLIC. A broker credential, an ad_* table dump or a "
            "file under a removed trading path must not be committed here. Move it to "
            "the private ICDEV[FT] repository, or reference the secret with an "
            "env:/vault: ref (args/databridge_connections.yaml grammar). An allow "
            "entry in args/domain_leak_gate.yaml needs a written reason.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
